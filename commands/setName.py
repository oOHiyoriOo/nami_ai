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

    def register_commands(self):
        @self.tree.command(name="setname", description="Set's a User's nickname")
        async def setname(interaction: discord.Interaction, user: discord.Member, nickname: str):
            await interaction.response.defer()
            try:
                admin_role = discord.utils.get(interaction.guild.roles, id=__main__.cfg.data['dc']['admin_role'])
                if admin_role not in interaction.user.roles:
                    await interaction.followup.send("You do not have the required permissions to use this command.")
                else:
                    db = TinyDB('nicknames.json')
                    query = Query()
                    result = db.search((query.id == user.id) & (query.guild_id == interaction.guild_id))

                    if result:
                        db.update({'nickname': nickname}, (query.id == user.id) & (query.guild_id == interaction.guild_id))
                    else:
                        db.insert({'id': user.id, 'guild_id': interaction.guild_id, 'nickname': nickname})

                    await interaction.followup.send(f"Name for {user.name} set to {nickname}")

                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message( omsg.id )

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")