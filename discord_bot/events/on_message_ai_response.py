import logging, discord, asyncio, json, os
import time

from lib.load_events import register_event
from lib.sqlite_helper import SQLiteHelper
from lib.ollama_helper import OllamaHelper
from lib.vector_helper import VectorHelper
from lib.global_registry import g_data
from lib.chat_helper import ChatHelper

from ollama import Client as Ollama

async def ai_chat_handler(msg : discord.Message, client : discord.Client , cfg : dict, ollama_helper : OllamaHelper):
    reply_context       : str               = None;
    history             : list              = []
    # ollama_helper       : OllamaHelper      = OllamaHelper(client, cfg) # moved to wrapped_ai_chat_handler
    sqlite_helper       : SQLiteHelper      = SQLiteHelper(client)
    vector_helper       : VectorHelper      = VectorHelper(client, cfg)

    # Use only channel id for conversation_id
    conversation_id     : str               = str(msg.channel.id)

    msg_content = await ollama_helper.should_respond(msg)
    if msg_content is None:
        return

    async with msg.channel.typing():
        # Store the incoming message in the database FIRST
        await sqlite_helper.handle_new_message(
            channel_id      = int(msg.channel.id),
            channel_name    = msg.channel.name if not isinstance(msg.channel, discord.channel.DMChannel) else msg.author.name,
            user_id         = int(msg.author.id),
            user_name       = msg.author.name,
            message_id      = int(msg.id),
            content         = str(msg.content).lstrip(),
            reply_to        = int(msg.reference.message_id) if msg.type == discord.MessageType.reply and msg.reference and msg.reference.message_id else None,
            conversation_id = str(msg.channel.id),
        )

        # Always build history from the database after storing the new message
        sql_history = await sqlite_helper.retrieve_message(
            conversation_id=conversation_id,
            limit=64,
        )
        history = await ChatHelper.build_history(sql_history, msg, client)

        history.insert(0, {"role": "system", "content": await g_data.get("system_prompt").get_prompt() })

        user_info = {
            "name": msg.author.name,
            "nickname": msg.author.display_name if hasattr(msg.author, 'display_name') else msg.author.name,
            "id": msg.author.id,
            "avatar_url": msg.author.avatar.url if hasattr(msg.author, 'avatar') else None,
            "created_at": msg.author.created_at.strftime("%B %d, %Y"),
            "is_bot": msg.author.bot
        }

        # Generate the natural language string
        user_context_text = f"Context about your chat partner: " \
                            f"You are talking to '{user_info['nickname']}' " \
                            f"(internal username: '{user_info['name']}', ID: {user_info['id']}). " \
                            f"Their account was created on {user_info['created_at']}. " \
                            f"This user is {'a bot' if user_info['is_bot'] else 'not a bot'}."

        vector_entries : int = g_data.get('memory_db').get_total_entries()
        logging.info(f"Total of {vector_entries} entries in the vector database.")

        history.insert(1, {"role": "tool", "name": "user_info", "content": user_context_text})

        if vector_entries > 0:
            try:
                # Suche relevante Erinnerungen basierend auf der aktuellen User-Nachricht
                # search_with_context returns list[dict] with 'text', 'score', 'type'
                retrieved_memories = g_data.get('memory_db').search_with_context(query=msg_content, top_k=5, context_k=20)

                # --- Updated processing logic ---
                if retrieved_memories:
                    similarity_threshold = 0.65
                    formatted_memories_list = []
                    for mem in retrieved_memories:
                        mem_text = mem.get('text')
                        mem_type = mem.get('type')
                        mem_score = mem.get('score', 0.0)

                        if not mem_text: continue # Skip if text is missing

                        # Include context memories or vector memories above threshold
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

                        logging.info(f"Retrieved {len(formatted_memories_list)} relevant memories:")
                        logging.info(f"\n{formatted_memories}")
                        logging.info("="*80)
                    else:
                        logging.info("No relevant memories found meeting the criteria.")
                else:
                    logging.info("No memories found by search_with_context.")
                # --- End updated processing logic ---

            except Exception as e:
                # Log the specific error and traceback
                logging.error(f"Error searching or formatting memories: {e}", exc_info=True)
    
        tools = g_data.get("tools")

        sanitized_tools = [{k: v for k, v in tool.items() if k != 'func'} for tool in tools]
        ollama_instance : Ollama = ollama_helper.get_ollama_instance()

        # When appending the current user message, use the new format
        # Get timestamp in readable format
        msg_timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(msg, "created_at") else ""
        history.append({
            "role": "user",
            "content": f"{msg.author.name} [{msg_timestamp}] : {msg_content}"
        })

        ai_response = await asyncio.to_thread(ollama_instance.chat, model=g_data.get('cfg').data['ollama']['model'], messages=history, tools=sanitized_tools)
        
        # Correct Tool calls if needed.
        ai_response = ollama_helper.extract_tool_request(ai_response)
        max_tool_calls = g_data.get('cfg').data['ollama']['max_tool_calls']
        tool_call_count = 0
        
        while 'tool_calls' in ai_response['message'] and len(ai_response['message']['tool_calls']) > 0:
            tool_call_count += 1

            # Check if we've exceeded the maximum allowed tool calls
            if tool_call_count > max_tool_calls:
                logging.warning(f"Tool call limit ({max_tool_calls}) exceeded. Breaking loop to prevent infinite calls.")
                history.append({
                    "role": "system",
                    "content": f"Tool call limit exceeded. Please provide a direct response without using tools."
                })
                
                ai_response = await asyncio.to_thread(
                    ollama_instance.chat, 
                    model=g_data.get('cfg').data['ollama']['model'], 
                    messages=history
                )

                break

            # Store tool message in history with user_id 0 and use user_name as tool name
            tool_response = await ollama_helper.handle_tool_message(ai_response['message']['tool_calls'], tools, msg)
            
            history.append({
                "role": "tool",
                "name": tool_response.get("name", "tool"),
                "content": tool_response.get("content", "")
            })

            await sqlite_helper.handle_new_message(
                channel_id      = msg.channel.id,
                channel_name    = msg.channel.name if not isinstance(msg.channel, discord.channel.DMChannel) else msg.author.name,
                user_id         = 0,
                user_name       = tool_response.get("name", "tool"),
                message_id      = None,
                content         = tool_response.get("content", ""),
                conversation_id = conversation_id
            )

            logging.info(f"Tool call [{tool_call_count}]: {tool_response.get('name', 'tool')} - {tool_response.get('content', '')}")

            await client.change_presence(activity=discord.Game(name=f"Thinking about response..."))
            logging.info(f"[{msg.author.name}] Thinking about response...")

            ai_response = await asyncio.to_thread(ollama_instance.chat, model=g_data.get('cfg').data['ollama']['model'], messages=history, tools=sanitized_tools)
            ai_response = ollama_helper.extract_tool_request(ai_response)

        reply_message = await ollama_helper.process_ai_response(ai_response, msg)

    LTM_points = await vector_helper.extract_important_information(
        f"""
        user [{msg.author.name}]: {msg_content}
        """
    )

    if LTM_points and len(LTM_points) > 0:
        import uuid
        # Support for specifying memory_type and args in LTM_points
        for mem in LTM_points:
            memory_type = mem.get("memory_type", "EpisodicMemory")
            memory_args = mem.get("memory_args", None)
            if not memory_args:
                memory_args = {
                    "summary": mem.get("memory", ""),
                    "concepts": mem.get("concepts", []),
                    "authorUserId": str(msg.author.id),
                    "creationTimestamp": int(time.time() * 1000)
                }
            else:
                memory_args.setdefault("authorUserId", str(msg.author.id))
                memory_args.setdefault("creationTimestamp", int(time.time() * 1000))

            # Ensure every memory_args has a unique id
            if "id" not in memory_args:
                memory_args["id"] = str(uuid.uuid4())

            g_data.get('memory_db').add_memory(
                user_id=msg.author.id,
                user_name=msg.author.name,
                memory_type=memory_type,
                memory_args=memory_args
            )

        logging.info(f"Added a total of {len(LTM_points)} Memories.")

    # Store the AI response in the database after it is finished
    await sqlite_helper.handle_new_message(
        channel_id      = int(msg.channel.id),
        channel_name    = msg.channel.name if not isinstance(msg.channel, discord.channel.DMChannel) else msg.author.name,
        user_id         = int(client.user.id),
        user_name       = client.user.name,
        message_id      = int(reply_message.id),
        content         = ai_response['message']['content'],
        conversation_id = str(msg.channel.id),
    )

async def setup(client, cfg):
    # Per-channel lock dictionary
    if not hasattr(client, "_channel_locks"):
        client._channel_locks = {}

    async def wrapped_ai_chat_handler(msg):
        
        ollama_helper       : OllamaHelper      = OllamaHelper(client, cfg)
        if not await ollama_helper.should_respond(msg):
            return

        lock = client._channel_locks.setdefault(msg.channel.id, asyncio.Lock())
        logging.info(f"[LOCK] Processing channel: {getattr(msg.channel, 'name', msg.channel.id)} (ID: {msg.channel.id}), user: {msg.author.name} (ID: {msg.author.id})")
        async with lock:
            logging.info(f"[LOCK] Acquired for channel: {getattr(msg.channel, 'name', msg.channel.id)} (ID: {msg.channel.id}), user: {msg.author.name} (ID: {msg.author.id})")
            await ai_chat_handler(msg, client, cfg, ollama_helper)

    register_event("on_message", wrapped_ai_chat_handler)