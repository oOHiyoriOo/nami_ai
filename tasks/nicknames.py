import __main__
import asyncio
import logging

from tinydb import TinyDB, Query

async def task(client, cfg):
    await client.wait_until_ready()
    while not client.is_closed() and client.is_ready():
        db = TinyDB('nicknames.json')    
        
        for user in db.all():
            # {'id': 814656948535558154, 'guild_id': 1165032222173175868, 'nickname': 'DjCrafterHD.sql'}
            guild = client.get_guild(user['guild_id'])
            member = guild.get_member(user['id'])
            if member and member.nick != user['nickname']:
                await member.edit(nick=user['nickname'])
                logging.info(f"Changed nickname for {member.name} to {user['nickname']}")

        await asyncio.sleep(2)

def register(client, cfg):
    return task(client, cfg)