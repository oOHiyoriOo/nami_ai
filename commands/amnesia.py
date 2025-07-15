import discord, logging, asyncio, os

from langchain_community.chat_message_histories import ChatMessageHistory
from lib.sqlite_helper import SQLiteHelper
from lib.ollama_helper import OllamaHelper
from lib.global_registry import g_data

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()
        self.user_memories = {}
        self.lock = asyncio.Lock()
        self.file = os.path.abspath('./user_memories.json')
        self.sqlite_helper : SQLiteHelper = SQLiteHelper(client) 
        self.ollama_helper : OllamaHelper = OllamaHelper(client, cfg)

    def register_commands(self):
        @self.tree.command(name="amnesia", description="Let the AI Forget your Conversation!")
        async def amnesia(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                cfg = g_data.get("cfg")
                # Only allow permitted users (see toggle_ai.py)
                if int(interaction.user.id) not in cfg.data['dc']['permitted_users']:
                    await interaction.followup.send("You do not have the required permissions to use this command.")
                    return

                conversation_id = str(interaction.channel.id)

                await self.sqlite_helper.delete_message_history(conversation_id=conversation_id)

                if not await self.sqlite_helper.retrieve_message(
                    conversation_id=conversation_id,
                    limit=1,
                ):
                    await interaction.followup.send("User context cleared successfully!")

                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message(omsg.id)

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")