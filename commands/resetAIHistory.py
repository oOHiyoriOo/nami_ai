import discord, logging, asyncio, __main__, os, json

from langchain_community.chat_message_histories import ChatMessageHistory

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()
        self.user_memories = {}
        self.lock = asyncio.Lock()
        self.file = os.path.abspath('./user_memories.json')

    def register_commands(self):
        @self.tree.command(name="amnesia", description="Let the AI Forget your Conversation!")
        async def amnesia(interaction: discord.Interaction):
            await interaction.response.defer()
            try:

                await self.load_memories_async()
                self.user_memories[str(interaction.user.id)] = []
                await self.save_memories_async()

                await interaction.followup.send("User context cleared successfully!")
                
                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message(omsg.id)

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")


    # Async load memories
    async def load_memories_async(self):
        async with self.lock:
            try:
                with open(self.file, 'r') as f:
                    self.user_memories.update(json.load(f))
            except json.JSONDecodeError:
                logging.error("Error decoding the memory file.")
            except FileNotFoundError:
                logging.warning(f"Memory file {self.file} not found. Creating a new one.")

    # Async save memories
    async def save_memories_async(self):
        async with self.lock:
            with open(self.file, 'w') as f:
                json.dump(self.user_memories, f)