import discord
import logging
import __main__
import asyncio

from tinydb import TinyDB, Query

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

        self.no_permission = "You do not have the required permissions to use this command."
        self.db = TinyDB('counters.json')

    def register_commands(self):
        @self.tree.command(name="newcounter", description="Start a new personal counter")
        async def newcounter(interaction: discord.Interaction, name: str = "Counter", start: int = 0):
            await interaction.response.defer()
            try:
                user_data = self.db.get(Query().user == interaction.user.id)
                
                if user_data:
                    counters = user_data.get('counters', [])
                    counters.append({"name": name, "count": start})
                    self.db.update({"counters": counters}, Query().user == interaction.user.id)
                else:
                    self.db.insert({"user": interaction.user.id, "counters": [{"name": name, "count": start}]})
                
                await interaction.followup.send(f"Counter {name} started at {start}")

                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message(omsg.id)

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send(self.no_permission)