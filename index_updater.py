import argparse
import html
import json
import os
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BLOG_FEED_URL = "https://www.dynatrace.com/news/blog/feed/"
BLOG_MIN_YEAR = 2025
BLOG_MAX_PAGES = 30
RECENT_MAX_ITEMS = 100
RECENT_MAX_AGE_DAYS = 365

CHANNEL_VIDEO_URL = "https://www.youtube.com/@dynatrace/videos"
CHANNEL_SHORTS_URL = "https://www.youtube.com/@dynatrace/shorts"

LANGDOCK_ENDPOINT = "https://chat.langdock.internal.dynatrace.com/api/public/openai/eu/v1/chat/completions"


def _get_langdock_model() -> str:
    return os.environ.get("LANGDOCK_MODEL", "gpt-5-mini")


def _load_env_file(path: Path, *, overwrite: bool = False) -> int:
    """Load KEY=VALUE pairs from a .env-style file into os.environ."""
    if not path.exists():
        return 0

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if not overwrite and key in os.environ:
            continue

        os.environ[key] = value
        loaded += 1

    return loaded


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


def _pub_date_dt(pub_date: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(pub_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def build_blog_index(
    base_dir: Path,
    *,
    feed_url: str = BLOG_FEED_URL,
    min_year: int = BLOG_MIN_YEAR,
    max_pages: int = BLOG_MAX_PAGES,
    user_agent: str = "Mozilla/5.0 (DevRelToolbox Blog Indexer)",
    max_items: int = RECENT_MAX_ITEMS,
    max_age_days: int = RECENT_MAX_AGE_DAYS,
    status_cb=None,
) -> list[dict]:
    blog_index_file = base_dir / "blog_index.json"
    try:
        with open(blog_index_file, encoding="utf-8") as f:
            existing_blogs = json.load(f)
        if not isinstance(existing_blogs, list):
            existing_blogs = []
    except (FileNotFoundError, json.JSONDecodeError):
        existing_blogs = []

    existing_urls = {
        b.get("url", "") for b in existing_blogs if isinstance(b, dict) and b.get("url", "")
    }

    blogs: list[dict] = []
    seen_urls: set[str] = set(existing_urls)

    def emit_status(msg: str):
        if status_cb:
            status_cb(msg)

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
    max_items = max(1, int(max_items))

    emit_status(f"Loaded {len(existing_blogs)} existing blog post(s)")
    emit_status(
        f"Recent-only mode: up to {max_items} posts, newer than {cutoff_dt.date().isoformat()}"
    )

    for page in range(1, max_pages + 1):
        emit_status(f"Fetching blog feed page {page}/{max_pages}…")
        current_feed_url = feed_url if page == 1 else f"{feed_url}?paged={page}"
        req = urllib.request.Request(current_feed_url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        if not items:
            emit_status(f"No blog items found on page {page}; stopping pagination")
            break

        has_target_year = False
        new_on_page = 0
        known_on_page = 0
        old_on_page = 0
        for item in items:
            pub_date = _child_text(item, "pubDate")
            pub_dt = _pub_date_dt(pub_date)
            year = _pub_date_year(pub_date)
            if pub_dt is not None and pub_dt < cutoff_dt:
                old_on_page += 1
                continue

            if year is not None and year < min_year:
                old_on_page += 1
                continue

            has_target_year = True
            title = _child_text(item, "title")
            link = _child_text(item, "link")
            if not link:
                continue

            if link in existing_urls:
                known_on_page += 1
                continue

            if link in seen_urls:
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
            new_on_page += 1

            if len(blogs) >= max_items:
                emit_status(f"Reached max recent blog item limit ({max_items}); stopping")
                break

        emit_status(
            f"Page {page}: {new_on_page} new, {known_on_page} already indexed, {old_on_page} older than cutoff, {len(blogs)} total new so far"
        )

        if len(blogs) >= max_items:
            break

        if existing_urls and new_on_page == 0 and known_on_page > 0:
            emit_status(
                "Encountered a fully known page with no new posts; stopping early to avoid unnecessary fetches"
            )
            break

        if old_on_page > 0 and new_on_page == 0:
            emit_status("Encountered page with only old posts; stopping early")
            break

        if not has_target_year:
            emit_status(f"Reached posts older than {min_year} on page {page}; stopping pagination")
            break

        emit_status(f"Discovered {len(blogs)} new blog post(s) so far")

    if not blogs:
        emit_status("No new blog posts found; skipping blog_index.json rewrite")
        return existing_blogs

    # Keep new posts first, then keep prior posts while preventing duplicates by URL.
    merged = blogs[:]
    merged_urls = {b.get("url", "") for b in merged if isinstance(b, dict)}
    for item in existing_blogs:
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        if not url or url in merged_urls:
            continue
        merged.append(item)
        merged_urls.add(url)

    with open(blog_index_file, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    emit_status(f"Wrote {len(merged)} blog post(s) to {blog_index_file.name} ({len(blogs)} new)")

    return merged


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
        "model": _get_langdock_model(),
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
    cleaned = line.strip()
    cleaned = re.sub(r"^[\-\*\u2022\d\.\)\s]+", "", cleaned)
    m = re.match(r"^(\d+:\d{2}(?::\d{2})?)\s+(.+)$", cleaned)
    if not m:
        return None
    time_str = m.group(1)
    parts = time_str.split(":")
    secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) if len(parts) == 3 else int(parts[0]) * 60 + int(parts[1])
    return {"time": time_str, "seconds": secs, "title": m.group(2).strip()}


def _format_seconds_mmss(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fallback_chapters_from_transcript(timestamped: str, *, max_chapters: int = 8, min_gap_seconds: int = 90) -> list[dict]:
    """Best-effort chapter extraction from transcript lines like: [M:SS] text."""
    lines = [ln.strip() for ln in timestamped.splitlines() if ln.strip()]
    parsed: list[tuple[int, str]] = []
    for ln in lines:
        m = re.match(r"^\[(\d+:\d{2}(?::\d{2})?)\]\s+(.+)$", ln)
        if not m:
            continue
        ts = m.group(1)
        text = m.group(2).strip()
        parts = ts.split(":")
        seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) if len(parts) == 3 else int(parts[0]) * 60 + int(parts[1])
        parsed.append((seconds, text))

    if not parsed:
        return []

    chapters: list[dict] = [{"time": "0:00", "seconds": 0, "title": "Introduction"}]
    next_allowed = 0
    for seconds, text in parsed:
        if len(chapters) >= max_chapters:
            break
        if seconds < next_allowed:
            continue
        title_words = re.sub(r"\s+", " ", text).strip().split(" ")
        title = " ".join(title_words[:8]).strip(" -:.,") or "Section"
        if seconds == 0:
            continue
        chapters.append({"time": _format_seconds_mmss(seconds), "seconds": seconds, "title": title})
        next_allowed = seconds + min_gap_seconds

    deduped = []
    seen_seconds = set()
    for c in chapters:
        if c["seconds"] in seen_seconds:
            continue
        seen_seconds.add(c["seconds"])
        deduped.append(c)
    return deduped


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


def _fetch_video_metadata(video_id: str, *, timeout: int = 45) -> dict:
    """Fetch full metadata for a single video without downloading media."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = ["yt-dlp", "--skip-download", "--dump-json", "--no-warnings", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:400] or "yt-dlp metadata fetch failed")

    payload = (result.stdout or "").strip()
    if not payload:
        raise RuntimeError("yt-dlp metadata output was empty")
    return json.loads(payload)


def build_video_index(
    base_dir: Path,
    *,
    batch_size: int = 50,
    force: bool = False,
    max_items: int = RECENT_MAX_ITEMS,
    max_age_days: int = RECENT_MAX_AGE_DAYS,
    channel_video_url: str = CHANNEL_VIDEO_URL,
    channel_shorts_url: str = CHANNEL_SHORTS_URL,
    status_cb=None,
    video_cb=None,
) -> dict:
    channel_index_file = base_dir / "channel_index.json"

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

    existing_by_id = {} if force else {v["id"]: v for v in existing_videos if isinstance(v, dict) and "id" in v}
    emit_status(f"Loaded {len(existing_videos)} existing video record(s)")
    emit_status("Fast mode: indexing metadata only (title + description), skipping subtitles and chapter generation")

    emit_status("Fetching channel videos and shorts…")
    source_lists: list[tuple[list[dict], str]] = []
    batch_target = max(1, int(batch_size))
    max_items = max(1, int(max_items))
    max_age_days = max(1, int(max_age_days))
    date_after = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y%m%d")
    emit_status(
        f"Recent-only mode: up to {max_items} entries per source, newer than {date_after}"
    )

    for source_url, label in ((channel_video_url, "videos"), (channel_shorts_url, "shorts")):
        emit_status(f"Listing channel {label} with yt-dlp…")
        cmd = ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings"]
        cmd.extend(["--playlist-end", str(max_items), "--dateafter", date_after])
        cmd.append(source_url)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list channel {label}: {result.stderr[:400]}")
        source_items = [json.loads(l) for l in result.stdout.splitlines() if l.strip()]
        emit_status(f"Found {len(source_items)} item(s) in channel {label}")
        source_lists.append((source_items, label))

    video_list: list[tuple[dict, str]] = []
    seen_ids = set()
    for source_items, label in source_lists:
        for item in source_items:
            video_id = item.get("id", "")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            video_list.append((item, label))
            if len(video_list) >= max_items:
                break
        if len(video_list) >= max_items:
            break

    emit_status(f"Found {len(video_list)} videos. Indexing up to {batch_target} new video(s)…")

    new_count = 0
    skipped = 0
    errors = 0
    result_videos = list(existing_by_id.values()) if not force else []
    indexed_ids = {v["id"] for v in result_videos if isinstance(v, dict) and "id" in v}
    known_streak = 0

    total_candidates = len(video_list)
    for idx, (raw, source_label) in enumerate(video_list, start=1):
        if new_count >= batch_target:
            break

        video_id = raw.get("id", "")
        title = raw.get("title") or raw.get("fulltitle") or video_id
        duration = raw.get("duration_string") or ""

        if video_id in indexed_ids:
            skipped += 1
            known_streak += 1
            emit_video(title, "skip")
            if not force and new_count == 0 and known_streak >= max(20, batch_target):
                emit_status(
                    "Encountered a long run of already indexed videos with no new items; stopping early"
                )
                break
            continue

        known_streak = 0

        emit_status(f"[{idx}/{total_candidates}] Processing '{title}'")
        emit_video(title, "progress")

        try:
            meta = _fetch_video_metadata(video_id)
        except Exception as exc:
            errors += 1
            emit_video(f"⚠ Metadata fetch failed: {title}", "error")
            emit_status(
                f"[{idx}/{total_candidates}] Skipped '{title}' because metadata fetch failed: {str(exc)[:180]}"
            )
            continue

        description = (meta.get("description") or raw.get("description") or "").strip()
        uploaded = meta.get("upload_date") or raw.get("upload_date") or ""
        searchable_text = f"{title} {description}".strip().lower()

        result_videos.append(
            {
                "id": video_id,
                "title": title,
                "description": description,
                "duration": meta.get("duration_string") or duration,
                "uploaded": uploaded,
                "source": source_label,
                "searchable_text": searchable_text,
            }
        )
        indexed_ids.add(video_id)
        new_count += 1
        emit_video(title, "done")

        with open(channel_index_file, "w", encoding="utf-8") as f:
            json.dump(result_videos, f, indent=2, ensure_ascii=False)

        emit_status(
            f"Saved progress: {new_count} new, {skipped} skipped, {errors} errors, {len(result_videos)} total indexed"
        )

    if new_count == 0:
        emit_status("No new videos found; channel_index.json unchanged")

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
    p_video.add_argument("--max-items", type=int, default=RECENT_MAX_ITEMS)
    p_video.add_argument("--max-age-days", type=int, default=RECENT_MAX_AGE_DAYS)
    p_video.add_argument("--force", action="store_true")

    args = parser.parse_args()
    base_dir = Path(args.base_dir).resolve()

    env_path = base_dir / ".env"
    loaded_env_vars = _load_env_file(env_path, overwrite=False)
    if loaded_env_vars:
        print(f"[status] Loaded {loaded_env_vars} env var(s) from {env_path.name}", flush=True)
    if os.environ.get("LANGDOCK_API_KEY", ""):
        print("[status] LANGDOCK_API_KEY detected", flush=True)
    if os.environ.get("LANGDOCK_MODEL", ""):
        print(f"[status] LANGDOCK_MODEL={_get_langdock_model()}", flush=True)

    if args.command == "blog":
        blogs = build_blog_index(
            base_dir,
            min_year=args.min_year,
            max_pages=args.max_pages,
            status_cb=lambda m: print(f"[status] {m}", flush=True),
        )
        print(f"Blog index updated: {len(blogs)} posts", flush=True)
        return 0

    if args.command == "video":
        stats = build_video_index(
            base_dir,
            batch_size=args.batch_size,
            max_items=args.max_items,
            max_age_days=args.max_age_days,
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
