import discord, asyncio, json, logging
import os

from lib.ollama_helper import OllamaHelper
from lib.global_registry import g_data

def load_memory_prompt():
    prompt_path = os.path.join(
        os.path.dirname(__file__),
        "..", "system_prompt", "memory", "FACT_RETRIEVAL.md"
    )
    with open(prompt_path, encoding="utf-8") as f:
        return f.read()

class VectorHelper:
    def __init__(self, client: discord.Client, cfg: dict):
        self.client: discord.Client = client
        self.cfg = cfg

        self.ollama_helper = OllamaHelper(client, cfg)
        self.memory_prompt = load_memory_prompt()

    async def extract_important_information(self, msg_content: str, depth : int = 0, max_depth : int = 3):
        known_memory = self.get_memories(msg_content)

        summary_prompt = self.memory_prompt

        logging.info(f"Injecting Known Memory: \n{known_memory}")
        ai_response = await asyncio.to_thread(
            self.ollama_helper.get_ollama_instance().chat, 
            model = g_data.get('cfg').data['ollama']['model'],
            messages = [{
                "role": "system",
                "content": summary_prompt
            },{
                "role": "tool",
                "name": "memory",
                "content": known_memory
            },{
                "role": "user",
                "content": msg_content
            }]
        )

        # Logge die Rohantwort des LLM
        raw_response_content = ai_response['message']['content']

        # Remove <think>...</think> blocks if present
        import re
        raw_response_content = re.sub(r'<think>.*?</think>', '', raw_response_content, flags=re.DOTALL).strip()
        
        raw_response_content = raw_response_content.replace('\n', '').replace('\r', '')
        raw_response_content = raw_response_content.replace("```json","").replace("```","").strip()

        relevant_content = []
        try:
            parsed_json = json.loads(raw_response_content)
            # Stelle sicher, dass das Ergebnis eine Liste ist (auch wenn sie leer ist)
            if isinstance(parsed_json, list):
                relevant_content = parsed_json
            else:
                logging.warning(f"AI response was valid JSON but not a list: {raw_response_content}")
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON from AI response: {raw_response_content}. Retrying...")
            return await self.extract_important_information(msg_content, depth + 1, max_depth) if depth < max_depth else []
        except Exception as e:
            logging.error(f"An unexpected error occurred processing AI response: {e}", exc_info=True)
            return []

        logging.info(f"Extracted Memories: \n{json.dumps(relevant_content, indent=4) }\n") # Logge das Ergebnis
        return relevant_content

    def get_memories(self, query: str) -> str:
        """
        Retrieves top-k relevant memories from the memory_db and formats them according to the new schema.
        """
        memory_db = g_data.get("memory_db")
        results = memory_db.search(query, top_k=5)
        
        # For each result, try to fetch the full memory node (with all properties) if possible.
        # If search only returns text and score, just return those.
        # Here, we assume search returns (memory_text, score), but you may want to extend it to return more fields.
        structured_results = []
        for memory_text, similarity in results:
            # In a real implementation, you might want to fetch the full node by text or id.
            # Here, we just return the text and similarity.
            structured_results.append({
                "memory": memory_text,
                "similarity": similarity
            })
        
        return json.dumps(structured_results)