import argparse
import html
import json
import os
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

BLOG_FEED_URL = "https://www.dynatrace.com/news/blog/feed/"
BLOG_MIN_YEAR = 2025
BLOG_MAX_PAGES = 30

CHANNEL_VIDEO_URL = "https://www.youtube.com/@dynatrace/videos"
CHANNEL_SHORTS_URL = "https://www.youtube.com/@dynatrace/shorts"

LANGDOCK_ENDPOINT = "https://chat.langdock.internal.dynatrace.com/api/public/openai/eu/v1/chat/completions"
LANGDOCK_MODEL = "gpt-5-mini"


def _strip_html(raw_text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", raw_text)
    cleaned = html.unescape(no_tags)
    return re.sub(r"\s+", " ", cleaned).strip()


def _child_text(item: ET.Element, tag_name: str) -> str:
    for child in item:
        if child.tag.split("}")[-1] == tag_name:
            return (child.text or "").strip()
    return ""


def _extract_first_image_url(raw_html: str) -> str:
    if not raw_html:
        return ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_rss_image(item: ET.Element, raw_html: str) -> str:
    for child in item:
        name = child.tag.split("}")[-1]
        if name not in ("content", "thumbnail"):
            continue

        url = (child.attrib.get("url") or "").strip()
        if not url:
            continue

        medium = (child.attrib.get("medium") or "").strip().lower()
        mime_type = (child.attrib.get("type") or "").strip().lower()
        if name == "thumbnail" or medium == "image" or mime_type.startswith("image/") or not medium:
            return url

    return _extract_first_image_url(raw_html)


def _pub_date_year(pub_date: str) -> int | None:
    try:
        return parsedate_to_datetime(pub_date).year
    except Exception:
        return None


def build_blog_index(
    base_dir: Path,
    *,
    feed_url: str = BLOG_FEED_URL,
    min_year: int = BLOG_MIN_YEAR,
    max_pages: int = BLOG_MAX_PAGES,
    user_agent: str = "Mozilla/5.0 (DevRelToolbox Blog Indexer)",
) -> list[dict]:
    blog_index_file = base_dir / "blog_index.json"
    blogs: list[dict] = []
    seen_urls: set[str] = set()

    for page in range(1, max_pages + 1):
        current_feed_url = feed_url if page == 1 else f"{feed_url}?paged={page}"
        req = urllib.request.Request(current_feed_url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        if not items:
            break

        has_target_year = False
        for item in items:
            pub_date = _child_text(item, "pubDate")
            year = _pub_date_year(pub_date)
            if year is not None and year < min_year:
                continue

            has_target_year = True
            title = _child_text(item, "title")
            link = _child_text(item, "link")
            if not link or link in seen_urls:
                continue

            description = _child_text(item, "description")
            full_content = _child_text(item, "encoded")
            raw_summary = full_content or description
            summary = _strip_html(raw_summary)[:320]
            image_url = _extract_rss_image(item, raw_summary)

            categories = []
            for child in item:
                if child.tag.split("}")[-1] == "category" and (child.text or "").strip():
                    categories.append((child.text or "").strip())

            blogs.append(
                {
                    "title": title,
                    "url": link,
                    "published": pub_date,
                    "summary": summary,
                    "image_url": image_url,
                    "categories": categories,
                }
            )
            seen_urls.add(link)

        if not has_target_year:
            break

    with open(blog_index_file, "w", encoding="utf-8") as f:
        json.dump(blogs, f, indent=2, ensure_ascii=False)

    return blogs


def _parse_srt(content: str) -> list[dict]:
    blocks = []
    for block in re.split(r"\n\n+", content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        if not lines[0].strip().isdigit() or "-->" not in lines[1]:
            continue
        blocks.append({"index": lines[0].strip(), "timecode": lines[1].strip(), "text_lines": lines[2:]})
    return blocks


def _format_srt(blocks: list[dict]) -> str:
    parts = []
    for block in blocks:
        text = "\n".join(block["text_lines"])
        parts.append(f"{block['index']}\n{block['timecode']}\n{text}")
    return "\n\n".join(parts) + "\n"


def _correct_text(text: str, wordlist: dict, block_index: str) -> tuple[str, int]:
    count = 0
    phrase_keys = sorted([k for k in wordlist if " " in k], key=lambda k: len(k), reverse=True)
    for wrong in phrase_keys:
        right = wordlist[wrong]
        pattern = re.compile(re.escape(wrong), re.IGNORECASE)

        def repl(m, right=right):
            nonlocal count
            if m.group(0) != right:
                count += 1
            return right

        text = pattern.sub(repl, text)

    single_keys = {k.lower(): v for k, v in wordlist.items() if " " not in k}

    def replace_word(m):
        nonlocal count
        word = m.group(0)
        right = single_keys.get(word.lower())
        if right is not None:
            if word != right:
                count += 1
            return right
        return word

    text = re.sub(r"\b[a-zA-Z][a-zA-Z'-]*\b", replace_word, text)
    return text, count


def _apply_wordlist_to_srt(content: str, wordlist: dict) -> tuple[str, int]:
    blocks = _parse_srt(content)
    corrected_blocks = []
    changes = 0

    for block in blocks:
        corrected_lines = []
        for line in block["text_lines"]:
            corrected_line, c = _correct_text(line, wordlist, block["index"])
            changes += c
            corrected_lines.append(corrected_line)
        corrected_blocks.append({**block, "text_lines": corrected_lines})

    if not corrected_blocks:
        return content, 0
    return _format_srt(corrected_blocks), changes


def _srt_time_to_seconds(t: str) -> float:
    t = t.strip().replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _srt_to_timestamped_text(srt_content: str) -> str:
    blocks = _parse_srt(srt_content)
    lines = []
    for block in blocks:
        start_str = block["timecode"].split(" --> ")[0]
        seconds = _srt_time_to_seconds(start_str)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ts_label = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
        text = " ".join(block["text_lines"])
        lines.append(f"[{ts_label}] {text}")
    return "\n".join(lines)


def _call_openai_chapters(timestamped_transcript: str) -> str:
    api_key = os.environ.get("LANGDOCK_API_KEY", "")
    if not api_key:
        raise ValueError("LANGDOCK_API_KEY environment variable is not set")

    payload = {
        "model": LANGDOCK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are an expert video editor creating YouTube chapter markers. Output ONLY the chapter list.",
            },
            {
                "role": "user",
                "content": (
                    "Here is a video transcript with timestamps. Identify key moments and output YouTube chapter markers. "
                    "First chapter must be 0:00. Format each line as M:SS Chapter Title. Output only chapter lines.\n\n"
                    f"Transcript:\n{timestamped_transcript}"
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        LANGDOCK_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def _parse_chapter_line(line: str) -> dict | None:
    m = re.match(r"^(\d+:\d{2}(?::\d{2})?)\s+(.+)$", line.strip())
    if not m:
        return None
    time_str = m.group(1)
    parts = time_str.split(":")
    secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) if len(parts) == 3 else int(parts[0]) * 60 + int(parts[1])
    return {"time": time_str, "seconds": secs, "title": m.group(2).strip()}


def _fetch_srt_for_video(base_dir: Path, video_id: str) -> str:
    subtitle_cache_dir = base_dir / "subtitle_cache"
    subtitle_cache_dir.mkdir(exist_ok=True)
    subtitle_cache_file = subtitle_cache_dir / f"{video_id}.srt"
    if subtitle_cache_file.exists():
        return subtitle_cache_file.read_text(encoding="utf-8", errors="replace")

    output_template = str((base_dir / "subtitle_cache") / "%(title)s.%(ext)s")
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = ["yt-dlp", "--write-auto-subs", "--sub-format", "srt", "--skip-download", "-o", output_template, url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Could not download subtitles: {result.stderr[:400]}")

    srt_files = sorted((base_dir / "subtitle_cache").glob("*.srt"), key=os.path.getctime)
    if not srt_files:
        raise RuntimeError("No subtitles found for this video.")

    content = srt_files[-1].read_text(encoding="utf-8", errors="replace")
    subtitle_cache_file.write_text(content, encoding="utf-8")
    return content


def build_video_index(
    base_dir: Path,
    *,
    batch_size: int = 50,
    force: bool = False,
    channel_video_url: str = CHANNEL_VIDEO_URL,
    channel_shorts_url: str = CHANNEL_SHORTS_URL,
    status_cb=None,
    video_cb=None,
) -> dict:
    channel_index_file = base_dir / "channel_index.json"
    wordlist_file = base_dir / "wordlist.json"

    def emit_status(msg: str):
        if status_cb:
            status_cb(msg)

    def emit_video(msg: str, state: str):
        if video_cb:
            video_cb(msg, state)

    try:
        with open(channel_index_file, encoding="utf-8") as f:
            existing_videos = json.load(f)
        if not isinstance(existing_videos, list):
            existing_videos = []
    except (FileNotFoundError, json.JSONDecodeError):
        existing_videos = []

    try:
        with open(wordlist_file, encoding="utf-8") as f:
            wordlist = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        wordlist = {}

    existing_by_id = {} if force else {v["id"]: v for v in existing_videos if isinstance(v, dict) and "id" in v}

    emit_status("Fetching channel videos and shorts…")
    source_lists = []
    for source_url, label in ((channel_video_url, "videos"), (channel_shorts_url, "shorts")):
        cmd = ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings", source_url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list channel {label}: {result.stderr[:400]}")
        source_items = [json.loads(l) for l in result.stdout.splitlines() if l.strip()]
        source_lists.append(source_items)

    video_list = []
    seen_ids = set()
    for source_items in source_lists:
        for item in source_items:
            video_id = item.get("id", "")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            video_list.append(item)

    batch_target = max(1, int(batch_size))
    emit_status(f"Found {len(video_list)} videos. Indexing up to {batch_target} new video(s)…")

    new_count = 0
    skipped = 0
    errors = 0
    result_videos = list(existing_by_id.values()) if not force else []
    indexed_ids = {v["id"] for v in result_videos if isinstance(v, dict) and "id" in v}

    for raw in video_list:
        if new_count >= batch_target:
            break

        video_id = raw.get("id", "")
        title = raw.get("title") or raw.get("fulltitle") or video_id
        duration = raw.get("duration_string") or ""

        if video_id in indexed_ids:
            skipped += 1
            emit_video(title, "skip")
            continue

        emit_video(title, "progress")

        try:
            srt_content = _fetch_srt_for_video(base_dir, video_id)
        except Exception:
            errors += 1
            emit_video(f"⚠ No subtitles: {title}", "error")
            continue

        corrected_srt, correction_count = _apply_wordlist_to_srt(srt_content, wordlist)
        if correction_count > 0:
            emit_status(f"Applied {correction_count} subtitle correction(s) for '{title}'")

        timestamped = _srt_to_timestamped_text(corrected_srt)
        if not timestamped:
            errors += 1
            emit_video(f"⚠ Empty transcript: {title}", "error")
            continue

        raw_chapters = None
        last_exc = None
        for attempt in range(1, 3):
            try:
                raw_chapters = _call_openai_chapters(timestamped)
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    emit_status(f"Chapter detection retry for '{title}'…")
                    time.sleep(1.0)

        if raw_chapters is None:
            errors += 1
            err_text = str(last_exc).lower() if last_exc else ""
            if "timeout" in err_text:
                emit_video(f"⚠ Chapter detection timed out: {title}", "error")
            else:
                emit_video(f"⚠ No chapters: {title}", "error")
            time.sleep(0.3)
            continue

        chapters = [c for c in (_parse_chapter_line(line) for line in raw_chapters.splitlines()) if c]
        if not chapters:
            errors += 1
            emit_video(f"⚠ Empty chapters: {title}", "error")
            continue

        result_videos.append({"id": video_id, "title": title, "duration": duration, "chapters": chapters})
        indexed_ids.add(video_id)
        new_count += 1
        emit_video(title, "done")

        with open(channel_index_file, "w", encoding="utf-8") as f:
            json.dump(result_videos, f, indent=2, ensure_ascii=False)

        time.sleep(0.4)

    return {"new_count": new_count, "skipped": skipped, "errors": errors, "total": len(result_videos)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DevRel blog/video indexes out of band")
    sub = parser.add_subparsers(dest="command", required=True)

    p_blog = sub.add_parser("blog", help="Refresh blog_index.json")
    p_blog.add_argument("--base-dir", default=str(Path(__file__).parent), help="Workspace root containing blog_index.json")
    p_blog.add_argument("--min-year", type=int, default=BLOG_MIN_YEAR)
    p_blog.add_argument("--max-pages", type=int, default=BLOG_MAX_PAGES)

    p_video = sub.add_parser("video", help="Refresh channel_index.json")
    p_video.add_argument("--base-dir", default=str(Path(__file__).parent), help="Workspace root containing channel_index.json")
    p_video.add_argument("--batch-size", type=int, default=50)
    p_video.add_argument("--force", action="store_true")

    args = parser.parse_args()
    base_dir = Path(args.base_dir).resolve()

    if args.command == "blog":
        blogs = build_blog_index(base_dir, min_year=args.min_year, max_pages=args.max_pages)
        print(f"Blog index updated: {len(blogs)} posts", flush=True)
        return 0

    if args.command == "video":
        stats = build_video_index(
            base_dir,
            batch_size=args.batch_size,
            force=args.force,
            status_cb=lambda m: print(f"[status] {m}", flush=True),
            video_cb=lambda m, s: print(f"[video:{s}] {m}", flush=True),
        )
        print(
            f"Video index updated: {stats['new_count']} new, {stats['skipped']} skipped, {stats['errors']} errors, {stats['total']} total",
            flush=True,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
