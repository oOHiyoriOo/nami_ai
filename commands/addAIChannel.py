import discord
import logging
import asyncio
import yaml
import os
import __main__

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()
        self.dm_allow = [210428907386699777, 345007494009061377, 405715159084957698, 487655467149950976]

    def register_commands(self):
        @self.tree.command(name="toggle_ai", description="Toggles AI for the current channel")
        async def toggle_ai(interaction: discord.Interaction):
            await interaction.response.defer()
            try:
                # Always allow the command for your user ID
                if interaction.user.id in self.dm_allow:
                    is_admin = True
                else:
                    # Check if the interaction is in a guild and the user has the admin role
                    if interaction.guild is not None:
                        admin_role = discord.utils.get(interaction.guild.roles, id=__main__.cfg.data['dc']['admin_role'])
                        is_admin = admin_role in interaction.user.roles
                    else:
                        is_admin = False

                if not is_admin:
                    await interaction.followup.send("You do not have the required permissions to use this command.")
                else:
                    __main__.cfg.load()
                    if interaction.channel.id in __main__.cfg.data['ai_channel']:
                        __main__.cfg.data['ai_channel'].remove(interaction.channel.id)
                        await interaction.followup.send("AI has been disabled for this channel.")
                    else:
                        __main__.cfg.data['ai_channel'].append(interaction.channel.id)
                        await interaction.followup.send("AI has been enabled for this channel.")
                    __main__.cfg.save()

                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message(omsg.id)

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")