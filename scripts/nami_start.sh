#!/bin/sh
# Startup script for the nami_ai container.
# Installs build deps, Node.js LTS, pip packages, then starts the API server.
set -e

apt-get update -qq
apt-get install -y -qq gcc g++ libxml2-dev libxslt1-dev curl openssh-client universal-ctags fonts-liberation fonts-noto-color-emoji fonts-dejavu-core fonts-freefont-ttf

# ── Node.js LTS (for MCP servers) ────────────────────────────────────────────
if ! command -v node > /dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - > /dev/null
    apt-get install -y -qq nodejs
fi

chown -R root:root /root/.cache/pip 2>/dev/null || true
python -m ensurepip --upgrade
python -m pip install -q -r requirements.txt

# ── MCP server dependencies ───────────────────────────────────────────────────
# gitea-mcp-stdio Python deps
python -m pip install -q -r /app/mcp/gitea-mcp-stdio/requirements.txt

# neo-memory-mcp Node deps + build compiled JS (clean install — avoid stale x86_64 artifacts)
rm -rf /app/mcp/neo-memory-mcp/node_modules
npm install --prefix /app/mcp/neo-memory-mcp --silent

# Detect architecture once — used for tokenizer, browser binary, etc.
ARCH=$(uname -m)

# Explicitly ensure the platform-specific tokenizer binary is installed.
# @anush008/tokenizers publishes per-arch optional packages; npm may silently skip
# them if the registry times out. This explicit install makes the failure visible.
if [ "$ARCH" = "aarch64" ]; then
    npm install --prefix /app/mcp/neo-memory-mcp @anush008/tokenizers-linux-arm64-gnu --no-save 2>&1 || \
        echo "[startup] WARNING: @anush008/tokenizers-linux-arm64-gnu not available — neo-memory will use full-text fallback"
fi

npm run build --prefix /app/mcp/neo-memory-mcp

# ── Browser binary for Playwright MCP ───────────────────────────────────────
# Prefer real Google Chrome on amd64 for authentic TLS fingerprint, WebGL
# vendor, and plugin behavior. On arm64, Chrome has no Linux build — fall
# back to Debian's Chromium package (same BoringSSL, essentially the real
# ARM Chrome build minus the branding).
if [ "$ARCH" = "x86_64" ]; then
    # ── Google Chrome Stable (amd64) ────────────────────────────────────────
    if ! command -v google-chrome-stable > /dev/null 2>&1; then
        apt-get install -y -qq wget gnupg
        wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
        apt-get update -qq
        apt-get install -y -qq google-chrome-stable
        apt-mark hold google-chrome-stable
    fi
    export CHROMIUM_PATH=/usr/bin/google-chrome-stable
    # config/playwright-mcp.json already has "channel": "chrome" — works on amd64
elif [ "$ARCH" = "aarch64" ]; then
    # ── System Chromium (arm64) ─────────────────────────────────────────────
    # No Google Chrome Linux arm64 build. Debian's chromium package is the
    # closest equivalent — same codebase, same TLS stack (BoringSSL).
    # Swap channel:chrome for executablePath since channel won't resolve.
    export CHROMIUM_PATH=/usr/bin/chromium
    # Idempotent: only swap channel→executablePath once
    if grep -q '"channel": "chrome"' /app/config/playwright-mcp.json 2>/dev/null; then
        sed -i 's/"channel": "chrome",/"executablePath": "\/usr\/bin\/chromium",/' /app/config/playwright-mcp.json
    fi
fi

# ── Playwright npm package (for Playwright Stealth MCP) ────────────────────
# The stealth MCP server needs the playwright npm package.
# --with-deps installs system libraries that both Chrome and Chromium need
# (libgtk, libnss, etc.).
npm install --prefix /app/mcp/playwright-stealth-mcp --silent
/app/mcp/playwright-stealth-mcp/node_modules/.bin/playwright install chromium --with-deps 2>&1 || \
    echo "[startup] WARNING: Playwright browser install failed — mcp_playwright tools will be unavailable"

# ── Toktoken code index ──────────────────────────────────────────────────────
# Create or update the code index for Nami to search her own codebase.
/app/mcp/toktoken-mcp/toktoken index:update --path /app 2>&1 || \
    /app/mcp/toktoken-mcp/toktoken index:create --path /app 2>&1

exec python api_server.py
