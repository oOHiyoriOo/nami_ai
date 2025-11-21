import discord
import logging
import asyncio

from lib.global_registry import g_data

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

    def register_commands(self):
        @self.tree.command(name="toggle_ai", description="Toggles AI for the current channel")
        async def toggle_ai(interaction: discord.Interaction):
            await interaction.response.defer()

            cfg = g_data.get("cfg")

            try:
                # Always allow the command for your user ID
                if int(interaction.user.id) in self.cfg.data['dc']['permitted_users']:
                    is_admin = True
                else:
                    # Check if the interaction is in a guild and the user has the admin role
                    if interaction.guild is not None:
                        admin_role = discord.utils.get(interaction.guild.roles, id=cfg.data['dc']['admin_role'])
                        is_admin = admin_role in interaction.user.roles
                    else:
                        is_admin = False

                if not is_admin:
                    await interaction.followup.send("You do not have the required permissions to use this command.")
                else:
                    cfg.load()
                    if int(interaction.channel.id) in cfg.data['ai_channel']:
                        cfg.data['ai_channel'].remove(int(interaction.channel.id))
                        await interaction.followup.send("AI has been disabled for this channel.")
                    else:
                        cfg.data['ai_channel'].append(int(interaction.channel.id))
                        await interaction.followup.send("AI has been enabled for this channel.")
                    cfg.save()

                await asyncio.sleep(3)
                omsg = (await interaction.original_response())
                await interaction.followup.delete_message(omsg.id)

            except Exception as e:
                logging.error(f"Exception: {str(e)}")
                await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")