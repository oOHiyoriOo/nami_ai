import datetime, pytz
from lib.load_events import register_event

async def stille_treppe_handler(msg):
    if not msg.guild:
        return
    
    for member in msg.mentions:
        if member.timed_out_until is not None:
            # Compare the timed_out_until datetime with the current datetime
            if member.timed_out_until > datetime.datetime.now(pytz.utc):
                await msg.reply(f"<@{member.id}> ist auf der stillen Treppe!")

async def setup(client, cfg):
    register_event("on_message", stille_treppe_handler)
