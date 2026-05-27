/**
 * index.js — nami_ai WhatsApp WebSocket bridge.
 *
 * Connects WhatsApp via WWeb.js and forwards messages to nami_ai over
 * the adapter WebSocket event protocol.
 *
 * Bootstrap (env vars only — everything else comes from nami config):
 *   NAMI_WS_URL               — WebSocket endpoint, default ws://localhost:11434/api/ws/adapter
 *   ADAPTER_NAME              — adapter name registered in nami, default "whatsapp"
 *   BRIDGE_SECRET             — shared secret for WS authentication
 *   PUPPETEER_EXECUTABLE_PATH — optional path to Chromium binary
 *
 * Startup sequence:
 *   1. Connect to nami WebSocket
 *   2. Send capabilities.register with supported WhatsApp actions
 *   3. Receive capabilities.ack with config (permitted_numbers, ai_groups, session_dir, …)
 *   4. Initialise WWeb.js client using config values
 *
 * Supported adapter actions (invoked by nami via action.invoke):
 *   send_image(conversation_id, image_url, caption?)
 *   send_audio(conversation_id, audio_url)
 *   mark_as_read(message_id)
 *   send_location(conversation_id, latitude, longitude, description?)
 *   send_file(conversation_id, file_url, filename?, caption?)
 */

require('dotenv').config();

const fs = require('fs');
const path = require('path');
const WebSocket = require('ws');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { ConversationHistory } = require('./history');

// Minimal bootstrap — only what's needed to reach nami
const NAMI_WS_URL = process.env.NAMI_WS_URL || 'ws://localhost:11434/api/ws/adapter';
const ADAPTER_NAME = process.env.ADAPTER_NAME || 'whatsapp';
const BRIDGE_SECRET = process.env.BRIDGE_SECRET || '';

const RESPONSE_TIMEOUT_MS = 120_000;
const WS_READY_TIMEOUT_MS = 30_000;
const WS_RECONNECT_INITIAL_MS = 5_000;
const WS_RECONNECT_MAX_MS = 60_000;
const PING_INTERVAL_MS = 30_000;
const TYPING_INTERVAL_MS = 10_000;

// Populated from capabilities.ack
let namiConfig = null;
let whatsappClient = null;
let platformInitialised = false;
/** True as soon as initWhatsAppClient() has been called (not just when ready fires). */
let clientInitStarted = false;

/** Resolvers waiting for capabilities.ack — one-shot. */
let configResolve = null;
let configReject = null;
const configPromise = new Promise((resolve, reject) => {
  configResolve = resolve;
  configReject = reject;
});

const pendingResponses = new Map();
const convQueues = new Map();
const wsReadyWaiters = new Set();

let namiWs = null;
let pingTimer = null;
let reconnectTimer = null;
let reconnectDelayMs = WS_RECONNECT_INITIAL_MS;
let lastPongAt = null;

// history is created after ack so we can use the configured max_history value;
// default to 50 until config arrives
let history = new ConversationHistory('history.db', 50);

// ---------------------------------------------------------------------------
// WhatsApp capability action declarations
// ---------------------------------------------------------------------------

/** Full capability schema sent to nami on connect. */
const CAPABILITIES = {
  actions: [
    {
      type: 'function',
      function: {
        name: 'send_image',
        description: 'Send an image to a WhatsApp conversation by URL.',
        parameters: {
          type: 'object',
          properties: {
            conversation_id: { type: 'string', description: 'WhatsApp chat ID (e.g. "447700900000@c.us").' },
            image_url: { type: 'string', description: 'Publicly accessible URL of the image.' },
            caption: { type: 'string', description: 'Optional caption for the image.' },
          },
          required: ['conversation_id', 'image_url'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'send_audio',
        description: 'Send an audio file to a WhatsApp conversation by URL.',
        parameters: {
          type: 'object',
          properties: {
            conversation_id: { type: 'string', description: 'WhatsApp chat ID.' },
            audio_url: { type: 'string', description: 'Publicly accessible URL of the audio file.' },
          },
          required: ['conversation_id', 'audio_url'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'mark_as_read',
        description: 'Mark a WhatsApp message as read.',
        parameters: {
          type: 'object',
          properties: {
            message_id: { type: 'string', description: 'Serialised WhatsApp message ID.' },
          },
          required: ['message_id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'send_file',
        description: 'Send a file (document) to a WhatsApp conversation by URL.',
        parameters: {
          type: 'object',
          properties: {
            conversation_id: { type: 'string', description: 'WhatsApp chat ID.' },
            file_url: { type: 'string', description: 'Publicly accessible URL of the file.' },
            filename: { type: 'string', description: 'Optional filename override.' },
            caption: { type: 'string', description: 'Optional caption/message.' },
          },
          required: ['conversation_id', 'file_url'],
        },
      },
    },
  ],
};

// ---------------------------------------------------------------------------
// Adapter action handlers — called when nami sends action.invoke
// ---------------------------------------------------------------------------

/**
 * Execute an action.invoke request from nami.
 * @param {string} action
 * @param {Record<string, unknown>} params
 * @returns {Promise<Record<string, unknown>>} result data or { error }
 */
async function executeAction(action, params) {
  if (!whatsappClient) {
    return { error: 'WhatsApp client not initialised' };
  }

  try {
    switch (action) {
      case 'send_image': {
        const media = await MessageMedia.fromUrl(params.image_url, { unsafeMime: true });
        await whatsappClient.sendMessage(params.conversation_id, media, { caption: params.caption || undefined });
        return { success: true };
      }

      case 'send_audio': {
        const media = await MessageMedia.fromUrl(params.audio_url, { unsafeMime: true });
        await whatsappClient.sendMessage(params.conversation_id, media, { sendAudioAsVoice: true });
        return { success: true };
      }

      case 'mark_as_read': {
        const msg = await whatsappClient.getMessageById(params.message_id);
        if (!msg) return { error: 'Message not found' };
        await msg.getChat().then(chat => chat.sendSeen());
        return { success: true };
      }

      case 'send_file': {
        const media = await MessageMedia.fromUrl(params.file_url, { unsafeMime: true });
        if (params.filename) media.filename = params.filename;
        await whatsappClient.sendMessage(params.conversation_id, media, {
          sendMediaAsDocument: true,
          caption: params.caption || undefined,
        });
        return { success: true };
      }

      default:
        return { error: `Unknown action: ${action}` };
    }
  } catch (error) {
    console.error(`[nami] action '${action}' failed: ${error.message}`);
    return { error: error.message };
  }
}

// ---------------------------------------------------------------------------
// Platform initialisation (deferred until capabilities.ack)
// ---------------------------------------------------------------------------

/**
 * Remove stale Chromium singleton lock files from the auth directory.
 * @param {string} dir
 * @param {number} depth
 */
function removeStaleLocks(dir, depth = 0) {
  if (depth > 4) return;

  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      removeStaleLocks(fullPath, depth + 1);
      continue;
    }
    if (!['SingletonLock', 'SingletonCookie', 'SingletonSocket'].includes(entry.name)) continue;
    try {
      fs.unlinkSync(fullPath);
      console.log(`[nami] Removed stale lock: ${fullPath}`);
    } catch (error) {
      console.warn(`[nami] Could not remove ${fullPath}: ${error.message}`);
    }
  }
}

/**
 * Initialise the WWeb.js client using config received from nami.
 * Called once after capabilities.ack.
 */
function initWhatsAppClient() {
  const sessionDir = namiConfig.session_dir || '.wwebjs_auth';
  const maxHistory = parseInt(String(namiConfig.max_history || '50'), 10);
  const permittedNumbers = new Set(
    (namiConfig.permitted_numbers || []).map(n => String(n).trim())
  );
  const aiGroups = new Set(
    (namiConfig.ai_groups || []).map(g => String(g).trim())
  );

  // Recreate history with configured limit
  history = new ConversationHistory('history.db', maxHistory);

  console.log(`[nami] Scanning for stale Chromium locks in: ${path.resolve(sessionDir)}`);
  removeStaleLocks(sessionDir);

  whatsappClient = new Client({
    authStrategy: new LocalAuth({ dataPath: sessionDir }),
    puppeteer: {
      executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  });

  whatsappClient.on('loading_screen', (percent, message) => {
    console.log(`[nami] WhatsApp loading: ${percent}% — ${message}`);
  });

  whatsappClient.on('qr', qr => {
    console.log('[nami] Scan this QR code to log in:');
    qrcode.generate(qr, { small: true });
  });

  whatsappClient.on('ready', () => {
    console.log(`[nami] WhatsApp bridge ready — connected as ${whatsappClient.info?.pushname || 'unknown'}`);
    platformInitialised = true;
  });

  whatsappClient.on('auth_failure', () => {
    console.error('[nami] Authentication failed — delete session and restart');
  });

  whatsappClient.on('disconnected', reason => {
    console.warn(`[nami] WhatsApp disconnected: ${reason}`);
  });

  whatsappClient.on('message', message => {
    handleIncomingWhatsAppMessage(message, permittedNumbers, aiGroups).catch(error => {
      console.error(`[nami] Unhandled WhatsApp message error: ${error.stack || error.message}`);
    });
  });

  whatsappClient.initialize();
}

// ---------------------------------------------------------------------------
// WebSocket helpers
// ---------------------------------------------------------------------------

/**
 * Build the authenticated nami WebSocket URL.
 * @returns {string}
 */
function buildWsUrl() {
  const url = new URL(NAMI_WS_URL);
  url.searchParams.set('name', ADAPTER_NAME);
  url.searchParams.set('secret', BRIDGE_SECRET);
  return url.toString();
}

/**
 * Redact secrets from a WebSocket URL for logging.
 * @param {string} urlString
 * @returns {string}
 */
function redactWsUrl(urlString) {
  const url = new URL(urlString);
  if (url.searchParams.has('secret')) {
    url.searchParams.set('secret', url.searchParams.get('secret') ? '***' : '');
  }
  return url.toString();
}

/**
 * Enqueue an async task per conversation so each conversation stays FIFO.
 * @param {string} conversationId
 * @param {() => Promise<void>} fn
 */
function enqueue(conversationId, fn) {
  const previous = convQueues.get(conversationId) || Promise.resolve();
  const next = previous
    .then(fn)
    .catch(error => console.error(`[nami] queue error (${conversationId}): ${error.stack || error.message}`));

  convQueues.set(conversationId, next);
  next.finally(() => {
    if (convQueues.get(conversationId) === next) convQueues.delete(conversationId);
  });
}

/**
 * Register a pending nami response for a conversation.
 * @param {string} conversationId
 * @returns {Promise<string>}
 */
function registerPendingResponse(conversationId) {
  const existing = pendingResponses.get(conversationId);
  if (existing) existing.reject(new Error(`Superseded pending response for ${conversationId}`));

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pendingResponses.delete(conversationId);
      reject(new Error(`Timed out waiting for response.ready (${conversationId})`));
    }, RESPONSE_TIMEOUT_MS);

    pendingResponses.set(conversationId, {
      resolve: content => {
        clearTimeout(timeout);
        pendingResponses.delete(conversationId);
        resolve(content);
      },
      reject: error => {
        clearTimeout(timeout);
        pendingResponses.delete(conversationId);
        reject(error);
      },
    });
  });
}

/**
 * Reject a single pending response.
 * @param {string} conversationId
 * @param {Error} error
 */
function rejectPendingResponse(conversationId, error) {
  const pending = pendingResponses.get(conversationId);
  if (pending) pending.reject(error);
}

/**
 * Reject all pending responses, typically after disconnect.
 * @param {Error} error
 */
function rejectAllPendingResponses(error) {
  for (const pending of pendingResponses.values()) pending.reject(error);
  pendingResponses.clear();
}

/**
 * Resolve all waiters that were waiting for the nami socket to open.
 */
function resolveWsReadyWaiters() {
  for (const waiter of Array.from(wsReadyWaiters)) {
    clearTimeout(waiter.timeout);
    wsReadyWaiters.delete(waiter);
    waiter.resolve();
  }
}

/**
 * Wait until the nami socket is connected.
 * @param {number} timeoutMs
 * @returns {Promise<void>}
 */
function waitForWsReady(timeoutMs = WS_READY_TIMEOUT_MS) {
  if (namiWs && namiWs.readyState === WebSocket.OPEN) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const waiter = { resolve: () => resolve(), reject: error => reject(error), timeout: null };
    waiter.timeout = setTimeout(() => {
      wsReadyWaiters.delete(waiter);
      reject(new Error('nami WebSocket not connected'));
    }, timeoutMs);
    wsReadyWaiters.add(waiter);
  });
}

/**
 * Start the ping keepalive loop.
 */
function startPingLoop() {
  stopPingLoop();
  pingTimer = setInterval(() => {
    if (!namiWs || namiWs.readyState !== WebSocket.OPEN) return;
    namiWs.send(JSON.stringify({ type: 'ping' }), error => {
      if (error) console.warn(`[nami] Failed to send ping: ${error.message}`);
    });
  }, PING_INTERVAL_MS);
}

/**
 * Stop the ping keepalive loop.
 */
function stopPingLoop() {
  if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
}

/**
 * Schedule a reconnect with exponential backoff.
 */
function scheduleReconnect() {
  if (reconnectTimer) return;
  const delayMs = reconnectDelayMs;
  console.log(`[nami] Reconnecting to nami WS in ${Math.round(delayMs / 1000)}s`);
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectNamiWebSocket(); }, delayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, WS_RECONNECT_MAX_MS);
}

/**
 * Open the nami adapter WebSocket connection.
 */
function connectNamiWebSocket() {
  if (namiWs && (namiWs.readyState === WebSocket.OPEN || namiWs.readyState === WebSocket.CONNECTING)) return;

  const wsUrl = buildWsUrl();
  console.log(`[nami] Connecting to nami WS: ${redactWsUrl(wsUrl)}`);
  const ws = new WebSocket(wsUrl);
  namiWs = ws;

  ws.on('open', () => {
    console.log('[nami] Connected to nami WS — sending capabilities.register');
    reconnectDelayMs = WS_RECONNECT_INITIAL_MS;
    lastPongAt = Date.now();
    startPingLoop();

    ws.send(JSON.stringify({ type: 'capabilities.register', data: CAPABILITIES }), error => {
      if (error) console.error(`[nami] Failed to send capabilities.register: ${error.message}`);
    });

    resolveWsReadyWaiters();
  });

  ws.on('message', data => {
    handleWsMessage(data.toString()).catch(error => {
      console.error(`[nami] Failed to handle WS event: ${error.stack || error.message}`);
    });
  });

  ws.on('error', error => {
    console.error(`[nami] nami WS error: ${error.message}`);
  });

  ws.on('close', (code, reasonBuffer) => {
    const reason = Buffer.isBuffer(reasonBuffer) ? reasonBuffer.toString() : String(reasonBuffer || '');
    if (namiWs === ws) namiWs = null;
    stopPingLoop();
    rejectAllPendingResponses(new Error('nami WebSocket disconnected'));
    console.warn(`[nami] nami WS closed (${code})${reason ? `: ${reason}` : ''}`);
    scheduleReconnect();
  });
}

/**
 * Send an event to nami over the WebSocket connection.
 * @param {Record<string, unknown>} event
 * @returns {Promise<void>}
 */
async function sendWsEvent(event) {
  await waitForWsReady();
  if (!namiWs || namiWs.readyState !== WebSocket.OPEN) throw new Error('nami WebSocket not connected');
  await new Promise((resolve, reject) => {
    namiWs.send(JSON.stringify(event), error => {
      if (error) { reject(error); return; }
      resolve();
    });
  });
}

/**
 * Send a message.received event and wait for the matching response.ready.
 * @param {Record<string, unknown> & { conversation_id: string }} event
 * @returns {Promise<string>}
 */
async function requestNamiResponse(event) {
  const conversationId = event.conversation_id;
  const responsePromise = registerPendingResponse(conversationId);
  try {
    await sendWsEvent(event);
    return await responsePromise;
  } catch (error) {
    rejectPendingResponse(conversationId, error);
    throw error;
  }
}

// ---------------------------------------------------------------------------
// Incoming WS event handler
// ---------------------------------------------------------------------------

/**
 * Handle one incoming event from nami.
 * @param {string} rawMessage
 * @returns {Promise<void>}
 */
async function handleWsMessage(rawMessage) {
  let event;
  try {
    event = JSON.parse(rawMessage);
  } catch (error) {
    console.warn(`[nami] Ignoring invalid WS payload: ${error.message}`);
    return;
  }

  switch (event.type) {
    case 'pong': {
      lastPongAt = Date.now();
      return;
    }

    case 'capabilities.ack': {
      namiConfig = event.config || {};
      console.log('[nami] capabilities.ack received — config keys:', Object.keys(namiConfig).join(', '));
      if (!clientInitStarted) {
        clientInitStarted = true;
        initWhatsAppClient();
        if (configResolve) { configResolve(); configResolve = null; }
      }
      return;
    }

    case 'action.invoke': {
      const { call_id, action, params } = event;
      const result = await executeAction(action, params || {});
      await sendWsEvent({ type: 'action.result', call_id, data: result });
      return;
    }

    case 'response.ready': {
      const pending = pendingResponses.get(String(event.conversation_id || ''));
      if (!pending) {
        console.warn(`[nami] No pending response for conversation ${event.conversation_id}`);
        return;
      }
      pending.resolve(String(event.content || ''));
      return;
    }

    case 'send.message': {
      if (!event.conversation_id || typeof event.content !== 'string') {
        console.warn('[nami] Ignoring invalid send.message payload');
        return;
      }
      await sendWhatsAppConversationMessage(String(event.conversation_id), event.content);
      return;
    }

    case 'send.dm': {
      if (!event.user_id || typeof event.content !== 'string') {
        console.warn('[nami] Ignoring invalid send.dm payload');
        return;
      }
      await sendWhatsAppDirectMessage(String(event.user_id), event.content);
      return;
    }

    case 'status.update': {
      console.log(`[nami] status.update: ${event.status || ''}`);
      return;
    }

    default: {
      console.log(`[nami] Ignoring WS event type=${event.type}`);
    }
  }
}

// ---------------------------------------------------------------------------
// WhatsApp message helpers
// ---------------------------------------------------------------------------

/**
 * Normalize a user ID for WhatsApp direct sends.
 * @param {string} userId
 * @returns {string}
 */
function normalizeDirectChatId(userId) {
  return userId.includes('@') ? userId : `${userId}@c.us`;
}

/**
 * Resolve the sender's phone number, including @lid contacts.
 * @param {import('whatsapp-web.js').Message} message
 * @returns {Promise<string>}
 */
async function resolveSenderNumber(message) {
  if (message.from.endsWith('@lid') || (message.author && message.author.endsWith('@lid'))) {
    try {
      const contact = await message.getContact();
      return contact.number || (message.author || message.from).replace(/@.*$/, '');
    } catch {
      return (message.author || message.from).replace(/@.*$/, '');
    }
  }
  return (message.author || message.from).replace(/@.*$/, '');
}

/**
 * Resolve a friendly display name for a WhatsApp sender.
 * @param {import('whatsapp-web.js').Message} message
 * @returns {Promise<string>}
 */
async function resolveDisplayName(message) {
  try {
    const contact = await message.getContact();
    return contact.pushname || contact.name || contact.shortName || contact.number || await resolveSenderNumber(message);
  } catch {
    return resolveSenderNumber(message);
  }
}

/**
 * Decide whether the bridge should respond to a WhatsApp message.
 * @param {import('whatsapp-web.js').Message} message
 * @param {Set<string>} permittedNumbers
 * @param {Set<string>} aiGroups
 * @returns {Promise<boolean>}
 */
async function shouldRespond(message, permittedNumbers, aiGroups) {
  if (message.fromMe) return false;
  if (permittedNumbers.size === 0) return false;

  const isGroup = message.from.endsWith('@g.us');
  const senderNumber = await resolveSenderNumber(message);
  const isPermitted = permittedNumbers.has(senderNumber);

  if (!isGroup) return isPermitted;
  if (aiGroups.has(message.from)) return true;

  const botId = whatsappClient?.info?.wid?._serialized;
  const mentioned = Array.isArray(message.mentionedIds) && botId
    ? message.mentionedIds.includes(botId)
    : false;

  return Boolean(mentioned && isPermitted);
}

/**
 * Start a typing keepalive loop for a WhatsApp chat.
 * @param {import('whatsapp-web.js').Chat} chat
 * @returns {Promise<NodeJS.Timeout | null>}
 */
async function startTypingLoop(chat) {
  try {
    await chat.sendStateTyping();
  } catch (error) {
    console.warn(`[nami] Failed to start typing indicator: ${error.message}`);
  }
  return setInterval(() => {
    chat.sendStateTyping().catch(() => {});
  }, TYPING_INTERVAL_MS);
}

/**
 * Stop a typing keepalive loop.
 * @param {NodeJS.Timeout | null} timer
 */
function stopTypingLoop(timer) {
  if (timer) clearInterval(timer);
}

/**
 * Clear the WhatsApp typing state.
 * @param {import('whatsapp-web.js').Chat} chat
 * @returns {Promise<void>}
 */
async function clearTypingState(chat) {
  try {
    await chat.clearState();
  } catch {
    // WWeb.js occasionally fails here; not worth exploding over.
  }
}

/**
 * Map bridge errors to user-facing replies.
 * @param {Error} error
 * @returns {string}
 */
function errorReply(error) {
  const message = error.message || '';
  if (/\b(401|403)\b/.test(message)) return '🔒 AI bridge authentication error — check config';
  if (message.includes('Timed out') || message.includes('timed out')) return '⏳ The AI took too long to respond, try again';
  if (message.includes('WebSocket') || /\b(ECONNREFUSED|ENOTFOUND|ECONNRESET|ETIMEDOUT)\b/.test(message)) return '🔌 Cannot reach the AI server right now';
  return '⚠️ Something went wrong, try again later.';
}

/**
 * Send a proactive message to a WhatsApp conversation.
 * @param {string} conversationId
 * @param {string} content
 * @returns {Promise<void>}
 */
async function sendWhatsAppConversationMessage(conversationId, content) {
  if (!whatsappClient) return;
  try {
    await whatsappClient.sendMessage(conversationId, content);
    history.append(conversationId, 'assistant', 'nami [proactive]', content);
    console.log(`[nami] ✓ sent WhatsApp message conv=${conversationId}`);
  } catch (error) {
    console.error(`[nami] Failed to send WhatsApp message (${conversationId}): ${error.message}`);
  }
}

/**
 * Send a proactive WhatsApp direct message.
 * @param {string} userId
 * @param {string} content
 * @returns {Promise<void>}
 */
async function sendWhatsAppDirectMessage(userId, content) {
  const conversationId = normalizeDirectChatId(userId);
  try {
    await sendWhatsAppConversationMessage(conversationId, content);
  } catch (error) {
    console.error(`[nami] Failed to send WhatsApp DM (${userId}): ${error.message}`);
  }
}

/**
 * Handle one inbound WhatsApp user message.
 * @param {import('whatsapp-web.js').Message} message
 * @param {Set<string>} permittedNumbers
 * @param {Set<string>} aiGroups
 * @returns {Promise<void>}
 */
async function handleIncomingWhatsAppMessage(message, permittedNumbers, aiGroups) {
  console.log(
    `[nami] ← msg from=${message.from} author=${message.author || '-'} fromMe=${message.fromMe} body="${(message.body || '').substring(0, 80)}"`
  );

  if (!await shouldRespond(message, permittedNumbers, aiGroups)) {
    console.log(`[nami] ignoring (shouldRespond=false) from=${message.from}`);
    return;
  }

  const conversationId = message.from;
  if ((message.body || '').trim() === '!clear') {
    history.clear(conversationId);
    await message.reply('🧹 Conversation history cleared.');
    return;
  }

  enqueue(conversationId, async () => {
    const chat = await message.getChat();
    const typingTimer = await startTypingLoop(chat);

    try {
      const senderNumber = await resolveSenderNumber(message);
      const displayName = await resolveDisplayName(message);
      const content = message.body || '';

      history.append(conversationId, 'user', displayName, content);

      const event = {
        type: 'message.received',
        conversation_id: conversationId,
        user_id: senderNumber,
        user_name: senderNumber,
        display_name: displayName,
        content,
        is_dm: !conversationId.endsWith('@g.us'),
        channel_name: conversationId.endsWith('@g.us') ? (chat.name || '') : '',
        guild_name: null,
        image_urls: [],
        history: history.getMessages(conversationId),
      };

      console.log(`[nami] → WS message.received conv=${conversationId} user=${senderNumber}`);
      const reply = await requestNamiResponse(event);

      if (reply === '<ignore>') {
        history.append(conversationId, 'assistant', 'assistant', '<ignore>');
        console.log(`[nami] ← WS <ignore> conv=${conversationId}`);
        return;
      }

      await message.reply(reply);
      history.append(conversationId, 'assistant', 'assistant', reply);
      console.log(`[nami] ✓ replied to ${senderNumber}`);
    } catch (error) {
      console.error(`[nami] AI error (${conversationId}): ${error.stack || error.message}`);
      try {
        await message.reply(errorReply(error));
      } catch (replyError) {
        console.error(`[nami] Failed to send error reply (${conversationId}): ${replyError.message}`);
      }
    } finally {
      stopTypingLoop(typingTimer);
      await clearTypingState(chat);
    }
  });
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * Start the nami WS connection and wait for config before initialising WhatsApp.
 * @returns {Promise<void>}
 */
async function main() {
  if (!BRIDGE_SECRET) {
    throw new Error('BRIDGE_SECRET is required');
  }

  connectNamiWebSocket();

  const CONFIG_TIMEOUT_MS = 30_000;
  await Promise.race([
    configPromise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Timed out waiting for capabilities.ack from nami')), CONFIG_TIMEOUT_MS)
    ),
  ]);

  console.log('[nami] Config received — WhatsApp bridge fully operational');
}

main().catch(error => {
  console.error(`[nami] Fatal startup error: ${error.stack || error.message}`);
  process.exit(1);
});
