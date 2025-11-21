import os
import importlib
import logging
from discord.ext import commands

event_handlers = {
    'on_message': [],
    # Add other events as needed
}

def register_event(event_name, handler):
    if event_name in event_handlers:
        event_handlers[event_name].append(handler)
    else:
        event_handlers[event_name] = [handler]

async def handle_event(event_name, *args, **kwargs):
    if event_name in event_handlers:
        for handler in event_handlers[event_name]:
            await handler(*args, **kwargs)

async def load_events(client, cfg) -> None:
    events_dir = './events'

    for filename in os.listdir(events_dir):
        if filename.endswith('.py') and not filename.startswith('__'):
            module_name = filename[:-3]
            module = importlib.import_module(f'events.{module_name}')
            if hasattr(module, 'setup'):
                await module.setup(client, cfg)
                logging.info(f"Loaded event {module_name}")


def dynamically_register_events(client):
    for event_name in event_handlers.keys():
        # Dynamically assign an event handler to the client for each event
        async def event_handler(*args, event_name=event_name, **kwargs):
            await handle_event(event_name, *args, **kwargs)

        # Bind the event handler to the client with the correct event name
        setattr(client, event_name, event_handler)
