import discord, os, importlib, asyncio, logging

async def load_tasks(client: discord.Client, cfg: dict) -> None:
    tasks_dir = 'tasks'
    for filename in os.listdir(tasks_dir):
        if filename.endswith('.py'):
            module_name = filename[:-3]
            module_path = os.path.join(tasks_dir, filename)
            
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if hasattr(module, 'register'):
                task_coroutine = module.register(client, cfg)
                asyncio.create_task(task_coroutine)
                logging.info(f"Registered task from {filename}")