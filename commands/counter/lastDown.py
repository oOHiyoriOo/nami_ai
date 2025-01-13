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
        @self.tree.command(name="lastdown", description="Decrement the last added counter")
        async def lastdown(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                user_data = self.db.get(Query().user == interaction.user.id)
                
                if user_data and user_data.get('counters'):
                    counters = user_data['counters']
                    counters[-1]['count'] -= 1
                    self.db.update({"counters": counters}, Query().user == interaction.user.id)
                    await interaction.followup.send(f"Counter {counters[-1]['name']} decremented to {counters[-1]['count']}")
                else:
                    await interaction.followup.send("No counters found to decrement.")

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send(self.no_permission)