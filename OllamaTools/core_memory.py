import logging, discord, json
from langchain.tools import Tool
from pydantic import BaseModel, Field

async def core_memory(client: discord.Client, source_id: str, memory : str ):
    try:
        if memory is not None:
            memory_list = await read_memory_from_file("core_memory.json")
            memory_list.append(memory)
            return await save_memory_to_file("core_memory.json", memory_list)
        else:
            memory_list = await read_memory_from_file("core_memory.json")
            return "\n".join(f"-{item}" for item in memory_list)

    except Exception as e:
        logging.error(f"Error querying user: {e}")
        logging.exception(e)
        return f"Error: {str(e)}"

async def read_memory_from_file(file_path: str) -> list:
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return json.load(file)
        
    except FileNotFoundError:
        logging.warning(f"File {file_path} not found. Creating a new file and returning empty list.")
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump([], file)
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from file {file_path}: {e}")
        return []

async def save_memory_to_file(file_path: str, memory_list: list):
    try:
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(memory_list, file, indent=4)
        return "Saved all Memories."
    except Exception as e:
        logging.error(f"Error saving memory to file {file_path}: {e}")
        logging.exception(e)
        return "Error saving Memories."

def get_tool():
    return {
      "type": "function",
      "function": {
        "name": "core_memory",
        "description": "Save Important Memories",
        "parameters": {
          "type": "object",
          "properties": {
            "memory": {
              "type": "string",
              "description": "A Short memory note to persist."
            }
          }
        }
      }
    }