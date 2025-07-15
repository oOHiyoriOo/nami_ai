import discord
import sys

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

    def register_commands(self):
        @self.tree.command(name="join", description="Set's a User's nickname")
        async def join(interaction: discord.Interaction):
            if interaction.user.id == 210428907386699777:
                if interaction.user.voice is not None:
                    channel = interaction.user.voice.channel
                    await channel.connect()
                    await interaction.response.send_message("✔️")

