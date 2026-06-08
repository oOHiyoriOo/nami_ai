/**
 * index.js — nami_ai Discord WebSocket bridge.
 *
 * Connects Discord to nami_ai over the adapter WebSocket event protocol.
 *
 * Bootstrap (env vars only — everything else comes from nami config):
 *   NAMI_WS_URL    — WebSocket endpoint, default ws://localhost:11434/api/ws/adapter
 *   ADAPTER_NAME   — adapter name registered in nami, default "discord"
 *   BRIDGE_SECRET  — shared secret for WS authentication
 *
 * Startup sequence:
 *   1. Connect to nami WebSocket
 *   2. Send capabilities.register with supported Discord actions
 *   3. Receive capabilities.ack with config (token, permitted_users, ai_channels, …)
 *   4. Initialise Discord.js client using config values
 *
 * Supported adapter actions (invoked by nami via action.invoke):
 *   add_reaction(channel_id, message_id, emoji)
 *   remove_reaction(channel_id, message_id, emoji)
 *   delete_message(channel_id, message_id)
 *   create_thread(channel_id, message_id, name, auto_archive_duration?)
 *   pin_message(channel_id, message_id)
 *   send_file(conversation_id, file_url, filename?, caption?)
 *   send_message(channel_id, content)
 *   send_dm(user_id, content)
 */

require('dotenv').config();

const WebSocket = require('ws');
const { Client, GatewayIntentBits, Partials, AttachmentBuilder } = require('discord.js');
const { ConversationHistory } = require('./history');

// Minimal bootstrap — only what's needed to reach nami
const NAMI_WS_URL = process.env.NAMI_WS_URL || 'ws://localhost:11434/api/ws/adapter';
const ADAPTER_NAME = process.env.ADAPTER_NAME || 'discord';
const BRIDGE_SECRET = process.env.BRIDGE_SECRET || '';

const RESPONSE_TIMEOUT_MS = 300_000; // legacy — kept for reference but no longer used directly

// 3-phase lifecycle timeouts
const ACK_TIMEOUT_MS       =  10_000; // Phase 1: waiting for message.queued (WS ack)
const QUEUE_TIMEOUT_MS     = 30 * 60_000; // Phase 2: waiting for message.processing (queue wait)
const INACTIVITY_TIMEOUT_MS = 15 * 60_000; // Phase 3: no status.update while processing
const RECOVERY_TIMEOUT_MS  =  5 * 60_000; // grace period after message.recover sent
const WS_READY_TIMEOUT_MS = 30_000;
const WS_RECONNECT_INITIAL_MS = 5_000;
const WS_RECONNECT_MAX_MS = 60_000;
const PING_INTERVAL_MS = 30_000;
const TYPING_INTERVAL_MS = 8_000;

// Populated from capabilities.ack
let namiConfig = null;
let discordClient = null;
let platformInitialised = false;
/** True as soon as initDiscordClient() has been called (not just when ready fires). */
let clientInitStarted = false;

/** Resolvers waiting for capabilities.ack — one-shot. */
let configResolve = null;
let configReject = null;
const configPromise = new Promise((resolve, reject) => {
  configResolve = resolve;
  configReject = reject;
});

const pendingResponses = new Map();
/** Fallback delivery registry: conv_id → { channel, discordMessage } — outlives pendingResponses entries */
const conversationChannels = new Map();
const convQueues = new Map();
const wsReadyWaiters = new Set();
const history = new ConversationHistory('history.db', 50); // default; updated from config

let namiWs = null;
let pingTimer = null;
let reconnectTimer = null;
let reconnectDelayMs = WS_RECONNECT_INITIAL_MS;
let lastPongAt = null;

// ---------------------------------------------------------------------------
// Discord capability action declarations
// ---------------------------------------------------------------------------

/** Full capability schema sent to nami on connect. */
const CAPABILITIES = {
  actions: [
    {
      type: 'function',
      function: {
        name: 'add_reaction',
        description: 'Add an emoji reaction to a Discord message.',
        parameters: {
          type: 'object',
          properties: {
            channel_id: { type: 'string', description: 'Channel ID containing the message.' },
            message_id: { type: 'string', description: 'ID of the message to react to.' },
            emoji: { type: 'string', description: 'Emoji to add, e.g. "👍" or "🎉".' },
          },
          required: ['channel_id', 'message_id', 'emoji'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'remove_reaction',
        description: "Remove the bot's own emoji reaction from a Discord message.",
        parameters: {
          type: 'object',
          properties: {
            channel_id: { type: 'string', description: 'Channel ID containing the message.' },
            message_id: { type: 'string', description: 'ID of the message to remove the reaction from.' },
            emoji: { type: 'string', description: 'Emoji to remove.' },
          },
          required: ['channel_id', 'message_id', 'emoji'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'delete_message',
        description: 'Delete a Discord message (requires Manage Messages permission).',
        parameters: {
          type: 'object',
          properties: {
            channel_id: { type: 'string', description: 'Channel ID containing the message.' },
            message_id: { type: 'string', description: 'ID of the message to delete.' },
          },
          required: ['channel_id', 'message_id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'create_thread',
        description: 'Create a thread from a Discord message.',
        parameters: {
          type: 'object',
          properties: {
            channel_id: { type: 'string', description: 'Channel ID containing the parent message.' },
            message_id: { type: 'string', description: 'Message to start the thread from.' },
            name: { type: 'string', description: 'Thread name (max 100 characters).' },
            auto_archive_duration: {
              type: 'integer',
              description: 'Auto-archive after N minutes: 60, 1440, 4320, or 10080. Default 1440.',
              enum: [60, 1440, 4320, 10080],
            },
          },
          required: ['channel_id', 'message_id', 'name'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'pin_message',
        description: 'Pin a message in a Discord channel (requires Manage Messages permission).',
        parameters: {
          type: 'object',
          properties: {
            channel_id: { type: 'string', description: 'Channel ID containing the message.' },
            message_id: { type: 'string', description: 'ID of the message to pin.' },
          },
          required: ['channel_id', 'message_id'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'send_file',
        description: 'Send a file to a Discord channel or DM by URL.',
        parameters: {
          type: 'object',
          properties: {
            conversation_id: { type: 'string', description: 'Channel or DM channel ID.' },
            file_url: { type: 'string', description: 'Public URL of the file to send.' },
            filename: { type: 'string', description: 'Optional filename override.' },
            caption: { type: 'string', description: 'Optional text message to accompany the file.' },
          },
          required: ['conversation_id', 'file_url'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'send_message',
        description: 'Send a text message to any Discord channel (proactive — not a reply). Use to post in a different channel or server than the current conversation.',
        parameters: {
          type: 'object',
          properties: {
            channel_id: { type: 'string', description: 'ID of the Discord channel to post in.' },
            content: { type: 'string', description: 'Text content to send.' },
          },
          required: ['channel_id', 'content'],
        },
      },
    },
    {
      type: 'function',
      function: {
        name: 'send_dm',
        description: 'Send a direct message to a Discord user by their user ID.',
        parameters: {
          type: 'object',
          properties: {
            user_id: { type: 'string', description: 'Discord user ID to DM.' },
            content: { type: 'string', description: 'Text content to send.' },
          },
          required: ['user_id', 'content'],
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
  if (!discordClient || !discordClient.isReady()) {
    return { error: 'Discord client not ready' };
  }

  try {
    switch (action) {
      case 'add_reaction': {
        const channel = await discordClient.channels.fetch(params.channel_id);
        const message = await channel.messages.fetch(params.message_id);
        await message.react(params.emoji);
        return { success: true };
      }

      case 'remove_reaction': {
        const channel = await discordClient.channels.fetch(params.channel_id);
        const message = await channel.messages.fetch(params.message_id);
        const reaction = message.reactions.cache.find(r => r.emoji.toString() === params.emoji);
        if (reaction) await reaction.users.remove(discordClient.user.id);
        return { success: true };
      }

      case 'delete_message': {
        const channel = await discordClient.channels.fetch(params.channel_id);
        const message = await channel.messages.fetch(params.message_id);
        await message.delete();
        return { success: true };
      }

      case 'create_thread': {
        const channel = await discordClient.channels.fetch(params.channel_id);
        const message = await channel.messages.fetch(params.message_id);
        const thread = await message.startThread({
          name: params.name,
          autoArchiveDuration: params.auto_archive_duration || 1440,
        });
        return { success: true, thread_id: thread.id };
      }

      case 'pin_message': {
        const channel = await discordClient.channels.fetch(params.channel_id);
        const message = await channel.messages.fetch(params.message_id);
        await message.pin();
        return { success: true };
      }

      case 'send_file': {
        const channel = await discordClient.channels.fetch(params.conversation_id)
          || discordClient.channels.cache.get(params.conversation_id);
        const attachment = new AttachmentBuilder(params.file_url, { name: params.filename || undefined });
        await channel.send({ content: params.caption || undefined, files: [attachment] });
        return { success: true };
      }

      case 'send_message': {
        const channel = await discordClient.channels.fetch(params.channel_id);
        await channel.send(params.content);
        return { success: true };
      }

      case 'send_dm': {
        const user = await discordClient.users.fetch(params.user_id);
        const dm = await user.createDM();
        await dm.send(params.content);
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
 * Initialise the Discord.js client using config received from nami.
 * Called once after capabilities.ack.
 */
function initDiscordClient() {
  const token = namiConfig.token;
  if (!token) {
    console.error('[nami] capabilities.ack missing discord token — cannot initialise');
    process.exit(1);
  }

  const maxHistory = parseInt(String(namiConfig.max_history || '50'), 10);
  const permittedUsers = new Set(
    (namiConfig.permitted_users || []).map(id => String(id))
  );
  const aiChannels = new Set(
    (namiConfig.ai_channels || []).map(id => String(id))
  );

  // Apply configured history limit (previously hardcoded to 50)
  history.maxMessages = maxHistory;

  discordClient = new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMembers,
      GatewayIntentBits.GuildMessages,
      GatewayIntentBits.DirectMessages,
      GatewayIntentBits.MessageContent,
    ],
    partials: [Partials.Channel],
  });

  discordClient.once('clientReady', () => {
    console.log(`[nami] Discord bridge ready — connected as ${discordClient.user?.tag || discordClient.user?.id}`);
    platformInitialised = true;
  });

  discordClient.on('messageCreate', message => {
    handleIncomingDiscordMessage(message, permittedUsers, aiChannels).catch(error => {
      console.error(`[nami] Unhandled Discord message error: ${error.stack || error.message}`);
    });
  });

  discordClient.on('error', error => {
    console.error(`[nami] Discord client error: ${error.message}`);
  });

  discordClient.login(token);
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
    if (convQueues.get(conversationId) === next) {
      convQueues.delete(conversationId);
    }
  });
}

/**
 * Register a pending nami response using a 3-phase lifecycle state machine.
 *
 * Phase 1 — ACK (waiting for message.queued):
 *   Short ack timeout (10s). If it fires, the WS connection is dead.
 *
 * Phase 2 — QUEUED (waiting for message.processing):
 *   Long queue timeout (30 min). Nami is alive but busy. If it fires,
 *   react ❌ and send a short apology message.
 *
 * Phase 3 — PROCESSING (waiting for response.ready):
 *   Inactivity timeout resets on every status.update. If it fires, send
 *   message.recover to the server and enter a recovery grace period.
 *   If recovery also times out, fall back to Phase 2 behaviour (❌ + message).
 *
 * @param {string} conversationId
 * @param {import('discord.js').TextBasedChannel} channel - used to start typing on processing
 * @param {import('discord.js').Message} discordMessage   - used for reactions and replies on failure
 * @returns {Promise<string>}
 */
function registerPendingResponse(conversationId, channel, discordMessage) {
  const existing = pendingResponses.get(conversationId);
  if (existing) {
    existing.reject(new Error(`Superseded pending response for ${conversationId}`));
  }

  let resolveOuter, rejectOuter;
  const promise = new Promise((resolve, reject) => {
    resolveOuter = resolve;
    rejectOuter = reject;
  });

  const entry = {
    phase: 'ack',
    typingTimer: null,
    ackTimer: null,
    queueTimer: null,
    inactivityTimer: null,

    resolve(content) {
      entry._clearAll();
      pendingResponses.delete(conversationId);
      resolveOuter(content);
    },

    reject(error) {
      entry._clearAll();
      pendingResponses.delete(conversationId);
      rejectOuter(error);
    },

    /** Called by handleWsMessage when message.queued arrives. */
    onQueued() {
      if (entry.phase !== 'ack') return;
      clearTimeout(entry.ackTimer);
      entry.ackTimer = null;
      entry.phase = 'queued';
      entry.queueTimer = setTimeout(() => entry._onQueueTimeout(), QUEUE_TIMEOUT_MS);
    },

    /** Called by handleWsMessage when message.processing arrives. */
    async onProcessing() {
      if (entry.phase !== 'queued') return;
      clearTimeout(entry.queueTimer);
      entry.queueTimer = null;
      entry.phase = 'processing';
      entry.typingTimer = await startTypingLoop(channel);
      entry.resetInactivity();
    },

    /** Called by handleWsMessage on status.update — resets inactivity clock. */
    resetInactivity() {
      if (entry.phase === 'processing') {
        clearTimeout(entry.inactivityTimer);
        entry.inactivityTimer = setTimeout(() => entry._onInactivityTimeout(), INACTIVITY_TIMEOUT_MS);
      } else if (entry.phase === 'recovering') {
        // Status updates during recovery extend the grace period
        clearTimeout(entry.inactivityTimer);
        entry.inactivityTimer = setTimeout(() => entry._onRecoveryTimeout(), RECOVERY_TIMEOUT_MS);
      }
    },

    async _onQueueTimeout() {
      entry._clearAll();
      pendingResponses.delete(conversationId);
      console.warn(`[nami] Queue timeout for conv=${conversationId} — Nami never picked it up`);
      try {
        await discordMessage.react('❌');
      } catch { /* best effort */ }
      try {
        await discordMessage.reply({ content: "Sorry, I seem to have lost that message in my queue — could you try again?" });
      } catch { /* best effort */ }
      rejectOuter(new Error(`Queue timeout (${conversationId})`));
    },

    async _onInactivityTimeout() {
      if (entry.phase !== 'processing') return;
      console.warn(`[nami] Inactivity timeout for conv=${conversationId} — sending recovery request`);
      entry.phase = 'recovering';

      try {
        await sendWsEvent({ type: 'message.recover', conversation_id: conversationId });
      } catch (e) {
        console.error(`[nami] Failed to send message.recover: ${e.message}`);
      }

      // Recovery grace period — resets on every status.update via resetInactivity()
      entry.inactivityTimer = setTimeout(() => entry._onRecoveryTimeout(), RECOVERY_TIMEOUT_MS);
    },

    async _onRecoveryTimeout() {
      entry._clearAll();
      pendingResponses.delete(conversationId);
      console.error(`[nami] Recovery also timed out for conv=${conversationId} — giving up`);
      try {
        await discordMessage.react('❌');
      } catch { /* best effort */ }
      try {
        await discordMessage.reply({ content: "Something went wrong while I was working on that — could you try again?" });
      } catch { /* best effort */ }
      rejectOuter(new Error(`Recovery timeout (${conversationId})`));
    },

    _clearAll() {
      clearTimeout(entry.ackTimer);
      clearTimeout(entry.queueTimer);
      clearTimeout(entry.inactivityTimer);
      if (entry.typingTimer) {
        stopTypingLoop(entry.typingTimer);
        entry.typingTimer = null;
      }
    },
  };

  // Start Phase 1 ack timer
  entry.ackTimer = setTimeout(() => {
    entry._clearAll();
    pendingResponses.delete(conversationId);
    console.error(`[nami] Ack timeout — no message.queued received for conv=${conversationId}`);
    rejectOuter(new Error(`Ack timeout (${conversationId})`));
  }, ACK_TIMEOUT_MS);

  pendingResponses.set(conversationId, entry);
  return promise;
}

/**
 * Reject a single pending response.
 * @param {string} conversationId
 * @param {Error} error
 */
function rejectPendingResponse(conversationId, error) {
  const pending = pendingResponses.get(conversationId);
  if (pending) {
    pending.reject(error);
  }
}

/**
 * Reject all pending responses, typically after disconnect.
 * @param {Error} error
 */
function rejectAllPendingResponses(error) {
  for (const pending of pendingResponses.values()) {
    pending.reject(error);
  }
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
  if (namiWs && namiWs.readyState === WebSocket.OPEN) {
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const waiter = {
      resolve: () => resolve(),
      reject: error => reject(error),
      timeout: null,
    };

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
    if (!namiWs || namiWs.readyState !== WebSocket.OPEN) {
      return;
    }

    namiWs.send(JSON.stringify({ type: 'ping' }), error => {
      if (error) {
        console.warn(`[nami] Failed to send ping: ${error.message}`);
      }
    });
  }, PING_INTERVAL_MS);
}

/**
 * Stop the ping keepalive loop.
 */
function stopPingLoop() {
  if (pingTimer) {
    clearInterval(pingTimer);
    pingTimer = null;
  }
}

/**
 * Schedule a reconnect with exponential backoff.
 */
function scheduleReconnect() {
  if (reconnectTimer) {
    return;
  }

  const delayMs = reconnectDelayMs;
  console.log(`[nami] Reconnecting to nami WS in ${Math.round(delayMs / 1000)}s`);

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectNamiWebSocket();
  }, delayMs);

  reconnectDelayMs = Math.min(reconnectDelayMs * 2, WS_RECONNECT_MAX_MS);
}

/**
 * Open the nami adapter WebSocket connection.
 */
function connectNamiWebSocket() {
  if (namiWs && (namiWs.readyState === WebSocket.OPEN || namiWs.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const wsUrl = buildWsUrl();
  console.log(`[nami] Connecting to nami WS: ${redactWsUrl(wsUrl)}`);

  const ws = new WebSocket(wsUrl);
  namiWs = ws;

  ws.on('open', () => {
    console.log('[nami] Connected to nami WS — sending capabilities.register');
    reconnectDelayMs = WS_RECONNECT_INITIAL_MS;
    lastPongAt = Date.now();
    startPingLoop();

    // Always re-register capabilities on reconnect so nami has current state
    ws.send(JSON.stringify({ type: 'capabilities.register', data: CAPABILITIES }), error => {
      if (error) console.error(`[nami] Failed to send capabilities.register: ${error.message}`);
    });

    // Query state of any conversations that were pending before the reconnect
    for (const [convId] of pendingResponses) {
      ws.send(JSON.stringify({ type: 'message.query', conversation_id: convId }), error => {
        if (error) console.warn(`[nami] Failed to send message.query for conv=${convId}: ${error.message}`);
      });
    }

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
    if (namiWs === ws) {
      namiWs = null;
    }

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

  if (!namiWs || namiWs.readyState !== WebSocket.OPEN) {
    throw new Error('nami WebSocket not connected');
  }

  await new Promise((resolve, reject) => {
    namiWs.send(JSON.stringify(event), error => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

/**
 * Send a message.received event and wait for the matching response.ready.
 * @param {Record<string, unknown> & { conversation_id: string }} event
 * @param {import('discord.js').TextBasedChannel} channel
 * @param {import('discord.js').Message} discordMessage
 * @returns {Promise<string>}
 */
async function requestNamiResponse(event, channel, discordMessage) {
  const conversationId = event.conversation_id;
  const responsePromise = registerPendingResponse(conversationId, channel, discordMessage);

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
        initDiscordClient();
        if (configResolve) {
          configResolve();
          configResolve = null;
        }
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
      const convId = String(event.conversation_id || '');
      const content = String(event.content || '').trim();
      const pending = pendingResponses.get(convId);
      if (pending) {
        pending.resolve(content);
        return;
      }
      // Entry already gone (e.g. recovery timeout fired while pipeline kept running).
      // Fall back to direct delivery using the channel registry.
      console.warn(`[nami] No pending response for conv=${convId} — attempting fallback delivery`);
      if (content) {
        const ctx = conversationChannels.get(convId);
        if (ctx) {
          await safeReply(ctx.discordMessage, content).catch(() => {});
        }
      }
      return;
    }

    case 'message.queued': {
      const pending = pendingResponses.get(String(event.conversation_id || ''));
      if (pending) pending.onQueued();
      return;
    }

    case 'message.processing': {
      const pending = pendingResponses.get(String(event.conversation_id || ''));
      if (pending) await pending.onProcessing();
      return;
    }

    case 'message.status': {
      // Response to a message.query — used for reconnect recovery
      const convId = String(event.conversation_id || '');
      const pending = pendingResponses.get(convId);
      if (!pending) return;

      if (event.state === 'done' && event.response) {
        pending.resolve(event.response);
      } else if (event.state === 'error' || event.state === 'unknown') {
        pending.reject(new Error(`Server reported state=${event.state} for conv=${convId}`));
      }
      // queued/processing: stay in current phase — server will send lifecycle events
      return;
    }

    case 'send.message': {
      if (!event.conversation_id || typeof event.content !== 'string') {
        console.warn('[nami] Ignoring invalid send.message payload');
        return;
      }
      await sendDiscordChannelMessage(String(event.conversation_id), event.content);
      return;
    }

    case 'send.dm': {
      if (!event.user_id || typeof event.content !== 'string') {
        console.warn('[nami] Ignoring invalid send.dm payload');
        return;
      }
      await sendDiscordDirectMessage(String(event.user_id), event.content);
      return;
    }

    case 'status.update': {
      console.log(`[nami] status.update: ${event.status || ''}`);
      if (event.conversation_id) {
        const pending = pendingResponses.get(String(event.conversation_id));
        if (pending?.resetInactivity) pending.resetInactivity();
      }
      return;
    }

    default: {
      console.log(`[nami] Ignoring WS event type=${event.type}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Discord message helpers
// ---------------------------------------------------------------------------

/**
 * Extract image attachment URLs from a Discord message.
 * @param {import('discord.js').Message} message
 * @returns {string[]}
 */
function extractImageUrls(message) {
  return Array.from(message.attachments.values())
    .filter(attachment => {
      if (attachment.contentType && attachment.contentType.startsWith('image/')) {
        return true;
      }
      const haystack = `${attachment.name || ''} ${attachment.url || ''}`;
      return /\.(png|jpe?g|gif|webp|bmp|svg)(\?.*)?$/i.test(haystack);
    })
    .map(attachment => attachment.url);
}

/**
 * Get the Discord conversation ID used by nami.
 * @param {import('discord.js').Message} message
 * @returns {string}
 */
function getConversationId(message) {
  return String(message.channel.isDMBased() ? message.channel.id : message.channelId);
}

/**
 * Get a friendly display name for a Discord user.
 * @param {import('discord.js').Message} message
 * @returns {string}
 */
function getDisplayName(message) {
  // Use the global Discord display name or the unique username — intentionally
  // skipping member.displayName (server nickname) to prevent impersonation via
  // a carefully chosen server nickname.
  return message.author.globalName || message.author.username;
}

/**
 * Decide whether the bridge should respond to a Discord message.
 * @param {import('discord.js').Message} message
 * @param {Set<string>} permittedUsers
 * @param {Set<string>} aiChannels
 * @returns {boolean}
 */
function shouldRespond(message, permittedUsers, aiChannels) {
  if (message.author.bot && discordClient.user && message.author.id === discordClient.user.id) {
    return false;
  }
  if (message.author.bot) {
    return false;
  }
  if (aiChannels.has(String(message.channelId))) {
    return true;
  }
  const isPermitted = permittedUsers.size > 0 && permittedUsers.has(message.author.id);
  if (message.channel.isDMBased()) {
    return isPermitted;
  }
  return Boolean(discordClient.user && isPermitted && message.mentions.has(discordClient.user));
}

/**
 * Start a typing keepalive loop for a Discord channel.
 * @param {import('discord.js').TextBasedChannel} channel
 * @returns {Promise<NodeJS.Timeout | null>}
 */
async function startTypingLoop(channel) {
  if (typeof channel.sendTyping !== 'function') {
    return null;
  }
  try {
    await channel.sendTyping();
  } catch (error) {
    console.warn(`[nami] Failed to start typing indicator: ${error.message}`);
  }
  return setInterval(() => {
    channel.sendTyping().catch(() => {});
  }, TYPING_INTERVAL_MS);
}

/**
 * Stop a typing keepalive loop.
 * @param {NodeJS.Timeout | null} timer
 */
function stopTypingLoop(timer) {
  if (timer) {
    clearInterval(timer);
  }
}

const DISCORD_MAX_CHARS = 1900; // Discord hard limit is 2000 for messages and replies

/**
 * Split a long message into chunks that fit within Discord's character limit.
 * Tries to break on newlines first, then spaces, otherwise hard-cuts.
 * @param {string} content
 * @returns {string[]}
 */
function chunkMessage(content) {
  if (content.length <= DISCORD_MAX_CHARS) return [content];

  const chunks = [];
  let remaining = content;
  while (remaining.length > DISCORD_MAX_CHARS) {
    let cut = remaining.lastIndexOf('\n', DISCORD_MAX_CHARS);
    if (cut <= 0) cut = remaining.lastIndexOf(' ', DISCORD_MAX_CHARS);
    if (cut <= 0) cut = DISCORD_MAX_CHARS;
    chunks.push(remaining.slice(0, cut).trimEnd());
    remaining = remaining.slice(cut).trimStart();
  }
  if (remaining.length > 0) chunks.push(remaining);
  return chunks;
}

/**
 * Reply to a Discord message without crashing the bridge on failure.
 * Long responses are automatically split into sequential chunks.
 * @param {import('discord.js').Message} message
 * @param {string} content
 * @returns {Promise<boolean>}
 */
async function safeReply(message, content) {
  try {
    const chunks = chunkMessage(content);
    await message.reply(chunks[0]);
    for (const chunk of chunks.slice(1)) {
      await message.channel.send(chunk);
    }
    return true;
  } catch (error) {
    console.error(`[nami] Failed to send Discord reply (${message.channelId}): ${error.message}`);
    return false;
  }
}

/**
 * Send a proactive message to a Discord channel.
 * Long responses are automatically split into sequential chunks.
 * @param {string} conversationId
 * @param {string} content
 * @returns {Promise<void>}
 */
async function sendDiscordChannelMessage(conversationId, content) {
  if (!discordClient) return;
  try {
    const channel = discordClient.channels.cache.get(conversationId) || await discordClient.channels.fetch(conversationId);
    if (!channel || typeof channel.send !== 'function') {
      throw new Error('Channel not found or not sendable');
    }
    for (const chunk of chunkMessage(content)) {
      await channel.send(chunk);
    }
    history.append(conversationId, 'assistant', 'nami [proactive]', content);
    console.log(`[nami] ✓ sent channel message conv=${conversationId}`);
  } catch (error) {
    console.error(`[nami] Failed to send channel message (${conversationId}): ${error.message}`);
  }
}

/**
 * Send a proactive direct message to a Discord user.
 * Long responses are automatically split into sequential chunks.
 * @param {string} userId
 * @param {string} content
 * @returns {Promise<void>}
 */
async function sendDiscordDirectMessage(userId, content) {
  if (!discordClient) return;
  try {
    const user = await discordClient.users.fetch(userId);
    const dm = await user.createDM();
    for (const chunk of chunkMessage(content)) {
      await dm.send(chunk);
    }
    history.append(String(dm.id), 'assistant', 'nami [proactive]', content);
    console.log(`[nami] ✓ sent DM user=${userId}`);
  } catch (error) {
    console.error(`[nami] Failed to send Discord DM (${userId}): ${error.message}`);
  }
}

/**
 * Handle one inbound Discord user message.
 * @param {import('discord.js').Message} message
 * @param {Set<string>} permittedUsers
 * @param {Set<string>} aiChannels
 * @returns {Promise<void>}
 */
async function handleIncomingDiscordMessage(message, permittedUsers, aiChannels) {
  if (!shouldRespond(message, permittedUsers, aiChannels)) return;

  const conversationId = getConversationId(message);

  // Register channel context for fallback delivery (survives pending entry deletion)
  conversationChannels.set(conversationId, { channel: message.channel, discordMessage: message });

  enqueue(conversationId, async () => {
    // No typing loop here — it starts when message.processing arrives from nami
    try {
      const displayName = getDisplayName(message);
      const content = message.content || '';

      history.append(conversationId, 'user', displayName, content);

      const event = {
        type: 'message.received',
        conversation_id: conversationId,
        user_id: String(message.author.id),
        user_name: message.author.username || String(message.author.id),
        display_name: displayName,
        content,
        is_dm: message.channel.isDMBased(),
        channel_name: message.channel.isDMBased() ? '' : (message.channel.name || ''),
        guild_name: message.guild?.name ?? null,
        image_urls: extractImageUrls(message),
        history: history.getMessages(conversationId),
      };

      console.log(`[nami] → WS message.received conv=${conversationId} user=${message.author.id}`);
      const reply = await requestNamiResponse(event, message.channel, message);

      if (!reply || reply.trim() === '') {
        console.warn(`[nami] Empty reply for conv=${conversationId} — skipping`);
        return;
      }

      if (reply === '<ignore>') {
        history.append(conversationId, 'assistant', 'assistant', '<ignore>');
        console.log(`[nami] ← WS <ignore> conv=${conversationId}`);
        await message.react('❌').catch(() => {});
        return;
      }

      const sent = await safeReply(message, reply);
      if (sent) {
        history.append(conversationId, 'assistant', 'assistant', reply);
        console.log(`[nami] ✓ replied conv=${conversationId}`);
      }
    } catch (error) {
      console.error(`[nami] Failed to process Discord message (${conversationId}): ${error.stack || error.message}`);
    }
    // Note: typing loop is cleaned up by the pending response entry's _clearAll()
  });
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * Start the nami WS connection and wait for config before anything else.
 * @returns {Promise<void>}
 */
async function main() {
  if (!BRIDGE_SECRET) {
    throw new Error('BRIDGE_SECRET is required');
  }

  connectNamiWebSocket();

  // Wait for capabilities.ack (and thus namiConfig) before proceeding
  const CONFIG_TIMEOUT_MS = 30_000;
  await Promise.race([
    configPromise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Timed out waiting for capabilities.ack from nami')), CONFIG_TIMEOUT_MS)
    ),
  ]);

  console.log('[nami] Config received — Discord bridge fully operational');
}

main().catch(error => {
  console.error(`[nami] Fatal startup error: ${error.stack || error.message}`);
  process.exit(1);
});
