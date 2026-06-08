// Anti-detection JavaScript injected via page.addInitScript() before any page scripts run.
// Patches navigator, WebGL, and permissions APIs to mimic a real browser.
//
// Inspired by puppeteer-extra-plugin-stealth but minimal — only the APIs that
// headless Chromium (even real Chrome in headless mode) leaks through.

export const STEALTH_SCRIPT = `
// ── navigator.plugins ────────────────────────────────────────────────────────
// Real Chrome reports at least 3 plugins (PDF Viewer, PDF Plugin, Native Client).
// Headless Chromium reports plugins.length === 0, an instant detection flag.
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
        ];
        plugins.item = (i) => plugins[i] || null;
        plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
        plugins.refresh = () => {};
        Object.setPrototypeOf(plugins, PluginArray.prototype);
        return plugins;
    }
});

// ── navigator.languages ─────────────────────────────────────────────────────
// Some headless setups report empty or single-element language arrays.
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// ── navigator.hardwareConcurrency ────────────────────────────────────────────
// Docker containers sometimes report fewer cores than real machines.
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// ── WebGL vendor strings ─────────────────────────────────────────────────────
// Headless Chromium returns "Google Inc. (Google SwiftShader)" which is a dead
// giveaway. Real Chrome on real hardware returns GPU vendor strings like
// "Intel Inc." / "Intel Iris OpenGL Engine" or similar from AMD/NVIDIA.
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    // UNMASKED_VENDOR_WEBGL = 37445
    if (parameter === 37445) return 'Intel Inc.';
    // UNMASKED_RENDERER_WEBGL = 37446
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};

// ── navigator.permissions ───────────────────────────────────────────────────
// The Permissions API fails for 'notifications' in headless mode because
// there's no UI to grant permission. This patches the query to return a
// plausible state instead of throwing.
if (navigator.permissions && navigator.permissions.query) {
    const originalQuery = navigator.permissions.query;
    navigator.permissions.query = function(parameters) {
        if (parameters.name === 'notifications') {
            return Promise.resolve({
                state: Notification.permission,
                onchange: null
            });
        }
        return originalQuery.call(this, parameters);
    };
}

// ── chrome.runtime ──────────────────────────────────────────────────────────
// Headless Chromium has no extension support, so chrome.runtime is undefined.
// Many detection scripts check if chrome.runtime exists. We provide a stub.
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) window.chrome.runtime = {};
if (!window.chrome.runtime.connect) {
    window.chrome.runtime.connect = () => ({
        onMessage: { addListener: () => {} },
        onDisconnect: { addListener: () => {} },
        postMessage: () => {},
        disconnect: () => {}
    });
}
if (!window.chrome.runtime.sendMessage) {
    window.chrome.runtime.sendMessage = () => {};
}

// ── navigator.webdriver ──────────────────────────────────────────────────────
// Even with --disable-blink-features=AutomationControlled, some Chromium
// builds still set this. Explicit override as defense-in-depth.
Object.defineProperty(navigator, 'webdriver', { get: () => false });
`;
