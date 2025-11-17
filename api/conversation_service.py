"""
Conversation service - handles chat logic for the personality proxy.
Extracted from the Discord bot to work independently.
"""
import logging
import asyncio
import json
import time
import uuid
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from lib.sqlite_helper import SQLiteHelper
from lib.ollama_helper import OllamaHelper
from lib.vector_helper import VectorHelper
from lib.global_registry import g_data
from ollama import Client as Ollama


class ConversationService:
    """Service for handling conversations with the personality."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sqlite_helper = SQLiteHelper(None)  # Pass None for client as we don't need Discord
        self.vector_helper = VectorHelper(None, cfg)
        self.ollama_client = Ollama(host=cfg['ollama']['url'])

    async def chat(
        self,
        message: str,
        user_id: str,
        user_name: str,
        conversation_id: str,
        max_history: int = 64,
        include_thinking: bool = False
    ) -> Dict[str, Any]:
        """
        Process a chat message and return the AI response.

        Args:
            message: The user's message
            user_id: Unique identifier for the user
            user_name: Display name for the user
            conversation_id: Conversation/channel ID
            max_history: Maximum number of historical messages to include
            include_thinking: Whether to include the AI's thinking process

        Returns:
            Dictionary containing:
                - response: The AI's response text
                - thinking: The AI's thinking process (if requested)
                - tools_used: List of tools used
        """
        history = []
        tools_used = []

        # Store the incoming message in the database
        await self.sqlite_helper.handle_new_message(
            channel_id=conversation_id,
            channel_name=f"api_conversation_{conversation_id}",
            user_id=int(user_id) if user_id.isdigit() else hash(user_id) % (10 ** 8),
            user_name=user_name,
            message_id=None,
            content=message,
            reply_to=None,
            conversation_id=conversation_id,
        )

        # Build history from the database
        sql_history = await self.sqlite_helper.retrieve_message(
            conversation_id=conversation_id,
            limit=max_history,
        )
        history = await self._build_history(sql_history, user_name)

        # Add system prompt
        system_prompt = await g_data.get("system_prompt").get_prompt()
        history.insert(0, {"role": "system", "content": system_prompt})

        # Add user context
        user_context_text = (
            f"Context about your chat partner: "
            f"You are talking to '{user_name}' "
            f"(ID: {user_id}). "
            f"This is an API conversation."
        )
        history.insert(1, {"role": "tool", "name": "user_info", "content": user_context_text})

        # Retrieve and add relevant memories
        vector_entries = g_data.get('memory_db').get_total_entries()
        logging.info(f"Total of {vector_entries} entries in the vector database.")

        if vector_entries > 0:
            try:
                retrieved_memories = g_data.get('memory_db').search_with_context(
                    query=message,
                    top_k=5,
                    context_k=20
                )

                if retrieved_memories:
                    similarity_threshold = 0.65
                    formatted_memories_list = []

                    for mem in retrieved_memories:
                        mem_text = mem.get('text')
                        mem_type = mem.get('type')
                        mem_score = mem.get('score', 0.0)

                        if not mem_text:
                            continue

                        if mem_type == 'context' or (mem_type == 'vector' and mem_score >= similarity_threshold):
                            score_info = f"(Score: {mem_score:.2f})" if mem_type == 'vector' else "(Context)"
                            formatted_memories_list.append(f"- {mem_text} {score_info}")

                    if formatted_memories_list:
                        formatted_memories = "\n".join(formatted_memories_list)
                        memory_context = f"Here are some relevant memories based on the user's message:\n{formatted_memories}"

                        history.insert(2, {
                            "role": "tool",
                            "name": "retrieved_memories",
                            "content": memory_context
                        })

                        logging.info(f"Retrieved {len(formatted_memories_list)} relevant memories")
            except Exception as e:
                logging.error(f"Error searching or formatting memories: {e}", exc_info=True)

        # Get tools
        tools = g_data.get("tools")
        sanitized_tools = [{k: v for k, v in tool.items() if k != 'func'} for tool in tools]

        # Add current user message
        msg_timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        history.append({
            "role": "user",
            "content": f"{user_name} [{msg_timestamp}] : {message}"
        })

        # Call Ollama
        ai_response = await asyncio.to_thread(
            self.ollama_client.chat,
            model=self.cfg['ollama']['model'],
            messages=history,
            tools=sanitized_tools
        )

        # Handle tool calls
        ai_response = self._extract_tool_request(ai_response)
        max_tool_calls = self.cfg['ollama'].get('max_tool_calls', 10)
        tool_call_count = 0

        while 'tool_calls' in ai_response['message'] and len(ai_response['message']['tool_calls']) > 0:
            tool_call_count += 1

            if tool_call_count > max_tool_calls:
                logging.warning(f"Tool call limit ({max_tool_calls}) exceeded.")
                history.append({
                    "role": "system",
                    "content": "Tool call limit exceeded. Please provide a direct response without using tools."
                })

                ai_response = await asyncio.to_thread(
                    self.ollama_client.chat,
                    model=self.cfg['ollama']['model'],
                    messages=history
                )
                break

            # Execute tool
            tool_response = await self._handle_tool_message(
                ai_response['message']['tool_calls'],
                tools,
                user_id,
                user_name
            )

            tools_used.append(tool_response.get("name", "tool"))

            history.append({
                "role": "tool",
                "name": tool_response.get("name", "tool"),
                "content": tool_response.get("content", "")
            })

            await self.sqlite_helper.handle_new_message(
                channel_id=conversation_id,
                channel_name=f"api_conversation_{conversation_id}",
                user_id=0,
                user_name=tool_response.get("name", "tool"),
                message_id=None,
                content=tool_response.get("content", ""),
                conversation_id=conversation_id
            )

            logging.info(f"Tool call [{tool_call_count}]: {tool_response.get('name', 'tool')}")

            ai_response = await asyncio.to_thread(
                self.ollama_client.chat,
                model=self.cfg['ollama']['model'],
                messages=history,
                tools=sanitized_tools
            )
            ai_response = self._extract_tool_request(ai_response)

        # Extract thinking and clean response
        response_text, thinking_text = self._extract_thinking(ai_response['message']['content'])

        # Extract important information for long-term memory
        await self._store_memories(message, user_id, user_name)

        # Store AI response in database
        await self.sqlite_helper.handle_new_message(
            channel_id=conversation_id,
            channel_name=f"api_conversation_{conversation_id}",
            user_id=0,
            user_name="assistant",
            message_id=None,
            content=response_text,
            conversation_id=conversation_id,
        )

        result = {
            "response": response_text,
            "tools_used": tools_used
        }

        if include_thinking and thinking_text:
            result["thinking"] = thinking_text

        return result

    async def _build_history(self, sql_history: List[Dict], current_user_name: str) -> List[Dict]:
        """Build chat history from SQL results."""
        history = []

        for msg in sql_history:
            role = "assistant" if msg['user_id'] == 0 and msg['user_name'] != "tool" else "user"

            if msg['user_name'] == "tool":
                role = "tool"

            timestamp = msg.get('timestamp', '')
            if timestamp:
                timestamp_str = f" [{timestamp}]"
            else:
                timestamp_str = ""

            if role == "tool":
                history.append({
                    "role": "tool",
                    "name": msg['user_name'],
                    "content": msg['content']
                })
            elif role == "assistant":
                history.append({
                    "role": "assistant",
                    "content": msg['content']
                })
            else:
                history.append({
                    "role": "user",
                    "content": f"{msg['user_name']}{timestamp_str} : {msg['content']}"
                })

        return history

    def _extract_thinking(self, content: str) -> Tuple[str, Optional[str]]:
        """Extract <think>...</think> tags from content."""
        think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
        think_content = think_match.group(1).strip() if think_match else None

        if think_match:
            content = content[:think_match.start()] + content[think_match.end():]
            content = content.strip()

        return content, think_content

    def _extract_tool_request(self, response: Dict) -> Dict:
        """Extract tool calls from response content if they're embedded in text."""
        content = response.get('message', {}).get('content', '')

        tool_call_match = re.search(r'<tool_call>(.*?)</tool_call>', content, re.DOTALL)
        if tool_call_match:
            tool_call_content = tool_call_match.group(1)
            try:
                tool_call = json.loads(tool_call_content)
                logging.info("Tool call corrected.")
                return {
                    "model": response.get("model", "unknown_model"),
                    "created_at": response.get('created_at', "unknown_created_at"),
                    "message": {
                        "role": response.get('message', {}).get("role", "unknown_role"),
                        "content": "",
                        "tool_calls": [{
                            "function": {
                                "name": tool_call.get("name", "unknown_name"),
                                "arguments": tool_call.get("args", tool_call.get("arguments", {}))
                            }
                        }]
                    }
                }
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse tool call JSON: {e}")
                return response

        return response

    async def _handle_tool_message(
        self,
        tool_calls: List[Dict],
        tools: List[Dict],
        user_id: str,
        user_name: str
    ) -> Dict:
        """Handle tool execution."""
        for tool_call in tool_calls:
            tool_name = tool_call['function']['name']
            tool_args = tool_call['function']['arguments']

            logging.info(f"Executing tool: {tool_name}")

            tool = next((t for t in tools if t['function']["name"] == tool_name), None)
            if tool:
                try:
                    # Create a mock source user object
                    class MockUser:
                        def __init__(self, uid, uname):
                            self.id = uid
                            self.name = uname

                    source_user = MockUser(user_id, user_name)
                    raw_tool_result = await tool['func'](client=None, source_user=source_user, **tool_args)

                    if asyncio.iscoroutine(raw_tool_result):
                        raw_tool_result = await raw_tool_result

                    return {
                        "role": "tool",
                        "name": tool_name,
                        "content": str(raw_tool_result)
                    }

                except Exception as e:
                    import traceback
                    stack_trace = traceback.format_exc()
                    logging.error(f"Error executing tool {tool_name}: {e}")
                    logging.error(f"Stacktrace: {stack_trace}")

                    return {
                        "role": "tool",
                        "name": tool_name,
                        "content": str(stack_trace)
                    }
            else:
                logging.warning(f"Tool {tool_name} not found.")
                return {
                    "role": "system",
                    "content": f"Tool {tool_name} not found."
                }

        return {"role": "tool", "name": "unknown", "content": "No tool executed"}

    async def _store_memories(self, message: str, user_id: str, user_name: str):
        """Extract and store important information in long-term memory."""
        try:
            LTM_points = await self.vector_helper.extract_important_information(
                f"user [{user_name}]: {message}"
            )

            if LTM_points and len(LTM_points) > 0:
                for mem in LTM_points:
                    memory_type = mem.get("memory_type", "EpisodicMemory")
                    memory_args = mem.get("memory_args", None)

                    if not memory_args:
                        memory_args = {
                            "summary": mem.get("memory", ""),
                            "concepts": mem.get("concepts", []),
                            "authorUserId": str(user_id),
                            "creationTimestamp": int(time.time() * 1000)
                        }
                    else:
                        memory_args.setdefault("authorUserId", str(user_id))
                        memory_args.setdefault("creationTimestamp", int(time.time() * 1000))

                    if "id" not in memory_args:
                        memory_args["id"] = str(uuid.uuid4())

                    g_data.get('memory_db').add_memory(
                        user_id=user_id,
                        user_name=user_name,
                        memory_type=memory_type,
                        memory_args=memory_args
                    )

                logging.info(f"Added a total of {len(LTM_points)} Memories.")

        except Exception as e:
            logging.error(f"Error storing memories: {e}", exc_info=True)

    async def get_conversation_history(
        self,
        conversation_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get conversation history for a given conversation ID."""
        sql_history = await self.sqlite_helper.retrieve_message(
            conversation_id=conversation_id,
            limit=limit,
        )

        messages = []
        for msg in sql_history:
            role = "assistant" if msg['user_id'] == 0 else "user"
            messages.append({
                "role": role,
                "content": msg['content'],
                "timestamp": msg.get('timestamp'),
                "user_name": msg.get('user_name')
            })

        return messages
