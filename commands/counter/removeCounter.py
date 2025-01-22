import discord
import logging
import __main__

from tinydb import TinyDB, Query

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

        self.no_permission = "You do not have the required permissions to use this command."
        self.db = TinyDB('counters.json')

    def register_commands(self):
        @self.tree.command(name="removecounter", description="Remove a counter by name")
        async def removecounter(interaction: discord.Interaction, name: str):
            await interaction.response.defer()
            try:
                user_data = self.db.get(Query().user == interaction.user.id)
            
                if user_data and user_data.get('counters'):
                    counters = user_data['counters']
                    counters = [counter for counter in counters if counter['name'] != name]
                    self.db.update({"counters": counters}, Query().user == interaction.user.id)
                    await interaction.followup.send(f"Counter {name} removed.")
                else:
                    await interaction.followup.send("No counters found to remove.")

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send(self.no_permission)