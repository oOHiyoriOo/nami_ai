import discord
from discord import app_commands # Wichtig für Sub-Befehle
import logging
import time # Für Zeitstempel

from lib.memory_db import MemoryDb
from lib.global_registry import g_data

# Die ID des erlaubten Benutzers (als Konstante definieren)
ALLOWED_USER_ID = 210428907386699777

class Command:
    def __init__(self, client, cfg):
        self.client = client # Client speichern für Zugriff auf tree
        self.cfg = cfg
        # Eine Command Group erstellen
        self.debug_group = app_commands.Group(name="debug", description="Debug commands for memory management.")
        self.register_commands()

    def register_commands(self):
        # Only add the add_knowledge command to the debug group
        self.client.tree.add_command(self.debug_group)

        @self.debug_group.command(name="add_knowledge", description="Add a KnowledgeUnit memory for your user.")
        @app_commands.describe(statement="The knowledge statement.", type="Type/category of knowledge.", confidence="Confidence score (0-1).")
        async def add_knowledge(interaction: discord.Interaction, statement: str, type: str = "fact", confidence: float = 1.0):
            """Fügt eine KnowledgeUnit Erinnerung für den ausführenden Benutzer hinzu."""
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id != ALLOWED_USER_ID:
                await interaction.followup.send("You do not have the required permissions to use this command.", ephemeral=True)
                return

            try:
                memory_db : MemoryDb = g_data.get("memory_db")
                if not memory_db:
                    await interaction.followup.send("MemoryDB instance not found.", ephemeral=True)
                    return

                memory_args = {
                    "statement": statement,
                    "type": type,
                    "confidenceScore": confidence,
                    "authorUserId": str(interaction.user.id),
                    "creationTimestamp": int(time.time() * 1000)
                }
                memory_db.add_memory(
                    user_id=interaction.user.id,
                    user_name=interaction.user.name,
                    memory_type="KnowledgeUnit",
                    memory_args=memory_args
                )

                await interaction.followup.send(f"KnowledgeUnit added for user {interaction.user.name}:\n'{statement}'", ephemeral=True)

            except Exception as e:
                logging.exception("Error in debug add_knowledge command:")
                await interaction.followup.send(f"An error occurred while adding the knowledge: {e}", ephemeral=True)

        @self.debug_group.command(name="query", description="Execute a manual Cypher query on the Neo4j database (admin only, returns first 10 rows).")
        @app_commands.describe(query="The Cypher query to execute.")
        async def query(interaction: discord.Interaction, query: str):
            """Führt eine manuelle Cypher-Abfrage in der Neo4j-Datenbank aus (nur für Admin)."""
            await interaction.response.defer(ephemeral=True)
            if interaction.user.id != ALLOWED_USER_ID:
                await interaction.followup.send("You do not have the required permissions to use this command.", ephemeral=True)
                return

            try:
                memory_db : MemoryDb = g_data.get("memory_db")
                if not memory_db:
                    await interaction.followup.send("MemoryDB instance not found.", ephemeral=True)
                    return

                driver = memory_db.get_driver()
                with driver.session() as session:
                    result = session.run(query)
                    rows = result.values()
                    keys = result.keys()
                    # Limit output to first 10 rows
                    rows = rows[:10]
                if not rows:
                    await interaction.followup.send("Query executed. No results returned.", ephemeral=True)
                    return

                # Format as a code block table
                table = ' | '.join(keys) + '\n' + ('-' * (3 * len(keys) + 2)) + '\n'
                for row in rows:
                    table += ' | '.join(str(item) for item in row) + '\n'
                if len(table) > 1900:
                    table = table[:1900] + '\n... (truncated)'
                await interaction.followup.send(f"Results for query:\n```\n{table}```", ephemeral=True)

            except Exception as e:
                logging.exception("Error in debug manual_query command:")
                await interaction.followup.send(f"An error occurred while executing the query: {e}", ephemeral=True)