#!/bin/sh
# Startup script for the nami_ai container.
# Installs build deps, Node.js LTS, pip packages, then starts the API server.
set -e

apt-get update -qq
apt-get install -y -qq gcc g++ libxml2-dev libxslt1-dev curl openssh-client

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

# Explicitly ensure the platform-specific tokenizer binary is installed.
# @anush008/tokenizers publishes per-arch optional packages; npm may silently skip
# them if the registry times out. This explicit install makes the failure visible.
ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
    npm install --prefix /app/mcp/neo-memory-mcp @anush008/tokenizers-linux-arm64-gnu --no-save 2>&1 || \
        echo "[startup] WARNING: @anush008/tokenizers-linux-arm64-gnu not available — neo-memory will use full-text fallback"
fi

npm run build --prefix /app/mcp/neo-memory-mcp

# ── Playwright Chromium (for Playwright MCP web browsing) ────────────────────
# Installs Chromium binary + all required OS-level dependencies.
# Skipped if the binary is already present (cached across restarts).
npx --yes playwright@latest install chromium --with-deps 2>&1 || \
    echo "[startup] WARNING: Playwright browser install failed — mcp_playwright tools will be unavailable"

# ── Toktoken code index ──────────────────────────────────────────────────────
# Create or update the code index for Nami to search her own codebase.
/app/mcp/toktoken-mcp/toktoken index:update --path /app 2>&1 || \
    /app/mcp/toktoken-mcp/toktoken index:create --path /app 2>&1

exec python api_server.py
