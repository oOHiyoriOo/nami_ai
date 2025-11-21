import logging, asyncio, traceback, discord, hashlib, re, json, os
from lib.global_registry import g_data
from lib.nextcloud_image_uploader import NextcloudImageUploader

from ollama import Client as Ollama

class OllamaHelper:
    def __init__(self, client: discord.Client, cfg: dict):
        self.client : discord.Client = client
        self.cfg = cfg


    def get_ollama_instance(self):
        ollama_url = g_data.get('cfg').data['ollama']['url']
        return Ollama(host=ollama_url)

    async def should_respond(self, msg):
        if f"<@{self.client.user.id}>" not in msg.content or msg.channel.id in g_data.get('cfg').data['ai_channel']:
            if msg.author == self.client.user or msg.author.bot: # bot or self message
                return None
            
            # we do need this check because if the bot is not pinged we also land here.
            if msg.channel.id not in g_data.get('cfg').data['ai_channel']:
                return None

        # Name is in the message this we try to check if we override the ai_channel.
        if f"<@{self.client.user.id}>" in msg.content and msg.channel.id not in g_data.get('cfg').data['ai_channel']:
            if msg.author.id not in g_data.get('cfg').data['dc']['permitted_users']: # not permitted user
                logging.warning(f"Unauthorized override Attempt of {msg.author.name} catched.")
                return None
            
            msg.content = msg.content.replace(f"<@{self.client.user.id}>","") # remove ping
        
        return msg.content


    async def get_conversation_id(self, author_id : int, channel_id : int):
        conv_id_source = f"{author_id}{channel_id}"
        return hashlib.md5(conv_id_source.encode()).hexdigest()

    async def get_reply_context(self, msg: discord.Message, processed_message_ids: set, depth: int, limit : int = 3) -> list:
        if depth > 3 or msg.id in processed_message_ids:
            return []
        processed_message_ids.add(msg.id)

        refCh: discord.PartialMessageable = self.client.get_partial_messageable(id=msg.reference.channel_id, guild_id=msg.reference.guild_id)

        if refCh is not None:
            # Collect the messages in reply context
            reply_context = [message async for message in refCh.history(limit=limit, before=discord.Object(id=msg.reference.message_id))]

            # Sort messages by their creation time (older messages first)
            reply_context.sort(key=lambda m: m.created_at)

            # For each message in the context, resolve replies recursively
            for message in reply_context:
                # Check if the message has a reply and resolve it recursively
                if message.type == discord.MessageType.reply:
                    reply_context.extend(await self.get_reply_context(message, processed_message_ids, depth + 1))

        return reply_context

    async def handle_reply_message(self, msg: discord.Message, limit : int = 3):
        if msg.author.id == self.client.user.id: # ignore self messages
            return None
        
        if msg.type == discord.MessageType.reply:

            processed_message_ids = set()
            reply_context = await self.get_reply_context(msg, processed_message_ids, depth=0, limit=limit)

            # Print the messages in the correct order
            return " ".join([f"\n{msg.author.name}: {msg.content}" for msg in reply_context])

        return None

    def extract_tool_request(self, response):
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
                logging.error(f"Response content: {tool_call_content}")
                return response

        return response

    async def process_ai_response(self, ai_response, msg : discord.Message) -> None:
        """
        Processes the AI response and updates the reply message accordingly.
        If replying to the user's message fails (e.g., message deleted), sends the reply in the channel
        and documents what the user's message was.
        Additionally, extracts <think>...</think> from the AI message and posts it as a thread to the reply.
        """
        return_message = None
        user_message_content = msg.content if hasattr(msg, "content") else "<no content>"

        # Extract <think>...</think> before any checks
        content = ai_response['message']['content']
        think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
        think_content = think_match.group(1).strip() if think_match else None
        # Remove <think>...</think> from the content for the reply
        if think_match:
            content = content[:think_match.start()] + content[think_match.end():]
            content = content.strip()
        ai_response['message']['content'] = content

        async def safe_reply(content):
            try:
                return await msg.reply(content=content)
            except Exception as e:
                logging.warning(f"Failed to reply to message (possibly deleted): {e}")
                # Fallback: send in channel with user message context
                channel = msg.channel
                fallback_content = f"(User's message was: \"{user_message_content}\")\n{content}"
                return await channel.send(content=fallback_content)

        # Check for ignore or empty content
        if len(ai_response['message']['content']) == 0 or ai_response['message']['content'] == "<ignore>":
            logging.info(ai_response)
            return_message = await safe_reply("I'm sorry, I don't have a response for that.")
            # Do not send think thread if ignored
            return return_message

        elif len(ai_response['message']['content']) < 1980:
            return_message = await safe_reply(ai_response['message']['content'])
        else:
            chunks = [ai_response['message']['content'][i:i+1980] for i in range(0, len(ai_response['message']['content']), 1980)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    return_message = await safe_reply(chunk)
                else:
                    await safe_reply("... " + chunk)

        # If there is a think and we did reply, send it as a thread message
        if think_content and return_message:
            try:
                thread = await return_message.create_thread(name="AI's Thoughts")
                # Split think_content into <2000 symbol chunks
                think_chunks = [think_content[i:i+1990] for i in range(0, len(think_content), 1990)]
                for i, chunk in enumerate(think_chunks):
                    await thread.send(chunk)
            except Exception as e:
                logging.warning(f"Failed to create thread for AI's think: {e}")

        return return_message

    async def handle_tool_message(self, tool_calls, tools, msg : discord.Message) -> dict:
        for tool_call in tool_calls:
            tool_name = tool_call['function']['name']
            tool_args = tool_call['function']['arguments']

            await self.client.change_presence(activity=discord.Game(name=tool_name))

            logging.info(f"Executing tool: {tool_name}")

            tool = next((t for t in tools if t['function']["name"] == tool_name), None)
            if tool:
                try:
                    raw_tool_result = await tool['func'](client=self.client, source_user=msg.author, **tool_args)

                    if asyncio.iscoroutine(raw_tool_result):
                        raw_tool_result = await raw_tool_result

                    tool_data_dict = None
                    if isinstance(raw_tool_result, dict):
                        tool_data_dict = raw_tool_result
                    elif isinstance(raw_tool_result, str):
                        try:
                            # Attempt to parse if it's a JSON string, especially if it might contain image paths
                            if '"image_paths"' in raw_tool_result: # Heuristic for JSON string
                                tool_data_dict = json.loads(raw_tool_result)
                        except json.JSONDecodeError:
                            logging.warning(f"Tool result substring '\"image_paths\"' found, but failed to parse as JSON: {raw_tool_result[:200]}")
                            # Keep tool_data_dict as None, will fall through to default handling below

                    # Check if we have a dictionary, it indicates success, and contains a list of image_paths
                    if isinstance(tool_data_dict, dict) and \
                       tool_data_dict.get('status') == 'success' and \
                       isinstance(tool_data_dict.get('image_paths'), list):
                        
                        image_paths = tool_data_dict['image_paths']

                        nextcloud_url = g_data.get('cfg').data['nextcloud']["url"]
                        nextcloud_username = g_data.get('cfg').data['nextcloud']["user"]
                        nextcloud_password = g_data.get('cfg').data['nextcloud']["pass"]

                        uploader: NextcloudImageUploader = g_data.get_or_create(
                            'nextcloud_uploader',
                            NextcloudImageUploader,
                            nextcloud_url,
                            nextcloud_username,
                            nextcloud_password
                        )

                        uploaded_image_urls = []
                        for image_path_item in image_paths:
                            if not isinstance(image_path_item, str):
                                logging.warning(f"Skipping non-string image path item: {image_path_item}")
                                continue

                            if os.path.exists(image_path_item):
                                try:
                                    public_url = uploader.upload_image(image_path_item)
                                    if public_url: # Check if upload returned a URL
                                        uploaded_image_urls.append(public_url)
                                        os.remove(image_path_item)
                                    else:
                                        logging.warning(f"Failed to upload image {image_path_item}: No URL returned by uploader.")
                                except Exception as e:
                                    logging.error(f"Error uploading image {image_path_item}: {e}")
                            else:
                                logging.warning(f"Image path does not exist, skipping: {image_path_item}")
                        
                        # Return the list of successfully uploaded image URLs as a JSON string
                        return {
                            "role": "tool",
                            "name": tool_name,
                            "content": json.dumps(uploaded_image_urls) # Correctly format as JSON string
                        }

                    # Fallback: if not an image success case, or if parsing failed.
                    # Return the original (or parsed if dict) raw_tool_result as a string.
                    return {
                        "role": "tool",
                        "name": tool_name,
                        "content": str(raw_tool_result if tool_data_dict is None else tool_data_dict)
                    }

                except Exception as e:
                    stack_trace = traceback.format_exc()
                    logging.error(f"Error executing tool {tool_name}: {e}")
                    logging.error(f"Stacktrace: {stack_trace}")
                    logging.info(f"Tool args: {tool_args}")

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