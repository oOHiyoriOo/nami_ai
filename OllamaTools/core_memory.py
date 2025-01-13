import logging
import json
import asyncio
from typing import List, Union
from langchain.tools import Tool
from pydantic import BaseModel, Field
from aiofiles import open as aio_open
from aiofiles.os import wrap as aio_wrap

# File lock for concurrency safety
class FileLock:
    def __init__(self, file_path):
        self.lock = asyncio.Lock()
        self.file_path = file_path

    async def __aenter__(self):
        await self.lock.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        self.lock.release()

file_lock = FileLock("core_memory.json")

async def core_memory(client, source_id, action: str, memory: Union[str, None] = None) -> str:
    """
    Handles storing and retrieving core memory.

    :param action: The action to perform ("save" or "retrieve").
    :param memory: The memory to save (if action is "save").
    :return: A message or the list of memories.
    """
    try:
        async with file_lock:
            if action == "save":
                if memory is None or not memory.strip():
                    return "Memory cannot be empty."
                memory_list = await read_memory_from_file("core_memory.json")
                memory_list.append({"note": memory, "timestamp": asyncio.get_event_loop().time()})
                await save_memory_to_file("core_memory.json", memory_list)
                return "Memory saved successfully."

            elif action == "retrieve":
                memory_list = await read_memory_from_file("core_memory.json")
                if not memory_list:
                    return "No memories found."
                return "\n".join(f"- {item['note']}" for item in memory_list)

            else:
                return "Invalid action. Use 'save' or 'retrieve'."

    except Exception as e:
        logging.error(f"Error in core_memory function: {e}")
        return f"Error: {str(e)}"

async def read_memory_from_file(file_path: str) -> List[dict]:
    """Reads memory from the specified JSON file."""
    try:
        async with aio_open(file_path, mode='r', encoding='utf-8') as file:
            data = await file.read()
            return json.loads(data) if data else []
    except FileNotFoundError:
        logging.warning(f"File {file_path} not found. Creating a new file.")
        await save_memory_to_file(file_path, [])
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from file {file_path}: {e}")
        return []

async def save_memory_to_file(file_path: str, memory_list: List[dict]):
    """Saves the memory list to the specified JSON file."""
    try:
        async with aio_open(file_path, mode='w', encoding='utf-8') as file:
            await file.write(json.dumps(memory_list, indent=4))
    except Exception as e:
        logging.error(f"Error saving memory to file {file_path}: {e}")
        raise

def get_tool():
    return {
        "type": "function",
        "function": {
            "name": "core_memory",
            "description": "Manage important long-term memory. Actions: 'save' to store a memory, 'retrieve' to get all memories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform: 'save' or 'retrieve'."
                    },
                    "memory": {
                        "type": "string",
                        "description": "The memory to save (required for 'save' action).",
                        "nullable": True
                    }
                },
                "required": ["action"]
            }
        }
    }
