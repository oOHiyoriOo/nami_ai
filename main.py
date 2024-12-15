import logging, os, time, discord, __main__, asyncio, shutil

from colorama import Fore, init
from discord import app_commands

from lib.load_tasks import load_tasks
from lib.load_commands import load_commands
from lib.load_events import load_events
from lib.configurationFile import ConfigurationFile
from lib.load_events import dynamically_register_events

init(convert=True, autoreset=True)

cfg = ConfigurationFile('config.yml')

intents     : discord.Intents           = discord.Intents().all()
client      : discord.Client            = discord.Client(intents=intents,member_cache_flags=discord.MemberCacheFlags.all())
tree        : app_commands.CommandTree  = app_commands.CommandTree(client)
client.tree                             = tree

if not os.path.exists('./logs'):
    os.mkdir('./logs')

logging.basicConfig(
    level= int( __main__.cfg.data['bot']['log_level'] ),
    format= (f'[%(asctime)s] {Fore.YELLOW} {"[%(levelname)s]":7} {Fore.RESET} %(message)s'),
    handlers=[
        logging.FileHandler(f"./logs/{  time.strftime('%Y-%m-%d_%H_%M_%S') }.log",'w','utf-8'),
        logging.StreamHandler()
    ]
)

@client.event
async def on_ready():
    if __main__.cfg.data['dc']['sync_guild'] != -1:
        await client.tree.sync() 
    else:
        client.tree.sync_guild(guild=discord.Object(id=__main__.cfg.data['dc']['sync_guild']))

def remove_pycache_folders(path='.'):
    for root, dirs, files in os.walk(path):
        if '__pycache__' in dirs:
            pycache_path = os.path.join(root, '__pycache__')
            logging.info(f"Removing {pycache_path}")
            shutil.rmtree(pycache_path)

async def main():
    remove_pycache_folders()
    
    await load_events(client, None)  # Load events and handlers
    dynamically_register_events(client)  # Register handlers dynamically

    await load_tasks(client, cfg)
    await load_commands(client, cfg)
    await client.start(__main__.cfg.data['dc']['token'])



if __name__ == "__main__":
    asyncio.run(main())