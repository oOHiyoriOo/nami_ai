#!/usr/bin/env node
/**
 * Playwright Stealth MCP Server
 *
 * Drop-in replacement for @playwright/mcp that injects anti-detection
 * JavaScript (navigator, WebGL, permissions patching) via addInitScript()
 * before every page load.
 *
 * Reads the same config format as @playwright/mcp from --config <file>.
 * Exposes the core browser tools with mcp__ prefix.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { chromium } from 'playwright';
import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import { STEALTH_SCRIPT } from './stealth-script.js';

// ── Config parsing ───────────────────────────────────────────────────────────

function parseArgs() {
    const args = process.argv.slice(2);
    let configPath = null;
    let headless = true;
    let isolated = false;

    for (let i = 0; i < args.length; i++) {
        if (args[i] === '--config' && i + 1 < args.length) {
            configPath = args[++i];
        } else if (args[i] === '--headless') {
            headless = args[i + 1] !== 'false';
        } else if (args[i] === '--isolated') {
            isolated = true;
        } else if (args[i] === '--no-headless' || args[i] === '--headed') {
            headless = false;
        }
    }
    return { configPath, headless, isolated };
}

function loadConfig(configPath, fallbackHeadless) {
    const defaults = {
        browser: {
            browserName: 'chromium',
            launchOptions: { headless: fallbackHeadless, args: [] },
            contextOptions: {
                userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                viewport: { width: 1920, height: 1080 },
                locale: 'en-US',
                timezoneId: 'America/New_York',
                extraHTTPHeaders: {
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            },
        },
    };

    if (!configPath) return defaults;

    const resolved = resolve(configPath);
    if (!existsSync(resolved)) {
        console.error(`[playwright-stealth] Config file not found: ${resolved}, using defaults`);
        return defaults;
    }

    try {
        const raw = readFileSync(resolved, 'utf-8');
        const user = JSON.parse(raw);
        // Deep merge user config over defaults
        return deepMerge(defaults, user);
    } catch (e) {
        console.error(`[playwright-stealth] Failed to parse config: ${e.message}, using defaults`);
        return defaults;
    }
}

function deepMerge(base, override) {
    const result = { ...base };
    for (const key of Object.keys(override)) {
        if (key === '_comment') continue;
        if (override[key] && typeof override[key] === 'object' && !Array.isArray(override[key])) {
            result[key] = deepMerge(base[key] || {}, override[key]);
        } else {
            result[key] = override[key];
        }
    }
    return result;
}

// ── MCP Server ───────────────────────────────────────────────────────────────

async function main() {
    const { configPath, headless, isolated } = parseArgs();
    const config = loadConfig(configPath, headless);

    const launchOpts = {
        headless: config.browser.launchOptions?.headless ?? headless,
        args: config.browser.launchOptions?.args || [],
    };

    // Handle channel vs executablePath
    if (config.browser.launchOptions?.channel) {
        launchOpts.channel = config.browser.launchOptions.channel;
    }
    if (config.browser.launchOptions?.executablePath) {
        launchOpts.executablePath = config.browser.launchOptions.executablePath;
    }
    // CHROMIUM_PATH env var override (set by nami_start.sh)
    if (process.env.CHROMIUM_PATH) {
        launchOpts.executablePath = process.env.CHROMIUM_PATH;
        delete launchOpts.channel;
    }

    const ctxOpts = config.browser.contextOptions || {};

    console.error(`[playwright-stealth] Launching browser: ${JSON.stringify(launchOpts.channel || launchOpts.executablePath || 'default')}, headless: ${launchOpts.headless}`);

    const browser = await chromium.launch(launchOpts);

    // ── Context/page management ──────────────────────────────────────────
    let context = null;
    let page = null;

    async function ensureContext() {
        if (isolated && context) {
            await context.close();
            context = null;
            page = null;
        }
        if (!context) {
            context = await browser.newContext(ctxOpts);
            // Inject stealth script at context level — runs on every new page
            await context.addInitScript(STEALTH_SCRIPT);
        }
    }

    async function ensurePage() {
        await ensureContext();
        if (!page || page.isClosed()) {
            page = await context.newPage();
            // addInitScript is already on the context, but also on page for safety
            await page.addInitScript(STEALTH_SCRIPT);
        }
        return page;
    }

    // ── Server setup ─────────────────────────────────────────────────────
    const server = new McpServer({
        name: 'playwright-stealth',
        version: '1.0.0',
    });

    // ── browser_navigate ─────────────────────────────────────────────────
    server.tool(
        'browser_navigate',
        'Navigate to a URL and return page snapshot',
        {
            url: { type: 'string', description: 'URL to navigate to' },
        },
        async ({ url }) => {
            const p = await ensurePage();
            await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
            // Let the page settle
            await p.waitForTimeout(500);
            const snapshot = await p.accessibility.snapshot({ interestingOnly: false });
            const title = await p.title();
            const pageUrl = p.url();

            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        url: pageUrl,
                        title,
                        snapshot: snapshot ? summarizeSnapshot(snapshot) : '(empty page)',
                    }),
                }],
            };
        }
    );

    // ── browser_snapshot ─────────────────────────────────────────────────
    server.tool(
        'browser_snapshot',
        'Get accessibility snapshot of current page',
        {},
        async () => {
            const p = await ensurePage();
            const snapshot = await p.accessibility.snapshot({ interestingOnly: false });
            const title = await p.title();
            const pageUrl = p.url();

            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        url: pageUrl,
                        title,
                        snapshot: snapshot ? summarizeSnapshot(snapshot) : '(empty page)',
                    }),
                }],
            };
        }
    );

    // ── browser_click ────────────────────────────────────────────────────
    server.tool(
        'browser_click',
        'Click on an element by its accessibility node index',
        {
            index: { type: 'number', description: 'The index of the element to click (from snapshot)' },
            ref: { type: 'string', description: 'Alternative: aria-ref from snapshot' },
        },
        async ({ index, ref }) => {
            const p = await ensurePage();
            // Use text match based on the snapshot - find element by ordinal
            const snapshot = await p.accessibility.snapshot({ interestingOnly: false });
            const elements = flattenSnapshot(snapshot);

            let target;
            if (ref) {
                target = elements.find(el => el.ref === ref);
            } else if (index !== undefined && index >= 0 && index < elements.length) {
                target = elements[index];
            }

            if (!target) {
                return { content: [{ type: 'text', text: `Element not found (index=${index}, ref=${ref}). Available: ${elements.length} elements.` }] };
            }

            try {
                // Try clicking by role+name first (most accessible)
                if (target.role && target.name) {
                    await p.getByRole(target.role, { name: target.name }).first().click({ timeout: 5000 });
                } else if (target.name) {
                    await p.getByText(target.name).first().click({ timeout: 5000 });
                }
            } catch {
                return { content: [{ type: 'text', text: `Failed to click element: ${JSON.stringify(target)}` }] };
            }

            await p.waitForTimeout(500);
            const newSnapshot = await p.accessibility.snapshot({ interestingOnly: false });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        clicked: { role: target.role, name: target.name },
                        url: p.url(),
                        title: await p.title(),
                        snapshot: newSnapshot ? summarizeSnapshot(newSnapshot) : '(empty)',
                    }),
                }],
            };
        }
    );

    // ── browser_type ─────────────────────────────────────────────────────
    server.tool(
        'browser_type',
        'Type text into an input field by its snapshot index',
        {
            index: { type: 'number', description: 'The index of the input element (from snapshot)' },
            text: { type: 'string', description: 'Text to type' },
        },
        async ({ index, text }) => {
            const p = await ensurePage();
            const snapshot = await p.accessibility.snapshot({ interestingOnly: false });
            const elements = flattenSnapshot(snapshot);

            if (index === undefined || index < 0 || index >= elements.length) {
                return { content: [{ type: 'text', text: `Element not found at index ${index}. Available: ${elements.length} elements.` }] };
            }

            const target = elements[index];
            try {
                if (target.role === 'textbox' || target.role === 'searchbox') {
                    const locator = target.name
                        ? p.getByRole(target.role, { name: target.name }).first()
                        : p.getByRole(target.role).first();
                    await locator.fill(text, { timeout: 5000 });
                } else {
                    await p.keyboard.type(text);
                }
            } catch (e) {
                return { content: [{ type: 'text', text: `Failed to type: ${e.message}` }] };
            }

            return { content: [{ type: 'text', text: `Typed "${text}" into ${target.role} "${target.name || ''}"` }] };
        }
    );

    // ── browser_screenshot ───────────────────────────────────────────────
    server.tool(
        'browser_screenshot',
        'Take a screenshot of the current page',
        {
            fullPage: { type: 'boolean', description: 'Capture full scrollable page (default: false)' },
        },
        async ({ fullPage }) => {
            const p = await ensurePage();
            const buffer = await p.screenshot({ fullPage: !!fullPage, type: 'png' });
            return {
                content: [{
                    type: 'image',
                    data: buffer.toString('base64'),
                    mimeType: 'image/png',
                }],
            };
        }
    );

    // ── browser_scroll ───────────────────────────────────────────────────
    server.tool(
        'browser_scroll',
        'Scroll the page up or down',
        {
            direction: { type: 'string', description: 'Direction: "up" or "down"' },
            amount: { type: 'number', description: 'Pixels to scroll (default: 500)' },
        },
        async ({ direction, amount }) => {
            const p = await ensurePage();
            const px = amount || 500;
            await p.evaluate(({ dir, px }) => {
                window.scrollBy({ top: dir === 'up' ? -px : px, behavior: 'smooth' });
            }, { dir: direction || 'down', px });
            await p.waitForTimeout(300);
            return { content: [{ type: 'text', text: `Scrolled ${direction || 'down'} by ${px}px` }] };
        }
    );

    // ── browser_go_back ──────────────────────────────────────────────────
    server.tool(
        'browser_go_back',
        'Navigate back in browser history',
        {},
        async () => {
            const p = await ensurePage();
            await p.goBack({ timeout: 10000 });
            await p.waitForTimeout(500);
            const snapshot = await p.accessibility.snapshot({ interestingOnly: false });
            return {
                content: [{
                    type: 'text',
                    text: JSON.stringify({
                        url: p.url(),
                        title: await p.title(),
                        snapshot: snapshot ? summarizeSnapshot(snapshot) : '(empty)',
                    }),
                }],
            };
        }
    );

    // ── browser_evaluate ─────────────────────────────────────────────────
    server.tool(
        'browser_evaluate',
        'Execute JavaScript in the current page context',
        {
            script: { type: 'string', description: 'JavaScript code to evaluate' },
        },
        async ({ script }) => {
            const p = await ensurePage();
            try {
                const result = await p.evaluate(script);
                return {
                    content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
                };
            } catch (e) {
                return { content: [{ type: 'text', text: `Error: ${e.message}` }] };
            }
        }
    );

    // ── Stealth-specific: browser_inject_script ──────────────────────────
    server.tool(
        'browser_inject_script',
        'Inject additional JavaScript to run before every future page load in this session',
        {
            script: { type: 'string', description: 'JavaScript code to inject via addInitScript' },
        },
        async ({ script }) => {
            const p = await ensurePage();
            await p.addInitScript(script);
            return {
                content: [{ type: 'text', text: 'Script injected. It will run on every new page load in this session.' }],
            };
        }
    );

    // ── Cleanup on exit ──────────────────────────────────────────────────
    process.on('SIGINT', async () => {
        console.error('[playwright-stealth] Shutting down...');
        if (page && !page.isClosed()) await page.close().catch(() => {});
        if (context) await context.close().catch(() => {});
        await browser.close().catch(() => {});
        process.exit(0);
    });

    process.on('SIGTERM', async () => {
        if (page && !page.isClosed()) await page.close().catch(() => {});
        if (context) await context.close().catch(() => {});
        await browser.close().catch(() => {});
        process.exit(0);
    });

    // ── Start ────────────────────────────────────────────────────────────
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error('[playwright-stealth] Ready — anti-detection init scripts active');
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function flattenSnapshot(node, refs = []) {
    if (!node) return refs;
    refs.push({ role: node.role, name: node.name || '', ref: node.nodeId || '' });
    if (node.children) {
        for (const child of node.children) {
            flattenSnapshot(child, refs);
        }
    }
    return refs;
}

function summarizeSnapshot(node, depth = 0) {
    if (!node) return '';
    const indent = '  '.repeat(depth);
    const name = node.name ? ` "${node.name}"` : '';
    let out = `${indent}[${node.role}]${name}`;
    if (node.value) out += ` = "${node.value}"`;
    out += '\n';
    if (node.children) {
        for (const child of node.children) {
            out += summarizeSnapshot(child, depth + 1);
        }
    }
    return out;
}

main().catch((err) => {
    console.error('[playwright-stealth] Fatal:', err);
    process.exit(1);
});
