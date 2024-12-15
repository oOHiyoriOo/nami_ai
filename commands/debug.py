import discord
import logging
import __main__
import asyncio
import json

from tinydb import TinyDB, Query
from lib.tool_loader import load_tools

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

    def register_commands(self):
        @self.tree.command(name="debug", description="A debug command to test features.")
        async def debug(interaction: discord.Interaction):
            await interaction.response.send_message("Triggered!")
            try:
                if interaction.user.id != 210428907386699777:
                    await interaction.followup.send("You do not have the required permissions to use this command.")
                else:
                    external_tools = await load_tools(__main__.client)

                    tool = next((t for t in external_tools if t['function']["name"] == "query_discord_user"), None)
                    # Call the function using asyncio.ensure_future
                    query_discord_user : function = tool['func']

                    userdata = await query_discord_user(client=__main__.client, user_id=str(interaction.user.id))
                    logging.info(userdata)

                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message( omsg.id )

            except Exception as e:
                logging.exception(e)
                await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")
