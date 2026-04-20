/**
 * DevRel Toolbox - Form Handlers
 * Form submission logic for all features
 */

// ============================================================================
// FEATURE 1: YouTube Subtitle Downloader
// ============================================================================

const urlForm = document.getElementById('urlForm');
const message = document.getElementById('message');
const spinner = document.getElementById('spinner');
const submitBtn = document.getElementById('submitBtn');
const ytSummaryContainer = document.getElementById('ytSummaryContainer');
const ytSummaryContent = document.getElementById('ytSummaryContent');

urlForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const url = document.getElementById('youtubeUrl').value.trim();
    if (!url) return;

    hideMessage(message);
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
            showMessage(
                message,
                'message success',
                `✓ Subtitles downloaded and corrected! ${data.changes_count} change(s) made.<br><a href="${data.download_url}" class="download-link">📥 Download Corrected SRT</a>`
            );
            ytSummaryContent.innerHTML = renderMarkdownSafe(data.summary);
            ytSummaryContainer.style.display = 'block';
            urlForm.reset();
        } else {
            showMessage(message, 'message error', '✗ Error: ' + (data.detail || 'Failed to download subtitles'));
        }
    } catch (error) {
        showMessage(message, 'message error', '✗ Error: ' + error.message);
    } finally {
        spinner.style.display = 'none';
        submitBtn.disabled = false;
        submitBtn.textContent = 'Download Subtitles';
    }
});

// ============================================================================
// FEATURE 2: SRT Corrector
// ============================================================================

const srtForm = document.getElementById('srtForm');
const srtMessage = document.getElementById('srtMessage');
const srtSpinner = document.getElementById('srtSpinner');
const srtSubmitBtn = document.getElementById('srtSubmitBtn');
const summaryContainer = document.getElementById('summaryContainer');
const summaryContent = document.getElementById('summaryContent');

srtForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const fileInput = document.getElementById('srtFile');
    const file = fileInput.files[0];
    if (!file) return;

    hideMessage(srtMessage);
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
            showMessage(
                srtMessage,
                'message success',
                `✓ Correction complete! ${data.changes_count} change(s) made.<br><a href="${data.download_url}" class="download-link">📥 Download Corrected SRT</a>`
            );
            summaryContent.innerHTML = renderMarkdownSafe(data.summary);
            summaryContainer.style.display = 'block';
            srtForm.reset();
        } else {
            showMessage(srtMessage, 'message error', '✗ Error: ' + (data.detail || 'Failed to correct SRT'));
        }
    } catch (error) {
        showMessage(srtMessage, 'message error', '✗ Error: ' + error.message);
    } finally {
        srtSpinner.style.display = 'none';
        srtSubmitBtn.disabled = false;
        srtSubmitBtn.textContent = 'Correct SRT';
    }
});

// ============================================================================
// FEATURE 3: MP4 Transcriber
// ============================================================================

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

    hideMessage(mp4Message);
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
            showMessage(
                mp4Message,
                'message success',
                `\u2713 Transcription complete! ${data.changes_count} wordlist correction(s) applied.<br><a href="${data.download_url}" class="download-link">📥 Download Corrected SRT</a>`
            );
            mp4SummaryContent.innerHTML = renderMarkdownSafe(data.summary);
            mp4SummaryContainer.style.display = 'block';
            mp4Form.reset();
        } else {
            showMessage(mp4Message, 'message error', '\u2717 Error: ' + (data.detail || 'Transcription failed'));
        }
    } catch (error) {
        showMessage(mp4Message, 'message error', '\u2717 Error: ' + error.message);
    } finally {
        mp4Spinner.style.display = 'none';
        mp4SubmitBtn.disabled = false;
        mp4SubmitBtn.textContent = 'Transcribe';
    }
});

// ============================================================================
// FEATURE 4: Wordlist Manager
// ============================================================================

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

        hideMessage(wordlistMessage);
        wordlistSubmitBtn.disabled = true;

        try {
            const response = await fetch('/api/wordlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wrong, right })
            });
            const data = await response.json();
            if (response.ok) {
                showMessage(
                    wordlistMessage,
                    'message success',
                    `\u2713 Added: "${wrong}" \u2192 "${right}"${data.updated ? ' (updated existing entry)' : ''}`
                );
                wordlistForm.reset();
                loadWordlist();
            } else {
                showMessage(wordlistMessage, 'message error', '\u2717 Error: ' + (data.detail || 'Failed to update wordlist'));
            }
        } catch (error) {
            showMessage(wordlistMessage, 'message error', '\u2717 Error: ' + error.message);
        } finally {
            wordlistMessage.style.display = 'block';
            wordlistSubmitBtn.disabled = false;
        }
    });

    loadWordlist();
}

// ============================================================================
// FEATURE 7: Primary Brand Colors
// ============================================================================

const primaryColorStatus = document.getElementById('primaryColorStatus');
const primaryColorSwatches = document.querySelectorAll('.primary-color-swatch');

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
            await copyToClipboard(hex);
            setPrimaryColorStatus('success', 'Copied ' + name + ' ' + hex + ' to clipboard');
        } catch (error) {
            setPrimaryColorStatus('error', 'Copy failed: ' + error.message);
        }
    });
});

// ============================================================================
// FEATURE 5: Highlight Reel
// ============================================================================

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
    copyToClipboard(highlightSummary.value).then(() => {
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
        showMessage(highlightMessage, 'message error', '\u2717 Please enter at least one timestamp range');
        return;
    }

    hideMessage(highlightMessage);
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
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const event = JSON.parse(line.slice(6));
                if (event.type === 'status') {
                    if (currentStatusEl) {
                        currentStatusEl.innerHTML = '<span style="color:#28a745; font-weight:600;">&#10003;</span> ' + currentStatusEl.dataset.text;
                    }
                    currentStatusEl = addStatusLine(event.message);
                    currentStatusEl.dataset.text = event.message;
                } else if (event.type === 'done') {
                    if (currentStatusEl) {
                        currentStatusEl.innerHTML = '<span style="color:#28a745; font-weight:600;">&#10003;</span> ' + currentStatusEl.dataset.text;
                    }
                    showMessage(highlightMessage, 'message success', '\u2713 Highlight reel generated!');
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
        showMessage(highlightMessage, 'message error', '\u2717 Error: ' + error.message);
    } finally {
        highlightSpinner.style.display = 'none';
        highlightSubmitBtn.disabled = false;
        highlightSubmitBtn.textContent = 'Generate Highlight Reel';
    }
});

// ============================================================================
// FEATURE 6: Chapter Detector
// ============================================================================

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
    copyToClipboard(chapterOutput.value).then(() => {
        copyChaptersBtn.textContent = 'Copied!';
        setTimeout(() => { copyChaptersBtn.textContent = 'Copy'; }, 1500);
    });
});

chapterForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const mode = document.querySelector('input[name="chapterMode"]:checked').value;

    hideMessage(chapterMessage);
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
            const lines = buffer.split('\n');
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
                    showMessage(chapterMessage, 'message success', '\u2713 Chapters detected!');
                } else if (event.type === 'error') {
                    throw new Error(event.detail);
                }
            }
        }
    } catch (error) {
        if (currentChapterStatusEl) {
            currentChapterStatusEl.innerHTML = '<span style="color:#dc3545;">&#10007;</span> ' + currentChapterStatusEl.dataset.text;
        }
        showMessage(chapterMessage, 'message error', '\u2717 Error: ' + error.message);
    } finally {
        chapterSpinner.style.display = 'none';
        chapterSubmitBtn.disabled = false;
        chapterSubmitBtn.textContent = 'Detect Chapters';
    }
});

// ============================================================================
// FEATURE 8: Metadata Optimizer
// ============================================================================

const metadataForm = document.getElementById('metadataForm');
const metadataMessage = document.getElementById('metadataMessage');
const metadataSpinner = document.getElementById('metadataSpinner');
const metadataSubmitBtn = document.getElementById('metadataSubmitBtn');
const metadataResultContainer = document.getElementById('metadataResultContainer');
const metadataOutput = document.getElementById('metadataOutput');
const copyMetadataBtn = document.getElementById('copyMetadataBtn');

if (copyMetadataBtn) {
    copyMetadataBtn.addEventListener('click', () => {
        copyToClipboard(metadataOutput.value).then(() => {
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
            showMessage(metadataMessage, 'message error', '\u2717 Please enter both a YouTube URL and target keywords');
            return;
        }

        hideMessage(metadataMessage);
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

            const msg = data.subtitle_warning
                ? '\u2713 Recommendations generated. Note: ' + data.subtitle_warning
                : '\u2713 Recommendations generated';
            showMessage(metadataMessage, 'message success', msg);
        } catch (error) {
            showMessage(metadataMessage, 'message error', '\u2717 Error: ' + error.message);
        } finally {
            metadataSpinner.style.display = 'none';
            metadataSubmitBtn.disabled = false;
            metadataSubmitBtn.textContent = 'Investigate Metadata';
        }
    });
}

