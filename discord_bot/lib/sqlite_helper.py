import discord

from lib.global_registry import g_data
from lib.asyncsqlite import AsyncSQLite

class SQLiteHelper:
    def __init__(self, client: discord.Client):
        self.client: discord.Client = client

    async def handle_new_message(self, channel_id, channel_name, user_id, user_name, message_id, content, conversation_id, reply_to=None):
        # Ensure all Discord IDs are ints
        channel_id = int(channel_id)
        user_id = int(user_id)
        message_id = int(message_id) if message_id is not None else None
        conversation_id = str(conversation_id)  # conversation_id may be a string (channel id)
        reply_to = int(reply_to) if reply_to is not None else None

        await self.insert_channel(g_data.get("history_db"), channel_id, channel_name)
        await self.insert_user(g_data.get("history_db"), user_id, user_name)
        await self.insert_message(g_data.get("history_db"), channel_id, user_id, message_id, content, conversation_id, reply_to)

    # ============================================================================================
    #                                       INSERTIONS
    # ============================================================================================

    async def insert_channel(self, history_db: AsyncSQLite, channel_id, channel_name):
        channel_id = int(channel_id)
        query = "SELECT id FROM channels WHERE discord_channel_id = ?"
        channel = await history_db.fetch_one(query, (channel_id,))
        
        if not channel:
            # If the channel does not exist, insert it
            insert_query = "INSERT INTO channels (discord_channel_id, name) VALUES (?, ?)"
            await history_db.execute(insert_query, (channel_id, channel_name))
            

    async def insert_user(self, history_db: AsyncSQLite, user_id, user_name):
        user_id = int(user_id)
        # Fallback to string user_id if user_name is empty
        if not user_name:
            user_name = str(user_id)
        query = "SELECT id FROM users WHERE discord_user_id = ?"
        user = await history_db.fetch_one(query, (user_id,))
        if not user:
            insert_query = "INSERT INTO users (discord_user_id, name) VALUES (?, ?)"
            await history_db.execute(insert_query, (user_id, user_name))
            

    async def insert_message(self, history_db: AsyncSQLite, channel_id, user_id, message_id, content, conversation_id, reply_to=None, timestamp=None):
        channel_id = int(channel_id)
        user_id = int(user_id)
        message_id = int(message_id) if message_id is not None else None
        reply_to = int(reply_to) if reply_to is not None else None

        # Insert message into the messages table
        # discord_message_id can now be None (nullable)
        # Optionally allow explicit timestamp for future-proofing
        if timestamp:
            insert_query = """
                INSERT INTO messages (discord_message_id, channel_id, conversation_id, user_id, content, reply_to, timestamp)
                VALUES (?, 
                        (SELECT id FROM channels WHERE discord_channel_id = ?),
                        ?,
                        (SELECT id FROM users WHERE discord_user_id = ?), 
                        ?, ?, ?)
            """
            await history_db.execute(insert_query, (message_id, channel_id, conversation_id, user_id, content, reply_to, timestamp))
        else:
            insert_query = """
                INSERT INTO messages (discord_message_id, channel_id, conversation_id, user_id, content, reply_to)
                VALUES (?, 
                        (SELECT id FROM channels WHERE discord_channel_id = ?),
                        ?,
                        (SELECT id FROM users WHERE discord_user_id = ?), 
                        ?, ?)
            """
            await history_db.execute(insert_query, (message_id, channel_id, conversation_id, user_id, content, reply_to))
        
    
    async def retrieve_message(self, conversation_id, limit: int):
        # conversation_id may be a string (channel id)
        history_db: AsyncSQLite = g_data.get("history_db")
        
        # Retrieve messages content along with user information, ordered by timestamp (oldest first), limited by the given number
        message_query = """
            SELECT messages.content, users.discord_user_id, users.name, messages.timestamp
            FROM messages 
            JOIN users ON messages.user_id = users.id
            WHERE conversation_id = ? 
            ORDER BY messages.timestamp ASC
            LIMIT ?
        """

        messages = await history_db.fetch_all(message_query, (conversation_id, limit))
        return [
            {
                "content": row[0],
                "user_id": int(row[1]),  # Always int
                "name": row[2],
                "timestamp": row[3]
            }
            for row in messages
        ]

    # New function to delete message history by conversation_id
    async def delete_message_history(self, conversation_id: str):
        history_db: AsyncSQLite = g_data.get("history_db")
        delete_query = "DELETE FROM messages WHERE conversation_id = ?"
        await history_db.execute(delete_query, (conversation_id,))