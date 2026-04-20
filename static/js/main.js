/**
 * DevRel Toolbox - Main JavaScript
 * Global utilities, menu handling, and markdown rendering
 */

// ============================================================================
// HEADER MENU MANAGEMENT
// ============================================================================

const menuToggle = document.getElementById('menuToggle');
const toolMenu = document.getElementById('toolMenu');

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

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Safely render markdown content
 * Falls back to a message if input is invalid
 */
function renderMarkdownSafe(input) {
    if (typeof input !== 'string' || !input.trim()) {
        return '<p><em>No summary available.</em></p>';
    }
    return marked.parse(input);
}

/**
 * Copy text to clipboard with fallback for older browsers
 */
async function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    // Fallback for older browsers
    const helper = document.createElement('textarea');
    helper.value = text;
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

/**
 * Show/hide message with animation
 */
function showMessage(element, className, html) {
    element.className = className;
    element.innerHTML = html;
    element.style.display = 'block';
}

function hideMessage(element) {
    element.style.display = 'none';
    element.innerHTML = '';
}

/**
 * Update button state during async operations
 */
function setButtonLoading(button, isLoading, originalText, loadingText) {
    button.disabled = isLoading;
    button.textContent = isLoading ? loadingText : originalText;
}
