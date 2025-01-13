import discord, logging, __main__, asyncio, aiohttp, pandas as pd, pytz

from datetime import datetime, timedelta

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()

    async def make_post_request(self, payload : dict, url: str, headers: dict):
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.exception(f"Request failed, Status: {response.status}")
                    return {
                                "token": "Failed",
                                "uses_allowed": 0,
                                "pending": 0,
                                "completed": 0,
                                "expiry_time": 0
                            }

    def register_commands(self):
        @self.tree.command(name="create_matrix_token", description="Create a Registration Token for Matrix")
        async def create_matrix_token(interaction: discord.Interaction, uses_allowed: int, length: int, access_token: str):
            client : discord.Client = __main__.client
            await interaction.response.defer()

            if interaction.user.id != 210428907386699777:
                await interaction.followup.send("You do not have the required permissions to use this command.")
            else:
                try:
                    admin_role = discord.utils.get(interaction.guild.roles, id=__main__.cfg.data['dc']['admin_role'])
                    if admin_role not in interaction.user.roles:
                        await interaction.followup.send("You do not have the required permissions to use this command.")
                    else:
                        expiry_time = datetime.now(pytz.utc) + timedelta(hours=24)
                        expiry_timestamp = int(expiry_time.timestamp() * 1000)  # Convert to milliseconds

                        result = await self.make_post_request(
                            {
                                "uses_allowed": uses_allowed,
                                "expiry_time": expiry_timestamp,
                                "length": length
                            }, 
                            "https://msg.hanime.zip/_synapse/admin/v1/registration_tokens/new", 
                            {
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": "application/json"
                            }
                        )

                        # Wrap the result in a list to create a DataFrame with a single row
                        df = pd.DataFrame([result])
                        markdown_table = df.to_markdown(index=False)
                        await interaction.followup.send(f"```md\n{markdown_table}\n```")

                    await asyncio.sleep(10)
                    omsg = (await interaction.original_response())
                    await interaction.followup.delete_message( omsg.id )

                except Exception as e:
                    logging.exception(f"Exception: {str(e)}")
                    await interaction.channel.send("Tut mir leid, hier ist was schief gelaufen.")