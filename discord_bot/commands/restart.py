import discord
import sys

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

    def register_commands(self):
        @self.tree.command(name="restart", description="Set's a User's nickname")
        async def restart(interaction: discord.Interaction):
            if interaction.user.id == 210428907386699777:
                await interaction.response.send_message("Restarting...")
                sys.exit(0)
