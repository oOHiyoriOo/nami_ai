/**
 * history.js — SQLite-backed per-conversation message history for Discord bridge.
 *
 * Rolling message buffer per conversation_id, mirroring the
 * role of history_db in the main nami_ai application.
 * Identical structure to the WhatsApp bridge history.
 */

const Database = require('better-sqlite3');

const DEFAULT_MAX_MESSAGES = 50;

class ConversationHistory {
  /**
   * @param {string} dbPath      - Path to the SQLite file (default: "history.db")
   * @param {number} maxMessages - Max messages kept per conversation (rolling window)
   */
  constructor(dbPath = 'history.db', maxMessages = DEFAULT_MAX_MESSAGES) {
    this.maxMessages = maxMessages;
    this.db = new Database(dbPath);
    this._initSchema();
  }

  /** @private */
  _initSchema() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT    NOT NULL,
        role            TEXT    NOT NULL,
        name            TEXT    NOT NULL DEFAULT '',
        content         TEXT    NOT NULL,
        timestamp       INTEGER NOT NULL DEFAULT (unixepoch())
      );
      CREATE INDEX IF NOT EXISTS idx_conv_ts
        ON messages (conversation_id, timestamp);
    `);
    // Migrate existing DBs that lack the name column
    const cols = this.db.prepare('PRAGMA table_info(messages)').all().map(c => c.name);
    if (!cols.includes('name')) {
      this.db.exec("ALTER TABLE messages ADD COLUMN name TEXT NOT NULL DEFAULT ''");
    }
  }

  /**
   * Append a message and prune the conversation to the rolling window.
   * @param {string} conversationId
   * @param {"user"|"assistant"} role
   * @param {string} name      - Display name of the sender
   * @param {string} content
   */
  append(conversationId, role, name, content) {
    this.db
      .prepare('INSERT INTO messages (conversation_id, role, name, content) VALUES (?, ?, ?, ?)')
      .run(conversationId, role, name, content);

    // Prune oldest messages beyond the rolling window
    this.db
      .prepare(`
        DELETE FROM messages
        WHERE conversation_id = ?
          AND id NOT IN (
            SELECT id FROM messages
            WHERE conversation_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
          )
      `)
      .run(conversationId, conversationId, this.maxMessages);
  }

  /**
   * Return all messages for a conversation in chronological order,
   * formatted with name prefix, ready for the nami_ai /api/chat messages array.
   * @param {string} conversationId
   * @returns {{ role: string, content: string }[]}
   */
  getMessages(conversationId) {
    return this.db
      .prepare('SELECT role, name, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC')
      .all(conversationId)
      .map(row => {
        const ts = new Date(row.timestamp * 1000).toISOString().replace('T', ' ').slice(0, 19);
        const prefix = row.name ? `[${row.name}] [${ts}]: ` : '';
        return { role: row.role, content: `${prefix}${row.content}` };
      });
  }

  /**
   * Wipe history for a single conversation (e.g. on !clear command).
   * @param {string} conversationId
   */
  clear(conversationId) {
    this.db.prepare('DELETE FROM messages WHERE conversation_id = ?').run(conversationId);
  }
}

module.exports = { ConversationHistory };
