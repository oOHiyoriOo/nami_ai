import discord
from discord import app_commands
import logging

from lib.memory_db import MemoryDb
from lib.global_registry import g_data

ALLOWED_USER_ID = 210428907386699777

class Command:
    def __init__(self, client, cfg):
        self.client = client
        self.cfg = cfg
        self.neo4j_group = app_commands.Group(name="neo4j", description="Neo4j query commands.")
        self.register_commands()

    def register_commands(self):
        self.client.tree.add_command(self.neo4j_group)

        @self.neo4j_group.command(name="query", description="Execute a Cypher query on Neo4j and return the result.")
        @app_commands.describe(query="The Cypher query to execute.")
        async def query(interaction: discord.Interaction, query: str):
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id != ALLOWED_USER_ID:
                await interaction.followup.send("You do not have the required permissions to use this command.", ephemeral=True)
                return

            try:
                memory_db: MemoryDb = g_data.get("memory_db")
                if not memory_db:
                    await interaction.followup.send("MemoryDB instance not found.", ephemeral=True)
                    return

                driver = memory_db.get_driver()
                with driver.session() as session:
                    result = session.run(query)
                    records = list(result)
                    if not records:
                        await interaction.followup.send("Query executed. No results returned.", ephemeral=True)
                        return

                    # Format the first few results for display
                    lines = []
                    keys = records[0].keys() if records else []
                    for i, record in enumerate(records):
                        if i >= 50:
                            lines.append("... (results truncated)")
                            break
                        # Show as dict for readability
                        lines.append(str(dict(record)))
                    response = "\n".join(lines)
                    if not response:
                        response = "Query executed. No results returned."
                    await interaction.followup.send(f"Results:\n{response}", ephemeral=True)

            except Exception as e:
                logging.exception("Error executing Neo4j query:")
                await interaction.followup.send(f"An error occurred while executing the query: {e}", ephemeral=True)
