import re
import ssl
import shutil
import time
import io
import zipfile
import uvicorn
import asyncio
import threading
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field
from loguru import logger
import sys
import subprocess
import os
from pathlib import Path
import tempfile
import json
import uuid
import urllib.request
import urllib.error
import socket
from typing import Any

# Configure loguru
logger.remove()
logger.add(sys.stdout, format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}", level="INFO")


def load_dotenv(dotenv_path: Path) -> None:
    """Load simple KEY=VALUE pairs from a .env file into process environment."""
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # Strip optional single/double quotes around values.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        # Preserve any value already provided by the shell/runtime environment.
        os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

YOUTUBE_AUTH_HELP = (
    "YouTube requested sign-in verification. In remote/browser devcontainer sessions, "
    "the app cannot access your local browser cookies automatically. "
    "Run the devcontainer locally and provide yt-dlp cookies as needed."
)


def _yt_requires_auth(stderr_text: str) -> bool:
    text = (stderr_text or "").lower()
    return (
        "sign in to confirm" in text
        or "not a bot" in text
        or "--cookies-from-browser" in text
        or "--cookies" in text
    )


def _subtitle_error_detail(stderr_text: str) -> str:
    raw = (stderr_text or "").strip() or "yt-dlp subtitle download failed"
    if _yt_requires_auth(raw):
        return f"Failed to download subtitles: {YOUTUBE_AUTH_HELP}"
    return f"Failed to download subtitles: {raw}"

app = FastAPI()

# Mount static files for CSS, JS, and other assets
static_path = BASE_DIR / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Setup Jinja2 templating
templates_path = BASE_DIR / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(templates_path)))

class YouTubeURL(BaseModel):
    url: str

class TimestampRange(BaseModel):
    start: str
    end: str

class HighlightReelRequest(BaseModel):
    url: str
    timestamps: list[TimestampRange]

class ChapterDetectRequest(BaseModel):
    url: str

class MetadataInvestigationRequest(BaseModel):
    url: str
    keywords: str


class DevcontainerFeatureOption(BaseModel):
    type: str
    default: Any = None
    proposals: list[str] = Field(default_factory=list)
    description: str = ""


class DevcontainerFeatureCatalogItem(BaseModel):
    id: str
    displayName: str
    maintainer: str
    reference: str
    documentationURL: str
    description: str
    options: dict[str, DevcontainerFeatureOption] = Field(default_factory=dict)


class DevcontainerSelectedFeature(BaseModel):
    reference: str
    options: dict[str, Any] = Field(default_factory=dict)


DEVCONTAINER_BUILDER_FILES_DIR = BASE_DIR / "devcontainer-builder-files"


class DevcontainerBuildRequest(BaseModel):
    name: str
    profile: str = "base"
    baseImage: str = "ubuntu:noble"
    features: list[DevcontainerSelectedFeature] = Field(default_factory=list)
    includeGitIgnore: bool = True
    forwardPorts: list[int] = Field(default_factory=list)
    portsAttributes: dict[str, dict[str, str]] = Field(default_factory=dict)
    hostRequirements: dict[str, Any] = Field(default_factory=dict)
    postCreateCommand: str = ""
    postAttachCommand: str = ""
    secrets: dict[str, dict[str, str]] = Field(default_factory=dict)


def render_template_html(template_name: str) -> str:
    template = jinja_env.get_template(template_name)
    return template.render()


DEVCONTAINER_FEATURE_CATALOG_FILE = BASE_DIR / "devcontainer_feature_catalog.json"


def _load_devcontainer_feature_catalog() -> list[DevcontainerFeatureCatalogItem]:
    if not DEVCONTAINER_FEATURE_CATALOG_FILE.exists():
        logger.warning("Devcontainer feature catalog file not found")
        return []

    try:
        raw = json.loads(DEVCONTAINER_FEATURE_CATALOG_FILE.read_text(encoding="utf-8"))
        items = raw.get("features", [])
        return [DevcontainerFeatureCatalogItem(**item) for item in items]
    except Exception as exc:
        logger.error(f"Failed to load devcontainer feature catalog: {str(exc)}")
        return []


DEVCONTAINER_FEATURES = _load_devcontainer_feature_catalog()
DEVCONTAINER_FEATURE_BY_REF = {feature.reference: feature for feature in DEVCONTAINER_FEATURES}

MANDATORY_FEATURE_REFS = [
    "ghcr.io/devcontainers/features/docker-in-docker:2.16.1",
    "ghcr.io/devcontainers/features/github-cli:1.1.0",
    "ghcr.io/devcontainers/features/python:1.8.0",
    "ghcr.io/devcontainers-extra/features/wget-apt-get:1.0.17",
]


def _sanitize_devcontainer_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Demo name is required")

    clean = re.sub(r"[^a-zA-Z0-9._ -]", "_", name)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Demo name is invalid")
    if len(clean) > 80:
        clean = clean[:80].rstrip()
    return clean


def _coerce_feature_option_value(option_name: str, option_meta: DevcontainerFeatureOption, value: Any) -> Any:
    if option_meta.type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise HTTPException(status_code=400, detail=f"Invalid boolean value for option '{option_name}'")

    if option_meta.type == "string":
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value)

    return value


def _validate_selected_features(features: list[DevcontainerSelectedFeature]) -> dict[str, dict[str, Any]]:
    if len(features) > 25:
        raise HTTPException(status_code=400, detail="Too many selected features (max 25)")

    assembled: dict[str, dict[str, Any]] = {}
    for selected in features:
        ref = (selected.reference or "").strip()
        if not ref:
            raise HTTPException(status_code=400, detail="Feature reference is required")
        if ref in assembled:
            raise HTTPException(status_code=400, detail=f"Duplicate feature selected: {ref}")

        catalog_item = DEVCONTAINER_FEATURE_BY_REF.get(ref)
        if not catalog_item:
            raise HTTPException(status_code=400, detail=f"Unknown feature reference: {ref}")

        provided_options = selected.options or {}
        validated_options: dict[str, Any] = {}

        for provided_name, provided_value in provided_options.items():
            if provided_name not in catalog_item.options:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown option '{provided_name}' for feature '{ref}'",
                )
            option_meta = catalog_item.options[provided_name]
            validated_options[provided_name] = _coerce_feature_option_value(
                provided_name,
                option_meta,
                provided_value,
            )

        assembled[ref] = validated_options

    return assembled


def _default_feature_options(reference: str) -> dict[str, Any]:
    catalog_item = DEVCONTAINER_FEATURE_BY_REF.get(reference)
    if not catalog_item:
        return {}

    defaults: dict[str, Any] = {}
    for option_name, option_meta in catalog_item.options.items():
        defaults[option_name] = _coerce_feature_option_value(option_name, option_meta, option_meta.default)
    return defaults


def _apply_mandatory_features(selected_features: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged = dict(selected_features)
    for reference in MANDATORY_FEATURE_REFS:
        if reference not in merged and reference in DEVCONTAINER_FEATURE_BY_REF:
            merged[reference] = _default_feature_options(reference)
    return merged


_BASE_DEFAULTS = {
    "postCreateCommand": "pip install -r .devcontainer/requirements.txt && python environment_installer.py",
    "postAttachCommand": "python on_attach.py"
}

_KUBERNETES_DEFAULTS = {
    "forwardPorts": [8080],
    "portsAttributes": {"8080": {"label": "OTEL Demo"}},
    "hostRequirements": {"cpus": 2},
    "postCreateCommand": "pip install -r .devcontainer/requirements.txt && python environment_installer.py",
    "postAttachCommand": "python on_attach.py",
    "secrets": {
        "DT_ENVIRONMENT_ID": {"description": "eg. abc12345 from https://abc12345.live.dynatrace.com"},
        "DT_ENVIRONMENT_TYPE": {"description": "eg. live, sprint or dev. If unsure, use live."},
        "DT_API_TOKEN": {"description": "Dynatrace API token"},
    },
}


def _build_devcontainer_json_payload(request_data: DevcontainerBuildRequest) -> dict[str, Any]:
    container_name = _sanitize_devcontainer_name(request_data.name)
    base_image = (request_data.baseImage or "").strip() or "ubuntu:noble"
    selected_features = _apply_mandatory_features(_validate_selected_features(request_data.features))
    is_kubernetes = request_data.profile == "kubernetes"

    payload: dict[str, Any] = {
        "name": container_name,
        "image": base_image,
        "features": selected_features,
    }

    forward_ports = request_data.forwardPorts or (is_kubernetes and _KUBERNETES_DEFAULTS["forwardPorts"]) or []
    if forward_ports:
        payload["forwardPorts"] = forward_ports

    ports_attributes = request_data.portsAttributes or (is_kubernetes and _KUBERNETES_DEFAULTS["portsAttributes"]) or {}
    if ports_attributes:
        payload["portsAttributes"] = ports_attributes

    host_requirements = request_data.hostRequirements or (is_kubernetes and _KUBERNETES_DEFAULTS["hostRequirements"]) or {}
    if host_requirements:
        payload["hostRequirements"] = host_requirements

    post_create = (request_data.postCreateCommand or "").strip()
    if not post_create:
        post_create = _KUBERNETES_DEFAULTS["postCreateCommand"] if is_kubernetes else _BASE_DEFAULTS["postCreateCommand"]
    if post_create:
        payload["postCreateCommand"] = post_create

    post_attach = (request_data.postAttachCommand or "").strip()
    if not post_attach:
        post_attach = _KUBERNETES_DEFAULTS["postAttachCommand"] if is_kubernetes else _BASE_DEFAULTS["postAttachCommand"]
    if post_attach:
        payload["postAttachCommand"] = post_attach

    secrets = request_data.secrets or (is_kubernetes and _KUBERNETES_DEFAULTS["secrets"]) or {}
    if secrets:
        payload["secrets"] = secrets

    payload["remoteEnv"] = {"RepositoryName": container_name}

    return payload


def _build_devcontainer_zip_bytes(request_data: DevcontainerBuildRequest) -> bytes:
    devcontainer_json = _build_devcontainer_json_payload(request_data)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(zipfile.ZipInfo(".devcontainer/"), "")
        zipf.writestr(".devcontainer/devcontainer.json", json.dumps(devcontainer_json, indent=2) + "\n")

        readme_content = (
            "# Devcontainer Scaffold\n\n"
            "Generated by DevRel Toolbox.\n\n"
            "## Quick Start\n"
            "1. Extract this ZIP into your repository root.\n"
            "2. Open the folder in VS Code.\n"
            "3. Run: Dev Containers: Reopen in Container.\n"
        )
        zipf.writestr(".devcontainer/README.md", readme_content)

        env_content = (
            "DT_ENVIRONMENT_ID=abc12345\n"
            "# Use \"live\", \"sprint\", or \"dev\". Defaults to \"live\" if unset.\n"
            "DT_ENVIRONMENT_TYPE=live\n"
            "DT_API_TOKEN=dt0s01.sample.secret\n"
        )
        zipf.writestr(".env", env_content)

        if request_data.includeGitIgnore:
            gitignore_content = (
                "# Local environment files\n"
                ".env\n"
                ".env.*\n\n"
                "# Python cache\n"
                "__pycache__/\n"
            )
            zipf.writestr(".gitignore", gitignore_content)

        utils_path = DEVCONTAINER_BUILDER_FILES_DIR / "utils.py"
        if utils_path.exists():
            zipf.write(utils_path, "utils.py")

        if request_data.profile == "kubernetes":
            installer_src = DEVCONTAINER_BUILDER_FILES_DIR / "environment_installer_kubernetes.py"
        else:
            installer_src = DEVCONTAINER_BUILDER_FILES_DIR / "environment_installer_base.py"
        if installer_src.exists():
            zipf.write(installer_src, "environment_installer.py")

        requirements_path = DEVCONTAINER_BUILDER_FILES_DIR / "requirements.txt"
        if requirements_path.exists():
            zipf.write(requirements_path, ".devcontainer/requirements.txt")

        if request_data.profile == "kubernetes":
            on_attach_src = DEVCONTAINER_BUILDER_FILES_DIR / "on_attach_kubernetes.py"
        else:
            on_attach_src = DEVCONTAINER_BUILDER_FILES_DIR / "on_attach.py"
        if on_attach_src.exists():
            zipf.write(on_attach_src, "on_attach.py")

        if request_data.profile == "kubernetes":
            kind_cluster_src = DEVCONTAINER_BUILDER_FILES_DIR / "kind-cluster.yml"
            if kind_cluster_src.exists():
                kind_cluster_content = kind_cluster_src.read_text(encoding="utf-8")
                kind_cluster_content = kind_cluster_content.replace("{name}", devcontainer_json["name"])
                zipf.writestr(".devcontainer/kind-cluster.yml", kind_cluster_content)

    return buffer.getvalue()


# Create a temporary directory for subtitles
SUBTITLES_DIR = Path(tempfile.gettempdir()) / "youtube_subtitles"
SUBTITLES_DIR.mkdir(exist_ok=True)

# Persistent cache: raw SRT files keyed by YouTube video ID
SUBTITLE_CACHE_DIR = BASE_DIR / "subtitle_cache"
SUBTITLE_CACHE_DIR.mkdir(exist_ok=True)


def extract_video_id(url: str) -> str | None:
    """Return the YouTube video ID from a URL, or None if it cannot be parsed."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None

WORDLIST_FILE = BASE_DIR / "wordlist.json"
try:
    with open(WORDLIST_FILE) as f:
        WORDLIST = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.error(f"Could not load wordlist: {e}")
    WORDLIST = {}

# Temporary directory for corrected SRT files
CORRECTED_DIR = Path(tempfile.gettempdir()) / "corrected_srts"
CORRECTED_DIR.mkdir(exist_ok=True)

# Temporary directory for MP4 transcription outputs
TRANSCRIPTIONS_DIR = Path(tempfile.gettempdir()) / "srt_transcriptions"
TRANSCRIPTIONS_DIR.mkdir(exist_ok=True)

# Persistent cache: downloaded videos keyed by YouTube video ID
VIDEO_CACHE_DIR = BASE_DIR / "video_cache"
VIDEO_CACHE_DIR.mkdir(exist_ok=True)

# Temporary directory for generated highlight GIFs
GIF_OUTPUT_DIR = Path(tempfile.gettempdir()) / "highlight_gifs"
GIF_OUTPUT_DIR.mkdir(exist_ok=True)

# LangDock / OpenAI-compatible endpoint configuration
LANGDOCK_ENDPOINT = "https://chat.langdock.internal.dynatrace.com/api/public/openai/eu/v1/chat/completions"
LANGDOCK_MODEL = "gpt-5-1"


def parse_srt(content: str) -> list:
    """Parse SRT file content into a list of blocks."""
    blocks = []
    for block in re.split(r'\n\n+', content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        if not lines[0].strip().isdigit():
            continue
        if '-->' not in lines[1]:
            continue
        blocks.append({
            'index': lines[0].strip(),
            'timecode': lines[1].strip(),
            'text_lines': lines[2:],
        })
    return blocks


def format_srt(blocks: list) -> str:
    """Serialize SRT blocks back to a string."""
    parts = []
    for block in blocks:
        text = '\n'.join(block['text_lines'])
        parts.append(f"{block['index']}\n{block['timecode']}\n{text}")
    return '\n\n'.join(parts) + '\n'


def correct_text(text: str, wordlist: dict, block_index: str) -> tuple:
    """
    Correct text using the wordlist only (case-insensitive matching).
    Multi-word phrases are matched first, then single words.
    Returns (corrected_text, list_of_change_dicts).
    """
    changes = []

    # ── Pass 1: multi-word phrase replacements ───────────────────────────────
    # Sort longest phrases first so "Dino TR ace" beats "Dino TR" if both exist.
    phrase_keys = sorted(
        [k for k in wordlist if ' ' in k],
        key=lambda k: len(k),
        reverse=True,
    )
    for wrong in phrase_keys:
        right = wordlist[wrong]
        pattern = re.compile(re.escape(wrong), re.IGNORECASE)
        def _replace_phrase(m, right=right, wrong=wrong):
            original = m.group(0)
            if original != right:
                changes.append({
                    'original': original,
                    'corrected': right,
                    'type': 'wordlist',
                    'block': block_index,
                })
            return right
        text = pattern.sub(_replace_phrase, text)

    # ── Pass 2: single-word replacements ────────────────────────────────────
    single_keys = {k.lower(): v for k, v in wordlist.items() if ' ' not in k}

    def replace_word(m):
        word = m.group(0)
        right = single_keys.get(word.lower())
        if right is not None:
            if word != right:
                changes.append({
                    'original': word,
                    'corrected': right,
                    'type': 'wordlist',
                    'block': block_index,
                })
            return right
        return word

    text = re.sub(r"\b[a-zA-Z][a-zA-Z'-]*\b", replace_word, text)
    return text, changes


def generate_summary(all_changes: list, total_blocks: int, filename: str) -> str:
    """Generate a markdown executive summary of all corrections made."""
    lines = [
        "# SRT Correction Summary",
        "",
        f"**File:** `{filename}`  ",
        f"**Subtitle blocks processed:** {total_blocks}  ",
        f"**Total corrections made:** {len(all_changes)}",
        "",
    ]

    if not all_changes:
        lines.append("_No corrections were necessary. The file looks clean!_")
        return '\n'.join(lines)

    lines += [
        f"## Wordlist Corrections ({len(all_changes)})",
        "",
        "| Block | Original | Corrected |",
        "|-------|----------|-----------|",
    ]
    for c in all_changes:
        lines.append(f"| {c['block']} | `{c['original']}` | `{c['corrected']}` |")
    lines.append("")

    return '\n'.join(lines)


def apply_wordlist_to_srt(content: str) -> tuple[str, int]:
    """Apply existing wordlist correction logic to full SRT content."""
    blocks = parse_srt(content)
    corrected_blocks = []
    all_changes = []

    for block in blocks:
        corrected_lines = []
        for line in block['text_lines']:
            corrected_line, changes = correct_text(line, WORDLIST, block['index'])
            all_changes.extend(changes)
            corrected_lines.append(corrected_line)
        corrected_blocks.append({**block, 'text_lines': corrected_lines})

    if not corrected_blocks:
        return content, 0

    return format_srt(corrected_blocks), len(all_changes)


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the HTML UI"""
    template = jinja_env.get_template("base.html")
    return template.render()


@app.get("/color-picker", response_class=HTMLResponse)
async def get_color_picker():
    """Serve the standalone Dynatrace core color picker page."""
    return render_template_html("color-picker.html")


@app.get("/wordlist-manager", response_class=HTMLResponse)
async def get_wordlist_manager():
    """Serve the standalone Wordlist Manager page."""
    return render_template_html("wordlist-manager.html")


@app.get("/code-cards", response_class=HTMLResponse)
async def get_code_cards():
    """Serve the standalone Code Cards page."""
    return HTMLResponse(
        content=render_template_html("code-cards.html"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/devcontainer-builder", response_class=HTMLResponse)
async def get_devcontainer_builder():
    """Serve the standalone Devcontainer Builder wizard page."""
    return render_template_html("devcontainer-builder.html")

@app.post("/api/download-subtitles")
async def download_subtitles(data: YouTubeURL):
    """Download automatic subtitles from YouTube video"""
    try:
        logger.info(f"Processing YouTube URL: {data.url}")

        video_id = extract_video_id(data.url)
        cache_file = SUBTITLE_CACHE_DIR / f"{video_id}.srt" if video_id else None

        if cache_file and cache_file.exists():
            logger.info(f"Cache hit for video ID {video_id} – skipping yt-dlp")
            raw_content = cache_file.read_text(encoding='utf-8', errors='replace')
            source_name = f"{video_id}.srt"
        else:
            # Generate output filename
            output_template = str(SUBTITLES_DIR / "%(title)s.%(ext)s")

            # Use yt-dlp to download automatic subtitles
            cmd = [
                "yt-dlp",
                "--write-auto-subs",  # Download automatic subtitles
                "--sub-format", "srt",  # Format as SRT
                "--skip-download",  # Skip video download
                "-o", output_template,
                data.url
            ]

            logger.info(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                logger.error(f"yt-dlp error: {result.stderr}")
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "detail": _subtitle_error_detail(result.stderr)
                })

            logger.info(f"yt-dlp output: {result.stdout}")

            # Find the downloaded SRT file
            srt_files = list(SUBTITLES_DIR.glob("*.srt"))
            if not srt_files:
                logger.error("No SRT file was generated")
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "detail": "No subtitles found for this video. The video may not have automatic captions available."
                })

            # Get the most recently created file
            srt_file = max(srt_files, key=os.path.getctime)
            logger.info(f"Subtitles downloaded successfully: {srt_file}")
            raw_content = srt_file.read_text(encoding='utf-8', errors='replace')
            source_name = srt_file.name

            # Store in cache for future requests
            if cache_file:
                cache_file.write_text(raw_content, encoding='utf-8')
                logger.info(f"Cached raw SRT for video ID {video_id}")

        # ── Auto-correct with wordlist ────────────────────────────────────
        blocks = parse_srt(raw_content)
        all_changes = []
        corrected_blocks = []
        for block in blocks:
            corrected_lines = []
            for line in block['text_lines']:
                corrected_line, changes = correct_text(line, WORDLIST, block['index'])
                all_changes.extend(changes)
                corrected_lines.append(corrected_line)
            corrected_blocks.append({**block, 'text_lines': corrected_lines})
        corrected_content = format_srt(corrected_blocks)

        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', source_name)
        output_filename = f"corrected_{safe_name}"
        output_path = CORRECTED_DIR / output_filename
        output_path.write_text(corrected_content, encoding='utf-8', errors='replace')
        logger.info(f"Auto-correction applied: {len(all_changes)} change(s) for {source_name}")
        summary = generate_summary(all_changes, len(blocks), source_name)
        # ─────────────────────────────────────────────────────────────────

        return {
            "status": "success",
            "message": "Subtitles downloaded and corrected successfully",
            "filename": output_filename,
            "download_url": f"/download-corrected/{output_filename}",
            "changes_count": len(all_changes),
            "summary": summary,
        }
        
    except subprocess.TimeoutExpired:
        logger.error("Download timed out")
        return JSONResponse(status_code=400, content={
            "status": "error",
            "detail": "Download timed out. Please try again."
        })
    except Exception as e:
        logger.error(f"Error downloading subtitles: {str(e)}")
        return JSONResponse(status_code=400, content={
            "status": "error",
            "detail": f"Error: {str(e)}"
        })

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download the subtitle file"""
    file_path = SUBTITLES_DIR / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path, media_type="text/plain", filename=filename)


@app.post("/api/correct-srt")
async def correct_srt_endpoint(file: UploadFile = File(...)):
    """Upload an SRT file, correct spelling and wordlist terms, return corrected SRT + summary."""
    if not file.filename or not file.filename.lower().endswith('.srt'):
        raise HTTPException(status_code=400, detail="Only .srt files are accepted")

    content_bytes = await file.read()
    if len(content_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    try:
        content = content_bytes.decode('utf-8', errors='replace')
    except UnicodeDecodeError:
        content = content_bytes.decode('latin-1', errors='replace')

    blocks = parse_srt(content)
    if not blocks:
        raise HTTPException(status_code=400, detail="No valid SRT blocks found. Please check the file format.")

    logger.info(f"Correcting SRT: {file.filename} ({len(blocks)} blocks)")

    all_changes = []
    corrected_blocks = []
    for block in blocks:
        corrected_lines = []
        for line in block['text_lines']:
            corrected_line, changes = correct_text(line, WORDLIST, block['index'])
            all_changes.extend(changes)
            corrected_lines.append(corrected_line)
        corrected_blocks.append({**block, 'text_lines': corrected_lines})

    corrected_content = format_srt(corrected_blocks)

    # Sanitise filename to prevent path traversal
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename)
    output_filename = f"corrected_{safe_name}"
    output_path = CORRECTED_DIR / output_filename
    output_path.write_text(corrected_content, encoding='utf-8', errors='replace')

    summary = generate_summary(all_changes, len(blocks), file.filename)
    logger.info(f"Correction complete for {file.filename}: {len(all_changes)} change(s)")

    return {
        "status": "success",
        "download_url": f"/download-corrected/{output_filename}",
        "summary": summary,
        "changes_count": len(all_changes),
    }


@app.get("/download-corrected/{filename}")
async def download_corrected_file(filename: str):
    """Download a corrected SRT file."""
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    file_path = (CORRECTED_DIR / safe_name).resolve()
    # Guard against path traversal
    if not str(file_path).startswith(str(CORRECTED_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="text/plain", filename=safe_name)


WHISPER_MODEL_DIR = Path(__file__).parent / "whisper_models"
WHISPER_MODEL_DIR.mkdir(exist_ok=True)

# Whisper model is loaded lazily on first transcription request and cached.
_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        # Disable SSL certificate verification so the model can be downloaded
        # through corporate proxies that use self-signed certificates.
        # This is applied in-process and only triggers on the initial download.
        ssl._create_default_https_context = ssl._create_unverified_context
        logger.info(f"Loading Whisper tiny model (download_root={WHISPER_MODEL_DIR})")
        import whisper as _whisper
        _whisper_model = _whisper.load_model("tiny", download_root=str(WHISPER_MODEL_DIR))
        logger.info("Whisper model ready")
    return _whisper_model


def _segments_to_srt(segments: list) -> str:
    """Convert Whisper result segments to an SRT string."""
    def fmt(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    parts = []
    idx = 1
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        parts.append(f"{idx}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{text}")
        idx += 1
    return "\n\n".join(parts) + "\n"


@app.post("/api/transcribe-mp4")
async def transcribe_mp4_endpoint(file: UploadFile = File(...)):
    """Upload an MP4, transcribe with Whisper, apply wordlist corrections, return corrected SRT + summary."""
    if not file.filename or not file.filename.lower().endswith('.mp4'):
        raise HTTPException(status_code=400, detail="Only .mp4 files are accepted")

    safe_stem = re.sub(r'[^a-zA-Z0-9._-]', '_', Path(file.filename).stem)

    with tempfile.TemporaryDirectory() as workdir:
        workdir = Path(workdir)
        input_path = workdir / f"{safe_stem}.mp4"

        with open(input_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)

        size_mb = input_path.stat().st_size / 1024 / 1024
        logger.info(f"Transcribing {safe_stem}.mp4 ({size_mb:.1f} MB)")

        try:
            model = get_whisper_model()
            result = model.transcribe(str(input_path), language="en", verbose=False)
        except Exception as e:
            logger.error(f"Whisper error: {e}")
            raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

        raw_content = _segments_to_srt(result["segments"])

    # Run correction logic
    blocks = parse_srt(raw_content)
    if not blocks:
        raise HTTPException(status_code=500, detail="Whisper produced no transcription output.")

    all_changes = []
    corrected_blocks = []
    for block in blocks:
        corrected_lines = []
        for line in block['text_lines']:
            corrected_line, changes = correct_text(line, WORDLIST, block['index'])
            all_changes.extend(changes)
            corrected_lines.append(corrected_line)
        corrected_blocks.append({**block, 'text_lines': corrected_lines})

    corrected_content = format_srt(corrected_blocks)

    output_filename = f"transcript_{safe_stem}.srt"
    output_path = TRANSCRIPTIONS_DIR / output_filename
    output_path.write_text(corrected_content, encoding='utf-8', errors='replace')

    summary = generate_summary(all_changes, len(blocks), file.filename)
    logger.info(f"Transcription + correction complete for {file.filename}: {len(all_changes)} change(s)")

    return {
        "status": "success",
        "download_url": f"/download-transcription/{output_filename}",
        "summary": summary,
        "changes_count": len(all_changes),
    }


@app.get("/download-transcription/{filename}")
async def download_transcription_file(filename: str):
    """Download a transcribed + corrected SRT file."""
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    file_path = (TRANSCRIPTIONS_DIR / safe_name).resolve()
    if not str(file_path).startswith(str(TRANSCRIPTIONS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="text/plain", filename=safe_name)


class WordlistEntry(BaseModel):
    wrong: str
    right: str


def _save_wordlist(wordlist: dict) -> None:
    """Persist the wordlist dict to wordlist.json."""
    with open(WORDLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(wordlist, f, indent=4, ensure_ascii=False)


@app.get("/api/wordlist")
async def get_wordlist():
    """Return the current wordlist, re-read from disk on every call."""
    try:
        with open(WORDLIST_FILE, encoding='utf-8') as f:
            fresh = json.load(f)
        WORDLIST.clear()
        WORDLIST.update(fresh)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not reload wordlist: {e}")
    return {"wordlist": WORDLIST}


@app.post("/api/wordlist")
async def add_wordlist_entry(entry: WordlistEntry):
    """Add or update a wordlist entry and persist to wordlist.json."""
    wrong = entry.wrong.strip()
    right = entry.right.strip()
    if not wrong or not right:
        raise HTTPException(status_code=400, detail="Both fields must be non-empty")

    updated = wrong in WORDLIST
    WORDLIST[wrong] = right
    _save_wordlist(WORDLIST)
    logger.info(f"Wordlist {'updated' if updated else 'added'}: {wrong!r} -> {right!r}")
    return {"status": "success", "updated": updated, "wrong": wrong, "right": right}


# ── Highlight Reel helpers ────────────────────────────────────────────────────

def _srt_time_to_seconds(t: str) -> float:
    """Convert SRT timecode '00:01:30,000' to float seconds."""
    t = t.strip().replace(',', '.')
    parts = t.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def parse_timestamp(ts: str) -> float:
    """Convert HH:MM:SS, MM:SS, or bare-seconds string to float seconds."""
    ts = ts.strip()
    parts = ts.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(ts)


def extract_transcript_for_ranges(srt_content: str, ranges: list) -> str:
    """Return subtitle text whose blocks overlap any of the given (start_s, end_s) ranges."""
    blocks = parse_srt(srt_content)
    lines = []
    for block in blocks:
        tc = block['timecode']
        start_str, end_str = tc.split(' --> ')
        block_start = _srt_time_to_seconds(start_str)
        block_end = _srt_time_to_seconds(end_str)
        for (range_start, range_end) in ranges:
            if block_start < range_end and block_end > range_start:
                lines.append(' '.join(block['text_lines']))
                break
    return '\n'.join(lines)


def call_openai_summary(transcript_text: str, timestamps_desc: str) -> str:
    """Call the LangDock OpenAI-compatible endpoint and return a markdown summary."""
    api_key = os.environ.get("LANGDOCK_API_KEY", "")
    if not api_key:
        raise ValueError("LANGDOCK_API_KEY environment variable is not set")

    payload = {
        "model": LANGDOCK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are an expert at summarizing video content. Produce clear, concise executive summaries in markdown format.",
            },
            {
                "role": "user",
                "content": (
                    f"The following transcript is extracted from a YouTube video at these timestamps: {timestamps_desc}\n\n"
                    f"Transcript:\n{transcript_text}\n\n"
                    "Please provide an executive summary of this content in markdown format, "
                    "highlighting the key points and takeaways."
                ),
            },
        ],
        "temperature": 0.7,
        "max_tokens": 5000,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(LANGDOCK_ENDPOINT, data=body, headers=headers, method="POST")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def srt_to_timestamped_text(srt_content: str) -> str:
    """Convert SRT content to a compact [M:SS] transcript suitable for sending to the LLM."""
    blocks = parse_srt(srt_content)
    lines = []
    for block in blocks:
        start_str = block['timecode'].split(' --> ')[0]
        seconds = _srt_time_to_seconds(start_str)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ts_label = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
        text = ' '.join(block['text_lines'])
        lines.append(f"[{ts_label}] {text}")
    return '\n'.join(lines)


def call_openai_chapters(timestamped_transcript: str) -> str:
    """Call the LangDock endpoint and return YouTube-style chapter markers."""
    api_key = os.environ.get("LANGDOCK_API_KEY", "")
    if not api_key:
        raise ValueError("LANGDOCK_API_KEY environment variable is not set")

    payload = {
        "model": LANGDOCK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert video editor creating YouTube chapter markers. "
                    "Output ONLY the chapter list, nothing else — no intro sentence, no explanation."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Here is a video transcript with timestamps. Identify the key moments and output them as YouTube chapter markers.\n\n"
                    "Requirements:\n"
                    "- The first chapter MUST be at 0:00\n"
                    "- Format each line as: M:SS Chapter Title  (e.g. '0:00 Introduction')\n"
                    "- For videos over 1 hour use: H:MM:SS Chapter Title\n"
                    "- Chapter titles should be concise, 2-5 words\n"
                    "- Aim for 5-12 chapters based on topic transitions\n"
                    "- Output ONLY the chapter list, one per line, no extra text\n\n"
                    f"Transcript:\n{timestamped_transcript}"
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(LANGDOCK_ENDPOINT, data=body, headers=headers, method="POST")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    last_exc = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_exc = exc
            if attempt < 3:
                logger.warning(f"Chapter generation attempt {attempt}/3 timed out; retrying")
                time.sleep(1.5 * attempt)

    raise RuntimeError(f"Chapter generation timed out after 3 attempts: {last_exc}")


def _extract_keywords(raw_keywords: str) -> list[str]:
    """Parse comma/newline-separated keywords into a deduplicated list."""
    parts = re.split(r"[,\n]", raw_keywords or "")
    cleaned = []
    seen = set()
    for part in parts:
        value = part.strip()
        if not value:
            continue
        folded = value.lower()
        if folded in seen:
            continue
        seen.add(folded)
        cleaned.append(value)
    return cleaned


def _trim_text(value: str, max_chars: int) -> str:
    """Trim large text blobs before sending them to the LLM."""
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n... [truncated]"


def _fetch_video_metadata(url: str) -> dict:
    """Fetch a video's metadata using yt-dlp JSON output without downloading media."""
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        err = (result.stderr or "yt-dlp metadata fetch failed").strip()
        raise RuntimeError(err)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse yt-dlp metadata JSON: {exc}") from exc


def call_openai_metadata_recommendations(context: dict, keywords: list[str]) -> dict:
    """Call the LangDock endpoint to generate concrete metadata recommendations."""
    api_key = os.environ.get("LANGDOCK_API_KEY", "")
    if not api_key:
        raise ValueError("LANGDOCK_API_KEY environment variable is not set")

    payload = {
        "model": LANGDOCK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a YouTube growth and SEO strategist. Return only valid JSON. "
                    "Give concrete, copy-ready recommendations for metadata updates based on provided context and target keywords."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Create a metadata optimization plan for this video.\n\n"
                    "Target keywords:\n"
                    + "\n".join(f"- {k}" for k in keywords)
                    + "\n\nVideo context JSON:\n"
                    + json.dumps(context, ensure_ascii=False)
                    + "\n\nReturn ONLY JSON with this schema:\n"
                    "{\n"
                    "  \"search_intent\": [\"...\"],\n"
                    "  \"title_options\": [\"...\", \"...\", \"...\"],\n"
                    "  \"description_draft\": \"...\",\n"
                    "  \"thumbnail_concepts\": [\"...\"],\n"
                    "  \"keyword_plan\": [\n"
                    "    {\"keyword\": \"...\", \"placement\": \"title|description|chapters|tags|thumbnail text\", \"reason\": \"...\"}\n"
                    "  ],\n"
                    "  \"quick_wins\": [\"...\"],\n"
                    "  \"risks_to_avoid\": [\"...\"]\n"
                    "}\n"
                    "Use practical wording and avoid generic advice."
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 2500,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(LANGDOCK_ENDPOINT, data=body, headers=headers, method="POST")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    content = result["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return json.loads(content[first_brace:last_brace + 1])
        raise RuntimeError("LLM returned an unexpected response format")


def format_metadata_recommendations(recs: dict, keywords: list[str], video_title: str) -> str:
    """Render a concise markdown plan for direct copy/paste."""
    intent = recs.get("search_intent") or []
    titles = recs.get("title_options") or []
    description = (recs.get("description_draft") or "").strip()
    thumb_concepts = recs.get("thumbnail_concepts") or []
    keyword_plan = recs.get("keyword_plan") or []
    quick_wins = recs.get("quick_wins") or []
    risks = recs.get("risks_to_avoid") or []

    lines = [
        "# YouTube Metadata Optimization Plan",
        "",
        f"**Current video title:** {video_title}",
        f"**Target keywords:** {', '.join(keywords)}",
        "",
    ]

    if intent:
        lines.append("## What People Likely Search")
        lines.extend(f"- {item}" for item in intent)
        lines.append("")

    if titles:
        lines.append("## Title Options")
        lines.extend(f"- {item}" for item in titles)
        lines.append("")

    if description:
        lines.append("## Description Draft")
        lines.append(description)
        lines.append("")

    if thumb_concepts:
        lines.append("## Thumbnail Concepts")
        lines.extend(f"- {item}" for item in thumb_concepts)
        lines.append("")

    if keyword_plan:
        lines.append("## Keyword Placement Priorities")
        for item in keyword_plan:
            keyword = item.get("keyword", "")
            placement = item.get("placement", "")
            reason = item.get("reason", "")
            lines.append(f"- **{keyword}** -> {placement}: {reason}")
        lines.append("")

    if quick_wins:
        lines.append("## Quick Wins")
        lines.extend(f"- {item}" for item in quick_wins)
        lines.append("")

    if risks:
        lines.append("## Risks To Avoid")
        lines.extend(f"- {item}" for item in risks)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _get_cached_video(video_id: str, url: str) -> Path:
    """Download the video if not already cached; return local file path."""
    existing = list(VIDEO_CACHE_DIR.glob(f"{video_id}.*"))
    if existing:
        logger.info(f"Video cache hit for {video_id}")
        return existing[0]

    logger.info(f"Downloading video {video_id}")
    output_template = str(VIDEO_CACHE_DIR / f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Video download failed: {result.stderr[:500]}")

    existing = list(VIDEO_CACHE_DIR.glob(f"{video_id}.*"))
    if not existing:
        raise RuntimeError("Video download completed but output file not found")
    return existing[0]


def _build_trim_concat(ranges: list) -> tuple:
    """Return (filter_complex_prefix, label_of_combined_stream) for ffmpeg."""
    parts = []
    for i, (start, end) in enumerate(ranges):
        parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[c{i}]")

    if len(ranges) > 1:
        inputs = "".join(f"[c{i}]" for i in range(len(ranges)))
        parts.append(f"{inputs}concat=n={len(ranges)}:v=1:a=0[combined]")
        combined = "[combined]"
    else:
        combined = "[c0]"

    return ";".join(parts), combined


def _generate_gif(video_path: Path, ranges: list, gif_path: Path) -> None:
    """Generate an optimised GIF from specified time ranges using a two-pass ffmpeg approach."""
    base_fc, combined = _build_trim_concat(ranges)

    with tempfile.TemporaryDirectory() as tmpdir:
        palette_path = Path(tmpdir) / "palette.png"

        # Pass 1 – generate an optimised colour palette
        fc1 = f"{base_fc};{combined}fps=15,scale=640:-1:flags=lanczos,palettegen=stats_mode=diff[p]"
        cmd1 = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-filter_complex", fc1, "-map", "[p]", str(palette_path),
        ]
        r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=180)
        if r1.returncode != 0:
            raise RuntimeError(f"GIF palette generation failed: {r1.stderr[:500]}")

        # Pass 2 – apply palette to produce the final GIF
        fc2 = (
            f"{base_fc};{combined}fps=15,scale=640:-1:flags=lanczos[scaled];"
            "[scaled][1:v]paletteuse=dither=bayer:bayer_scale=5[out]"
        )
        cmd2 = [
            "ffmpeg", "-y",
            "-i", str(video_path), "-i", str(palette_path),
            "-filter_complex", fc2, "-map", "[out]", str(gif_path),
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=180)
        if r2.returncode != 0:
            raise RuntimeError(f"GIF generation failed: {r2.stderr[:500]}")


@app.post("/api/highlight-reel")
async def highlight_reel_endpoint(data: HighlightReelRequest):
    """Download a YouTube video, summarise the given timestamp ranges via OpenAI,
    and render a highlight GIF of those sections. Streams SSE progress events."""

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _emit(type: str, **kwargs):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": type, **kwargs})

        def run():
            try:
                # 1. Parse and validate timestamps
                ranges = []
                for ts in data.timestamps:
                    start_s = parse_timestamp(ts.start)
                    end_s = parse_timestamp(ts.end)
                    if end_s <= start_s:
                        _emit("error", detail=f"End must be after start: {ts.start} – {ts.end}")
                        return
                    ranges.append((start_s, end_s))

                if not ranges:
                    _emit("error", detail="At least one timestamp range is required")
                    return

                # 2. Resolve video ID
                video_id = extract_video_id(data.url)
                if not video_id:
                    _emit("error", detail="Could not extract video ID from URL")
                    return

                # 3. Get subtitles
                _emit("status", message="Fetching subtitles…")
                subtitle_cache_file = SUBTITLE_CACHE_DIR / f"{video_id}.srt"
                if subtitle_cache_file.exists():
                    srt_content = subtitle_cache_file.read_text(encoding='utf-8', errors='replace')
                else:
                    output_template = str(SUBTITLES_DIR / "%(title)s.%(ext)s")
                    sub_cmd = [
                        "yt-dlp", "--write-auto-subs", "--sub-format", "srt",
                        "--skip-download", "-o", output_template, data.url,
                    ]
                    sub_result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=60)
                    if sub_result.returncode != 0:
                        _emit("error", detail=_subtitle_error_detail(sub_result.stderr))
                        return
                    srt_files = sorted(SUBTITLES_DIR.glob("*.srt"), key=os.path.getctime)
                    if srt_files:
                        srt_content = srt_files[-1].read_text(encoding='utf-8', errors='replace')
                        subtitle_cache_file.write_text(srt_content, encoding='utf-8')
                    else:
                        srt_content = ""

                # 4. Extract transcript text for the requested ranges
                transcript_text = extract_transcript_for_ranges(srt_content, ranges)
                if not transcript_text:
                    transcript_text = "(No subtitle text available for the specified timestamps.)"

                # 5. Generate executive summary via OpenAI
                _emit("status", message="Generating executive summary…")
                timestamps_desc = ", ".join(f"{ts.start}–{ts.end}" for ts in data.timestamps)
                summary = call_openai_summary(transcript_text, timestamps_desc)
                logger.info(f"Summary generated for {video_id}")

                # 6. Download (or retrieve cached) video
                _emit("status", message="Downloading video (this may take a moment)…")
                video_path = _get_cached_video(video_id, data.url)

                # 7. Generate highlight GIF
                _emit("status", message="Rendering highlight GIF…")
                gif_filename = f"highlight_{video_id}_{uuid.uuid4().hex[:8]}.gif"
                gif_path = GIF_OUTPUT_DIR / gif_filename
                _generate_gif(video_path, ranges, gif_path)
                logger.info(f"GIF generated: {gif_path}")

                _emit("done", summary=summary, gif_url=f"/download-gif/{gif_filename}")

            except subprocess.TimeoutExpired:
                _emit("error", detail="Operation timed out – try shorter clips or a faster connection")
            except Exception as exc:
                logger.error(f"Highlight reel error: {exc}")
                _emit("error", detail=str(exc))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/download-gif/{filename}")
async def download_gif(filename: str):
    """Serve a generated highlight GIF."""
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    file_path = (GIF_OUTPUT_DIR / safe_name).resolve()
    if not str(file_path).startswith(str(GIF_OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="image/gif", filename=safe_name)


# ── Chapter Detector helpers & endpoints ─────────────────────────────────────

def _fetch_srt_for_video(video_id: str | None, url: str) -> str:
    """Return raw SRT content for a YouTube video, using the subtitle cache if available."""
    subtitle_cache_file = SUBTITLE_CACHE_DIR / f"{video_id}.srt" if video_id else None
    if subtitle_cache_file and subtitle_cache_file.exists():
        logger.info(f"Subtitle cache hit for {video_id}")
        return subtitle_cache_file.read_text(encoding='utf-8', errors='replace')

    output_template = str(SUBTITLES_DIR / "%(title)s.%(ext)s")
    cmd = [
        "yt-dlp", "--write-auto-subs", "--sub-format", "srt",
        "--skip-download", "-o", output_template, url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(_subtitle_error_detail(result.stderr[:400]))

    srt_files = sorted(SUBTITLES_DIR.glob("*.srt"), key=os.path.getctime)
    if not srt_files:
        raise RuntimeError("No subtitles found for this video.")

    content = srt_files[-1].read_text(encoding='utf-8', errors='replace')
    if subtitle_cache_file:
        subtitle_cache_file.write_text(content, encoding='utf-8')
        logger.info(f"Cached subtitles for {video_id}")
    return content


def _run_detect_chapters(srt_content: str, label: str, emit):
    """Shared SSE logic for chapter detection — runs in a thread."""
    emit("status", message="Parsing transcript\u2026")
    timestamped = srt_to_timestamped_text(srt_content)
    if not timestamped:
        emit("error", detail="No transcript text found")
        return

    emit("status", message="Detecting chapters with AI\u2026")
    try:
        chapters = call_openai_chapters(timestamped)
    except Exception as e:
        logger.error(f"Chapter detection error ({label}): {e}")
        emit("error", detail=f"AI chapter detection failed: {str(e)}")
        return

    logger.info(f"Chapters detected for {label}")
    emit("done", chapters=chapters.strip())


@app.post("/api/detect-chapters")
async def detect_chapters_url(data: ChapterDetectRequest):
    """Detect YouTube chapters from a video URL; streams SSE progress events."""
    video_id = extract_video_id(data.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Could not extract video ID from URL")

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _emit(type: str, **kwargs):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": type, **kwargs})

        def run():
            try:
                _emit("status", message="Fetching subtitles\u2026")
                srt_content = _fetch_srt_for_video(video_id, data.url)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                _emit("error", detail=str(e))
                return
            _run_detect_chapters(srt_content, video_id, _emit)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/detect-chapters-file")
async def detect_chapters_file(file: UploadFile = File(...)):
    """Detect YouTube chapters from an uploaded SRT file; streams SSE progress events."""
    if not file.filename or not file.filename.lower().endswith('.srt'):
        raise HTTPException(status_code=400, detail="Only .srt files are accepted")

    content_bytes = await file.read()
    if len(content_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    try:
        srt_content = content_bytes.decode('utf-8', errors='replace')
    except UnicodeDecodeError:
        srt_content = content_bytes.decode('latin-1', errors='replace')

    filename = file.filename

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _emit(type: str, **kwargs):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": type, **kwargs})

        def run():
            _run_detect_chapters(srt_content, filename, _emit)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/metadata-recommendations")
async def metadata_recommendations(data: MetadataInvestigationRequest):
    """Investigate video context and return concrete metadata optimization recommendations."""
    video_id = extract_video_id(data.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Could not extract video ID from URL")

    keywords = _extract_keywords(data.keywords)
    if not keywords:
        raise HTTPException(status_code=400, detail="Please provide at least one keyword")

    try:
        yt_meta = _fetch_video_metadata(data.url)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"Could not fetch video metadata: {str(exc)}")

    subtitle_warning = None
    transcript_snippet = ""
    try:
        srt_content = _fetch_srt_for_video(video_id, data.url)
        transcript_snippet = _trim_text(srt_to_timestamped_text(srt_content), 14000)
    except Exception as exc:
        subtitle_warning = f"Subtitles unavailable ({str(exc)}). Recommendations use title/description metadata only."

    video_context = {
        "title": (yt_meta.get("title") or "").strip(),
        "description": _trim_text((yt_meta.get("description") or "").strip(), 6000),
        "channel": yt_meta.get("channel") or yt_meta.get("uploader") or "",
        "duration_seconds": yt_meta.get("duration"),
        "view_count": yt_meta.get("view_count"),
        "categories": yt_meta.get("categories") or [],
        "tags": yt_meta.get("tags") or [],
        "chapters": [
            {
                "title": chapter.get("title", ""),
                "start_time": chapter.get("start_time"),
            }
            for chapter in (yt_meta.get("chapters") or [])[:20]
        ],
        "transcript_excerpt": transcript_snippet,
    }

    if not video_context["title"] and not video_context["description"] and not transcript_snippet:
        raise HTTPException(status_code=400, detail="Insufficient video context for recommendations")

    try:
        recs = call_openai_metadata_recommendations(video_context, keywords)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Metadata recommendation failed: {str(exc)}")

    markdown = format_metadata_recommendations(recs, keywords, video_context["title"] or "(unknown title)")
    return {
        "status": "success",
        "markdown": markdown,
        "title_options": recs.get("title_options") or [],
        "description_draft": recs.get("description_draft") or "",
        "thumbnail_concepts": recs.get("thumbnail_concepts") or [],
        "keyword_plan": recs.get("keyword_plan") or [],
        "subtitle_warning": subtitle_warning,
    }


@app.get("/api/devcontainer/features")
async def get_devcontainer_features(search: str = ""):
    """Return searchable feature catalog entries for Devcontainer Builder."""
    query = (search or "").strip().lower()
    features = DEVCONTAINER_FEATURES

    if query:
        features = [
            feature
            for feature in features
            if query in feature.displayName.lower()
            or query in feature.reference.lower()
            or query in feature.maintainer.lower()
            or query in feature.description.lower()
        ]

    return {
        "features": [feature.model_dump() for feature in features],
        "count": len(features),
    }


@app.post("/api/devcontainer/build")
async def build_devcontainer_scaffold(data: DevcontainerBuildRequest):
    """Build and return a ZIP scaffold containing a generated devcontainer config."""
    zip_bytes = _build_devcontainer_zip_bytes(data)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", _sanitize_devcontainer_name(data.name).lower())
    filename = f"{safe_name or 'devcontainer'}.zip"

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Channel Navigator ─────────────────────────────────────────────────────────

CHANNEL_INDEX_FILE = Path(__file__).parent / "channel_index.json"
CHANNEL_VIDEO_URL = "https://www.youtube.com/@dynatrace/videos"
CHANNEL_SHORTS_URL = "https://www.youtube.com/@dynatrace/shorts"
BLOG_INDEX_FILE = Path(__file__).parent / "blog_index.json"
BLOG_FEED_URL = "https://www.dynatrace.com/news/blog/feed/"
BLOG_MIN_YEAR = 2025
BLOG_MAX_PAGES = 30


@app.get("/api/channel-index")
async def get_channel_index():
    """Return the channel index JSON."""
    try:
        with open(CHANNEL_INDEX_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    return {"videos": data}


@app.get("/api/blog-index")
async def get_blog_index(refresh: bool = False):
    """Return a searchable blog index JSON; updates are handled out-of-band."""
    if refresh:
        logger.info("Ignoring blog refresh request: updater is disabled in app runtime")

    try:
        with open(BLOG_INDEX_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    return {"blogs": data, "refreshed": False}


@app.get("/navigator", response_class=HTMLResponse)
async def get_navigator():
    """Serve the standalone Channel Navigator page."""
    return render_template_html("navigator.html")


@app.get("/blog-navigator", response_class=HTMLResponse)
async def get_blog_navigator():
    """Serve the standalone Blog Navigator page."""
    return render_template_html("blog-navigator.html")


@app.get("/docs/index-refresh", response_class=HTMLResponse)
async def get_index_refresh_docs():
    """Serve maintainer docs for refreshing blog and video indexes."""
    return render_template_html("index-refresh-docs.html")


# ── Webhook Tester ─────────────────────────────────────────────────────────────

WEBHOOK_SITE_DIR = BASE_DIR / "webhook-site"
WEBHOOK_SITE_REPO = "https://github.com/webhooksite/webhook.site.git"
WEBHOOK_SITE_PORT = 8084


def _webhook_dir_ready() -> bool:
    return WEBHOOK_SITE_DIR.exists() and (WEBHOOK_SITE_DIR / "docker-compose.yml").exists()


def _webhook_running() -> bool:
    try:
        with socket.create_connection(("localhost", WEBHOOK_SITE_PORT), timeout=2):
            return True
    except OSError:
        return False


def _stream_cmd(cmd: list, cwd: str | None, emit_fn, timeout: int = 600) -> int:
    """Run a command, emitting each output line via emit_fn. Returns returncode."""
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    try:
        for line in proc.stdout:
            stripped = line.rstrip()
            if stripped:
                emit_fn("line", text=stripped)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    return proc.returncode


class WebhookSendRequest(BaseModel):
    url: str
    payload: dict


@app.get("/webhook-tester", response_class=HTMLResponse)
async def get_webhook_tester():
    return render_template_html("webhook-tester.html")


@app.post("/api/webhook/send")
async def send_webhook_test(data: WebhookSendRequest):
    """Proxy a CloudEvent test request to the user's webhook URL and return the response."""
    url = (data.url or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    body = json.dumps(data.payload, indent=2).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/cloudevents+json", "Accept": "*/*"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8", errors="replace")[:4000]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", errors="replace")[:4000]}
    except urllib.error.URLError as e:
        raise HTTPException(status_code=400, detail=f"Request failed: {e.reason}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/webhook/status")
async def get_webhook_status():
    running = await asyncio.to_thread(_webhook_running)
    return {"running": running, "port": WEBHOOK_SITE_PORT}


@app.post("/api/webhook/start")
async def start_webhook():
    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _emit(evt_type: str, **kwargs):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": evt_type, **kwargs})

        def run():
            try:
                if not _webhook_dir_ready():
                    _emit("cmd", text="$ git clone " + WEBHOOK_SITE_REPO)
                    rc = _stream_cmd(
                        ["git", "clone", WEBHOOK_SITE_REPO, str(WEBHOOK_SITE_DIR)],
                        cwd=None, emit_fn=_emit, timeout=120,
                    )
                    if rc != 0:
                        _emit("error", text="git clone failed — check output above")
                        return

                env_file = WEBHOOK_SITE_DIR / ".env"
                env_example = WEBHOOK_SITE_DIR / ".env.example"
                if not env_file.exists() and env_example.exists():
                    shutil.copy(env_example, env_file)
                    _emit("line", text="Copied .env.example \u2192 .env")

                _emit("cmd", text="$ docker compose up -d")
                rc = _stream_cmd(
                    ["docker", "compose", "up", "-d"],
                    cwd=str(WEBHOOK_SITE_DIR), emit_fn=_emit, timeout=600,
                )
                if rc != 0:
                    _emit("error", text="docker compose failed — check output above")
                    return

                _emit("done")
            except subprocess.TimeoutExpired:
                _emit("error", text="Timed out after 10 minutes")
            except Exception as exc:
                _emit("error", text=str(exc))

        threading.Thread(target=run, daemon=True).start()
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/webhook/stop")
async def stop_webhook():
    if not _webhook_dir_ready():
        return {"stopped": True}

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _emit(evt_type: str, **kwargs):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": evt_type, **kwargs})

        def run():
            try:
                _emit("cmd", text="$ docker compose down")
                rc = _stream_cmd(
                    ["docker", "compose", "down"],
                    cwd=str(WEBHOOK_SITE_DIR), emit_fn=_emit, timeout=120,
                )
                if rc != 0:
                    _emit("error", text="docker compose down failed — check output above")
                    return
                _emit("done")
            except Exception as exc:
                _emit("error", text=str(exc))

        threading.Thread(target=run, daemon=True).start()
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
