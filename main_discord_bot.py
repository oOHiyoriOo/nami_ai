import logging, os, time, discord, asyncio, shutil, json

from colorama import Fore, init
from discord import app_commands

from lib.memory_db import MemoryDb
from lib.load_tasks import load_tasks
from lib.tool_loader import load_tools
from lib.global_registry import g_data
from lib.asyncsqlite import AsyncSQLite
from lib.load_events import load_events
from lib.load_commands import load_commands
from lib.configurationFile import ConfigurationFile
from lib.system_prompt_parser import NamiSystemPrompt
from lib.load_events import dynamically_register_events
init(convert=True, autoreset=True)

# --- Moved Logging Configuration ---
os.makedirs('./logs', exist_ok=True) # Use makedirs and exist_ok=True

# Get log level string from config first
cfg_temp = ConfigurationFile("config.yml") # Load config temporarily just for log level
log_level_str = cfg_temp.data.get('bot', {}).get('log_level', 'INFO')
log_level = getattr(logging, str(log_level_str).upper(), logging.INFO) # Convert string to logging level

logging.basicConfig(
    level=log_level, # Set level directly from converted config value
    format=(f'[%(asctime)s] {Fore.YELLOW} {"[%(levelname)s]":<8} {Fore.RESET} [%(name)s] %(message)s'),
    handlers=[
        logging.FileHandler(f"./logs/{time.strftime('%Y-%m-%d_%H_%M_%S')}.log", 'w', 'utf-8'),
        logging.StreamHandler()
    ],
    force=True
)
logging.info(f"Logging configured with level {logging.getLevelName(log_level)}.") # Log confirmation
# --- End Logging Configuration ---


cfg                         : ConfigurationFile        = g_data.get_or_create("cfg", ConfigurationFile, "config.yml")
system_prompt_filename      : str                      = cfg.data['ollama']['system_prompt']
sys_prompt_instance         : NamiSystemPrompt         = g_data.get_or_create("system_prompt", NamiSystemPrompt, f"system_prompt/{system_prompt_filename}.md")

intents                     : discord.Intents          = discord.Intents().all()
client                      : discord.Client           = g_data.get_or_create("client", discord.Client, intents=intents)
tree_instance               : app_commands.CommandTree = app_commands.CommandTree(client)

# def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_pass: str, model_name: str = 'all-MiniLM-L6-v2'):
memory_db_instance = g_data.get_or_create(
    "memory_db",
    MemoryDb,
    neo4j_uri=cfg.data['neo4j']['uri'],
    neo4j_user=cfg.data['neo4j']['user'],
    neo4j_pass=cfg.data['neo4j']['pass'],
    model_name=cfg.data.get('memory_db', {}).get('model', 'all-MiniLM-L6-v2')
)


client.tree = tree_instance

with open( 'lib/Storage/history_schem.json' ) as schema_file:
    history_db = g_data.get_or_create(
        "history_db",
        AsyncSQLite,
        db_path="history.db",
        schema=json.load(schema_file)
    )

@client.event # Use the actual client instance for decorators
async def on_ready():
    logging.info("="*70)
    # Single Okay Print Statement
    print(await sys_prompt_instance.get_prompt())
    logging.info("="*70)

    sync_guild_id = cfg.data['dc']['sync_guild']
    if sync_guild_id == -1:
        await client.tree.sync()
        logging.info("Synced commands globally.")
    else:
        await client.tree.sync(guild=discord.Object(id=sync_guild_id))
        logging.info(f"Synced commands to guild {sync_guild_id}.")

    logging.info(f"{Fore.GREEN}Logged in as {client.user.name} ({client.user.id})")

def remove_pycache_folders(path='.'):
    for root, dirs, files in os.walk(path):
        if '__pycache__' in dirs:
            pycache_path = os.path.join(root, '__pycache__')
            logging.info(f"Removing {pycache_path}")
            shutil.rmtree(pycache_path)

async def main():
    logging.info("Initializing application...")
    # Prepare Database.
    await history_db.initialize() # Use the instance directly here
    logging.info("History database initialized.")

    remove_pycache_folders()

    # Tools asynchron laden UND DANN in der Registry speichern
    loaded_tools = await load_tools(client)
    g_data.get_or_create("tools", lambda: loaded_tools)
    logging.info(f"Loaded {len(loaded_tools)} tools globally.")

    await load_events(client, cfg)
    await load_tasks(client, cfg)
    await load_commands(client, cfg)

    dynamically_register_events(client)

    logging.info("Starting Discord client...")
    # Start client using token from container
    await client.start(cfg.data['dc']['token'])

if __name__ == "__main__":
    asyncio.run(main())