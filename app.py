import re
import ssl
import shutil
import time
import uvicorn
import asyncio
import threading
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
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

        <!-- Feature 6: Chapter Detector -->
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
                    ytSummaryContent.innerHTML = marked.parse(data.summary);
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
                    summaryContent.innerHTML = marked.parse(data.summary);
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
                    mp4SummaryContent.innerHTML = marked.parse(data.summary);
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
            <li><a href="https://cdn.dm.dynatrace.com/assets/documents/media-kit/dynatrace-logo-presskit.zip" target="_blank" rel="noopener">Dynatrace Logo Press Kit (ZIP)</a></li>
            <li><a href="https://live.standards.site/dynatrace/color" target="_blank" rel="noopener">Dynatrace Color Guidelines</a></li>
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
    min-height: 260px;
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
                return {
                    "status": "error",
                    "detail": f"Failed to download subtitles: {result.stderr}"
                }, 400

            logger.info(f"yt-dlp output: {result.stdout}")

            # Find the downloaded SRT file
            srt_files = list(SUBTITLES_DIR.glob("*.srt"))
            if not srt_files:
                logger.error("No SRT file was generated")
                return {
                    "status": "error",
                    "detail": "No subtitles found for this video. The video may not have automatic captions available."
                }, 400

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
        return {
            "status": "error",
            "detail": "Download timed out. Please try again."
        }, 400
    except Exception as e:
        logger.error(f"Error downloading subtitles: {str(e)}")
        return {
            "status": "error",
            "detail": f"Error: {str(e)}"
        }, 400

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download the subtitle file"""
    file_path = SUBTITLES_DIR / filename
    
    if not file_path.exists():
        return {"detail": "File not found"}, 404
    
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
                    subprocess.run(sub_cmd, capture_output=True, text=True, timeout=60)
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
        raise RuntimeError(f"Could not download subtitles: {result.stderr[:400]}")

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


@app.get("/navigator", response_class=HTMLResponse)
async def get_navigator():
    """Serve the standalone Channel Navigator page."""
    return NAVIGATOR_HTML


@app.get("/blog-navigator", response_class=HTMLResponse)
async def get_blog_navigator():
    """Serve the standalone Blog Navigator page."""
    return BLOG_NAVIGATOR_HTML


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)