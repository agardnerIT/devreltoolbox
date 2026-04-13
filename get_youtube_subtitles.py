#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib import parse


#SECRETS_FILE = Path("/etc/video-toolbox/secrets.env")
SECRETS_FILE = Path("/absoluterubbish")
DEFAULT_URL = "https://www.youtube.com/watch?v=t9kHPKL9yKY"
DEFAULT_OUTPUT = Path(__file__).parent / "subtitle_cache"


def log(msg: str) -> None:
    print(msg, flush=True)


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def extract_video_id(url: str) -> str:
    parsed = parse.urlparse(url)
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/")

    if "youtube.com" in parsed.netloc:
        q = parse.parse_qs(parsed.query)
        if "v" in q and q["v"]:
            return q["v"][0]

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url

    raise ValueError("Could not parse a YouTube video ID from input")


def find_yt_dlp_binary() -> str:
    local = Path(__file__).parent / "bin" / "yt-dlp"
    if local.exists():
        return str(local)
    return "yt-dlp"


def run_yt_dlp_subtitles(
    video_url: str,
    lang: str,
    output_dir: Path,
    cookies_file: str,
) -> None:
    outtmpl = str(output_dir / "%(id)s.%(language)s.%(ext)s")
    cmd = [
        find_yt_dlp_binary(),
        "--js-runtimes",
        "node",
        "--skip-download",
        "--write-sub",
        "--write-auto-sub",
        "--sub-langs",
        "en",
        "--sub-format",
        "srt",
        "-o",
        outtmpl,
        video_url,
    ]

    if cookies_file:
        cmd[1:1] = ["--cookies", cookies_file]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "yt-dlp subtitle download failed"
        raise RuntimeError(stderr)

def find_downloaded_subtitle(output_dir: Path, video_id: str, lang: str) -> Path | None:
    patterns = [
        f"{video_id}.{lang}*.srt",
        f"{video_id}.{lang}*.vtt",
        f"{video_id}.*.srt",
        f"{video_id}.*.vtt",
    ]
    for pattern in patterns:
        matches = sorted(output_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch subtitles for a YouTube video with yt-dlp.")
    parser.add_argument("--url", default=DEFAULT_URL, help="YouTube URL or video ID")
    parser.add_argument("--lang", default="en", help="Preferred subtitle language code")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cookies-file", default="", help="Optional Netscape cookies.txt file for authenticated subtitle fetches")
    args = parser.parse_args()

    try:
        video_id = extract_video_id(args.url)
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1

    env = load_env_file(SECRETS_FILE)
    cookies_file = args.cookies_file or env.get("YOUTUBE_COOKIES_FILE", "")
    if cookies_file and not Path(cookies_file).exists():
       log(f"ERROR: cookies file not found: {cookies_file}")
       return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_yt_dlp_subtitles(args.url, args.lang, output_dir, cookies_file)
    except Exception as exc:
        log(f"ERROR: yt-dlp failed: {exc}")
        log("HINT: if YouTube asks for sign-in, export cookies and set YOUTUBE_COOKIES_FILE in secrets.env.")
        return 3

    out_file = find_downloaded_subtitle(output_dir, video_id, args.lang)
    if out_file is None:
        log("ERROR: yt-dlp completed but no subtitle file was found in output directory")
        return 4

    log(f"Saved subtitles to {out_file}")
    if cookies_file:
        log("Fetch mode: yt-dlp with cookies")
    else:
        log("Fetch mode: yt-dlp without cookies")

    return 0


if __name__ == "__main__":
    sys.exit(main())
