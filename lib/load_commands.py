import os, importlib, logging

async def load_commands(client, cfg):
    commands_dir = 'commands'
    
    for root, _, files in os.walk(commands_dir):
        for filename in files:
            if filename.endswith('.py'):
                module_name = os.path.splitext(os.path.relpath(os.path.join(root, filename), commands_dir))[0].replace(os.sep, '.')
                module_path = os.path.join(root, filename)
                
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                if hasattr(module, 'Command'):
                    module.Command(client, cfg)
                    logging.info(f"Registered command from {filename}")