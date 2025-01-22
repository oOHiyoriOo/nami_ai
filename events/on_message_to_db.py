import aiosqlite
import json
from lib.load_events import register_event

# JSON to define the table schema
model = {
    "table_name": "messages",
    "fields": {
        "id": "TEXT PRIMARY KEY",  # Message ID
        "author": "TEXT",  # Author of the message
        "content": "TEXT",  # Message content
        "timestamp": "TEXT",  # Timestamp of the message
        "channel": "TEXT",  # Channel where the message was sent
        "guild": "TEXT"  # Guild/Server name (if applicable)
    }
}

# Initialize SQLite database and create table if it doesn't exist
async def init_db():
    async with aiosqlite.connect("messages.db") as conn:
        # Build the CREATE TABLE SQL dynamically from the JSON schema
        fields = ", ".join([f"{key} {value}" for key, value in model["fields"].items()])
        create_table_sql = f"CREATE TABLE IF NOT EXISTS {model['table_name']} ({fields})"
        await conn.execute(create_table_sql)
        await conn.commit()

# Save message to SQLite
async def message_saver(msg):
    data = {
        "id": msg.id,
        "author": str(msg.author),
        "content": msg.content,
        "timestamp": msg.created_at.isoformat(),
        "channel": str(msg.channel),
        "guild": str(msg.guild) if msg.guild else None
    }

    async with aiosqlite.connect("messages.db") as conn:
        # Prepare INSERT statement
        fields = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        insert_sql = f"INSERT OR REPLACE INTO {model['table_name']} ({fields}) VALUES ({placeholders})"
        await conn.execute(insert_sql, tuple(data.values()))
        await conn.commit()

# Setup function
async def setup(client, cfg):
    # Initialize the database and table
    await init_db()
    
    # Register the event
    register_event("on_message", message_saver)
