import re
import ssl
import shutil
import time
import uvicorn
import asyncio
import threading
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
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

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DevRel Toolbox</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'DT Flow', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 0 20px 40px;
        }

        .site-header {
            width: 100%;
            max-width: 1800px;
            display: flex;
            align-items: center;
            gap: 18px;
            padding: 28px 0 24px;
        }

        .site-header .dt-logo {
            width: 48px;
            height: 48px;
            flex-shrink: 0;
            fill: #ffffff;
            filter: drop-shadow(0 2px 6px rgba(0,0,0,0.25));
        }

        .site-header .header-text {
            display: flex;
            flex-direction: column;
            line-height: 1.1;
        }

        .site-header .brand {
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.75);
        }

        .site-header .product-title {
            font-size: 28px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.01em;
            text-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }

        .header-actions {
            margin-left: auto;
            position: relative;
        }

        .menu-toggle {
            width: auto;
            padding: 10px 14px;
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.45);
            background: rgba(255,255,255,0.14);
            color: #fff;
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.02em;
            cursor: pointer;
        }

        .menu-toggle:hover {
            box-shadow: none;
            transform: none;
            background: rgba(255,255,255,0.24);
        }

        .menu-drawer {
            position: absolute;
            top: calc(100% + 10px);
            right: 0;
            width: min(360px, calc(100vw - 40px));
            background: rgba(255,255,255,0.96);
            border-radius: 12px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.2);
            border: 1px solid rgba(255,255,255,0.6);
            padding: 16px;
            z-index: 1200;
        }

        .menu-links {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .menu-links a {
            display: inline-block;
            padding: 8px 14px;
            border-radius: 999px;
            background: #eef3ff;
            border: 1px solid #d8e2ff;
            color: #243b7b;
            text-decoration: none;
            font-size: 13px;
            font-weight: 700;
        }

        .menu-links a:hover {
            background: #e3ecff;
        }

        .external-link::after {
            content: " ↗";
            font-size: 12px;
            font-weight: 700;
            opacity: 0.9;
        }

        .page-wrapper {
            display: grid;
            grid-template-columns: repeat(5, minmax(240px, 1fr));
            gap: 16px;
            width: 100%;
            max-width: 1800px;
        }

        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            padding: 24px;
            min-width: 0;
            max-width: none;
        }

        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 24px;
        }

        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }

        .form-group {
            margin-bottom: 20px;
        }

        label {
            display: block;
            color: #333;
            font-weight: 600;
            margin-bottom: 8px;
            font-size: 14px;
        }

        input[type="url"], input[type="file"] {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s;
        }

        input[type="url"]:focus, input[type="file"]:focus {
            outline: none;
            border-color: #1966FF;
        }

        button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(25, 102, 255, 0.3);
        }
        
        button:active {
            transform: translateY(0);
        }
        
        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        
        .message {
            margin-top: 20px;
            padding: 12px;
            border-radius: 6px;
            display: none;
            font-size: 14px;
        }
        
        .message.success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .message.error {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .spinner {
            display: none;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(25, 102, 255, 0.3);
            border-radius: 50%;
            border-top-color: #1966FF;
            animation: spin 1s linear infinite;
            margin: 10px auto 0;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .download-link {
            display: inline-block;
            margin-top: 12px;
            padding: 10px 20px;
            background-color: #28a745;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            transition: background-color 0.2s;
        }

        .download-link:hover {
            background-color: #218838;
        }

        .summary-box {
            margin-top: 20px;
            padding: 16px;
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            font-size: 13px;
            overflow-x: auto;
        }

        .summary-box h1 { font-size: 18px; margin-bottom: 12px; color: #333; }
        .summary-box h2 { font-size: 15px; margin: 16px 0 8px; color: #444; }
        .summary-box p  { margin-bottom: 8px; line-height: 1.5; }
        .summary-box em { font-style: italic; }

        .summary-box table {
            border-collapse: collapse;
            width: 100%;
            margin-top: 8px;
        }

        .summary-box th, .summary-box td {
            border: 1px solid #dee2e6;
            padding: 6px 10px;
            text-align: left;
        }

        .summary-box th { background: #e9ecef; font-weight: 600; }
        .summary-box tr:nth-child(even) { background: #f2f2f2; }
        .summary-box code {
            background: #e9ecef;
            padding: 1px 4px;
            border-radius: 3px;
            font-family: monospace;
        }

        .timestamp-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }

        .timestamp-row input {
            flex: 1;
            min-width: 0;
            padding: 10px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s;
        }

        .timestamp-row input:focus {
            outline: none;
            border-color: #1966FF;
        }

        .remove-ts {
            width: auto !important;
            padding: 6px 10px !important;
            font-size: 13px !important;
            background: #dc3545 !important;
        }

        .add-ts-btn {
            width: auto;
            padding: 8px 16px;
            background: #6c757d;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            margin-top: 4px;
        }

        .add-ts-btn:hover { background: #5a6268; }

        .gif-preview {
            max-width: 100%;
            border-radius: 6px;
            border: 1px solid #dee2e6;
            margin-top: 8px;
        }

        .primary-color-tile {
            display: grid;
            gap: 12px;
        }

        .primary-color-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 8px;
        }

        .primary-color-swatch {
            position: relative;
            width: 100%;
            padding: 0;
            text-align: left;
            cursor: pointer;
            border: 1px solid #d8e2ff;
            border-radius: 8px;
            overflow: hidden;
            background: #fff;
            transition: transform 0.15s, box-shadow 0.15s;
        }

        .primary-color-swatch:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.12);
        }

        .primary-color-swatch:focus-visible {
            outline: 3px solid rgba(25, 102, 255, 0.35);
            outline-offset: 1px;
        }

        .primary-color-swatch::after {
            content: attr(data-tooltip);
            position: absolute;
            left: 50%;
            bottom: calc(100% + 8px);
            transform: translateX(-50%) translateY(4px);
            background: rgba(20, 29, 62, 0.96);
            color: #fff;
            border-radius: 6px;
            padding: 5px 8px;
            font-size: 11px;
            font-weight: 700;
            line-height: 1.2;
            white-space: nowrap;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.15s, transform 0.15s;
            z-index: 3;
        }

        .primary-color-swatch:hover::after,
        .primary-color-swatch:focus-visible::after {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }

        .primary-color-chip {
            height: 34px;
            border-bottom: 1px solid rgba(0,0,0,0.08);
        }

        .primary-color-label {
            padding: 6px;
            display: grid;
            gap: 2px;
            font-size: 11px;
            line-height: 1.2;
            color: #2b3350;
        }

        .primary-color-label strong {
            font-size: 11px;
            font-weight: 700;
            color: #1f2a46;
        }

        .primary-color-status {
            min-height: 18px;
            font-size: 12px;
            font-weight: 700;
            color: #1f8b4c;
        }

        .primary-color-status.error {
            color: #a23333;
        }

        .color-picker-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            padding: 10px 12px;
            border-radius: 8px;
            text-decoration: none;
            color: #fff;
            font-size: 13px;
            font-weight: 700;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
        }

        .color-picker-link:hover {
            filter: brightness(1.04);
        }

        @media (max-width: 900px) {
            .primary-color-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }

        @media (max-width: 520px) {
            .primary-color-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 1500px) {
            .page-wrapper {
                grid-template-columns: repeat(3, minmax(260px, 1fr));
            }
        }

        @media (max-width: 1080px) {
            .page-wrapper {
                grid-template-columns: repeat(2, minmax(260px, 1fr));
            }
        }

        @media (max-width: 680px) {
            .page-wrapper {
                grid-template-columns: 1fr;
            }

            .menu-drawer {
                width: min(340px, calc(100vw - 28px));
                padding: 12px;
            }
        }
    </style>
</head>
<body>
    <header class="site-header">
        <svg class="dt-logo" role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-label="Dynatrace">
            <path d="M9.372 0c-.31.006-.93.09-1.521.654-.872.824-5.225 4.957-6.973 6.617-.79.754-.72 1.595-.72 1.664v.377c.067-.292.187-.5.427-.825.496-.616 1.3-.788 1.627-.822a64.238 64.238 0 01.002 0 64.238 64.238 0 016.528-.55c4.335-.136 7.197.226 7.197.226l6.085-5.794s-3.188-.6-6.82-1.027a93.4 93.4 0 00-5.64-.514c-.02 0-.09-.008-.192-.006zm13.56 2.508l-6.066 5.79s.222 2.881-.137 7.2c-.189 2.45-.584 4.866-.875 6.494-.052.326-.256 1.114-.925 1.594-.29.198-.49.295-.748.363 1.546-.51 1.091-7.047 1.091-7.047-4.335.137-7.214-.223-7.214-.223l-6.085 5.793s3.223.634 6.856 1.045c2.056.24 4.833.429 5.227.463.023 0 .045-.007.068-.012-.013.003-.022.009-.035.012.138 0 .26.015.38.015.084 0 .924.105 1.712-.648 1.748-1.663 6.084-5.81 6.94-6.634.789-.754.72-1.594.72-1.68a81.846 81.846 0 00-.206-5.654 101.75 101.75 0 00-.701-6.872zM3.855 8.306c-1.73.002-3.508.208-3.696 1.021.017 1.216.05 3.137.205 5.28.24 3.65.703 6.887.703 6.887l6.083-5.79c-.017.016-.24-2.88.12-7.2 0 0-1.684-.201-3.416-.2z"/>
        </svg>
        <div class="header-text">
            <span class="brand">Dynatrace</span>
            <span class="product-title">DevRel Toolbox</span>
        </div>
        <div class="header-actions">
            <button type="button" id="menuToggle" class="menu-toggle" aria-expanded="false" aria-controls="toolMenu">&#9776; Menu</button>
            <div id="toolMenu" class="menu-drawer" hidden>
                <div class="menu-links">
                    <a href="https://live.standards.site/dynatrace/" target="_blank" rel="noopener" class="external-link">&#128278; Brand Guidelines</a>
                    <a href="/color-picker">&#127912; Color Picker</a>
                    <a href="/code-cards">&#128248; Code Cards</a>
                    <a href="/wordlist-manager">&#128221; Wordlist Manager</a>
                    <a href="/navigator">&#128269; Video Navigator</a>
                    <a href="/blog-navigator">&#128240; Blog Navigator</a>
                    <a href="/browser-recorder">&#127910; Browser Recorder</a>
                    <a href="/docs/index-refresh">&#128214; Index Refresh Docs</a>
                </div>
            </div>
        </div>
    </header>

    <div class="page-wrapper">

        <!-- Feature 1: YouTube Subtitle Downloader -->
        <div class="container">
            <h1>📥 YouTube Subtitle Downloader</h1>
            <p class="subtitle">Download automatic subtitles in SRT format</p>

            <form id="urlForm">
                <div class="form-group">
                    <label for="youtubeUrl">YouTube URL</label>
                    <input
                        type="url"
                        id="youtubeUrl"
                        name="url"
                        placeholder="https://www.youtube.com/watch?v=..."
                        required
                    >
                </div>

                <button type="submit" id="submitBtn">Download Subtitles</button>
                <div class="spinner" id="spinner"></div>
            </form>

            <div class="message" id="message"></div>
            <div id="ytSummaryContainer" style="display:none; margin-top:20px;">
                <div class="summary-box" id="ytSummaryContent"></div>
            </div>
        </div>

        <!-- Feature 2: SRT Corrector -->
        <div class="container">
            <h1>✏️ SRT Corrector</h1>
            <p class="subtitle">Fix spelling errors and domain-specific terms in SRT files</p>

            <form id="srtForm">
                <div class="form-group">
                    <label for="srtFile">Upload SRT File</label>
                    <input type="file" id="srtFile" accept=".srt" required>
                </div>

                <button type="submit" id="srtSubmitBtn">Correct SRT</button>
                <div class="spinner" id="srtSpinner"></div>
            </form>

            <div class="message" id="srtMessage"></div>
            <div id="summaryContainer" style="display:none">
                <div class="summary-box" id="summaryContent"></div>
            </div>
        </div>

        <!-- Feature 3: MP4 Transcriber -->
        <div class="container">
            <h1>🎬 MP4 Transcriber</h1>
            <p class="subtitle">Upload an MP4, get a corrected SRT transcript</p>

            <form id="mp4Form">
                <div class="form-group">
                    <label for="mp4File">Upload MP4 File</label>
                    <input type="file" id="mp4File" accept=".mp4,video/mp4" required>
                </div>

                <button type="submit" id="mp4SubmitBtn">Transcribe</button>
                <div class="spinner" id="mp4Spinner"></div>
            </form>

            <div class="message" id="mp4Message"></div>
            <div id="mp4SummaryContainer" style="display:none">
                <div class="summary-box" id="mp4SummaryContent"></div>
            </div>
        </div>

        <!-- Feature 5: Highlight Reel -->
        <div class="container">
            <h1>🎞️ Highlight Reel</h1>
            <p class="subtitle">Get an executive summary and GIF for specific video sections</p>

            <form id="highlightForm">
                <div class="form-group">
                    <label for="highlightUrl">YouTube URL</label>
                    <input type="url" id="highlightUrl" placeholder="https://www.youtube.com/watch?v=..." required>
                </div>

                <div class="form-group">
                    <label>Timestamps</label>
                    <div id="timestampList">
                        <div class="timestamp-row">
                            <input type="text" placeholder="Start (e.g. 1:30)" class="ts-start">
                            <span style="color:#666; flex-shrink:0">&ndash;</span>
                            <input type="text" placeholder="End (e.g. 2:15)" class="ts-end">
                            <button type="button" class="remove-ts" style="display:none">&times;</button>
                        </div>
                    </div>
                    <button type="button" id="addTimestamp" class="add-ts-btn">+ Add Timestamp</button>
                </div>

                <button type="submit" id="highlightSubmitBtn">Generate Highlight Reel</button>
                <div class="spinner" id="highlightSpinner"></div>
            </form>

            <div class="message" id="highlightMessage"></div>
            <div id="highlightStatusList" style="display:none; margin-top:12px; font-size:13px; color:#555;"></div>
            <div id="highlightResultContainer" style="display:none; margin-top:20px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                    <strong style="font-size:13px; color:#444;">Executive Summary (Markdown)</strong>
                    <button type="button" id="copySummaryBtn" style="width:auto; padding:5px 12px; font-size:12px;">Copy</button>
                </div>
                <textarea id="highlightSummary" readonly style="width:100%; min-height:180px; font-family:monospace; font-size:12px; padding:10px; border:1px solid #dee2e6; border-radius:6px; resize:vertical; background:#f8f9fa;"></textarea>
                <div id="highlightGifContainer" style="margin-top:16px; text-align:center;"></div>
            </div>
        </div>

        <!-- Feature 6: Browser Recorder -->
        <div class="container">
            <h1>🎬 Browser Recorder</h1>
            <p class="subtitle">Describe what you want to record, generate a Playwright script, and get an MP4 video</p>
            <a href="/browser-recorder" style="display:inline-block; margin-top:12px; padding:10px 16px; background-color:#1966FF; color:white; border-radius:6px; text-decoration:none; font-weight:600;">Open Browser Recorder →</a>
        </div>

        <!-- Feature 7: Chapter Detector -->
        <div class="container">
            <h1>&#128203; Chapter Detector</h1>
            <p class="subtitle">Generate YouTube key moments from a video transcript</p>

            <div style="display:flex; gap:0; margin-bottom:20px; border:2px solid #e0e0e0; border-radius:6px; overflow:hidden;">
                <label id="chapterModeUrlLabel" style="flex:1; text-align:center; padding:9px; font-size:13px; font-weight:600; cursor:pointer; background:#1966FF; color:#fff; transition:background 0.2s;">
                    <input type="radio" name="chapterMode" value="url" checked style="display:none;"> YouTube URL
                </label>
                <label id="chapterModeFileLabel" style="flex:1; text-align:center; padding:9px; font-size:13px; font-weight:600; cursor:pointer; background:#f8f9fa; color:#555; transition:background 0.2s;">
                    <input type="radio" name="chapterMode" value="file" style="display:none;"> SRT File
                </label>
            </div>

            <form id="chapterForm">
                <div class="form-group" id="chapterUrlGroup">
                    <label for="chapterUrl">YouTube URL</label>
                    <input type="url" id="chapterUrl" placeholder="https://www.youtube.com/watch?v=...">
                </div>
                <div class="form-group" id="chapterFileGroup" style="display:none;">
                    <label for="chapterFile">Upload SRT File</label>
                    <input type="file" id="chapterFile" accept=".srt">
                </div>

                <button type="submit" id="chapterSubmitBtn">Detect Chapters</button>
                <div class="spinner" id="chapterSpinner"></div>
            </form>

            <div class="message" id="chapterMessage"></div>
            <div id="chapterStatusList" style="display:none; margin-top:12px; font-size:13px; color:#555;"></div>
            <div id="chapterResultContainer" style="display:none; margin-top:20px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                    <strong style="font-size:13px; color:#444;">Key Moments (paste into YouTube description)</strong>
                    <button type="button" id="copyChaptersBtn" style="width:auto; padding:5px 12px; font-size:12px;">Copy</button>
                </div>
                <textarea id="chapterOutput" readonly style="width:100%; min-height:160px; font-family:monospace; font-size:13px; padding:10px; border:1px solid #dee2e6; border-radius:6px; resize:vertical; background:#f8f9fa;"></textarea>
            </div>
        </div>

        <!-- Feature 8: Metadata Optimizer -->
        <div class="container">
            <h1>&#128269; Metadata Optimizer</h1>
            <p class="subtitle">Investigate a YouTube video and get concrete metadata improvements</p>

            <form id="metadataForm">
                <div class="form-group">
                    <label for="metadataUrl">YouTube URL</label>
                    <input type="url" id="metadataUrl" placeholder="https://www.youtube.com/watch?v=..." required>
                </div>

                <div class="form-group">
                    <label for="metadataKeywords">Target keywords</label>
                    <textarea id="metadataKeywords" rows="4" style="width:100%; padding:12px; border:2px solid #e0e0e0; border-radius:6px; font-size:14px; resize:vertical;" placeholder="e.g. dynatrace tutorial, distributed tracing, observability"></textarea>
                </div>

                <button type="submit" id="metadataSubmitBtn">Investigate Metadata</button>
                <div class="spinner" id="metadataSpinner"></div>
            </form>

            <div class="message" id="metadataMessage"></div>
            <div id="metadataResultContainer" style="display:none; margin-top:20px;">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
                    <strong style="font-size:13px; color:#444;">Recommended metadata plan (Markdown)</strong>
                    <button type="button" id="copyMetadataBtn" style="width:auto; padding:5px 12px; font-size:12px;">Copy</button>
                </div>
                <textarea id="metadataOutput" readonly style="width:100%; min-height:260px; font-family:monospace; font-size:12px; padding:10px; border:1px solid #dee2e6; border-radius:6px; resize:vertical; background:#f8f9fa;"></textarea>
            </div>
        </div>

        <!-- Feature 7: Primary Brand Colors -->
        <div class="container">
            <h1>&#127912; Primary Brand Colors</h1>
            <p class="subtitle">Quick reference for core Dynatrace colors</p>

            <div class="primary-color-tile">
                <div class="primary-color-grid" aria-label="Primary Dynatrace colors">
                    <button type="button" class="primary-color-swatch" data-name="Pink" data-hex="#BB0FD2" data-tooltip="Pink - #BB0FD2" title="Pink - #BB0FD2" aria-label="Pink #BB0FD2 copy hex">
                        <div class="primary-color-chip" style="background:#BB0FD2;"></div>
                        <div class="primary-color-label"><strong>Pink</strong><span>#BB0FD2</span></div>
                    </button>
                    <button type="button" class="primary-color-swatch" data-name="Purple" data-hex="#5E29E5" data-tooltip="Purple - #5E29E5" title="Purple - #5E29E5" aria-label="Purple #5E29E5 copy hex">
                        <div class="primary-color-chip" style="background:#5E29E5;"></div>
                        <div class="primary-color-label"><strong>Purple</strong><span>#5E29E5</span></div>
                    </button>
                    <button type="button" class="primary-color-swatch" data-name="Blue" data-hex="#1966FF" data-tooltip="Blue - #1966FF" title="Blue - #1966FF" aria-label="Blue #1966FF copy hex">
                        <div class="primary-color-chip" style="background:#1966FF;"></div>
                        <div class="primary-color-label"><strong>Blue</strong><span>#1966FF</span></div>
                    </button>
                    <button type="button" class="primary-color-swatch" data-name="Turquoise" data-hex="#5DF2E0" data-tooltip="Turquoise - #5DF2E0" title="Turquoise - #5DF2E0" aria-label="Turquoise #5DF2E0 copy hex">
                        <div class="primary-color-chip" style="background:#5DF2E0;"></div>
                        <div class="primary-color-label"><strong>Turquoise</strong><span>#5DF2E0</span></div>
                    </button>
                    <button type="button" class="primary-color-swatch" data-name="Black" data-hex="#000000" data-tooltip="Black - #000000" title="Black - #000000" aria-label="Black #000000 copy hex">
                        <div class="primary-color-chip" style="background:#000000;"></div>
                        <div class="primary-color-label"><strong>Black</strong><span>#000000</span></div>
                    </button>
                </div>

                <div id="primaryColorStatus" class="primary-color-status" aria-live="polite"></div>

                <a class="color-picker-link" href="/color-picker">Open Full Color Picker</a>
            </div>
        </div>

    </div>

    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script>
        // Header menu overlay
        const menuToggle = document.getElementById('menuToggle');
        const toolMenu = document.getElementById('toolMenu');
        const urlForm = document.getElementById('urlForm');
        const message = document.getElementById('message');
        const spinner = document.getElementById('spinner');
        const submitBtn = document.getElementById('submitBtn');
        const ytSummaryContainer = document.getElementById('ytSummaryContainer');
        const ytSummaryContent = document.getElementById('ytSummaryContent');

        function closeToolMenu() {
            toolMenu.hidden = true;
            menuToggle.setAttribute('aria-expanded', 'false');
            menuToggle.textContent = '\u2630 Menu';
        }

        menuToggle.addEventListener('click', () => {
            const opening = toolMenu.hidden;
            toolMenu.hidden = !toolMenu.hidden;
            menuToggle.setAttribute('aria-expanded', String(opening));
            menuToggle.textContent = opening ? '\u2715 Close' : '\u2630 Menu';
        });

        document.addEventListener('click', (event) => {
            if (toolMenu.hidden) return;
            if (toolMenu.contains(event.target) || menuToggle.contains(event.target)) return;
            closeToolMenu();
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && !toolMenu.hidden) {
                closeToolMenu();
            }
        });

        function renderMarkdownSafe(input) {
            if (typeof input !== 'string' || !input.trim()) {
                return '<p><em>No summary available.</em></p>';
            }
            return marked.parse(input);
        }

        urlForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const url = document.getElementById('youtubeUrl').value.trim();
            if (!url) return;

            message.style.display = 'none';
            message.innerHTML = '';
            ytSummaryContainer.style.display = 'none';
            ytSummaryContent.innerHTML = '';

            spinner.style.display = 'block';
            submitBtn.disabled = true;
            submitBtn.textContent = 'Downloading...';

            try {
                const response = await fetch('/api/download-subtitles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url })
                });

                const data = await response.json();

                if (response.ok) {
                    message.className = 'message success';
                    message.innerHTML = `✓ Subtitles downloaded and corrected! ${data.changes_count} change(s) made.<br><a href="${data.download_url}" class="download-link">📥 Download Corrected SRT</a>`;
                    ytSummaryContent.innerHTML = renderMarkdownSafe(data.summary);
                    ytSummaryContainer.style.display = 'block';
                    urlForm.reset();
                } else {
                    message.className = 'message error';
                    message.textContent = '✗ Error: ' + (data.detail || 'Failed to download subtitles');
                }
            } catch (error) {
                message.className = 'message error';
                message.textContent = '✗ Error: ' + error.message;
            } finally {
                message.style.display = 'block';
                spinner.style.display = 'none';
                submitBtn.disabled = false;
                submitBtn.textContent = 'Download Subtitles';
            }
        });

        srtForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const fileInput = document.getElementById('srtFile');
            const file = fileInput.files[0];
            if (!file) return;

            srtMessage.style.display = 'none';
            srtMessage.innerHTML = '';
            summaryContainer.style.display = 'none';
            summaryContent.innerHTML = '';

            srtSpinner.style.display = 'block';
            srtSubmitBtn.disabled = true;
            srtSubmitBtn.textContent = 'Correcting...';

            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch('/api/correct-srt', {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (response.ok) {
                    srtMessage.className = 'message success';
                    srtMessage.innerHTML = `✓ Correction complete! ${data.changes_count} change(s) made.<br><a href="${data.download_url}" class="download-link">📥 Download Corrected SRT</a>`;
                    summaryContent.innerHTML = renderMarkdownSafe(data.summary);
                    summaryContainer.style.display = 'block';
                    srtForm.reset();
                } else {
                    srtMessage.className = 'message error';
                    srtMessage.textContent = '✗ Error: ' + (data.detail || 'Failed to correct SRT');
                }
            } catch (error) {
                srtMessage.className = 'message error';
                srtMessage.textContent = '✗ Error: ' + error.message;
            } finally {
                srtMessage.style.display = 'block';
                srtSpinner.style.display = 'none';
                srtSubmitBtn.disabled = false;
                srtSubmitBtn.textContent = 'Correct SRT';
            }
        });

        // Feature 3: MP4 Transcriber
        const mp4Form = document.getElementById('mp4Form');
        const mp4Message = document.getElementById('mp4Message');
        const mp4Spinner = document.getElementById('mp4Spinner');
        const mp4SubmitBtn = document.getElementById('mp4SubmitBtn');
        const mp4SummaryContainer = document.getElementById('mp4SummaryContainer');
        const mp4SummaryContent = document.getElementById('mp4SummaryContent');

        mp4Form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const fileInput = document.getElementById('mp4File');
            const file = fileInput.files[0];
            if (!file) return;

            mp4Message.style.display = 'none';
            mp4Message.innerHTML = '';
            mp4SummaryContainer.style.display = 'none';
            mp4SummaryContent.innerHTML = '';

            mp4Spinner.style.display = 'block';
            mp4SubmitBtn.disabled = true;
            mp4SubmitBtn.textContent = 'Transcribing\u2026 (this may take a few minutes)';

            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch('/api/transcribe-mp4', {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (response.ok) {
                    mp4Message.className = 'message success';
                    mp4Message.innerHTML = `\u2713 Transcription complete! ${data.changes_count} wordlist correction(s) applied.<br><a href="${data.download_url}" class="download-link">📥 Download Corrected SRT</a>`;
                    mp4SummaryContent.innerHTML = renderMarkdownSafe(data.summary);
                    mp4SummaryContainer.style.display = 'block';
                    mp4Form.reset();
                } else {
                    mp4Message.className = 'message error';
                    mp4Message.textContent = '\u2717 Error: ' + (data.detail || 'Transcription failed');
                }
            } catch (error) {
                mp4Message.className = 'message error';
                mp4Message.textContent = '\u2717 Error: ' + error.message;
            } finally {
                mp4Message.style.display = 'block';
                mp4Spinner.style.display = 'none';
                mp4SubmitBtn.disabled = false;
                mp4SubmitBtn.textContent = 'Transcribe';
            }
        });

        // Feature 4: Wordlist Manager
        const wordlistForm = document.getElementById('wordlistForm');
        const wordlistMessage = document.getElementById('wordlistMessage');
        const wordlistSubmitBtn = document.getElementById('wordlistSubmitBtn');
        const wordlistTableContainer = document.getElementById('wordlistTableContainer');
        const wordlistTableBody = document.querySelector('#wordlistTable tbody');

        async function loadWordlist() {
            if (!wordlistTableBody || !wordlistTableContainer) return;
            try {
                const resp = await fetch('/api/wordlist');
                const data = await resp.json();
                wordlistTableBody.innerHTML = '';
                const entries = Object.entries(data.wordlist);
                if (entries.length === 0) {
                    wordlistTableContainer.style.display = 'none';
                    return;
                }
                for (const [wrong, right] of entries) {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `<td>${wrong}</td><td>${right}</td>`;
                    wordlistTableBody.appendChild(tr);
                }
                wordlistTableContainer.style.display = 'block';
            } catch {}
        }

        if (wordlistForm && wordlistMessage && wordlistSubmitBtn) {
            wordlistForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const wrong = document.getElementById('wrongWord').value.trim();
                const right = document.getElementById('rightWord').value.trim();
                if (!wrong || !right) return;

                wordlistMessage.style.display = 'none';
                wordlistSubmitBtn.disabled = true;

                try {
                    const response = await fetch('/api/wordlist', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ wrong, right })
                    });
                    const data = await response.json();
                    if (response.ok) {
                        wordlistMessage.className = 'message success';
                        wordlistMessage.textContent = `\u2713 Added: \"${wrong}\" \u2192 \"${right}\"${data.updated ? ' (updated existing entry)' : ''}`;
                        wordlistForm.reset();
                        loadWordlist();
                    } else {
                        wordlistMessage.className = 'message error';
                        wordlistMessage.textContent = '\u2717 Error: ' + (data.detail || 'Failed to update wordlist');
                    }
                } catch (error) {
                    wordlistMessage.className = 'message error';
                    wordlistMessage.textContent = '\u2717 Error: ' + error.message;
                } finally {
                    wordlistMessage.style.display = 'block';
                    wordlistSubmitBtn.disabled = false;
                }
            });

            // Load wordlist on page open
            loadWordlist();
        }

        // Feature 7: Primary Brand Colors
        const primaryColorStatus = document.getElementById('primaryColorStatus');
        const primaryColorSwatches = document.querySelectorAll('.primary-color-swatch');

        async function copyPrimaryColorHex(hex) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(hex);
                return;
            }

            const helper = document.createElement('textarea');
            helper.value = hex;
            helper.setAttribute('readonly', '');
            helper.style.position = 'fixed';
            helper.style.opacity = '0';
            helper.style.pointerEvents = 'none';
            document.body.appendChild(helper);
            helper.select();
            const copied = document.execCommand('copy');
            document.body.removeChild(helper);

            if (!copied) {
                throw new Error('Clipboard unavailable in this browser context');
            }
        }

        function setPrimaryColorStatus(kind, text) {
            primaryColorStatus.className = kind === 'error' ? 'primary-color-status error' : 'primary-color-status';
            primaryColorStatus.textContent = text;
        }

        primaryColorSwatches.forEach((swatch) => {
            swatch.addEventListener('click', async () => {
                const name = swatch.dataset.name || 'Color';
                const hex = swatch.dataset.hex || '';
                if (!hex) return;

                try {
                    await copyPrimaryColorHex(hex);
                    setPrimaryColorStatus('success', 'Copied ' + name + ' ' + hex + ' to clipboard');
                } catch (error) {
                    setPrimaryColorStatus('error', 'Copy failed: ' + error.message);
                }
            });
        });

        // ── Feature 5: Highlight Reel ──────────────────────────────────────

        function updateRemoveButtons() {
            const rows = document.querySelectorAll('#timestampList .timestamp-row');
            rows.forEach(row => {
                row.querySelector('.remove-ts').style.display = rows.length > 1 ? 'inline-block' : 'none';
            });
        }

        document.getElementById('timestampList').addEventListener('click', (e) => {
            if (e.target.classList.contains('remove-ts')) {
                e.target.closest('.timestamp-row').remove();
                updateRemoveButtons();
            }
        });

        document.getElementById('addTimestamp').addEventListener('click', () => {
            const list = document.getElementById('timestampList');
            const row = document.createElement('div');
            row.className = 'timestamp-row';
            row.innerHTML = '<input type="text" placeholder="Start (e.g. 1:30)" class="ts-start"><span style="color:#666; flex-shrink:0">&ndash;</span><input type="text" placeholder="End (e.g. 2:15)" class="ts-end"><button type="button" class="remove-ts">\u00d7</button>';
            list.appendChild(row);
            updateRemoveButtons();
        });

        const highlightForm = document.getElementById('highlightForm');
        const highlightMessage = document.getElementById('highlightMessage');
        const highlightSpinner = document.getElementById('highlightSpinner');
        const highlightSubmitBtn = document.getElementById('highlightSubmitBtn');
        const highlightResultContainer = document.getElementById('highlightResultContainer');
        const highlightSummary = document.getElementById('highlightSummary');
        const highlightGifContainer = document.getElementById('highlightGifContainer');
        const highlightStatusList = document.getElementById('highlightStatusList');
        const copySummaryBtn = document.getElementById('copySummaryBtn');

        copySummaryBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(highlightSummary.value).then(() => {
                copySummaryBtn.textContent = 'Copied!';
                setTimeout(() => { copySummaryBtn.textContent = 'Copy'; }, 1500);
            });
        });

        function addStatusLine(text, done = false) {
            const el = document.createElement('div');
            el.style.cssText = 'display:flex; align-items:center; gap:6px; padding:3px 0;';
            el.innerHTML = done
                ? '<span style="color:#28a745; font-weight:600;">&#10003;</span> ' + text
                : '<span class="spinner" style="display:inline-block; width:12px; height:12px; border-width:2px; margin:0;"></span> ' + text;
            highlightStatusList.appendChild(el);
            highlightStatusList.style.display = 'block';
            return el;
        }

        highlightForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const url = document.getElementById('highlightUrl').value;
            const rows = document.querySelectorAll('#timestampList .timestamp-row');
            const timestamps = [];
            for (const row of rows) {
                const start = row.querySelector('.ts-start').value.trim();
                const end = row.querySelector('.ts-end').value.trim();
                if (start && end) timestamps.push({ start, end });
            }

            if (timestamps.length === 0) {
                highlightMessage.className = 'message error';
                highlightMessage.textContent = '\u2717 Please enter at least one timestamp range';
                highlightMessage.style.display = 'block';
                return;
            }

            highlightMessage.style.display = 'none';
            highlightMessage.innerHTML = '';
            highlightResultContainer.style.display = 'none';
            highlightSummary.value = '';
            highlightGifContainer.innerHTML = '';
            highlightStatusList.innerHTML = '';
            highlightStatusList.style.display = 'none';

            highlightSpinner.style.display = 'block';
            highlightSubmitBtn.disabled = true;
            highlightSubmitBtn.textContent = 'Generating\u2026';

            let currentStatusEl = null;

            try {
                const response = await fetch('/api/highlight-reel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, timestamps })
                });

                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.detail || 'Request failed');
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\\n');
                    buffer = lines.pop();
                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const event = JSON.parse(line.slice(6));
                        if (event.type === 'status') {
                            if (currentStatusEl) {
                                // mark previous line done
                                currentStatusEl.innerHTML = '<span style="color:#28a745; font-weight:600;">&#10003;</span> ' + currentStatusEl.dataset.text;
                            }
                            currentStatusEl = addStatusLine(event.message);
                            currentStatusEl.dataset.text = event.message;
                        } else if (event.type === 'done') {
                            if (currentStatusEl) {
                                currentStatusEl.innerHTML = '<span style="color:#28a745; font-weight:600;">&#10003;</span> ' + currentStatusEl.dataset.text;
                            }
                            highlightMessage.className = 'message success';
                            highlightMessage.textContent = '\u2713 Highlight reel generated!';
                            highlightMessage.style.display = 'block';
                            highlightSummary.value = event.summary;
                            highlightGifContainer.innerHTML = `<img src="${event.gif_url}" class="gif-preview" alt="Highlight GIF"><br><a href="${event.gif_url}" class="download-link" download>📥 Download GIF</a>`;
                            highlightResultContainer.style.display = 'block';
                        } else if (event.type === 'error') {
                            throw new Error(event.detail);
                        }
                    }
                }
            } catch (error) {
                if (currentStatusEl) {
                    currentStatusEl.innerHTML = '<span style="color:#dc3545;">&#10007;</span> ' + currentStatusEl.dataset.text;
                }
                highlightMessage.className = 'message error';
                highlightMessage.textContent = '\u2717 Error: ' + error.message;
                highlightMessage.style.display = 'block';
            } finally {
                highlightSpinner.style.display = 'none';
                highlightSubmitBtn.disabled = false;
                highlightSubmitBtn.textContent = 'Generate Highlight Reel';
            }
        });

        // ── Feature 6: Chapter Detector ────────────────────────────────────

        const chapterForm = document.getElementById('chapterForm');
        const chapterMessage = document.getElementById('chapterMessage');
        const chapterSpinner = document.getElementById('chapterSpinner');
        const chapterSubmitBtn = document.getElementById('chapterSubmitBtn');
        const chapterResultContainer = document.getElementById('chapterResultContainer');
        const chapterOutput = document.getElementById('chapterOutput');
        const copyChaptersBtn = document.getElementById('copyChaptersBtn');
        const chapterUrlGroup = document.getElementById('chapterUrlGroup');
        const chapterFileGroup = document.getElementById('chapterFileGroup');
        const chapterModeUrlLabel = document.getElementById('chapterModeUrlLabel');
        const chapterModeFileLabel = document.getElementById('chapterModeFileLabel');
        const chapterStatusList = document.getElementById('chapterStatusList');

        function addChapterStatusLine(text) {
            const el = document.createElement('div');
            el.style.cssText = 'display:flex; align-items:center; gap:6px; padding:3px 0;';
            el.innerHTML = '<span class="spinner" style="display:inline-block; width:12px; height:12px; border-width:2px; margin:0;"></span> ' + text;
            el.dataset.text = text;
            chapterStatusList.appendChild(el);
            chapterStatusList.style.display = 'block';
            return el;
        }

        function markChapterStatusDone(el) {
            el.innerHTML = '<span style="color:#28a745; font-weight:600;">&#10003;</span> ' + el.dataset.text;
        }

        document.querySelectorAll('input[name="chapterMode"]').forEach(radio => {
            radio.addEventListener('change', () => {
                const isUrl = radio.value === 'url';
                chapterUrlGroup.style.display = isUrl ? 'block' : 'none';
                chapterFileGroup.style.display = isUrl ? 'none' : 'block';
                chapterModeUrlLabel.style.cssText = isUrl
                    ? 'flex:1;text-align:center;padding:9px;font-size:13px;font-weight:600;cursor:pointer;background:#1966FF;color:#fff;transition:background 0.2s;'
                    : 'flex:1;text-align:center;padding:9px;font-size:13px;font-weight:600;cursor:pointer;background:#f8f9fa;color:#555;transition:background 0.2s;';
                chapterModeFileLabel.style.cssText = isUrl
                    ? 'flex:1;text-align:center;padding:9px;font-size:13px;font-weight:600;cursor:pointer;background:#f8f9fa;color:#555;transition:background 0.2s;'
                    : 'flex:1;text-align:center;padding:9px;font-size:13px;font-weight:600;cursor:pointer;background:#1966FF;color:#fff;transition:background 0.2s;';
            });
        });

        copyChaptersBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(chapterOutput.value).then(() => {
                copyChaptersBtn.textContent = 'Copied!';
                setTimeout(() => { copyChaptersBtn.textContent = 'Copy'; }, 1500);
            });
        });

        chapterForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const mode = document.querySelector('input[name="chapterMode"]:checked').value;

            chapterMessage.style.display = 'none';
            chapterMessage.innerHTML = '';
            chapterResultContainer.style.display = 'none';
            chapterOutput.value = '';
            chapterStatusList.innerHTML = '';
            chapterStatusList.style.display = 'none';

            chapterSpinner.style.display = 'block';
            chapterSubmitBtn.disabled = true;
            chapterSubmitBtn.textContent = 'Detecting\u2026';

            let currentChapterStatusEl = null;

            try {
                let response;
                if (mode === 'url') {
                    const url = document.getElementById('chapterUrl').value;
                    if (!url) throw new Error('Please enter a YouTube URL');
                    response = await fetch('/api/detect-chapters', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url })
                    });
                } else {
                    const fileInput = document.getElementById('chapterFile');
                    if (!fileInput.files[0]) throw new Error('Please select an SRT file');
                    const formData = new FormData();
                    formData.append('file', fileInput.files[0]);
                    response = await fetch('/api/detect-chapters-file', {
                        method: 'POST',
                        body: formData
                    });
                }

                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.detail || 'Request failed');
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\\n');
                    buffer = lines.pop();
                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const event = JSON.parse(line.slice(6));
                        if (event.type === 'status') {
                            if (currentChapterStatusEl) markChapterStatusDone(currentChapterStatusEl);
                            currentChapterStatusEl = addChapterStatusLine(event.message);
                        } else if (event.type === 'done') {
                            if (currentChapterStatusEl) markChapterStatusDone(currentChapterStatusEl);
                            chapterOutput.value = event.chapters;
                            chapterResultContainer.style.display = 'block';
                            chapterMessage.className = 'message success';
                            chapterMessage.textContent = '\u2713 Chapters detected!';
                            chapterMessage.style.display = 'block';
                        } else if (event.type === 'error') {
                            throw new Error(event.detail);
                        }
                    }
                }
            } catch (error) {
                if (currentChapterStatusEl) {
                    currentChapterStatusEl.innerHTML = '<span style="color:#dc3545;">&#10007;</span> ' + currentChapterStatusEl.dataset.text;
                }
                chapterMessage.className = 'message error';
                chapterMessage.textContent = '\u2717 Error: ' + error.message;
                chapterMessage.style.display = 'block';
            } finally {
                chapterSpinner.style.display = 'none';
                chapterSubmitBtn.disabled = false;
                chapterSubmitBtn.textContent = 'Detect Chapters';
            }
        });

        // Feature 8: Metadata Optimizer
        const metadataForm = document.getElementById('metadataForm');
        const metadataMessage = document.getElementById('metadataMessage');
        const metadataSpinner = document.getElementById('metadataSpinner');
        const metadataSubmitBtn = document.getElementById('metadataSubmitBtn');
        const metadataResultContainer = document.getElementById('metadataResultContainer');
        const metadataOutput = document.getElementById('metadataOutput');
        const copyMetadataBtn = document.getElementById('copyMetadataBtn');

        if (copyMetadataBtn) {
            copyMetadataBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(metadataOutput.value).then(() => {
                    copyMetadataBtn.textContent = 'Copied!';
                    setTimeout(() => { copyMetadataBtn.textContent = 'Copy'; }, 1500);
                });
            });
        }

        if (metadataForm) {
            metadataForm.addEventListener('submit', async (e) => {
                e.preventDefault();

                const url = document.getElementById('metadataUrl').value.trim();
                const keywords = document.getElementById('metadataKeywords').value.trim();
                if (!url || !keywords) {
                    metadataMessage.className = 'message error';
                    metadataMessage.textContent = '\u2717 Please enter both a YouTube URL and target keywords';
                    metadataMessage.style.display = 'block';
                    return;
                }

                metadataMessage.style.display = 'none';
                metadataMessage.innerHTML = '';
                metadataResultContainer.style.display = 'none';
                metadataOutput.value = '';

                metadataSpinner.style.display = 'block';
                metadataSubmitBtn.disabled = true;
                metadataSubmitBtn.textContent = 'Investigating\u2026';

                try {
                    const response = await fetch('/api/metadata-recommendations', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url, keywords })
                    });

                    const data = await response.json();
                    if (!response.ok) {
                        throw new Error(data.detail || 'Metadata investigation failed');
                    }

                    metadataOutput.value = data.markdown || '';
                    metadataResultContainer.style.display = 'block';

                    metadataMessage.className = 'message success';
                    if (data.subtitle_warning) {
                        metadataMessage.textContent = '\u2713 Recommendations generated. Note: ' + data.subtitle_warning;
                    } else {
                        metadataMessage.textContent = '\u2713 Recommendations generated';
                    }
                    metadataMessage.style.display = 'block';
                } catch (error) {
                    metadataMessage.className = 'message error';
                    metadataMessage.textContent = '\u2717 Error: ' + error.message;
                    metadataMessage.style.display = 'block';
                } finally {
                    metadataSpinner.style.display = 'none';
                    metadataSubmitBtn.disabled = false;
                    metadataSubmitBtn.textContent = 'Investigate Metadata';
                }
            });
        }

    </script>
</body>
</html>
"""

COLOR_PICKER_CORE_COLORS = {
    "Pink": "#BB0FD2",
    "Purple": "#5E29E5",
    "Blue": "#1966FF",
    "Turquoise": "#5DF2E0",
    "Black": "#000000",
}

DT_LOGO_PATH = """<path d=\"M9.372 0c-.31.006-.93.09-1.521.654-.872.824-5.225 4.957-6.973 6.617-.79.754-.72 1.595-.72 1.664v.377c.067-.292.187-.5.427-.825.496-.616 1.3-.788 1.627-.822a64.238 64.238 0 01.002 0 64.238 64.238 0 016.528-.55c4.335-.136 7.197.226 7.197.226l6.085-5.794s-3.188-.6-6.82-1.027a93.4 93.4 0 00-5.64-.514c-.02 0-.09-.008-.192-.006zm13.56 2.508l-6.066 5.79s.222 2.881-.137 7.2c-.189 2.45-.584 4.866-.875 6.494-.052.326-.256 1.114-.925 1.594-.29.198-.49.295-.748.363 1.546-.51 1.091-7.047 1.091-7.047-4.335.137-7.214-.223-7.214-.223l-6.085 5.793s3.223.634 6.856 1.045c2.056.24 4.833.429 5.227.463.023 0 .045-.007.068-.012-.013.003-.022.009-.035.012.138 0 .26.015.38.015.084 0 .924.105 1.712-.648 1.748-1.663 6.084-5.81 6.94-6.634.789-.754.72-1.594.72-1.68a81.846 81.846 0 00-.206-5.654 101.75 101.75 0 00-.701-6.872zM3.855 8.306c-1.73.002-3.508.208-3.696 1.021.017 1.216.05 3.137.205 5.28.24 3.65.703 6.887.703 6.887l6.083-5.79c-.017.016-.24-2.88.12-7.2 0 0-1.684-.201-3.416-.2z\"/>"""

STANDARD_TOOL_PAGE_BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'DT Flow', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
    min-height: 100vh;
}

header {
    background: rgba(0,0,0,0.25);
    backdrop-filter: blur(8px);
    padding: 16px 32px;
    display: flex;
    align-items: center;
    gap: 16px;
    position: sticky;
    top: 0;
    z-index: 100;
}

header svg { width: 32px; height: 32px; fill: #fff; flex-shrink: 0; }

header .titles { flex: 1; display: flex; flex-direction: column; line-height: 1.1; }

header .brand {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.65);
}

header h1 { font-size: 18px; font-weight: 700; color: #fff; }

header a.back {
    color: rgba(255,255,255,0.75);
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
    padding: 5px 14px;
    border: 1px solid rgba(255,255,255,0.35);
    border-radius: 20px;
    transition: background 0.2s;
}

header a.back:hover { background: rgba(255,255,255,0.15); }

.external-link::after {
    content: " ↗";
    font-size: 12px;
    font-weight: 700;
    opacity: 0.9;
}

.content {
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px 32px 48px;
    display: grid;
    gap: 16px;
}

.panel {
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 3px 12px rgba(0,0,0,0.15);
    padding: 16px;
}

.subtitle {
    color: #666;
    margin-bottom: 16px;
    font-size: 14px;
}

label {
    display: block;
    color: #333;
    font-weight: 600;
    margin-bottom: 8px;
    font-size: 14px;
}

input[type="text"] {
    width: 100%;
    padding: 12px;
    border: 2px solid #e0e0e0;
    border-radius: 6px;
    font-size: 14px;
}

input[type="text"]:focus {
    outline: none;
    border-color: #1966FF;
}

button {
    width: 100%;
    padding: 12px;
    background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
    color: #fff;
    border: none;
    border-radius: 6px;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
}

.message {
    margin-top: 12px;
    padding: 12px;
    border-radius: 6px;
    display: none;
    font-size: 14px;
}

.message.success {
    background-color: #d4edda;
    color: #155724;
    border: 1px solid #c3e6cb;
}

.message.error {
    background-color: #f8d7da;
    color: #721c24;
    border: 1px solid #f5c6cb;
}

@media (max-width: 700px) {
    header { padding: 14px 12px; }
    .content { padding: 16px 12px 36px; }
}
"""


def build_standard_tool_page(
    page_title: str,
    page_heading: str,
    body_html: str,
    script_html: str,
    extra_css: str = "",
    head_html: str = "",
) -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "    <meta charset=\"UTF-8\">\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"    <title>{page_title}</title>\n"
        f"{head_html}\n"
        "    <style>\n"
        f"{STANDARD_TOOL_PAGE_BASE_CSS}\n"
        f"{extra_css}\n"
        "    </style>\n"
        "</head>\n"
        "<body>\n"
        "    <header>\n"
        "        <svg role=\"img\" viewBox=\"0 0 24 24\" xmlns=\"http://www.w3.org/2000/svg\" aria-label=\"Dynatrace\">\n"
        f"            {DT_LOGO_PATH}\n"
        "        </svg>\n"
        "        <div class=\"titles\">\n"
        "            <span class=\"brand\">Dynatrace</span>\n"
        f"            <h1>{page_heading}</h1>\n"
        "        </div>\n"
        "        <a href=\"/\" class=\"back\">&larr; Back to Toolbox</a>\n"
        "    </header>\n"
        "\n"
        f"{body_html}\n"
        "\n"
        f"{script_html}\n"
        "</body>\n"
        "</html>\n"
    )


COLOR_PICKER_EXTRA_CSS = f"""
.intro p {{ color: #2b3350; font-size: 14px; line-height: 1.5; }}
.intro a {{ color: #2f5ac6; font-weight: 600; }}

.grid {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
}}

.color-btn {{
    border: 1px solid #dfe4f4;
    border-radius: 10px;
    background: #fff;
    padding: 10px;
    cursor: pointer;
    text-align: left;
    display: grid;
    gap: 8px;
    transition: transform 0.15s, box-shadow 0.15s;
}}

.color-btn:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.12); }}
.color-btn:focus-visible {{ outline: 3px solid rgba(102,126,234,0.35); outline-offset: 1px; }}

.swatch {{
    width: 100%;
    aspect-ratio: 16/9;
    border-radius: 7px;
    border: 1px solid rgba(0,0,0,0.15);
}}

.color-title {{ font-size: 13px; font-weight: 700; color: #1f2a46; }}
.color-hex {{ font-family: monospace; font-size: 12px; color: #5a6277; font-weight: 700; }}

.selection {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.selection-swatch {{ width: 56px; height: 56px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.2); background: {COLOR_PICKER_CORE_COLORS["Blue"]}; }}
.selection h2 {{ font-size: 18px; color: #1a1a1a; }}
.selection p {{ font-size: 13px; color: #4c556a; }}
.selection .hex {{ font-family: monospace; font-weight: 700; color: #1d315f; }}

.status {{ min-height: 18px; margin-top: 8px; font-size: 13px; font-weight: 700; }}
.status.success {{ color: #1f8b4c; }}
.status.error {{ color: #a23333; }}

.resources h3 {{ font-size: 15px; color: #273a72; margin-bottom: 8px; }}
.resources ul {{ margin-left: 18px; display: grid; gap: 5px; }}
.resources a {{ color: #2f5ac6; }}

@media (max-width: 1200px) {{ .grid {{ grid-template-columns: repeat(4, 1fr); }} }}
@media (max-width: 900px)  {{ .grid {{ grid-template-columns: repeat(3, 1fr); }} }}
@media (max-width: 600px)  {{ .grid {{ grid-template-columns: repeat(2, 1fr); }} }}
@media (max-width: 380px)  {{ .grid {{ grid-template-columns: 1fr; }} }}
"""

COLOR_PICKER_BODY_HTML = f"""
<main class="content">
    <section class="panel intro">
        <p>
            Use this page to quickly copy official core HEX values and jump to brand resources.
            Official brand font: DT Flow.
            <a href="https://brandfolder.com/s/txmgg9cs6nr55c9rptrrww7x" target="_blank" rel="noopener">Get DT Flow from Brandfolder</a>.
        </p>
    </section>

    <section class="panel">
        <div class="grid" id="coreColorGrid"></div>
    </section>

    <section class="panel" aria-live="polite" aria-atomic="true">
        <div class="selection">
            <div class="selection-swatch" id="selectionSwatch"></div>
            <div class="selection-meta">
                <h2 id="selectionName">Blue</h2>
                <p>Current HEX: <span class="hex" id="selectionHex">{COLOR_PICKER_CORE_COLORS["Blue"]}</span></p>
            </div>
        </div>
        <div class="status" id="statusMessage"></div>
    </section>

    <section class="panel resources" aria-label="Brand resources">
        <h3>Useful Brand Links</h3>
        <ul>
            <li><a href="https://live.standards.site/dynatrace/" target="_blank" rel="noopener" class="external-link">Dynatrace Brand Guidelines</a></li>
            <li><a href="https://cdn.dm.dynatrace.com/assets/documents/media-kit/dynatrace-logo-presskit.zip" target="_blank" rel="noopener" class="external-link">Dynatrace Logo Press Kit (ZIP)</a></li>
            <li><a href="https://live.standards.site/dynatrace/color" target="_blank" rel="noopener" class="external-link">Dynatrace Color Guidelines</a></li>
        </ul>
    </section>
</main>
"""

COLOR_PICKER_SCRIPT = f"""
<script>
    const coreColors = [
        {{ name: 'Pink', hex: '{COLOR_PICKER_CORE_COLORS["Pink"]}' }},
        {{ name: 'Purple', hex: '{COLOR_PICKER_CORE_COLORS["Purple"]}' }},
        {{ name: 'Blue', hex: '{COLOR_PICKER_CORE_COLORS["Blue"]}' }},
        {{ name: 'Turquoise', hex: '{COLOR_PICKER_CORE_COLORS["Turquoise"]}' }},
        {{ name: 'Black', hex: '{COLOR_PICKER_CORE_COLORS["Black"]}' }},
    ];

    const grid = document.getElementById('coreColorGrid');
    const statusMessage = document.getElementById('statusMessage');
    const selectionSwatch = document.getElementById('selectionSwatch');
    const selectionName = document.getElementById('selectionName');
    const selectionHex = document.getElementById('selectionHex');

    function setSelection(color) {{
        selectionName.textContent = color.name;
        selectionHex.textContent = color.hex;
        selectionSwatch.style.background = color.hex;
    }}

    async function copyHex(hex) {{
        if (navigator.clipboard && navigator.clipboard.writeText) {{
            await navigator.clipboard.writeText(hex);
            return;
        }}

        const helper = document.createElement('textarea');
        helper.value = hex;
        helper.setAttribute('readonly', '');
        helper.style.position = 'fixed';
        helper.style.opacity = '0';
        helper.style.pointerEvents = 'none';
        document.body.appendChild(helper);
        helper.select();
        const copied = document.execCommand('copy');
        document.body.removeChild(helper);

        if (!copied) {{
            throw new Error('Clipboard unavailable in this browser context');
        }}
    }}

    function updateStatus(kind, text) {{
        statusMessage.className = 'status ' + kind;
        statusMessage.textContent = text;
    }}

    function createColorButton(color) {{
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'color-btn';
        btn.setAttribute('aria-label', color.name + ' ' + color.hex + ' copy hex');
        btn.innerHTML =
            '<span class="swatch" style="background:' + color.hex + '"></span>' +
            '<span class="color-title">' + color.name + '</span>' +
            '<span class="color-hex">' + color.hex + '</span>';

        btn.addEventListener('click', async () => {{
            try {{
                await copyHex(color.hex);
                setSelection(color);
                updateStatus('success', 'Copied ' + color.hex + ' to clipboard');
            }} catch (error) {{
                updateStatus('error', 'Copy failed: ' + error.message);
            }}
        }});

        return btn;
    }}

    coreColors.forEach(color => grid.appendChild(createColorButton(color)));
    setSelection(coreColors[2]);
</script>
"""

COLOR_PICKER_HTML = build_standard_tool_page(
    "Dynatrace DevRel Toolbox - Brand Color Picker",
    "Brand Color Picker",
    COLOR_PICKER_BODY_HTML,
    COLOR_PICKER_SCRIPT,
    COLOR_PICKER_EXTRA_CSS,
)

WORDLIST_MANAGER_EXTRA_CSS = """
.content {
    grid-template-columns: minmax(320px, 1fr) minmax(360px, 1fr);
}

.panel h2 {
    color: #1d2c57;
    font-size: 18px;
    margin-bottom: 8px;
}

.form-group { margin-bottom: 14px; }

.table-wrap {
    border: 1px solid #dee2e6;
    border-radius: 8px;
    overflow: hidden;
    display: none;
    max-height: 600px;
    overflow-y: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
}

th, td {
    border-bottom: 1px solid #e9ecef;
    padding: 8px 10px;
    text-align: left;
    font-size: 13px;
    word-break: break-word;
}

th {
    position: sticky;
    top: 0;
    background: #f3f5f8;
    font-weight: 700;
}

@media (max-width: 980px) {
    .content {
        grid-template-columns: 1fr;
    }
}
"""

WORDLIST_MANAGER_BODY_HTML = """
<main class="content">
    <section class="panel">
        <h2>Manage Corrections</h2>
        <p class="subtitle">Add or update correction entries in the wordlist</p>

        <form id="wordlistForm">
            <div class="form-group">
                <label for="wrongWord">Incorrect word / phrase</label>
                <input type="text" id="wrongWord" placeholder="e.g. dinatrace" required>
            </div>
            <div class="form-group">
                <label for="rightWord">Correct replacement</label>
                <input type="text" id="rightWord" placeholder="e.g. Dynatrace" required>
            </div>
            <button type="submit" id="wordlistSubmitBtn">Add to Wordlist</button>
        </form>

        <div class="message" id="wordlistMessage"></div>
    </section>

    <section class="panel">
        <h2>Current Wordlist</h2>
        <p class="subtitle">Existing typo-to-correction mappings</p>
        <div id="wordlistTableContainer" class="table-wrap">
            <table id="wordlistTable">
                <thead><tr><th>Incorrect</th><th>Corrected</th></tr></thead>
                <tbody></tbody>
            </table>
        </div>
    </section>
</main>
"""

WORDLIST_MANAGER_SCRIPT = """
<script>
    const wordlistForm = document.getElementById('wordlistForm');
    const wordlistMessage = document.getElementById('wordlistMessage');
    const wordlistSubmitBtn = document.getElementById('wordlistSubmitBtn');
    const wordlistTableContainer = document.getElementById('wordlistTableContainer');
    const wordlistTableBody = document.querySelector('#wordlistTable tbody');

    async function loadWordlist() {
        try {
            const resp = await fetch('/api/wordlist');
            const data = await resp.json();
            wordlistTableBody.innerHTML = '';
            const entries = Object.entries(data.wordlist);
            if (entries.length === 0) {
                wordlistTableContainer.style.display = 'none';
                return;
            }
            for (const [wrong, right] of entries) {
                const tr = document.createElement('tr');
                tr.innerHTML = '<td>' + wrong + '</td><td>' + right + '</td>';
                wordlistTableBody.appendChild(tr);
            }
            wordlistTableContainer.style.display = 'block';
        } catch (error) {
            wordlistMessage.className = 'message error';
            wordlistMessage.textContent = '✗ Error: ' + error.message;
            wordlistMessage.style.display = 'block';
        }
    }

    wordlistForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const wrong = document.getElementById('wrongWord').value.trim();
        const right = document.getElementById('rightWord').value.trim();
        if (!wrong || !right) return;

        wordlistMessage.style.display = 'none';
        wordlistSubmitBtn.disabled = true;

        try {
            const response = await fetch('/api/wordlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wrong, right })
            });
            const data = await response.json();
            if (response.ok) {
                wordlistMessage.className = 'message success';
                wordlistMessage.textContent = '✓ Added: "' + wrong + '" → "' + right + '"' + (data.updated ? ' (updated existing entry)' : '');
                wordlistForm.reset();
                loadWordlist();
            } else {
                wordlistMessage.className = 'message error';
                wordlistMessage.textContent = '✗ Error: ' + (data.detail || 'Failed to update wordlist');
            }
        } catch (error) {
            wordlistMessage.className = 'message error';
            wordlistMessage.textContent = '✗ Error: ' + error.message;
        } finally {
            wordlistMessage.style.display = 'block';
            wordlistSubmitBtn.disabled = false;
        }
    });

    loadWordlist();
</script>
"""

WORDLIST_MANAGER_HTML = build_standard_tool_page(
    "Dynatrace DevRel Toolbox - Wordlist Manager",
    "Wordlist Manager",
    WORDLIST_MANAGER_BODY_HTML,
    WORDLIST_MANAGER_SCRIPT,
    WORDLIST_MANAGER_EXTRA_CSS,
)

CODE_CARDS_HEAD_HTML = """
    <link id="hljsTheme" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
"""

CODE_CARDS_EXTRA_CSS = """
.content {
    grid-template-columns: minmax(320px, 400px) minmax(420px, 1fr);
    align-items: start;
}

.panel h2 {
    color: #1d2c57;
    font-size: 18px;
    margin-bottom: 10px;
}

.controls-grid {
    display: grid;
    gap: 12px;
}

.controls-grid .row {
    display: grid;
    gap: 8px;
}

.controls-grid select,
.controls-grid input[type="text"],
.controls-grid textarea,
.controls-grid input[type="range"] {
    width: 100%;
    border: 2px solid #e0e0e0;
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
}

.controls-grid select,
.controls-grid input[type="text"],
.controls-grid textarea {
    padding: 10px 12px;
}

.controls-grid textarea {
    min-height: 220px;
    resize: vertical;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    line-height: 1.45;
}

.controls-grid select:focus,
.controls-grid input[type="text"]:focus,
.controls-grid textarea:focus {
    outline: none;
    border-color: #1966FF;
}

.range-meta {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: #5f6577;
}

.colour-picker {
    display: flex;
    gap: 12px;
    margin: 12px 0;
    flex-wrap: wrap;
}

.colour-swatch {
    width: 48px;
    height: 48px;
    border-radius: 6px;
    cursor: pointer;
    border: 3px solid transparent;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    position: relative;
}

.colour-swatch::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(0, 0, 0, 0.9);
    color: white;
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.2s ease;
    margin-bottom: 8px;
    z-index: 10;
    font-weight: 500;
}

.colour-swatch:hover::after {
    opacity: 1;
}

.colour-swatch:hover {
    transform: scale(1.1);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
}

.colour-swatch.active {
    border-color: #ffffff;
    box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.5), 0 2px 8px rgba(0, 0, 0, 0.2);
}

.btn-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
}

.secondary-btn {
    background: #eef2ff;
    color: #25386f;
    border: 1px solid #d3dbfb;
}

.preview-wrap {
    display: grid;
    gap: 12px;
}

.preview-note {
    font-size: 13px;
    color: #5a6277;
}

.capture-area {
    padding: 24px;
    border-radius: 14px;
    background: #FF8C00;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.08);
}

.code-shell {
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 14px 30px rgba(0,0,0,0.25);
    border: 2px solid #000000;
}

.shell-top {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: #000000;
}

.traffic {
    display: flex;
    gap: 6px;
}

.dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
}

.dot.red { background: #ff605c; }
.dot.yellow { background: #ffbd44; }
.dot.green { background: #00ca4e; }

.filename {
    margin-left: auto;
    font-size: 12px;
    color: #ffffff;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.code-surface {
    background: var(--code-bg, #0b1220);
    padding: var(--code-pad, 22px);
    min-height: 260px;
    color: #d4d4d4;
}

.code-surface pre {
    margin: 0;
    overflow-x: auto;
    line-height: 1.5;
}

.code-surface code {
    font-size: var(--code-size, 16px);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    color: inherit;
}

.code-surface code,
.code-surface code * {
    line-height: 1.5 !important;
}

.status {
    min-height: 18px;
    font-size: 13px;
    font-weight: 700;
}

.status.success { color: #1f8b4c; }
.status.error { color: #a23333; }

@media (max-width: 1080px) {
    .content {
        grid-template-columns: 1fr;
    }
}
"""

CODE_CARDS_BODY_HTML = """
<main class="content">
    <section class="panel">
        <h2>Code Card Controls</h2>
        <p class="subtitle">Build a shareable image from your code snippet.</p>

        <div class="controls-grid">
            <div class="row">
                <label for="languageSelect">Language</label>
                <select id="languageSelect">
                    <option value="python">Python</option>
                    <option value="javascript">JavaScript</option>
                    <option value="typescript">TypeScript</option>
                    <option value="bash">Bash</option>
                    <option value="json">JSON</option>
                    <option value="yaml">YAML</option>
                    <option value="html">HTML</option>
                    <option value="css">CSS</option>
                    <option value="sql">SQL</option>
                    <option value="go">Go</option>
                    <option value="java">Java</option>
                    <option value="plaintext">Plain text</option>
                </select>
            </div>

            <div class="row">
                <label for="themeSelect">Theme</label>
                <select id="themeSelect">
                    <option value="dt-dark">DT Dark</option>
                    <option value="dt-light">DT Light</option>
                    <option value="midnight">Midnight</option>
                </select>
            </div>

            <div class="row">
                <label>Background Colour</label>
                <div class="colour-picker">
                    <div class="colour-swatch" data-colour="#6002EE" data-tooltip="Purple - #6002EE" style="background-color: #6002EE;"></div>
                    <div class="colour-swatch" data-colour="#BB0FD2" data-tooltip="Pink - #BB0FD2" style="background-color: #BB0FD2;"></div>
                    <div class="colour-swatch" data-colour="#0B7EF0" data-tooltip="Blue - #0B7EF0" style="background-color: #0B7EF0;"></div>
                    <div class="colour-swatch" data-colour="#00A3E0" data-tooltip="Turquoise - #00A3E0" style="background-color: #00A3E0;"></div>
                    <div class="colour-swatch" data-colour="#000000" data-tooltip="Black - #000000" style="background-color: #000000;"></div>
                </div>
            </div>

            <div class="row">
                <label for="fileNameInput">Filename Label</label>
                <input id="fileNameInput" type="text" value="snippet.py" spellcheck="false">
            </div>

            <div class="row">
                <label for="fontSizeInput">Font Size</label>
                <input id="fontSizeInput" type="range" min="12" max="28" value="16">
                <div class="range-meta"><span>12px</span><span id="fontSizeValue">16px</span><span>28px</span></div>
            </div>

            <div class="row">
                <label for="paddingInput">Card Padding</label>
                <input id="paddingInput" type="range" min="12" max="48" value="24">
                <div class="range-meta"><span>12px</span><span id="paddingValue">24px</span><span>48px</span></div>
            </div>

            <div class="row">
                <label for="codeInput">Code</label>
                <textarea id="codeInput" spellcheck="false">def greet(name: str) -> str:
    return f"Hello, {name}!"


print(greet("Dynatrace"))</textarea>
            </div>

            <div class="btn-row">
                <button type="button" id="downloadBtn">Download PNG</button>
            </div>
            <div class="status" id="exportStatus"></div>
        </div>
    </section>

    <section class="panel preview-wrap">
        <h2>Preview</h2>
        <p class="preview-note">This renders live and exports as a PNG, similar to Carbon-style code cards.</p>
        <div id="captureArea" class="capture-area">
            <div class="code-shell">
                <div class="shell-top">
                    <div class="traffic">
                        <span class="dot red"></span>
                        <span class="dot yellow"></span>
                        <span class="dot green"></span>
                    </div>
                    <span class="filename" id="filenameLabel">snippet.py</span>
                </div>
                <div class="code-surface" id="codeSurface">
                    <pre><code id="codeBlock" class="language-python"></code></pre>
                </div>
            </div>
        </div>
    </section>
</main>
"""

CODE_CARDS_SCRIPT = """
<script>
    const themeMap = {
        'dt-dark': {
            sheet: 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css',
            canvasBg: '#0b1020',
            shellTop: 'rgba(255,255,255,0.05)',
            codeBg: '#111827',
            filename: 'rgba(255,255,255,0.8)'
        },
        'dt-light': {
            sheet: 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css',
            canvasBg: '#dbe7ff',
            shellTop: 'rgba(15,23,42,0.08)',
            codeBg: '#ffffff',
            filename: '#1f2f57'
        },
        'midnight': {
            sheet: 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css',
            canvasBg: '#0a0f1f',
            shellTop: 'rgba(255,255,255,0.06)',
            codeBg: '#1e2430',
            filename: 'rgba(255,255,255,0.82)'
        }
    };



    const codeInput = document.getElementById('codeInput');
    const languageSelect = document.getElementById('languageSelect');
    const themeSelect = document.getElementById('themeSelect');
    const fileNameInput = document.getElementById('fileNameInput');
    const fontSizeInput = document.getElementById('fontSizeInput');
    const paddingInput = document.getElementById('paddingInput');
    const codeBlock = document.getElementById('codeBlock');
    const fileNameLabel = document.getElementById('filenameLabel');
    const captureArea = document.getElementById('captureArea');
    const codeSurface = document.getElementById('codeSurface');
    const exportStatus = document.getElementById('exportStatus');
    const hljsTheme = document.getElementById('hljsTheme');
    const fontSizeValue = document.getElementById('fontSizeValue');
    const paddingValue = document.getElementById('paddingValue');
    const downloadBtn = document.getElementById('downloadBtn');
    const colourSwatches = document.querySelectorAll('.colour-swatch');
    
    let selectedBgColour = '#6002EE'; // Default to purple

    function setStatus(kind, text) {
        exportStatus.className = 'status ' + kind;
        exportStatus.textContent = text;
    }

    function updateBackgroundColour(hex) {
        selectedBgColour = hex;
        captureArea.style.background = hex;
        
        // Update active state
        colourSwatches.forEach(swatch => {
            if (swatch.dataset.colour === hex) {
                swatch.classList.add('active');
            } else {
                swatch.classList.remove('active');
            }
        });
    }

    function applyTheme() {
        const active = themeMap[themeSelect.value] || themeMap['dt-dark'];
        hljsTheme.href = active.sheet;
        captureArea.style.setProperty('--canvas-bg', active.canvasBg);
        captureArea.style.setProperty('--shell-top', active.shellTop);
        captureArea.style.setProperty('--code-bg', active.codeBg);
        captureArea.style.setProperty('--filename-color', active.filename);
    }

    function renderCode() {
        if (!codeBlock) {
            console.error('codeBlock element not found');
            return;
        }
        try {
            codeBlock.className = 'language-' + languageSelect.value;
            codeBlock.textContent = codeInput.value || ' ';
            delete codeBlock.dataset.highlighted;
            if (typeof hljs !== 'undefined' && hljs.highlightElement) {
                hljs.highlightElement(codeBlock);
            }
            fileNameLabel.textContent = fileNameInput.value.trim() || 'snippet';
            codeSurface.style.setProperty('--code-size', fontSizeInput.value + 'px');
            codeSurface.style.setProperty('--code-pad', paddingInput.value + 'px');
            fontSizeValue.textContent = fontSizeInput.value + 'px';
            paddingValue.textContent = paddingInput.value + 'px';
        } catch (err) {
            console.error('renderCode error:', err);
        }
    }

    async function canvasBlob(canvas) {
        return new Promise((resolve, reject) => {
            canvas.toBlob((blob) => {
                if (!blob) {
                    reject(new Error('Could not generate image blob'));
                    return;
                }
                resolve(blob);
            }, 'image/png');
        });
    }

    async function exportPng() {
        try {
            if (typeof html2canvas === 'undefined') {
                throw new Error('html2canvas library not loaded');
            }
            setStatus('', '');
            renderCode();
            await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            if (document.fonts && document.fonts.ready) {
                await document.fonts.ready;
            }

            const name = (fileNameInput.value.trim() || 'code-card').replace(/[^a-zA-Z0-9._-]/g, '_');
            const filename_ext = name.endsWith('.png') ? name : (name + '.png');

            const exportNode = captureArea.cloneNode(true);
            exportNode.style.position = 'fixed';
            exportNode.style.left = '-9999px';
            exportNode.style.top = '-9999px';
            exportNode.style.width = '1200px';
            exportNode.style.maxWidth = '1200px';
            exportNode.style.margin = '0';
            exportNode.style.zIndex = '-1';
            document.body.appendChild(exportNode);

            setStatus('', 'Rendering...');

            let canvas;
            try {
                canvas = await html2canvas(exportNode, {
                    backgroundColor: null,
                    scale: 2,
                    useCORS: true,
                    logging: false,
                    allowTaint: true
                });
            } finally {
                document.body.removeChild(exportNode);
            }
            
            if (!canvas) {
                throw new Error('Failed to render canvas');
            }

            const blob = await canvasBlob(canvas);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename_ext;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            setStatus('success', 'Downloaded PNG');
        } catch (error) {
            console.error('Export error:', error);
            setStatus('error', 'Export failed: ' + error.message);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        if (downloadBtn) {
            downloadBtn.addEventListener('click', exportPng);
        }

        // Wire up colour picker swatches
        colourSwatches.forEach(swatch => {
            swatch.addEventListener('click', () => {
                const colour = swatch.dataset.colour;
                updateBackgroundColour(colour);
            });
        });

        [codeInput, languageSelect, themeSelect, fileNameInput, fontSizeInput, paddingInput].forEach((el) => {
            if (!el) return;
            el.addEventListener('input', () => {
                if (el === themeSelect) applyTheme();
                renderCode();
            });
            el.addEventListener('change', () => {
                if (el === themeSelect) applyTheme();
                renderCode();
            });
        });

        // Initialize with default colour
        updateBackgroundColour(selectedBgColour);
        applyTheme();
        renderCode();
    });
</script>
"""

CODE_CARDS_HTML = build_standard_tool_page(
    "Dynatrace DevRel Toolbox - Code Cards",
    "Code Cards",
    CODE_CARDS_BODY_HTML,
    CODE_CARDS_SCRIPT,
    CODE_CARDS_EXTRA_CSS,
    CODE_CARDS_HEAD_HTML,
)

BROWSER_RECORDER_EXTRA_CSS = """
.install-banner { display:none; background:#ffeaa7; border:1px solid #fdcb6e; border-radius:8px; padding:16px; margin-bottom:20px; }
.install-banner.show { display:block; }
.install-banner code { background:#fff; padding:2px 6px; border-radius:3px; font-family:monospace; }
.install-log { background:#1e1e1e; color:#d4d4d4; padding:12px; border-radius:6px; font-family:monospace; font-size:12px; max-height:300px; overflow-y:auto; margin:10px 0; white-space:pre-wrap; word-break:break-word; line-height:1.4; }
.install-log .cmd-line { color:#4fc3f7; font-weight:bold; margin-top:8px; margin-bottom:4px; }

.recorder-layout { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
@media (max-width:900px) { .recorder-layout { grid-template-columns:1fr; } }

.step-section { margin:20px 0; padding:16px; background:#f8f9fa; border-radius:8px; }
.step-section h2 { margin-top:0; margin-bottom:12px; color:#333; }
.step-section textarea { width:100%; padding:10px; border:1px solid #ddd; border-radius:4px; font-family:monospace;}
.script-editor { min-height:420px; resize:vertical; background:#1e1e1e; color:#d4d4d4; }

.script-actions { display:flex; gap:8px; margin-top:12px; }
.script-actions button { padding:8px 16px; background:#1966FF; color:white; border:none; border-radius:4px; cursor:pointer; }
.copy-status { align-self:center; font-size:12px; color:#2e7d32; min-height:16px; }
.plain-note { background:#e3f2fd; border-left:4px solid #2196f3; padding:12px; margin:10px 0; border-radius:4px; font-size:14px; }
.advanced-tools { margin-top:12px; }
.advanced-tools button { padding:8px 12px; background:#eceff1; color:#263238; border:1px solid #cfd8dc; border-radius:4px; cursor:pointer; }
.advanced-panel { margin-top:10px; }

.progress-log { background:#1e1e1e; color:#d4d4d4; padding:12px; border-radius:6px; font-family:monospace; font-size:12px; min-height:300px; overflow-y:auto; }
.progress-log.auto-scroll { overflow-behavior:smooth; }

.result-section { background:#e8f5e9; border:1px solid #81c784; border-radius:8px; padding:16px; margin-top:20px; }
.mp4-download { display:inline-block; margin:12px 0; padding:10px 16px; background:#4caf50; color:white; border-radius:4px; text-decoration:none; font-weight:600; }
.result-video-wrap { margin-top:12px; }
.result-video { width:100%; max-width:900px; border:1px solid #d9e3f5; border-radius:8px; background:#000; }

.dropin-controls { display:flex; gap:8px; margin:10px 0; flex-wrap:wrap; align-items:center; }
.dropin-controls select { flex:1; min-width:200px; padding:8px; border:1px solid #ddd; border-radius:4px; }
.dropin-controls button { padding:8px 16px; background:#1966FF; color:white; border:none; border-radius:4px; cursor:pointer; }

.muted { color:#888; font-size:13px; }

.ai-disclaimer { background:#fff3e0; border-left:4px solid #ff9800; padding:12px; margin:16px 0; border-radius:4px; font-size:14px; line-height:1.5; }
.model-row { display:flex; gap:8px; align-items:center; margin:10px 0 12px 0; flex-wrap:wrap; }
.model-row label { font-weight:600; color:#333; }
.model-row select { min-width:320px; padding:8px; border:1px solid #ddd; border-radius:4px; background:#fff; }
.model-row button { padding:8px 12px; background:#1966FF; color:white; border:none; border-radius:4px; cursor:pointer; }
.model-hint { font-size:12px; color:#666; margin-top:4px; }
.model-speed-badge { display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; font-size:12px; font-weight:700; letter-spacing:0.2px; }
.model-speed-fast { background:#e8f5e9; color:#1b5e20; border:1px solid #a5d6a7; }
.model-speed-balanced { background:#e3f2fd; color:#0d47a1; border:1px solid #90caf9; }
.model-speed-quality { background:#fff3e0; color:#e65100; border:1px solid #ffcc80; }
.model-speed-unknown { background:#f3f4f6; color:#374151; border:1px solid #d1d5db; }
.hero-note { background:#f0f4ff; border:1px solid #c8d6ff; border-radius:8px; padding:12px; margin-bottom:16px; }
.hero-note p { margin:6px 0; }

.wizard-steps { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 14px 0; }
.wizard-step { padding:8px 12px; border-radius:999px; border:1px solid #d5dbea; background:#f5f7fb; color:#49566f; font-size:13px; font-weight:600; }
.wizard-step.active { background:#1966FF; border-color:#1966FF; color:#fff; }
.wizard-step.done { background:#e8f5e9; border-color:#81c784; color:#1b5e20; }
.wizard-subtitle { margin-top:0; margin-bottom:14px; color:#111827; background:#eef2ff; border:1px solid #c7d2fe; border-radius:8px; padding:8px 10px; font-size:13px; font-weight:600; }

.wait-modal-overlay { position:fixed; inset:0; background:rgba(12, 23, 56, 0.45); display:none; align-items:center; justify-content:center; z-index:1200; padding:16px; }
.wait-modal-overlay.show { display:flex; }
.wait-modal { width:min(680px, 100%); background:#ffffff; border-radius:14px; box-shadow:0 16px 40px rgba(0,0,0,0.2); overflow:hidden; }
.wait-modal-head { background:linear-gradient(120deg, #0f2a6b, #1966ff); color:#fff; padding:14px 16px; }
.wait-modal-head h3 { margin:0; font-size:18px; }
.wait-modal-head p { margin:4px 0 0 0; font-size:13px; opacity:0.95; }
.wait-modal-body { padding:16px; }
.wait-pill { display:inline-flex; align-items:center; gap:8px; background:#eef3ff; color:#1f3a8a; border-radius:999px; padding:6px 10px; font-size:12px; font-weight:600; }
.wait-pill.done { background:#e8f5e9; color:#1b5e20; }
.wait-spinner { width:14px; height:14px; border:2px solid #b9c8ff; border-top-color:#1966ff; border-radius:50%; animation:wait-spin 1s linear infinite; }
.wait-status-icon-tick { width:14px; height:14px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; background:#2e7d32; color:#fff; font-size:10px; font-weight:700; }
@keyframes wait-spin { to { transform:rotate(360deg); } }
.wait-model-note { margin-top:10px; font-size:13px; color:#334155; }
.wait-tip-box { margin-top:12px; padding:12px; border:1px solid #e5eaf5; border-radius:10px; background:#fbfcff; }
.wait-tip-title { margin:0; font-size:14px; color:#163067; }
.wait-tip-text { margin:8px 0; color:#2b3552; line-height:1.45; }
.wait-tip-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
.wait-tip-actions a { text-decoration:none; background:#1966FF; color:#fff; border-radius:6px; padding:10px 12px; font-size:13px; display:block; width:100%; text-align:center; font-weight:700; }
.wait-tip-actions button { border:1px solid #cfd8ea; background:#fff; color:#2d3b5c; border-radius:6px; padding:8px 10px; font-size:13px; cursor:pointer; }
.wait-tip-actions .wait-nav-btn { flex:1 1 0; min-width:0; display:inline-flex; align-items:center; justify-content:center; gap:6px; font-weight:700; }
.wait-tip-counter { display:inline-flex; align-items:center; padding:7px 10px; border:1px solid #dbe3f2; border-radius:6px; background:#fff; color:#334155; font-size:12px; font-weight:700; }
.wait-tip-video-wrap { margin:0 auto 0 auto; width:min(100%, 640px); border-radius:12px; overflow:hidden; border:0 solid #dbe3f2; background:#000; max-height:0; opacity:0; transition:max-height 220ms ease, opacity 220ms ease, margin-top 220ms ease, border-width 220ms ease; }
.wait-tip-video-wrap.is-visible { margin-top:10px; max-height:700px; opacity:1; border-width:1px; }
.wait-tip-video-wrap.is-short { width:min(320px, 100%); }
.wait-tip-video-wrap.is-video { width:min(100%, 640px); }
.wait-tip-video-player { width:100%; aspect-ratio:16/9; }
.wait-tip-video-player.is-short { aspect-ratio:9/16; }
.wait-tip-video-player.is-video { aspect-ratio:16/9; }
.wait-tip-lock-note { margin-top:8px; font-size:12px; color:#0f3d91; font-weight:700; }
.wait-ready-bar { margin-top:12px; padding:10px; border:1px solid #c9ddff; background:#eef4ff; border-radius:8px; }
.wait-ready-text { margin:0 0 8px 0; font-size:13px; color:#173a7a; }
.wait-ready-actions { display:flex; gap:8px; flex-wrap:wrap; }
.wait-ready-actions button { border:1px solid #cfd8ea; background:#fff; color:#2d3b5c; border-radius:6px; padding:8px 10px; font-size:13px; cursor:pointer; }
.wait-ready-actions .primary { background:#1966FF; color:#fff; border-color:#1966FF; }
"""

BROWSER_RECORDER_BODY_HTML = """
<div class="content">
    <div id="installBanner" class="install-banner" hidden>
        <h3>⚠ One-time setup needed</h3>
        <p>This tool requires Playwright, Chromium browser, and Linux browser dependencies.</p>
        <p id="installStatusHint" class="muted">Click "Install everything" to set everything up automatically.</p>
        <p>Manual install:<br><code>pip install playwright</code><br><code>python -m playwright install-deps</code><br><code>playwright install chromium</code></p>
        <button id="installBtn" onclick="runInstall();">Install everything</button>
        <pre id="installLog" class="install-log"></pre>
        <button id="recheckBtn" onclick="checkInstall();">Recheck</button>
    </div>

    <div class="wizard-steps" id="wizardSteps" aria-label="Recording steps">
        <span id="wizStep1" class="wizard-step active">1. Describe</span>
        <span id="wizStep2" class="wizard-step">2. Confirm</span>
        <span id="wizStep3" class="wizard-step">3. Record</span>
        <span id="wizStep4" class="wizard-step">4. Download</span>
    </div>
    <p id="wizardSubtitle" class="wizard-subtitle">Step 1 of 4: Describe what you want to record.</p>

    <section class="step-section" id="stepDescribe">
        <h2>Step 1 - Describe what you want to record</h2>
        <div class="hero-note">
            <p><strong>You do not need to code.</strong> Describe your clicks in plain language and we will do the rest.</p>
            <p>After generation, click <strong>Start Recording</strong>. Only use the technical script view if you are comfortable with code.</p>
        </div>
        <div class="ai-disclaimer">
            <strong>💡 AI-Powered Generation:</strong> This tool uses LangDock AI to generate Playwright scripts from your description.
            <ul style="margin:8px 0; padding-left:20px;">
                <li>Generation takes <strong>30-60 seconds</strong></li>
                <li>Always <strong>review the script</strong> before running—it may need fixes</li>
                <li>Test with simple interactions first</li>
            </ul>
        </div>
        <details class="advanced-tools">
            <summary>Advanced options (model selection)</summary>
            <div class="model-row">
                <label for="modelSelect">AI model</label>
                <select id="modelSelect" onchange="updateModelSpeedBadge();">
                    <option value="">Loading models...</option>
                </select>
                <span id="modelSpeedBadge" class="model-speed-badge model-speed-balanced">Balanced</span>
                <button id="modelRefreshBtn" onclick="loadLangdockModels();">Reload models</button>
            </div>
            <div id="modelHint" class="model-hint">Using a faster model by default for quicker script generation.</div>
        </details>
        <textarea id="descriptionInput" rows="6" placeholder="Go to https://dynatrace.com, click on Resources..."></textarea>
        <button id="generateBtn" onclick="generateScript();">Create Recording Plan</button>
        <div id="generateError" style="color:red; margin-top:8px;"></div>
    </section>

    <section class="step-section" id="stepScript" hidden>
        <h2>Step 2 - Confirm and start</h2>
        <div class="plain-note">
            <strong>No coding needed.</strong> Just click <strong>Start Recording</strong> below.
            <br>
            This script is <strong>already saved automatically</strong> in <code>playwright_scripts/</code> so you can reuse it from saved scripts later. Downloading is optional.
        </div>
        <div class="script-actions">
            <button id="backToStep1Btn" onclick="backToStep1();">← Back</button>
            <button id="runBtn" onclick="runRecording();">▶ Start Recording</button>
            <button id="downloadScriptBtn" onclick="downloadScript();">Download .py</button>
            <span id="copyStatus" class="copy-status" aria-live="polite"></span>
        </div>
        <div id="runError" style="color:red; margin-top:8px;"></div>
        <div class="advanced-tools">
            <button id="toggleAdvancedBtn" onclick="toggleAdvancedScriptView();">Show technical script (optional)</button>
        </div>
        <div id="advancedScriptPanel" class="advanced-panel" hidden>
            <textarea id="scriptEditor" class="script-editor" spellcheck="false"></textarea>
            <div class="script-actions">
                <button id="copyScriptBtn" onclick="copyScriptToClipboard();">Copy Script</button>
            </div>
        </div>
    </section>

    <section class="step-section" id="stepDropIn">
        <h2>Optional: Run a saved script</h2>
        <p>This is separate from the main 4-step flow. If someone shared a script with you, place the <code>.py</code> file in <code>playwright_scripts/</code>, then click <strong>Refresh</strong> and run it from here.</p>
        <div class="dropin-controls">
            <select id="dropInSelect"><option value="">-- select a script --</option></select>
            <button id="dropInRefreshBtn" onclick="refreshDropInScripts();">⟳ Refresh</button>
            <button id="dropInRunBtn" onclick="runDropInScript();" disabled>▶ Run Script</button>
        </div>
        <p id="dropInEmpty" class="muted">No scripts found in <code>playwright_scripts/</code> yet.</p>
    </section>

    <section class="step-section" id="stepProgress" hidden>
        <h2>Step 3 – Recording in progress</h2>
        <pre id="progressLog" class="progress-log"></pre>
        <div id="progressErrorBox" class="plain-note" hidden>
            <strong>Recording was unsuccessful.</strong>
            <p id="progressErrorText" style="margin:8px 0 12px 0;"></p>
            <div class="script-actions">
                <button id="editRetryBtn" onclick="editScriptAndRetry();">Edit script and retry</button>
                <button id="aiRetryBtn" onclick="retryGenerationWithModelChoice();">Ask AI to regenerate</button>
                <button id="startOverBtn" onclick="recordAgain();">Start over</button>
            </div>
        </div>
    </section>

    <section class="step-section" id="stepResult" hidden>
        <h2>Step 4 – Your recording is ready</h2>
        <p id="mp4Path"></p>
        <div id="resultVideoWrap" class="result-video-wrap" hidden>
            <video id="resultVideo" class="result-video" controls preload="metadata"></video>
        </div>
        <a id="mp4DownloadLink" class="mp4-download" href="#" download>⬇ Download MP4</a>
        <p class="muted">This file is also in the <code>recordings/</code> folder of your workspace.</p>
        <button id="recordAgainBtn" onclick="recordAgain();">Record another</button>
    </section>

    <div id="waitModal" class="wait-modal-overlay" role="dialog" aria-modal="true" aria-live="polite" hidden>
        <div class="wait-modal">
            <div class="wait-modal-head">
                <h3>Building your recording plan...</h3>
                <p>This usually takes 30-60 seconds.</p>
            </div>
            <div class="wait-modal-body">
                <div id="waitStatusPill" class="wait-pill"><span id="waitStatusIcon" class="wait-spinner"></span><span id="waitStatusText">AI generation in progress</span></div>
                <p id="waitModelHint" class="wait-model-note">Using the default model for balanced speed and quality.</p>
                <div class="wait-tip-box">
                    <h4 id="waitTipTitle" class="wait-tip-title">Did you know?</h4>
                    <p id="waitTipText" class="wait-tip-text">Dynatrace can map service dependencies automatically.</p>
                    <div id="waitTipVideoWrap" class="wait-tip-video-wrap">
                        <div id="waitTipVideoPlayer" class="wait-tip-video-player"></div>
                    </div>
                    <div class="wait-tip-actions">
                        <a id="waitTipLink" href="https://docs.dynatrace.com" target="_blank" rel="noopener">Open resource</a>
                        <button type="button" id="prevTipBtn" class="wait-nav-btn" onclick="showPreviousWaitTip();">← Previous</button>
                        <button type="button" id="nextTipBtn" class="wait-nav-btn" onclick="showNextWaitTip();">Next →</button>
                        <span id="waitTipCounter" class="wait-tip-counter">Tip 1/1</span>
                        <button type="button" id="closeWaitTipsBtn" onclick="dismissWaitModal();">Close modal</button>
                    </div>
                    <div id="waitReadyBar" class="wait-ready-bar" hidden>
                        <p id="waitReadyText" class="wait-ready-text">Your plan is ready.</p>
                        <div class="wait-ready-actions">
                            <button type="button" id="waitContinueBtn" class="primary" onclick="waitModalContinueNow();">Continue now</button>
                            <button type="button" id="waitKeepWatchingBtn" onclick="waitModalKeepWatching();">Keep watching</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
"""

BROWSER_RECORDER_SCRIPT_JS = """
<script>
let currentJobId = null;
let currentEventSource = null;
let waitTipIndex = 0;
const MIN_GENERATION_WAIT_MS = 20000;
let waitTipPlayer = null;
let waitTipPlayerVideoId = '';
let waitTipPlayerType = '';
let waitTipApiReadyPromise = null;
let waitReadyContinueHandler = null;
let waitReadyKeepWatching = false;

let WAIT_TIPS = [];
const WAIT_TIPS_FALLBACK = [
    {
        type: 'advice',
        title: 'Dynatrace docs',
        text: 'Open Dynatrace docs while your recording plan is generated.',
        linkLabel: 'Open docs',
        linkUrl: 'https://docs.dynatrace.com'
    }
];

function extractYouTubeVideoId(value) {
    const input = (value || '').trim();
    if (!input) return '';

    const directId = input.match(/^[A-Za-z0-9_-]{11}$/);
    if (directId) return directId[0];

    const shortsMatch = input.match(/\\/shorts\\/([A-Za-z0-9_-]{11})/);
    if (shortsMatch) return shortsMatch[1];

    const watchMatch = input.match(/[?&]v=([A-Za-z0-9_-]{11})/);
    if (watchMatch) return watchMatch[1];

    const embedMatch = input.match(/\\/embed\\/([A-Za-z0-9_-]{11})/);
    if (embedMatch) return embedMatch[1];

    return '';
}

function normalizeWaitTip(raw) {
    if (!raw || typeof raw !== 'object') return null;

    const typeRaw = (raw.type || 'advice').toString().trim().toLowerCase();
    const type = ['advice', 'short', 'video'].includes(typeRaw) ? typeRaw : 'advice';
    const title = (raw.title || '').toString().trim();
    const text = (raw.text || '').toString().trim();

    if (!title || !text) {
        return null;
    }

    if (type === 'short') {
        const videoId = extractYouTubeVideoId(raw.videoId || raw.linkUrl || raw.url || raw.watchUrl || raw.embedUrl || '');
        if (!videoId) {
            return null;
        }
        const linkUrl = (raw.linkUrl || raw.url || raw.watchUrl || ('https://www.youtube.com/shorts/' + videoId)).toString().trim();
        const linkLabel = (raw.linkLabel || 'Open short on YouTube').toString().trim();
        return { type, title, text, videoId, linkLabel, linkUrl };
    }

    const linkLabel = (raw.linkLabel || 'Open resource').toString().trim();
    const linkUrl = (raw.linkUrl || raw.url || '').toString().trim();
    if (!linkUrl) {
        return null;
    }

    if (type === 'video') {
        const videoId = extractYouTubeVideoId(raw.videoId || linkUrl || raw.watchUrl || raw.embedUrl || '');
        if (videoId) {
            return { type, title, text, videoId, linkLabel, linkUrl };
        }
    }

    return { type, title, text, linkLabel, linkUrl };
}

function getWaitTips() {
    return Array.isArray(WAIT_TIPS) && WAIT_TIPS.length > 0 ? WAIT_TIPS : WAIT_TIPS_FALLBACK;
}

async function loadWaitTips() {
    try {
        const resp = await fetch('/browser-recorder/wait-tips');
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || 'Failed to load tips');
        }
        const incoming = Array.isArray(data.tips) ? data.tips : [];
        WAIT_TIPS = incoming.map(normalizeWaitTip).filter(Boolean);
    } catch (err) {
        console.error(err);
        WAIT_TIPS = [];
    }
}

async function ensureYouTubeIframeApiReady() {
    if (window.YT && window.YT.Player) {
        return;
    }

    if (!waitTipApiReadyPromise) {
        waitTipApiReadyPromise = new Promise((resolve) => {
            const previous = window.onYouTubeIframeAPIReady;
            window.onYouTubeIframeAPIReady = () => {
                if (typeof previous === 'function') {
                    previous();
                }
                resolve();
            };

            const script = document.createElement('script');
            script.src = 'https://www.youtube.com/iframe_api';
            script.async = true;
            document.head.appendChild(script);
        });
    }

    await waitTipApiReadyPromise;
}

function resetWaitReadyState() {
    const bar = document.getElementById('waitReadyBar');
    const text = document.getElementById('waitReadyText');
    if (bar) bar.hidden = true;
    if (text) text.textContent = 'Your plan is ready.';
    waitReadyContinueHandler = null;
    waitReadyKeepWatching = false;
    setWaitStatusPill(false, 'AI generation in progress');
    updateWaitModalCloseLabel();
}

function setWaitStatusPill(done, text) {
    const pill = document.getElementById('waitStatusPill');
    const icon = document.getElementById('waitStatusIcon');
    const label = document.getElementById('waitStatusText');
    if (!pill || !icon || !label) return;

    pill.classList.toggle('done', !!done);
    if (done) {
        icon.className = 'wait-status-icon-tick';
        icon.textContent = '✓';
    } else {
        icon.className = 'wait-spinner';
        icon.textContent = '';
    }
    label.textContent = text || (done ? 'Plan ready' : 'AI generation in progress');
}

function updateWaitModalCloseLabel() {
    const closeBtn = document.getElementById('closeWaitTipsBtn');
    if (!closeBtn) return;
    if (waitReadyContinueHandler) {
        closeBtn.hidden = true;
        return;
    }
    closeBtn.hidden = false;
    closeBtn.textContent = 'Hide tips (keep generating)';
}

function waitModalContinueNow() {
    const handler = waitReadyContinueHandler;
    clearWaitTipVideoPlayer();
    resetWaitReadyState();
    if (typeof handler === 'function') {
        handler();
    }
}

function waitModalKeepWatching() {
    waitReadyKeepWatching = true;
    const text = document.getElementById('waitReadyText');
    if (text) {
        text.textContent = 'Plan is ready. Keep watching, then click Continue now when you are ready.';
    }
}

function showWaitReadyState(message, onContinue) {
    const bar = document.getElementById('waitReadyBar');
    const text = document.getElementById('waitReadyText');
    if (!bar || !text) {
        onContinue();
        return;
    }

    waitReadyContinueHandler = onContinue;
    waitReadyKeepWatching = false;
    setWaitStatusPill(true, 'Plan ready');
    text.textContent = message || 'Your plan is ready.';
    bar.hidden = false;
    updateWaitModalCloseLabel();
}

function clearWaitTipVideoPlayer() {
    if (waitTipPlayer && typeof waitTipPlayer.destroy === 'function') {
        waitTipPlayer.destroy();
    }
    waitTipPlayer = null;
    waitTipPlayerVideoId = '';
    waitTipPlayerType = '';
    const container = document.getElementById('waitTipVideoPlayer');
    const wrap = document.getElementById('waitTipVideoWrap');
    if (wrap) {
        wrap.classList.remove('is-visible', 'is-short', 'is-video');
    }
    if (container) {
        container.classList.remove('is-short', 'is-video');
        container.innerHTML = '';
    }
}

function onWaitShortStateChange(event) {
    if (!window.YT || !window.YT.PlayerState) {
        return;
    }
}

async function renderWaitTipVideo(videoId, tipType) {
    const playerWrap = document.getElementById('waitTipVideoWrap');
    const player = document.getElementById('waitTipVideoPlayer');
    if (!playerWrap || !videoId) {
        return;
    }

    const typeClass = tipType === 'short' ? 'is-short' : 'is-video';
    await ensureYouTubeIframeApiReady();

    if (waitTipPlayer && waitTipPlayerVideoId === videoId && waitTipPlayerType === tipType) {
        playerWrap.classList.remove('is-short', 'is-video');
        playerWrap.classList.add('is-visible', typeClass);
        if (player) {
            player.classList.remove('is-short', 'is-video');
            player.classList.add(typeClass);
        }
        return;
    }

    clearWaitTipVideoPlayer();

    playerWrap.classList.remove('is-short', 'is-video');
    playerWrap.classList.add('is-visible', typeClass);
    if (player) {
        player.classList.remove('is-short', 'is-video');
        player.classList.add(typeClass);
    }

    waitTipPlayer = new window.YT.Player('waitTipVideoPlayer', {
        width: '100%',
        height: '100%',
        videoId,
        playerVars: {
            rel: 0,
            modestbranding: 1,
            playsinline: 1,
        },
        events: {
            onStateChange: onWaitShortStateChange,
        },
    });
    waitTipPlayerVideoId = videoId;
    waitTipPlayerType = tipType || '';
}

function renderWaitTip(index) {
    const tips = getWaitTips();
    const tip = tips[index % tips.length];
    document.getElementById('waitTipTitle').textContent = tip.title || 'Did you know?';
    document.getElementById('waitTipText').textContent = tip.text || '';
    const link = document.getElementById('waitTipLink');
    const counter = document.getElementById('waitTipCounter');

    if (link) {
        if (tip.linkUrl) {
            link.hidden = false;
            link.textContent = tip.linkLabel || 'Open resource';
            link.href = tip.linkUrl;
        } else {
            link.hidden = true;
            link.removeAttribute('href');
        }
    }

    const playerWrap = document.getElementById('waitTipVideoWrap');
    if ((tip.type === 'short' || tip.type === 'video') && tip.videoId) {
        void renderWaitTipVideo(tip.videoId, tip.type);
    } else {
        if (playerWrap) {
            playerWrap.classList.remove('is-visible', 'is-short', 'is-video');
        }
        clearWaitTipVideoPlayer();
    }

    if (counter) {
        counter.textContent = 'Tip ' + (index + 1) + '/' + tips.length;
    }
}

function showNextWaitTip() {
    const tips = getWaitTips();
    waitTipIndex = (waitTipIndex + 1) % tips.length;
    renderWaitTip(waitTipIndex);
}

function showPreviousWaitTip() {
    const tips = getWaitTips();
    waitTipIndex = (waitTipIndex - 1 + tips.length) % tips.length;
    renderWaitTip(waitTipIndex);
}

function getModelSpeedHint(modelId) {
    const model = (modelId || '').toLowerCase();
    if (!model) {
        return 'Using the default model for balanced speed and quality.';
    }

    if (model.includes('mini') || model.includes('haiku')) {
        return 'Selected model is speed-focused, so this should finish quickly.';
    }

    if (model.includes('opus') || model.includes('sonnet') || model.includes('gpt-5') || model.includes('o3')) {
        return 'Selected model prioritizes output quality, so this may take a bit longer.';
    }

    return 'Selected model may vary in response time based on current provider load.';
}

function showWaitModal() {
    const modal = document.getElementById('waitModal');
    const modelHint = document.getElementById('waitModelHint');
    modelHint.textContent = getModelSpeedHint(getSelectedModel());
    resetWaitReadyState();
    const tips = getWaitTips();
    const firstShortIndex = tips.findIndex((tip) => tip && tip.type === 'short' && tip.videoId);
    if (firstShortIndex >= 0) {
        waitTipIndex = firstShortIndex;
    } else {
        waitTipIndex = Math.floor(Math.random() * tips.length);
    }
    renderWaitTip(waitTipIndex);
    modal.hidden = false;
    modal.classList.add('show');
}

function dismissWaitModal() {
    clearWaitTipVideoPlayer();
    const modal = document.getElementById('waitModal');
    modal.classList.remove('show');
    modal.hidden = true;
    resetWaitReadyState();
}

async function enforceMinimumGenerationWait(startedAtMs) {
    const elapsed = Date.now() - startedAtMs;
    const remaining = MIN_GENERATION_WAIT_MS - elapsed;
    if (remaining > 0) {
        await new Promise((resolve) => setTimeout(resolve, remaining));
    }
}

function hideProgressFailure() {
    const box = document.getElementById('progressErrorBox');
    const text = document.getElementById('progressErrorText');
    if (box) box.hidden = true;
    if (text) text.textContent = '';
}

function userFriendlyFailureMessage(detail) {
    const raw = (detail || '').toLowerCase();
    if (
        raw.includes('script exited with code') ||
        raw.includes('playwright') ||
        raw.includes('syntaxerror') ||
        raw.includes('timeout')
    ) {
        return 'The generated browser script appears to be invalid for this page. This is usually caused by AI-generated steps not matching the live site, not by this recorder app itself.';
    }
    return 'The recording could not be completed. This is often caused by unstable page selectors in the generated script.';
}

function showProgressFailure(detail) {
    const box = document.getElementById('progressErrorBox');
    const text = document.getElementById('progressErrorText');
    if (!box || !text) return;
    text.textContent = userFriendlyFailureMessage(detail);
    box.hidden = false;
    setWizardStep(3, 'Step 3 of 4: Recording failed. Choose one of the recovery options below.');
}

function editScriptAndRetry() {
    hideProgressFailure();
    document.getElementById('stepProgress').hidden = true;
    document.getElementById('stepScript').hidden = false;
    document.getElementById('stepDescribe').hidden = true;
    document.getElementById('stepDropIn').hidden = true;

    const panel = document.getElementById('advancedScriptPanel');
    const btn = document.getElementById('toggleAdvancedBtn');
    if (panel && panel.hidden) {
        panel.hidden = false;
        if (btn) btn.textContent = 'Hide technical script';
    }

    setWizardStep(2, 'Step 2 of 4: Edit the script, then click Start Recording again.');
    document.getElementById('stepScript').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function retryGenerationWithModelChoice() {
    hideProgressFailure();
    document.getElementById('stepProgress').hidden = true;
    document.getElementById('stepDescribe').hidden = false;
    document.getElementById('stepScript').hidden = true;
    document.getElementById('stepDropIn').hidden = false;
    document.getElementById('stepResult').hidden = true;

    const adv = document.querySelector('#stepDescribe details.advanced-tools');
    if (adv) adv.open = true;

    setWizardStep(1, 'Step 1 of 4: Choose a model (optional), then regenerate your plan.');
    const useCurrentModel = window.confirm('Retry generation now with the currently selected model? Click Cancel if you want to pick a different model first.');
    if (useCurrentModel) {
        generateScript();
        return;
    }

    const errDiv = document.getElementById('generateError');
    if (errDiv) {
        errDiv.innerHTML = 'Choose a model in Advanced options (optional), then click "Create Recording Plan".';
    }
    document.getElementById('stepDescribe').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function setWizardStep(step, subtitle) {
    const steps = [1, 2, 3, 4];
    for (const n of steps) {
        const el = document.getElementById('wizStep' + n);
        if (!el) continue;
        el.classList.remove('active', 'done');
        if (n < step) {
            el.classList.add('done');
        } else if (n === step) {
            el.classList.add('active');
        }
    }
    const subtitleEl = document.getElementById('wizardSubtitle');
    if (subtitleEl) {
        subtitleEl.textContent = subtitle;
    }
}

function backToStep1() {
    document.getElementById('stepDescribe').hidden = false;
    document.getElementById('stepScript').hidden = true;
    document.getElementById('stepDropIn').hidden = false;
    document.getElementById('stepProgress').hidden = true;
    document.getElementById('stepResult').hidden = true;
    setWizardStep(1, 'Step 1 of 4: Describe what you want to record.');
    document.getElementById('stepDescribe').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function getSelectedModel() {
    const select = document.getElementById('modelSelect');
    if (!select) return '';
    return select.value || '';
}

function classifyModelTier(modelId) {
    const model = (modelId || '').toLowerCase();
    if (!model) return 'balanced';

    if (model.includes('mini') || model.includes('haiku')) {
        return 'fast';
    }
    if (model.includes('opus') || model.includes('sonnet') || model.includes('gpt-5') || model.includes('o3')) {
        return 'quality';
    }
    return 'balanced';
}

function updateModelSpeedBadge() {
    const badge = document.getElementById('modelSpeedBadge');
    if (!badge) return;

    const tier = classifyModelTier(getSelectedModel());
    badge.classList.remove('model-speed-fast', 'model-speed-balanced', 'model-speed-quality', 'model-speed-unknown');

    if (tier === 'fast') {
        badge.classList.add('model-speed-fast');
        badge.textContent = 'Fast';
        return;
    }
    if (tier === 'quality') {
        badge.classList.add('model-speed-quality');
        badge.textContent = 'Quality';
        return;
    }
    if (tier === 'balanced') {
        badge.classList.add('model-speed-balanced');
        badge.textContent = 'Balanced';
        return;
    }

    badge.classList.add('model-speed-unknown');
    badge.textContent = 'Unknown';
}

async function loadLangdockModels() {
    const select = document.getElementById('modelSelect');
    const hint = document.getElementById('modelHint');
    const refreshBtn = document.getElementById('modelRefreshBtn');

    refreshBtn.disabled = true;
    select.disabled = true;
    select.innerHTML = '<option value="">Loading models…</option>';

    try {
        const resp = await fetch('/browser-recorder/models');
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || 'Failed to load models');
        }

        const models = Array.isArray(data.models) ? data.models : [];
        select.innerHTML = '';
        for (const modelId of models) {
            const opt = document.createElement('option');
            opt.value = modelId;
            opt.textContent = modelId;
            if (modelId === data.default_model) {
                opt.selected = true;
            }
            select.appendChild(opt);
        }

        if (models.length === 0) {
            select.innerHTML = '<option value="">No models available</option>';
        }
        updateModelSpeedBadge();

        const defaultNote = data.default_note ? data.default_note : '';
        const source = data.source ? 'Source: ' + data.source : '';
        const warning = data.warning ? ' ' + data.warning : '';
        hint.textContent = (defaultNote + ' ' + source + warning).trim() || 'Choose the model used for generation.';
    } catch (err) {
        select.innerHTML = '<option value="">Model list unavailable</option>';
        const badge = document.getElementById('modelSpeedBadge');
        if (badge) {
            badge.classList.remove('model-speed-fast', 'model-speed-balanced', 'model-speed-quality');
            badge.classList.add('model-speed-unknown');
            badge.textContent = 'Unknown';
        }
        hint.textContent = 'Could not load model list from LangDock. Generation will use server default model.';
        console.error(err);
    } finally {
        refreshBtn.disabled = false;
        select.disabled = false;
    }
}

async function checkInstall() {
    const resp = await fetch('/browser-recorder/check-install');
    const data = await resp.json();
    const depsOk = (data.deps_ok !== false);
    const hint = document.getElementById('installStatusHint');
    if (data.pkg_ok && data.browser_ok && depsOk) {
        document.getElementById('installBanner').classList.remove('show');
    } else {
        const missing = [];
        if (!data.pkg_ok) missing.push('Playwright package');
        if (!data.browser_ok) missing.push('Chromium browser');
        if (!depsOk) missing.push('Linux browser dependencies');
        if (hint) {
            hint.textContent = 'Missing: ' + missing.join(', ') + '. Click "Install everything" to fix this automatically.';
        }
        document.getElementById('installBanner').classList.add('show');
    }
}

async function runInstall() {
    const btn = document.getElementById('installBtn');
    const log = document.getElementById('installLog');
    btn.disabled = true;
    btn.textContent = 'Installing everything…';
    log.innerHTML = '';
    
    const source = new EventSource('/browser-recorder/install');
    source.addEventListener('message', (e) => {
        const event = JSON.parse(e.data);
        if (event.type === 'log') {
            log.innerHTML += event.line + '\\n';
            log.scrollTop = log.scrollHeight;
        } else if (event.type === 'done') {
            log.innerHTML += '\\n✓ Installation complete!\\n';
            source.close();
            btn.textContent = 'Install everything';
            btn.disabled = false;
            setTimeout(checkInstall, 1000);
        } else if (event.type === 'error') {
            log.innerHTML += '\\n✗ Error: ' + event.detail + '\\n';
            source.close();
            btn.textContent = 'Install everything';
            btn.disabled = false;
        }
    });
}

async function generateScript() {
    const desc = document.getElementById('descriptionInput').value.trim();
    const errDiv = document.getElementById('generateError');
    errDiv.innerHTML = '';
    
    if (!desc) {
        errDiv.innerHTML = 'Description is required';
        return;
    }
    
    const btn = document.getElementById('generateBtn');
    const generationStartedAt = Date.now();
    btn.disabled = true;
    btn.textContent = 'Generating plan with AI (30-60s)...';
    showWaitModal();
    
    currentJobId = crypto.randomUUID();
    
    try {
        const resp = await fetch('/browser-recorder/generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                description: desc,
                job_id: currentJobId,
                model: getSelectedModel()
            })
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Generation failed');
        }

        const data = await resp.json();
        await enforceMinimumGenerationWait(generationStartedAt);

        const copyStatus = document.getElementById('copyStatus');
        if (copyStatus && data.saved_script_filename) {
            copyStatus.textContent = 'Saved to playwright_scripts/' + data.saved_script_filename;
        }

        const finishSuccess = () => {
            document.getElementById('scriptEditor').value = data.script;
            document.getElementById('stepDescribe').hidden = true;
            document.getElementById('stepScript').hidden = false;
            document.getElementById('stepDropIn').hidden = true;
            setWizardStep(2, 'Step 2 of 4: Confirm and start your recording.');
            document.getElementById('stepScript').scrollIntoView({ behavior: 'smooth', block: 'start' });
            dismissWaitModal();
            btn.disabled = false;
            btn.textContent = 'Create Recording Plan';
        };
        showWaitReadyState('Plan is ready. Continue now, or keep watching.', finishSuccess);
    } catch (err) {
        await enforceMinimumGenerationWait(generationStartedAt);

        const finishError = () => {
            errDiv.innerHTML = 'Error: ' + err.message;
            currentJobId = null;
            dismissWaitModal();
            btn.disabled = false;
            btn.textContent = 'Create Recording Plan';
        };
        showWaitReadyState('Generation completed with an error. Continue now to see details.', finishError);
    }
}

function downloadScript() {
    const text = document.getElementById('scriptEditor').value;
    const blob = new Blob([text], {type: 'text/plain'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'recording_script.py';
    a.click();
    URL.revokeObjectURL(url);
}

async function copyScriptToClipboard() {
    const text = document.getElementById('scriptEditor').value;
    const status = document.getElementById('copyStatus');
    const btn = document.getElementById('copyScriptBtn');

    status.textContent = '';
    if (!text.trim()) {
        status.textContent = 'Nothing to copy yet';
        return;
    }

    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Copying…';

    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
        } else {
            // Fallback for environments where Clipboard API is unavailable.
            const temp = document.createElement('textarea');
            temp.value = text;
            temp.style.position = 'fixed';
            temp.style.opacity = '0';
            document.body.appendChild(temp);
            temp.focus();
            temp.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(temp);
            if (!ok) {
                throw new Error('Copy command failed');
            }
        }
        status.textContent = 'Copied to clipboard';
    } catch (err) {
        console.error(err);
        status.textContent = 'Copy failed. Try Download .py instead.';
    } finally {
        btn.disabled = false;
        btn.textContent = originalLabel;
        setTimeout(() => {
            status.textContent = '';
        }, 2500);
    }
}

function toggleAdvancedScriptView() {
    const panel = document.getElementById('advancedScriptPanel');
    const btn = document.getElementById('toggleAdvancedBtn');
    const isHidden = panel.hidden;
    panel.hidden = !isHidden;
    btn.textContent = isHidden ? 'Hide technical script' : 'Show technical script (optional)';
}

async function runRecording() {
    const errDiv = document.getElementById('runError');
    errDiv.innerHTML = '';
    hideProgressFailure();
    const script = document.getElementById('scriptEditor').value.trim();
    if (!script) {
        errDiv.innerHTML = 'We could not find the generated recording plan. Please click "Create Recording Plan" first.';
        return;
    }
    
    if (!currentJobId) currentJobId = crypto.randomUUID();
    
    try {
        const resp = await fetch('/browser-recorder/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({job_id: currentJobId, script: script})
        });
        
        if (!resp.ok) throw new Error('Failed to start recording');
        
        document.getElementById('stepScript').hidden = true;
        document.getElementById('stepDropIn').hidden = true;
        document.getElementById('stepProgress').hidden = false;
        setWizardStep(3, 'Step 3 of 4: Recording in progress.');
        streamProgress(currentJobId);
    } catch (err) {
        console.error(err);
        errDiv.innerHTML = 'Could not start recording. Please try again.';
    }
}

function streamProgress(jobId) {
    if (currentEventSource) currentEventSource.close();
    
    const log = document.getElementById('progressLog');
    log.innerHTML = '[Connecting…]\\n';
    let gotRealEvent = false;

    const warmupTimer = setInterval(() => {
        if (gotRealEvent) {
            clearInterval(warmupTimer);
            return;
        }

        const warmupLines = [
            '[Init] Preparing recording environment...',
            '[Init] Validating generated steps...',
            '[Init] Starting browser worker...',
            '[Run] Recording now... awaiting browser events.'
        ];

        const current = log.innerHTML;
        const line = warmupLines[Math.min((current.match(/\\[(Init|Run)\\]/g) || []).length, warmupLines.length - 1)];
        if (!current.includes(line)) {
            log.innerHTML += line + '\\n';
            log.scrollTop = log.scrollHeight;
        }
    }, 500);
    
    currentEventSource = new EventSource('/browser-recorder/stream/' + jobId);
    currentEventSource.addEventListener('message', (e) => {
        gotRealEvent = true;
        clearInterval(warmupTimer);
        const event = JSON.parse(e.data);
        
        if (event.type === 'log') {
            log.innerHTML += event.line + '\\n';
            log.scrollTop = log.scrollHeight;
        } else if (event.type === 'done') {
            currentEventSource.close();
            hideProgressFailure();
            showResult(event.mp4_filename);
        } else if (event.type === 'error') {
            currentEventSource.close();
            log.innerHTML += '\\n[ERROR] ' + event.detail + '\\n';
            showProgressFailure(event.detail || 'Unknown script error');
        }
    });
}

function showResult(mp4_filename) {
    document.getElementById('stepProgress').hidden = true;
    document.getElementById('stepResult').hidden = false;
    setWizardStep(4, 'Step 4 of 4: Download your recording.');
    document.getElementById('mp4Path').innerHTML = 'File: <code>recordings/' + mp4_filename + '</code>';
    const dl = document.getElementById('mp4DownloadLink');
    dl.href = '/download-recording/' + mp4_filename;

    const videoUrl = '/download-recording/' + mp4_filename;
    const videoWrap = document.getElementById('resultVideoWrap');
    const video = document.getElementById('resultVideo');
    if (video && videoWrap) {
        video.src = videoUrl;
        videoWrap.hidden = false;
        video.load();
    }
}

async function refreshDropInScripts() {
    try {
        const resp = await fetch('/browser-recorder/list-scripts');
        const data = await resp.json();
        
        const select = document.getElementById('dropInSelect');
        const btn = document.getElementById('dropInRunBtn');
        const empty = document.getElementById('dropInEmpty');
        
        select.innerHTML = '<option value="">-- select a script --</option>';
        for (const script of data.scripts) {
            const opt = document.createElement('option');
            opt.value = script;
            opt.textContent = script;
            select.appendChild(opt);
        }
        
        if (data.scripts.length > 0) {
            select.hidden = false;
            btn.hidden = false;
            empty.hidden = true;
        } else {
            select.hidden = true;
            btn.hidden = true;
            empty.hidden = false;
        }
    } catch (err) {
        console.error(err);
    }
}

async function runDropInScript() {
    const filename = document.getElementById('dropInSelect').value;
    if (!filename) {
        alert( 'Please select a script');
        return;
    }
    
    currentJobId = crypto.randomUUID();
    
    const resp = await fetch('/browser-recorder/run-script', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filename: filename})
    });
    
    if (!resp.ok) throw new Error('Failed to start script');
    
    const data = await resp.json();
    currentJobId = data.job_id;
    
    document.getElementById('stepDescribe').hidden = true;
    document.getElementById('stepScript').hidden = true;
    document.getElementById('stepDropIn').hidden = true;
    document.getElementById('stepProgress').hidden = false;
    setWizardStep(3, 'Step 3 of 4: Running your saved script.');
    streamProgress(currentJobId);
}

function recordAgain() {
    currentJobId = null;
    document.getElementById('descriptionInput').value = '';
    document.getElementById('scriptEditor').value = '';
    document.getElementById('generateError').innerHTML = '';
    document.getElementById('stepDescribe').hidden = false;
    document.getElementById('stepScript').hidden = true;
    document.getElementById('stepDropIn').hidden = false;
    document.getElementById('stepProgress').hidden = true;
    document.getElementById('stepResult').hidden = true;
    const video = document.getElementById('resultVideo');
    const videoWrap = document.getElementById('resultVideoWrap');
    if (video) {
        video.pause();
        video.removeAttribute('src');
        video.load();
    }
    if (videoWrap) {
        videoWrap.hidden = true;
    }
    hideProgressFailure();
    setWizardStep(1, 'Step 1 of 4: Describe what you want to record.');
    refreshDropInScripts();
}

// Run on page load
window.addEventListener('load', () => {
    setWizardStep(1, 'Step 1 of 4: Describe what you want to record.');
    checkInstall();
    loadWaitTips();
    loadLangdockModels();
    refreshDropInScripts();
});
</script>
"""

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
LANGDOCK_MODEL = os.environ.get("LANGDOCK_MODEL", "gpt-5-mini")

# Browser Recorder directories
RECORDINGS_DIR = BASE_DIR / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

PLAYWRIGHT_SCRIPTS_DIR = BASE_DIR / "playwright_scripts"
PLAYWRIGHT_SCRIPTS_DIR.mkdir(exist_ok=True)

BROWSER_RECORDER_WAIT_TIPS_FILE = BASE_DIR / "browser_recorder_wait_tips.json"

# Job registry for tracking browser recordings in progress
_recording_jobs: dict[str, dict] = {}


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


# ── Browser Recorder Helpers ──────────────────────────────────────────────────

PLAYWRIGHT_SYSTEM_PROMPT = """You are an expert Playwright Python automation engineer.

Your task: given a plain-English description of browser actions, write a complete
Python script that uses Playwright's synchronous API to perform those actions while
recording a video.

OUTPUT RULES:
- Output ONLY valid Python code. No markdown. No ``` fences. No explanatory prose.
- The script must be runnable with `python script.py` without any modification.

MANDATORY SCRIPT STRUCTURE — keep this structure exactly, only change the actions
section marked with the comment:

```
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from pathlib import Path
import time

OUTPUT_DIR = Path(__file__).parent

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        record_video_dir=str(OUTPUT_DIR),
        record_video_size={"width": 1920, "height": 1080},
        viewport={"width": 1920, "height": 1080},
    )
    page = context.new_page()

    # ── RECORDED ACTIONS ───────────────────────────────────────────────────
    # << REPLACE THIS COMMENT WITH THE ACTIONS >>
    # ── END RECORDED ACTIONS ───────────────────────────────────────────────

    context.close()
    browser.close()
```

COMMON PATTERNS — use these:
- Navigate:           page.goto("https://example.com")
- Wait for load:      page.wait_for_load_state("networkidle")
- Click by role:      page.get_by_role("button", name="Submit", exact=True).first.click()
- Click link by role: page.get_by_role("link", name="Dynatrace Hub", exact=True).first.click()
- Robust click (fallback):
  locator = page.get_by_role("link", name="Dynatrace Hub", exact=True).first
  locator.scroll_into_view_if_needed()
  try:
      locator.click(timeout=8000)
  except PlaywrightTimeoutError:
      locator.click(timeout=8000, force=True)
- Click by selector:  page.locator("css-selector").click()
- Fill a field:       page.get_by_label("Search").fill("query text")
- Search then Enter:  page.get_by_placeholder("Search").fill("text"); page.keyboard.press("Enter")
- Hover:              page.hover("selector")
- Wait for element:   page.wait_for_selector("selector", timeout=10000)
- Pause for camera:   time.sleep(0.7)  # keep the video readable but avoid long idle gaps
- Final hold:         time.sleep(5)  # always hold on the final state so viewers can see the result
- Scroll:             page.evaluate("window.scrollBy(0, 400)")
- Screenshot (debug): page.screenshot(path=str(OUTPUT_DIR / "debug.png"))

IMPORTANT:
- Prefer wait_for_load_state("networkidle") after navigation.
- Prefer exact matching for text and role locators to avoid strict mode collisions.
- DO NOT use `page.get_by_text(...).click()` for interactive actions.
- If the user provides a selector in their prompt, use that exact selector with `page.locator("...").first` for the click target.
- When using user-provided selectors for targets that may be off-screen, call `scroll_into_view_if_needed()` before clicking.
- For clickable elements without a user-provided selector, use role-based locators (`button`, `link`, `menuitem`) with `name=...`, `exact=True`, and `.first.click()`.
- For navigation links specifically, always use `page.get_by_role("link", name="...", exact=True).first.click()`.
- For links/cards that can be outside viewport, always call `scroll_into_view_if_needed()` before clicking.
- If click still times out after scrolling, retry with `force=True` as fallback.
- If a locator can match multiple elements, disambiguate with one of:
    - `exact=True` on role/text locators
    - a more specific accessible name
    - `.filter(has_text="...")`
    - `.first` when strict mode reports duplicate matches for the same accessible target
- Add short time.sleep(0.4–0.9) pauses after key actions so the recording visibly shows the result without long delays.
- ALWAYS add `time.sleep(5)` immediately after the final user-requested action so the video clearly shows the end state.
- Use try/except PlaywrightTimeoutError only if a step is genuinely optional.
- Never use page.pause() – it blocks headless execution.
"""


def _chromium_binary_exists() -> bool:
    """Check if Chromium browser is installed under ~/.cache/ms-playwright/."""
    import glob
    pattern = str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome")
    return bool(glob.glob(pattern))


def _playwright_runtime_dependencies_ok() -> tuple[bool, str]:
    """Verify Linux browser dependencies by launching Chromium once."""
    cmd = [
        sys.executable,
        "-c",
        (
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); "
            "b=p.chromium.launch(headless=True); "
            "b.close(); "
            "p.stop()"
        ),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if result.returncode == 0:
            return True, "ok"
        combined = (result.stderr or "") + "\n" + (result.stdout or "")
        detail = combined.strip()[-500:]
        return False, detail or "Chromium launch failed"
    except Exception as exc:
        return False, str(exc)


def _job_dir(job_id: str) -> Path:
    """Return (and create) the output dir for a job_id. Validates it stays inside RECORDINGS_DIR."""
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', job_id)
    path = (RECORDINGS_DIR / safe).resolve()
    if not str(path).startswith(str(RECORDINGS_DIR.resolve())):
        raise ValueError("Invalid job_id")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cleanup_failed_recording_job_dir(job_dir: Path) -> None:
    """Best-effort cleanup for failed recording jobs."""
    resolved = job_dir.resolve()
    if not str(resolved).startswith(str(RECORDINGS_DIR.resolve())):
        return
    if resolved.exists():
        shutil.rmtree(resolved, ignore_errors=True)


def _pick_browser_recorder_default_model(models: list[str], configured_model: str) -> str:
    """Pick a fast modern default model from available options."""
    preferred = [
        "gpt-5-1",
        "gpt-4.1-mini",
        "gpt-4o-mini",
        "gpt-5-mini",
        "claude-3-5-haiku",
        "claude-3-haiku",
    ]
    lowered = {m.lower(): m for m in models}
    for pref in preferred:
        for lm, original in lowered.items():
            if pref in lm:
                return original

    if configured_model in models:
        return configured_model

    return models[0] if models else configured_model


def _list_langdock_models() -> dict:
    """Fetch available models from LangDock with a safe fallback."""
    api_key = os.environ.get("LANGDOCK_API_KEY", "")
    configured_model = os.environ.get("LANGDOCK_MODEL", "gpt-5-mini")
    forced_default = "gpt-5-1"

    fallback_models = []
    for m in [forced_default, configured_model]:
        if m and m not in fallback_models:
            fallback_models.append(m)

    result = {
        "models": fallback_models,
        "default_model": forced_default if forced_default in fallback_models else configured_model,
        "configured_model": configured_model,
        "source": "configured",
    }

    if result["default_model"] != configured_model:
        result["default_note"] = (
            f"Defaulting to {result['default_model']} for speed in Browser Recorder "
            f"(LANGDOCK_MODEL is {configured_model})."
        )

    if not api_key:
        result["warning"] = "LANGDOCK_API_KEY is not set"
        return result

    models_url = LANGDOCK_ENDPOINT.rsplit("/chat/completions", 1)[0] + "/models"
    req = urllib.request.Request(
        models_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="GET",
    )

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        items = body.get("data", []) if isinstance(body, dict) else []
        models: list[str] = []
        for item in items:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id.strip():
                    models.append(model_id.strip())

        deduped = sorted(set(models))
        if deduped:
            default_model = _pick_browser_recorder_default_model(deduped, configured_model)
            payload = {
                "models": deduped,
                "default_model": default_model,
                "configured_model": configured_model,
                "source": "langdock",
            }
            if default_model != configured_model:
                payload["default_note"] = (
                    f"Defaulting to {default_model} for speed in Browser Recorder "
                    f"(LANGDOCK_MODEL is {configured_model})."
                )
            return payload

        result["warning"] = "No models returned from LangDock"
        return result
    except Exception as exc:
        result["warning"] = f"Failed to fetch model list: {exc}"
        return result


def _load_browser_recorder_wait_tips() -> dict:
    """Load wait tips for browser recorder from JSON file with safe fallback."""
    fallback = {
        "tips": [
            {
                "type": "advice",
                "title": "Dynatrace docs",
                "text": "Open Dynatrace docs while your recording plan is generated.",
                "linkLabel": "Open docs",
                "linkUrl": "https://docs.dynatrace.com",
            }
        ],
        "source": "fallback",
    }

    try:
        if not BROWSER_RECORDER_WAIT_TIPS_FILE.exists():
            return {
                **fallback,
                "warning": f"Wait tips file not found: {BROWSER_RECORDER_WAIT_TIPS_FILE.name}",
            }

        raw = json.loads(BROWSER_RECORDER_WAIT_TIPS_FILE.read_text(encoding="utf-8"))
        tips = raw.get("tips", []) if isinstance(raw, dict) else []

        def _extract_video_id(value: str) -> str:
            text = (value or "").strip()
            if not text:
                return ""
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
                return text
            patterns = [
                r"/shorts/([A-Za-z0-9_-]{11})",
                r"[?&]v=([A-Za-z0-9_-]{11})",
                r"/embed/([A-Za-z0-9_-]{11})",
            ]
            for pattern in patterns:
                m = re.search(pattern, text)
                if m:
                    return m.group(1)
            return ""

        normalized = []
        for tip in tips:
            if not isinstance(tip, dict):
                continue

            tip_type_raw = tip.get("type", "advice")
            tip_type = tip_type_raw.strip().lower() if isinstance(tip_type_raw, str) else "advice"
            if tip_type not in {"advice", "short", "video"}:
                tip_type = "advice"

            title = tip.get("title")
            text = tip.get("text")

            if not (isinstance(title, str) and title.strip() and isinstance(text, str) and text.strip()):
                continue

            if tip_type == "short":
                video_id = _extract_video_id(str(tip.get("videoId") or tip.get("linkUrl") or tip.get("url") or tip.get("watchUrl") or tip.get("embedUrl") or ""))
                if not video_id:
                    continue
                link_label = tip.get("linkLabel") if isinstance(tip.get("linkLabel"), str) and tip.get("linkLabel").strip() else "Open short on YouTube"
                if isinstance(tip.get("linkUrl"), str) and tip.get("linkUrl").strip():
                    link_url = tip.get("linkUrl")
                elif isinstance(tip.get("url"), str) and tip.get("url").strip():
                    link_url = tip.get("url")
                else:
                    link_url = f"https://www.youtube.com/shorts/{video_id}"
                normalized.append(
                    {
                        "type": "short",
                        "title": title.strip(),
                        "text": text.strip(),
                        "videoId": video_id,
                        "linkLabel": link_label.strip(),
                        "linkUrl": link_url.strip(),
                    }
                )
                continue

            link_label = tip.get("linkLabel")
            link_url = tip.get("linkUrl") or tip.get("url")
            if all(isinstance(v, str) and v.strip() for v in (link_label, link_url)):
                normalized_tip = {
                    "type": tip_type,
                    "title": title.strip(),
                    "text": text.strip(),
                    "linkLabel": link_label.strip(),
                    "linkUrl": link_url.strip(),
                }
                if tip_type == "video":
                    video_id = _extract_video_id(str(tip.get("videoId") or link_url or tip.get("watchUrl") or tip.get("embedUrl") or ""))
                    if video_id:
                        normalized_tip["videoId"] = video_id
                normalized.append(
                    normalized_tip
                )

        if not normalized:
            return {**fallback, "warning": "No valid tips in JSON file"}

        return {"tips": normalized, "source": "file"}
    except Exception as exc:
        return {**fallback, "warning": f"Failed to load tips file: {exc}"}


def _call_langdock_script(description: str, model_override: str | None = None) -> str:
    """Call LangDock to generate a Playwright script. Returns script text."""
    api_key = os.environ.get("LANGDOCK_API_KEY", "")
    if not api_key:
        raise RuntimeError("LANGDOCK_API_KEY is not set")

    model = (model_override or os.environ.get("LANGDOCK_MODEL", "gpt-5-mini")).strip()
    timeout_seconds = int(os.environ.get("BROWSER_RECORDER_LANGDOCK_TIMEOUT", "180"))
    retries = int(os.environ.get("BROWSER_RECORDER_LANGDOCK_RETRIES", "2"))
    max_tokens = int(os.environ.get("BROWSER_RECORDER_LANGDOCK_MAX_TOKENS", "6000"))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PLAYWRIGHT_SYSTEM_PROMPT},
            {"role": "user", "content": description},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    payload_json = json.dumps(payload).encode()

    req = urllib.request.Request(
        LANGDOCK_ENDPOINT,
        data=payload_json,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    def _extract_choice_content(body: dict) -> str:
        choices = body.get("choices") or []
        if not choices:
            return ""

        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        raw_content = message.get("content")

        # Most common OpenAI-compatible shape.
        if isinstance(raw_content, str):
            return raw_content.strip()

        # Some providers return content as typed chunks.
        if isinstance(raw_content, list):
            parts: list[str] = []
            for part in raw_content:
                if isinstance(part, str):
                    parts.append(part)
                    continue
                if isinstance(part, dict):
                    text_part = part.get("text")
                    if isinstance(text_part, str):
                        parts.append(text_part)
            return "\n".join(p for p in parts if p).strip()

        # Older completion-like shape fallback.
        text_fallback = choice.get("text")
        if isinstance(text_fallback, str):
            return text_fallback.strip()

        return ""

    last_error = None
    for attempt in range(1, retries + 2):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=timeout_seconds) as resp:
                resp_body = resp.read()
                body = json.loads(resp_body.decode("utf-8"))

                if "choices" not in body:
                    raise KeyError("'choices' not in response")

                content = _extract_choice_content(body)
                if not content:
                    last_error = RuntimeError(
                        "LangDock returned an empty script. Try a more specific prompt or retry."
                    )
                    if attempt <= retries:
                        backoff = min(2 * attempt, 5)
                        time.sleep(backoff)
                        continue
                    raise last_error

                return content
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code >= 500 and attempt <= retries:
                last_error = RuntimeError(f"LangDock API returned {e.code}: {error_body}")
                backoff = min(2 * attempt, 5)
                time.sleep(backoff)
                continue
            raise RuntimeError(f"LangDock API returned {e.code}: {error_body}")
        except urllib.error.URLError as e:
            last_error = RuntimeError(f"Network error connecting to LangDock: {e.reason}")
            if attempt <= retries:
                backoff = min(2 * attempt, 5)
                time.sleep(backoff)
                continue
            raise last_error
        except socket.timeout as e:
            last_error = RuntimeError(
                f"Request to LangDock timed out after {timeout_seconds}s"
            )
            if attempt <= retries:
                backoff = min(2 * attempt, 5)
                time.sleep(backoff)
                continue
            raise last_error
        except Exception:
            raise

    if last_error:
        raise last_error
    raise RuntimeError("LangDock request failed unexpectedly")


def _start_recording_job(job_id: str, job_dir: Path, script_path: Path):
    """Start a background recording job."""
    _recording_jobs[job_id] = {
        "status": "running",
        "log": [],
        "mp4_filename": None,
        "error": None,
    }

    def run():
        entry = _recording_jobs[job_id]
        try:
            # ── 1. Run the Playwright script ─────────────────────────────
            proc = subprocess.Popen(
                [sys.executable, str(script_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(job_dir),
            )
            try:
                for line in proc.stdout:
                    entry["log"].append(line.rstrip())
                proc.wait(timeout=300)  # 5-minute hard cap
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError("Script exceeded the 5-minute time limit.")

            if proc.returncode != 0:
                combined_log = "\n".join(entry["log"][-120:]).lower()
                if "host system is missing dependencies" in combined_log:
                    raise RuntimeError(
                        "Playwright system dependencies are missing. Use 'Install automatically' in Step 1, then try again."
                    )
                raise RuntimeError(f"Script exited with code {proc.returncode}")

            # ── 2. Find the WebM file ────────────────────────────────────
            webm_files = list(job_dir.glob("*.webm"))
            if not webm_files:
                raise RuntimeError(
                    "Script ran successfully but no .webm recording was found."
                )
            ranked_webm = sorted(
                webm_files,
                key=lambda p: (p.stat().st_size, p.stat().st_mtime),
                reverse=True,
            )
            webm_path = ranked_webm[0]
            file_summaries = ", ".join(
                f"{p.name} ({p.stat().st_size // 1024} KB)" for p in ranked_webm
            )
            entry["log"].append(
                f"[recorder] Found {len(ranked_webm)} WebM file(s): {file_summaries}"
            )
            if len(ranked_webm) > 1:
                entry["log"].append(
                    f"[recorder] Using largest recording candidate: {webm_path.name}"
                )
            else:
                entry["log"].append(f"[recorder] Video captured: {webm_path.name}")

            # ── 3. Convert WebM → MP4 via ffmpeg ────────────────────────
            mp4_path = job_dir / "recording.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(webm_path),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                str(mp4_path),
            ]
            entry["log"].append("[recorder] Converting to MP4…")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg conversion failed: {result.stderr[-500:]}")

            # Clean up raw WebM outputs so each job folder keeps only the final MP4.
            for raw in webm_files:
                try:
                    raw.unlink(missing_ok=True)
                except Exception:
                    pass

            entry["log"].append(
                f"[recorder] MP4 ready: recordings/{job_dir.name}/recording.mp4"
            )
            entry["mp4_filename"] = f"{job_dir.name}/recording.mp4"
            entry["status"] = "done"

        except Exception as exc:
            entry["error"] = str(exc)
            entry["log"].append(f"[recorder] ERROR: {exc}")
            _cleanup_failed_recording_job_dir(job_dir)
            entry["log"].append("[recorder] Cleaned up failed recording files.")
            entry["status"] = "error"

    threading.Thread(target=run, daemon=True).start()


# ── Browser Recorder Endpoints ────────────────────────────────────────────────


class BrowserRecorderGenerateRequest(BaseModel):
    description: str
    job_id: str
    model: str | None = None


class BrowserRecorderRunRequest(BaseModel):
    job_id: str
    script: str


class BrowserRecorderRunScriptRequest(BaseModel):
    filename: str


@app.get("/browser-recorder/check-install")
async def browser_recorder_check_install():
    """Check if Playwright and Chromium are installed."""
    import importlib.util
    pkg_ok = importlib.util.find_spec("playwright") is not None
    browser_ok = _chromium_binary_exists()
    deps_ok = False
    deps_detail = "not checked"
    if pkg_ok and browser_ok:
        deps_ok, deps_detail = _playwright_runtime_dependencies_ok()

    return JSONResponse(
        {
            "pkg_ok": pkg_ok,
            "browser_ok": browser_ok,
            "deps_ok": deps_ok,
            "deps_detail": deps_detail,
        }
    )


@app.get("/browser-recorder/models")
async def browser_recorder_models():
    """Return available LangDock models plus a fast default selection."""
    return JSONResponse(_list_langdock_models())


@app.get("/browser-recorder/wait-tips")
async def browser_recorder_wait_tips():
    """Return externalized wait-tip content for the browser recorder modal."""
    return JSONResponse(_load_browser_recorder_wait_tips())


@app.get("/browser-recorder/install")
async def browser_recorder_install():
    """Stream installation of Playwright and Chromium."""

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _emit(type_: str, **kwargs):
            loop.call_soon_threadsafe(queue.put_nowait, {"type": type_, **kwargs})

        def run():
            cmds = [
                [sys.executable, "-m", "pip", "install", "playwright"],
                [sys.executable, "-m", "playwright", "install-deps"],
                ["playwright", "install", "chromium"],
            ]
            for cmd in cmds:
                _emit("log", line=f"\n$ {' '.join(cmd)}\n", is_cmd=True)
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    for line in proc.stdout:
                        # Emit each line to preserve formatting
                        _emit("log", line=line.rstrip())
                    proc.wait(timeout=300)
                    if proc.returncode != 0:
                        _emit(
                            "error",
                            detail=f"Command failed (exit {proc.returncode}): {' '.join(cmd)}",
                        )
                        return
                except Exception as exc:
                    _emit("error", detail=str(exc))
                    return
            _emit("done")

        threading.Thread(target=run, daemon=True).start()
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/browser-recorder/generate")
async def browser_recorder_generate(data: BrowserRecorderGenerateRequest):
    """Generate a Playwright script from a description."""
    if not data.description.strip():
        raise HTTPException(status_code=422, detail="Description is required")

    try:
        requested_model = (data.model or "").strip() or None
        script_text = _call_langdock_script(data.description, requested_model)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {str(exc)}")

    job_dir = _job_dir(data.job_id)
    safe_script_name = re.sub(r"[^a-zA-Z0-9_-]", "_", data.job_id) + ".py"
    saved_script_path = (PLAYWRIGHT_SCRIPTS_DIR / safe_script_name).resolve()
    if not str(saved_script_path).startswith(str(PLAYWRIGHT_SCRIPTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid script path")
    saved_script_path.write_text(script_text, encoding="utf-8")

    return JSONResponse(
        {
            "job_id": data.job_id,
            "script": script_text,
            "saved_script_filename": safe_script_name,
        }
    )


@app.post("/browser-recorder/run")
async def browser_recorder_run(data: BrowserRecorderRunRequest):
    """Save a script and start recording."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", data.job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")
    if not data.script.strip():
        raise HTTPException(status_code=400, detail="Script is required")

    job_dir = _job_dir(data.job_id)
    script_path = job_dir / "record.py"
    script_path.write_text(data.script, encoding="utf-8")

    # Keep an up-to-date reusable copy in playwright_scripts/.
    safe_script_name = re.sub(r"[^a-zA-Z0-9_-]", "_", data.job_id) + ".py"
    saved_script_path = (PLAYWRIGHT_SCRIPTS_DIR / safe_script_name).resolve()
    if str(saved_script_path).startswith(str(PLAYWRIGHT_SCRIPTS_DIR.resolve())):
        saved_script_path.write_text(data.script, encoding="utf-8")

    _start_recording_job(data.job_id, job_dir, script_path)
    return JSONResponse({"status": "started"})


@app.get("/browser-recorder/stream/{job_id}")
async def browser_recorder_stream(job_id: str):
    """SSE stream for recording progress."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", job_id)
    if safe != job_id:
        raise HTTPException(status_code=400, detail="Invalid job_id")

    async def event_stream():
        sent = 0
        while True:
            entry = _recording_jobs.get(job_id)
            if entry is None:
                yield f"data: {json.dumps({'type': 'error', 'detail': 'Unknown job'})}\n\n"
                return

            # Drain any new log lines
            log = entry["log"]
            while sent < len(log):
                yield f"data: {json.dumps({'type': 'log', 'line': log[sent]})}\n\n"
                sent += 1

            if entry["status"] == "done":
                yield f"data: {json.dumps({'type': 'done', 'mp4_filename': entry['mp4_filename']})}\n\n"
                return
            if entry["status"] == "error":
                yield f"data: {json.dumps({'type': 'error', 'detail': entry['error']})}\n\n"
                return

            await asyncio.sleep(0.25)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/browser-recorder/list-scripts")
async def list_playwright_scripts():
    """List available drop-in scripts."""
    PLAYWRIGHT_SCRIPTS_DIR.mkdir(exist_ok=True)
    scripts = sorted(
        f.name
        for f in PLAYWRIGHT_SCRIPTS_DIR.iterdir()
        if f.is_file() and f.suffix == ".py"
    )
    return JSONResponse({"scripts": scripts})


@app.post("/browser-recorder/run-script")
async def browser_recorder_run_script(data: BrowserRecorderRunScriptRequest):
    """Run a drop-in script."""
    name = data.filename
    if "/" in name or "\\" in name or not name.endswith(".py"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    src_path = (PLAYWRIGHT_SCRIPTS_DIR / name).resolve()
    if not str(src_path).startswith(str(PLAYWRIGHT_SCRIPTS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="Script not found")

    job_id = str(uuid.uuid4())
    job_dir = _job_dir(job_id)

    # Copy the drop-in script into the job dir as record.py
    shutil.copy2(src_path, job_dir / "record.py")

    _start_recording_job(job_id, job_dir, job_dir / "record.py")
    return JSONResponse({"status": "started", "job_id": job_id})


@app.get("/download-recording/{job_id}/recording.mp4")
async def download_recording(job_id: str):
    """Download a recording MP4."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", job_id)
    mp4_path = (RECORDINGS_DIR / safe / "recording.mp4").resolve()
    # Path traversal guard
    if not str(mp4_path).startswith(str(RECORDINGS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not mp4_path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(mp4_path, media_type="video/mp4", filename="recording.mp4")



@app.get("/browser-recorder", response_class=HTMLResponse)
async def browser_recorder_page():
    """Serve the Browser Recorder page."""
    return HTMLResponse(build_standard_tool_page(
        page_title="Browser Recorder – DevRel Toolbox",
        page_heading="Browser Recorder",
        body_html=BROWSER_RECORDER_BODY_HTML,
        script_html=BROWSER_RECORDER_SCRIPT_JS,
        extra_css=BROWSER_RECORDER_EXTRA_CSS,
    ))


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the HTML UI"""
    return HTML_CONTENT


@app.get("/color-picker", response_class=HTMLResponse)
async def get_color_picker():
    """Serve the standalone Dynatrace core color picker page."""
    return COLOR_PICKER_HTML


@app.get("/wordlist-manager", response_class=HTMLResponse)
async def get_wordlist_manager():
    """Serve the standalone Wordlist Manager page."""
    return WORDLIST_MANAGER_HTML


@app.get("/code-cards", response_class=HTMLResponse)
async def get_code_cards():
    """Serve the standalone Code Cards page."""
    return HTMLResponse(
        content=CODE_CARDS_HTML,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

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

    def _ffmpeg_error_tail(stderr: str, max_lines: int = 12) -> str:
        # ffmpeg often prints a long banner first; keep the tail where the actionable error is.
        lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
        if not lines:
            return "Unknown ffmpeg error"
        return "\n".join(lines[-max_lines:])

    base_fc, combined = _build_trim_concat(ranges)

    with tempfile.TemporaryDirectory() as tmpdir:
        palette_path = Path(tmpdir) / "palette.png"

        # Pass 1 – generate an optimised colour palette
        fc1 = (
            f"{base_fc};"
            f"{combined}fps=15,scale=640:-1:flags=lanczos,format=rgb24,"
            "palettegen=stats_mode=diff[p]"
        )
        cmd1 = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-filter_complex", fc1, "-map", "[p]", str(palette_path),
        ]
        r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=180)
        if r1.returncode != 0:
            logger.warning(
                "Palette generation failed, falling back to single-pass GIF rendering: %s",
                _ffmpeg_error_tail(r1.stderr),
            )

            # Fallback path: generate GIF directly without a precomputed palette.
            fc_fallback = f"{base_fc};{combined}fps=15,scale=640:-1:flags=lanczos[out]"
            cmd_fallback = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-filter_complex", fc_fallback, "-map", "[out]", str(gif_path),
            ]
            r_fallback = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=180)
            if r_fallback.returncode != 0:
                raise RuntimeError(
                    "GIF palette generation failed and fallback generation failed:\n"
                    f"{_ffmpeg_error_tail(r_fallback.stderr)}"
                )
            return

        # Pass 2 – apply palette to produce the final GIF
        fc2 = (
            f"{base_fc};{combined}fps=15,scale=640:-1:flags=lanczos,format=rgb24[scaled];"
            "[scaled][1:v]paletteuse=dither=bayer:bayer_scale=5[out]"
        )
        cmd2 = [
            "ffmpeg", "-y",
            "-i", str(video_path), "-i", str(palette_path),
            "-filter_complex", fc2, "-map", "[out]", str(gif_path),
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=180)
        if r2.returncode != 0:
            raise RuntimeError(f"GIF generation failed:\n{_ffmpeg_error_tail(r2.stderr)}")


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


NAVIGATOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dynatrace DevRel Toolbox - Video Navigator</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'DT Flow', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
            min-height: 100vh;
        }

        header {
            background: rgba(0,0,0,0.25);
            backdrop-filter: blur(8px);
            padding: 16px 32px;
            display: flex;
            align-items: center;
            gap: 16px;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        header svg { width: 32px; height: 32px; fill: #fff; flex-shrink: 0; }

        header .titles { flex: 1; display: flex; flex-direction: column; line-height: 1.1; }

        header .brand {
            font-size: 11px; font-weight: 600;
            letter-spacing: 0.1em; text-transform: uppercase;
            color: rgba(255,255,255,0.65);
        }

        header h1 { font-size: 18px; font-weight: 700; color: #fff; }

        header a.back {
            color: rgba(255,255,255,0.75); text-decoration: none;
            font-size: 13px; font-weight: 500;
            padding: 5px 14px;
            border: 1px solid rgba(255,255,255,0.35);
            border-radius: 20px; transition: background 0.2s;
        }
        header a.back:hover { background: rgba(255,255,255,0.15); }

        .search-wrap {
            padding: 24px 32px 0;
            max-width: 1400px;
            margin: 0 auto;
        }

        .search-wrap input {
            width: 100%;
            padding: 14px 20px;
            font-size: 17px;
            border: none;
            border-radius: 10px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            outline: none;
        }
        .search-wrap input:focus {
            box-shadow: 0 4px 20px rgba(0,0,0,0.3), 0 0 0 3px rgba(255,255,255,0.4);
        }

        .meta-row {
            max-width: 1400px;
            margin: 10px auto 0;
            padding: 0 32px;
            font-size: 13px;
            color: rgba(255,255,255,0.7);
        }

        .grid {
            max-width: 1400px;
            margin: 18px auto 48px;
            padding: 0 32px;
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
        }

        @media (max-width: 1200px) { .grid { grid-template-columns: repeat(4, 1fr); } }
        @media (max-width: 900px)  { .grid { grid-template-columns: repeat(3, 1fr); } }
        @media (max-width: 600px)  { .grid { grid-template-columns: repeat(2, 1fr); gap: 10px; } }
        @media (max-width: 380px)  { .grid { grid-template-columns: 1fr; } }
        @media (max-width: 600px)  { .search-wrap, .meta-row, .grid { padding: 0 12px; } .search-wrap { padding-top: 16px; } }

        .card {
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 3px 12px rgba(0,0,0,0.15);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform 0.15s, box-shadow 0.15s;
        }
        .card:hover { transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,0.22); }

        .card-thumb {
            position: relative;
            width: 100%;
            padding-top: 56.25%; /* 16:9 */
            background: #000;
            overflow: hidden;
            flex-shrink: 0;
        }
        .card-thumb img {
            position: absolute;
            inset: 0; width: 100%; height: 100%;
            object-fit: cover;
        }
        .card-thumb .duration {
            position: absolute;
            bottom: 5px; right: 5px;
            background: rgba(0,0,0,0.82);
            color: #fff; font-size: 11px; font-weight: 600;
            padding: 1px 5px; border-radius: 3px;
        }

        .card-body { padding: 10px 12px 12px; flex: 1; display: flex; flex-direction: column; gap: 8px; }

        .card-title {
            font-size: 13px; font-weight: 700;
            color: #1a1a1a; line-height: 1.35;
            text-decoration: none;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .card-title:hover { color: #1966FF; }

        .chapters { display: flex; flex-direction: column; gap: 3px; }

        .chapter-link {
            display: flex; align-items: center; gap: 5px;
            font-size: 11.5px; color: #555;
            text-decoration: none; padding: 2px 0;
            transition: color 0.15s;
            min-width: 0;
        }
        .chapter-link:hover { color: #1966FF; }
        .chapter-link .ts {
            font-size: 10px; font-weight: 700;
            color: #fff; background: #1966FF;
            padding: 1px 5px; border-radius: 3px;
            flex-shrink: 0;
        }
        .chapter-link .ch-title {
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }

        .mark { background: #fff3cd; border-radius: 2px; padding: 0 1px; }

        .no-results, .empty-index {
            grid-column: 1 / -1;
            text-align: center;
            color: rgba(255,255,255,0.85);
            font-size: 17px;
            padding: 60px 0;
        }
    </style>
</head>
<body>
    <header>
        <svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-label="Dynatrace">
            <path d="M9.372 0c-.31.006-.93.09-1.521.654-.872.824-5.225 4.957-6.973 6.617-.79.754-.72 1.595-.72 1.664v.377c.067-.292.187-.5.427-.825.496-.616 1.3-.788 1.627-.822a64.238 64.238 0 01.002 0 64.238 64.238 0 016.528-.55c4.335-.136 7.197.226 7.197.226l6.085-5.794s-3.188-.6-6.82-1.027a93.4 93.4 0 00-5.64-.514c-.02 0-.09-.008-.192-.006zm13.56 2.508l-6.066 5.79s.222 2.881-.137 7.2c-.189 2.45-.584 4.866-.875 6.494-.052.326-.256 1.114-.925 1.594-.29.198-.49.295-.748.363 1.546-.51 1.091-7.047 1.091-7.047-4.335.137-7.214-.223-7.214-.223l-6.085 5.793s3.223.634 6.856 1.045c2.056.24 4.833.429 5.227.463.023 0 .045-.007.068-.012-.013.003-.022.009-.035.012.138 0 .26.015.38.015.084 0 .924.105 1.712-.648 1.748-1.663 6.084-5.81 6.94-6.634.789-.754.72-1.594.72-1.68a81.846 81.846 0 00-.206-5.654 101.75 101.75 0 00-.701-6.872zM3.855 8.306c-1.73.002-3.508.208-3.696 1.021.017 1.216.05 3.137.205 5.28.24 3.65.703 6.887.703 6.887l6.083-5.79c-.017.016-.24-2.88.12-7.2 0 0-1.684-.201-3.416-.2z"/>
        </svg>
        <div class="titles">
            <span class="brand">Dynatrace</span>
            <h1>Video Navigator</h1>
        </div>
        <a href="/" class="back">&larr; Back to Toolbox</a>
    </header>

    <div class="search-wrap">
        <input type="search" id="searchInput" placeholder="Search videos and chapters&hellip;" autocomplete="off" autofocus spellcheck="false">
    </div>
    <div class="meta-row" style="margin-top: 8px;">
        Need to refresh indexed content? See <a href="/docs/index-refresh" style="color:#fff;font-weight:700; text-decoration:underline;">Index Refresh Docs</a>.
    </div>
    <div class="meta-row" id="metaRow"></div>
    <div class="grid" id="grid"></div>

    <script>
        let allVideos = [];

        function escapeHtml(s) {
            return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function highlight(text, q) {
            if (!q) return escapeHtml(text);
            const esc = q.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
            return escapeHtml(text).replace(new RegExp('(' + esc + ')', 'gi'), '<span class="mark">$1</span>');
        }

        function renderResults(query) {
            const q = query.trim().toLowerCase();
            const grid = document.getElementById('grid');
            const meta = document.getElementById('metaRow');

            if (allVideos.length === 0) {
                grid.innerHTML = '<div class="empty-index">No videos indexed yet. Use the <a href="/" style="color:#fff;font-weight:600;">DevRel Toolbox</a> to build the channel index.</div>';
                meta.textContent = '';
                return;
            }

            let matchCount = 0;
            const html = [];

            for (const video of allVideos) {
                const titleHit = !q || video.title.toLowerCase().includes(q);
                const matchingChapters = q
                    ? video.chapters.filter(c => c.title.toLowerCase().includes(q) || titleHit)
                    : video.chapters;
                const hasMatch = !q || titleHit || video.chapters.some(c => c.title.toLowerCase().includes(q));
                if (!hasMatch) continue;
                matchCount++;

                const ytBase = 'https://www.youtube.com/watch?v=' + video.id;
                const thumb = 'https://img.youtube.com/vi/' + video.id + '/hqdefault.jpg';

                const chaptersHtml = matchingChapters.map(c => {
                    const url = ytBase + '&t=' + c.seconds + 's';
                    return '<a href="' + url + '" target="_blank" rel="noopener" class="chapter-link">'
                        + '<span class="ts">' + escapeHtml(c.time) + '</span>'
                        + '<span class="ch-title">' + highlight(c.title, q) + '</span>'
                        + '</a>';
                }).join('');

                html.push(
                    '<div class="card">'
                    + '<a href="' + ytBase + '" target="_blank" rel="noopener" class="card-thumb">'
                    + '<img src="' + thumb + '" alt="" loading="lazy">'
                    + (video.duration ? '<span class="duration">' + escapeHtml(video.duration) + '</span>' : '')
                    + '</a>'
                    + '<div class="card-body">'
                    + '<a href="' + ytBase + '" target="_blank" rel="noopener" class="card-title">' + highlight(video.title, q) + '</a>'
                    + '<div class="chapters">' + chaptersHtml + '</div>'
                    + '</div>'
                    + '</div>'
                );
            }

            if (q && html.length === 0) {
                grid.innerHTML = '<div class="no-results">No results for &ldquo;' + escapeHtml(query) + '&rdquo;</div>';
            } else {
                grid.innerHTML = html.join('');
            }

            meta.textContent = q
                ? matchCount + ' of ' + allVideos.length + ' video' + (allVideos.length !== 1 ? 's' : '')
                : allVideos.length + ' video' + (allVideos.length !== 1 ? 's' : '');
        }

        document.getElementById('searchInput').addEventListener('input', e => renderResults(e.target.value));

        fetch('/api/channel-index')
            .then(r => r.json())
            .then(data => { allVideos = data.videos || []; renderResults(''); });
    </script>
</body>
</html>"""


BLOG_NAVIGATOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dynatrace DevRel Toolbox - Blog Navigator</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'DT Flow', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
            min-height: 100vh;
        }

        header {
            background: rgba(0,0,0,0.25);
            backdrop-filter: blur(8px);
            padding: 16px 32px;
            display: flex;
            align-items: center;
            gap: 16px;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        header svg { width: 32px; height: 32px; fill: #fff; flex-shrink: 0; }

        header .titles { flex: 1; display: flex; flex-direction: column; line-height: 1.1; }

        header .brand {
            font-size: 11px; font-weight: 600;
            letter-spacing: 0.1em; text-transform: uppercase;
            color: rgba(255,255,255,0.65);
        }

        header h1 { font-size: 18px; font-weight: 700; color: #fff; }

        header a.back {
            color: rgba(255,255,255,0.75); text-decoration: none;
            font-size: 13px; font-weight: 500;
            padding: 5px 14px;
            border: 1px solid rgba(255,255,255,0.35);
            border-radius: 20px; transition: background 0.2s;
        }
        header a.back:hover { background: rgba(255,255,255,0.15); }

        .search-wrap {
            padding: 24px 32px 0;
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .search-wrap input {
            width: 100%;
            padding: 14px 20px;
            font-size: 17px;
            border: none;
            border-radius: 10px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            outline: none;
        }

        .search-wrap button {
            width: auto;
            padding: 12px 16px;
            border: none;
            border-radius: 10px;
            font-size: 14px;
            font-weight: 600;
            color: #fff;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
            cursor: pointer;
            white-space: nowrap;
        }

        .meta-row {
            max-width: 1400px;
            margin: 10px auto 0;
            padding: 0 32px;
            font-size: 13px;
            color: rgba(255,255,255,0.8);
        }

        .grid {
            max-width: 1400px;
            margin: 18px auto 48px;
            padding: 0 32px;
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
        }

        @media (max-width: 1200px) { .grid { grid-template-columns: repeat(4, 1fr); } }
        @media (max-width: 900px)  { .grid { grid-template-columns: repeat(3, 1fr); } }
        @media (max-width: 600px)  { .grid { grid-template-columns: repeat(2, 1fr); gap: 10px; } }
        @media (max-width: 380px)  { .grid { grid-template-columns: 1fr; } }
        @media (max-width: 700px)  { .search-wrap, .meta-row, .grid { padding: 0 12px; } .search-wrap { padding-top: 14px; } .search-wrap button { padding: 12px; } }

        .card {
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 3px 12px rgba(0,0,0,0.15);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform 0.15s, box-shadow 0.15s;
        }
        .card:hover { transform: translateY(-3px); box-shadow: 0 8px 24px rgba(0,0,0,0.22); }

        .card-thumb {
            position: relative;
            width: 100%;
            padding-top: 56.25%;
            background: #000;
            overflow: hidden;
            flex-shrink: 0;
        }

        .card-thumb img {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .card-thumb .blog-badge {
            position: absolute;
            bottom: 6px;
            right: 6px;
            background: rgba(0,0,0,0.7);
            color: #fff;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }

        .card-body {
            padding: 10px 12px 12px;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .card-title {
            font-size: 13px;
            font-weight: 700;
            color: #1a1a1a;
            text-decoration: none;
            line-height: 1.35;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .card-title:hover { color: #1966FF; }

        .meta {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            font-size: 11px;
            color: #666;
        }

        .tag {
            background: #e8f5fb;
            color: #17516a;
            border-radius: 999px;
            padding: 2px 7px;
            font-size: 10px;
            font-weight: 600;
            white-space: nowrap;
        }

        .summary {
            color: #444;
            font-size: 11.5px;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 4;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .mark { background: #fff3cd; border-radius: 2px; padding: 0 1px; }

        .no-results, .empty-index {
            grid-column: 1 / -1;
            text-align: center;
            color: rgba(255,255,255,0.9);
            font-size: 17px;
            padding: 60px 0;
        }
    </style>
</head>
<body>
    <header>
        <svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-label="Dynatrace">
            <path d="M9.372 0c-.31.006-.93.09-1.521.654-.872.824-5.225 4.957-6.973 6.617-.79.754-.72 1.595-.72 1.664v.377c.067-.292.187-.5.427-.825.496-.616 1.3-.788 1.627-.822a64.238 64.238 0 01.002 0 64.238 64.238 0 016.528-.55c4.335-.136 7.197.226 7.197.226l6.085-5.794s-3.188-.6-6.82-1.027a93.4 93.4 0 00-5.64-.514c-.02 0-.09-.008-.192-.006zm13.56 2.508l-6.066 5.79s.222 2.881-.137 7.2c-.189 2.45-.584 4.866-.875 6.494-.052.326-.256 1.114-.925 1.594-.29.198-.49.295-.748.363 1.546-.51 1.091-7.047 1.091-7.047-4.335.137-7.214-.223-7.214-.223l-6.085 5.793s3.223.634 6.856 1.045c2.056.24 4.833.429 5.227.463.023 0 .045-.007.068-.012-.013.003-.022.009-.035.012.138 0 .26.015.38.015.084 0 .924.105 1.712-.648 1.748-1.663 6.084-5.81 6.94-6.634.789-.754.72-1.594.72-1.68a81.846 81.846 0 00-.206-5.654 101.75 101.75 0 00-.701-6.872zM3.855 8.306c-1.73.002-3.508.208-3.696 1.021.017 1.216.05 3.137.205 5.28.24 3.65.703 6.887.703 6.887l6.083-5.79c-.017.016-.24-2.88.12-7.2 0 0-1.684-.201-3.416-.2z"/>
        </svg>
        <div class="titles">
            <span class="brand">Dynatrace</span>
            <h1>Blog Navigator</h1>
        </div>
        <a href="/" class="back">&larr; Back to Toolbox</a>
    </header>

    <div class="search-wrap">
        <input type="search" id="searchInput" placeholder="Search blogs by keyword, category, or summary..." autocomplete="off" autofocus spellcheck="false">
    </div>
    <div class="meta-row" style="margin-top: 8px;">
        Need to refresh indexed content? See <a href="/docs/index-refresh" style="color:#fff;font-weight:700; text-decoration:underline;">Index Refresh Docs</a>.
    </div>
    <div class="meta-row" id="metaRow"></div>
    <div class="grid" id="grid"></div>

    <script>
        let allBlogs = [];

        function escapeHtml(s) {
            return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function escapeAttr(s) {
            return escapeHtml(s).replace(/"/g, '&quot;');
        }

        function highlight(text, q) {
            if (!q) return escapeHtml(text);
            const esc = q.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
            return escapeHtml(text).replace(new RegExp('(' + esc + ')', 'gi'), '<span class="mark">$1</span>');
        }

        function renderResults(query) {
            const q = query.trim().toLowerCase();
            const grid = document.getElementById('grid');
            const meta = document.getElementById('metaRow');

            if (allBlogs.length === 0) {
                grid.innerHTML = '<div class="empty-index">No blog entries indexed yet. Click Refresh to fetch the latest posts.</div>';
                meta.textContent = '';
                return;
            }

            let matchCount = 0;
            const html = [];

            for (const blog of allBlogs) {
                const haystack = [
                    blog.title || '',
                    blog.summary || '',
                    (blog.categories || []).join(' '),
                    blog.published || ''
                ].join(' ').toLowerCase();

                if (q && !haystack.includes(q)) continue;
                matchCount++;

                const tags = (blog.categories || []).slice(0, 4).map(c => '<span class="tag">' + highlight(c, q) + '</span>').join('');
                const published = blog.published ? '<span>Published: ' + escapeHtml(blog.published) + '</span>' : '';
                const image = blog.image_url || '';
                const thumbInner = image
                    ? '<img src="' + escapeAttr(image) + '" alt="" loading="lazy">'
                    : '<div style="position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:rgba(255,255,255,0.8); font-size:12px;">No image</div>';

                html.push(
                    '<div class="card">'
                    + '<a href="' + escapeAttr(blog.url || '#') + '" target="_blank" rel="noopener" class="card-thumb">'
                    + thumbInner
                    + '<span class="blog-badge">Blog</span>'
                    + '</a>'
                    + '<div class="card-body">'
                    + '<a href="' + escapeAttr(blog.url || '#') + '" target="_blank" rel="noopener" class="card-title">' + highlight(blog.title || 'Untitled post', q) + '</a>'
                    + '<div class="meta">' + published + tags + '</div>'
                    + '<p class="summary">' + highlight(blog.summary || 'No summary available.', q) + '</p>'
                    + '</div>'
                    + '</div>'
                );
            }

            if (q && html.length === 0) {
                grid.innerHTML = '<div class="no-results">No results for &ldquo;' + escapeHtml(query) + '&rdquo;</div>';
            } else {
                grid.innerHTML = html.join('');
            }

            meta.textContent = q
                ? matchCount + ' of ' + allBlogs.length + ' blog post' + (allBlogs.length !== 1 ? 's' : '')
                : allBlogs.length + ' blog post' + (allBlogs.length !== 1 ? 's' : '');
        }

        async function loadBlogs() {
            try {
                const resp = await fetch('/api/blog-index');
                const data = await resp.json();
                allBlogs = data.blogs || [];
                renderResults(document.getElementById('searchInput').value || '');
            } catch (error) {
                const grid = document.getElementById('grid');
                grid.innerHTML = '<div class="no-results">Failed to load blog index: ' + escapeHtml(error.message) + '</div>';
            }
        }

        document.getElementById('searchInput').addEventListener('input', e => renderResults(e.target.value));
        loadBlogs();
    </script>
</body>
</html>"""


INDEX_REFRESH_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dynatrace DevRel Toolbox - Index Refresh Docs</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'DT Flow', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1966FF 0%, #5E29E5 100%);
            min-height: 100vh;
            color: #10224f;
        }

        header {
            background: rgba(0,0,0,0.25);
            backdrop-filter: blur(8px);
            padding: 16px 32px;
            display: flex;
            align-items: center;
            gap: 16px;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        header svg {
            width: 32px;
            height: 32px;
            fill: #fff;
            flex-shrink: 0;
        }

        .titles {
            flex: 1;
            display: flex;
            flex-direction: column;
            line-height: 1.1;
        }

        .brand {
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.65);
        }

        header h1 {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
        }

        .back {
            color: rgba(255,255,255,0.75);
            text-decoration: none;
            font-size: 13px;
            font-weight: 500;
            padding: 5px 14px;
            border: 1px solid rgba(255,255,255,0.35);
            border-radius: 20px;
            transition: background 0.2s;
            white-space: nowrap;
        }
        .back:hover { background: rgba(255,255,255,0.15); }

        .docs-wrap {
            max-width: 980px;
            margin: 24px auto 40px;
            padding: 0 16px;
        }

        .shell {
            background: rgba(255,255,255,0.96);
            border-radius: 14px;
            border: 1px solid rgba(255,255,255,0.65);
            box-shadow: 0 18px 40px rgba(0,0,0,0.22);
            overflow: hidden;
        }

        .content {
            padding: 20px 24px 26px;
            display: grid;
            gap: 18px;
        }

        .docs-top {
            padding: 20px 24px;
            background: linear-gradient(135deg, #e9f1ff 0%, #f3efff 100%);
            border-bottom: 1px solid #d7e2ff;
        }

        .docs-top h2 {
            margin: 0;
            font-size: 20px;
            color: #1e2f6b;
        }

        .callout {
            background: #eef4ff;
            border: 1px solid #d3e1ff;
            border-radius: 10px;
            padding: 12px 14px;
            font-size: 14px;
            line-height: 1.5;
            color: #223d7a;
        }

        section {
            background: #fff;
            border: 1px solid #e6ecff;
            border-radius: 10px;
            padding: 14px;
        }

        h2 {
            font-size: 18px;
            color: #1e2f6b;
            margin-bottom: 10px;
        }

        ol {
            margin-left: 18px;
            line-height: 1.6;
            font-size: 14px;
        }

        li + li {
            margin-top: 6px;
        }

        pre {
            margin-top: 10px;
            background: #0f1f4a;
            color: #e7eeff;
            border-radius: 8px;
            padding: 11px 12px;
            overflow-x: auto;
            font-size: 13px;
            line-height: 1.45;
        }

        code {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
        }

        @media (max-width: 680px) {
            header {
                padding: 14px 12px;
            }

            .back {
                padding: 5px 10px;
            }

            .docs-wrap {
                margin-top: 14px;
                padding: 0 12px;
            }
        }
    </style>
</head>
<body>
    <header>
        <svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-label="Dynatrace">
            <path d="M9.372 0c-.31.006-.93.09-1.521.654-.872.824-5.225 4.957-6.973 6.617-.79.754-.72 1.595-.72 1.664v.377c.067-.292.187-.5.427-.825.496-.616 1.3-.788 1.627-.822a64.238 64.238 0 01.002 0 64.238 64.238 0 016.528-.55c4.335-.136 7.197.226 7.197.226l6.085-5.794s-3.188-.6-6.82-1.027a93.4 93.4 0 00-5.64-.514c-.02 0-.09-.008-.192-.006zm13.56 2.508l-6.066 5.79s.222 2.881-.137 7.2c-.189 2.45-.584 4.866-.875 6.494-.052.326-.256 1.114-.925 1.594-.29.198-.49.295-.748.363 1.546-.51 1.091-7.047 1.091-7.047-4.335.137-7.214-.223-7.214-.223l-6.085 5.793s3.223.634 6.856 1.045c2.056.24 4.833.429 5.227.463.023 0 .045-.007.068-.012-.013.003-.022.009-.035.012.138 0 .26.015.38.015.084 0 .924.105 1.712-.648 1.748-1.663 6.084-5.81 6.94-6.634.789-.754.72-1.594.72-1.68a81.846 81.846 0 00-.206-5.654 101.75 101.75 0 00-.701-6.872zM3.855 8.306c-1.73.002-3.508.208-3.696 1.021.017 1.216.05 3.137.205 5.28.24 3.65.703 6.887.703 6.887l6.083-5.79c-.017.016-.24-2.88.12-7.2 0 0-1.684-.201-3.416-.2z"/>
        </svg>
        <div class="titles">
            <span class="brand">Dynatrace</span>
            <h1>Index Refresh Docs</h1>
        </div>
        <a href="/" class="back">&larr; Back to Toolbox</a>
    </header>

    <main class="docs-wrap">
    <div class="shell">
        <div class="docs-top">
            <h2>How To Refresh Content Indexes</h2>
        </div>

        <div class="content">
            <div class="callout">
                Use these commands from a local devcontainer terminal in the workspace root. This updates the JSON index files consumed by Blog Navigator and Video Navigator.
            </div>

            <section>
                <h2>Update Blog Posts</h2>
                <ol>
                    <li>Open the devcontainer terminal in the project root.</li>
                    <li>Run the blog index updater command.</li>
                    <li>Reload Blog Navigator to confirm new posts are searchable.</li>
                </ol>
                <pre><code>python3 index_updater.py blog --base-dir .</code></pre>
            </section>

            <section>
                <h2>Update YouTube Videos</h2>
                <ol>
                    <li>Open the devcontainer terminal in the project root.</li>
                    <li>Run a batch update for the channel index (videos and shorts).</li>
                    <li>Reload Video Navigator and verify new videos and chapter links appear.</li>
                </ol>
                <pre><code>python3 index_updater.py video --base-dir . --batch-size 50</code></pre>
            </section>

            <section>
                <h2>Helpful Variants</h2>
                <pre><code># Rebuild all video index entries from scratch
python3 index_updater.py video --base-dir . --batch-size 9999 --force

# Limit blog crawler depth when testing
python3 index_updater.py blog --base-dir . --max-pages 5</code></pre>
            </section>
        </div>
    </div>
    </main>
</body>
</html>"""


@app.get("/navigator", response_class=HTMLResponse)
async def get_navigator():
    """Serve the standalone Channel Navigator page."""
    return NAVIGATOR_HTML


@app.get("/blog-navigator", response_class=HTMLResponse)
async def get_blog_navigator():
    """Serve the standalone Blog Navigator page."""
    return BLOG_NAVIGATOR_HTML


@app.get("/docs/index-refresh", response_class=HTMLResponse)
async def get_index_refresh_docs():
    """Serve maintainer docs for refreshing blog and video indexes."""
    return INDEX_REFRESH_DOCS_HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)