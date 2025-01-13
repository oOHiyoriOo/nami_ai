import __main__, logging, os, json, discord, asyncio, traceback, json, re, inspect

from lib.load_events import register_event
from lib.tool_loader import load_tools

from ollama import Client as Ollama


# Create a dictionary to store user-specific memories
lock = asyncio.Lock()  # Initialize the lock

# Load memories from disk if available
async def load_memories(user_id: str):
    global lock
    async with lock:  # Acquire the lock
        if os.path.exists('user_memories.json'):
            with open('user_memories.json', 'r') as f:
                try:
                    data = json.load(f)
                    return  data.get(user_id, [])
                except json.JSONDecodeError:
                    logging.error("Error decoding the memory file.")
                    return []

# Save memories to disk
async def save_memories(user_id: str, history: list):
    global lock
    async with lock:  # Acquire the lock
        if os.path.exists('user_memories.json'):
            with open('user_memories.json', 'r') as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    logging.error("Error decoding the memory file.")
                    data = {}
        else:
            data = {}

        data[user_id] = history

        with open('user_memories.json', 'w') as f:
            json.dump(data, f)

# Create a new instance of the Ollama model with the provided base URL
def get_ollama_instance():
    ollama_url = __main__.cfg.data['ollama']['url']
    return Ollama(host=ollama_url)

def extract_tool_request(response):
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
                            "arguments": tool_call.get("args", {})
                        }
                    }]
                }
            }
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse tool call JSON: {e}")
            logging.error(f"Response content: {tool_call_content}")
            return response

    return response

async def process_ai_response(response, reply_message, history):
    if len(response['message']['content']) == 0:
        logging.info(response)
        await reply_message.edit(content="I'm sorry, I don't have a response for that.")
    elif len(response['message']['content']) < 1980:
        await reply_message.edit(content=response['message']['content'])
    else:
        logging.info("Response too long, splitting into chunks.")
        chunks = [response['message']['content'][i:i+1980] for i in range(0, len(response['message']['content']), 1980)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await reply_message.edit(content=chunk)
            else:
                await reply_message.reply("... " + chunk)
    
    history.append({"role":"assistant", "content": response['message']['content']})
    return history

async def ai_chat_handler(msg, client, cfg):
    model_name = cfg.data['ollama']['model']

    if f"<@{client.user.id}>" not in msg.content:
        if msg.author == client.user or msg.author.bot:
            return

        if msg.channel.id not in __main__.cfg.data['ai_channel']:
            return

    if f"<@{client.user.id}>" in msg.content:
        if msg.author.id not in cfg.data['dc']['permitted_users']:
            return
        
        msg.content = msg.content.replace(f"<@{client.user.id}>","")

    user_id = str(msg.author.id)
    user_input = msg.content
    history = await load_memories(user_id)

    try:
        reply_message = await msg.reply("I'm thinking...")
        external_tools = await load_tools(client)
        ollama = get_ollama_instance()

        history.append({"role": "user", "content": user_input})
        messages = await prepare_messages(external_tools, client, history, msg.author)
        
        sanitized_tools = [{k: v for k, v in tool.items() if k != 'func'} for tool in external_tools]
        response = await asyncio.to_thread(ollama.chat, model=model_name, messages=messages, tools=sanitized_tools)
        response = extract_tool_request(response)

        while 'tool_calls' in response['message'] and len(response['message']['tool_calls']) > 0:
            history = await handle_tool_message(history, response['message']['tool_calls'], external_tools, client, msg)
            messages = await prepare_messages(external_tools, client, history, msg.author)
            sanitized_tools = [{k: v for k, v in tool.items() if k != 'func'} for tool in external_tools]
            response = await asyncio.to_thread(ollama.chat, model=model_name, messages=messages, tools=sanitized_tools)
            response = extract_tool_request(response)

        history = await process_ai_response(response, reply_message, history)
        await save_memories(user_id, history=history)

    except Exception as e:
        logging.error(f"Error processing AI response: {e}")
        logging.exception("Stacktrace:")
        await msg.channel.send("Oops! I ran into an issue while trying to respond. Try again later!")

async def prepare_messages(external_tools, client, history, author):
    tool_result = {}
    memory_result = {}

    query_discord_user_tool = next((t for t in external_tools if t['function']["name"] == "query_discord_user"), None)
    core_memory_tool = next((t for t in external_tools if t['function']["name"] == "core_memory"), None)

    if query_discord_user_tool:
        try:
            tool_result = {
                "role": "system",
                "content": "Your Chat Partner: " + str( await query_discord_user_tool['func'](client, str(author.id), str(author.id)) )
            }
        except:
            pass
    
    if core_memory_tool:
        try:
            memory_result = {
                "role": "system",
                "content": "Your Memories:\na" + str( await core_memory_tool['func'](client, str(author.id), memory=None) )
            }
        except:
            pass

    messages = [{"role": "system", "content": __main__.cfg.data['ollama']['system_prompt']}]
    if tool_result and "None" not in tool_result['content']:
        logging.debug("Added User Info: " + tool_result['content'])
        logging.debug("="*50)
        messages.append(tool_result)

    if memory_result and "None" not in memory_result['content']:
        logging.debug("Added User Memories: " + memory_result['content'])
        logging.debug("="*50)
        messages.append(memory_result)

    messages.extend(history)

    return messages

async def handle_tool_message(history, tool_calls, external_tools, client, msg):
    for tool_call in tool_calls:
        tool_name = tool_call['function']['name']
        tool_args = tool_call['function']['arguments']

        logging.info(f"Executing tool: {tool_name}")

        # Find the tool by name from the list of external tools
        tool = next((t for t in external_tools if t['function']["name"] == tool_name), None)
        if tool:
            try:
                tool_result = await tool['func'](client=client, source_id=str(msg.author.id), **tool_args)

                if asyncio.iscoroutine(tool_result):
                    tool_result = await tool_result

                # if "images" in tool_result:
                #     embeds = []
                #     for image_path in tool_result["images"]:
                #         embed = Embed().set_image(url=f"attachment://{os.path.basename(image_path)}")
                #         embeds.append(embed)
                    
                #     files = [discord.File(image_path) for image_path in tool_result["images"]]
                #     await msg.channel.send(files=files, embeds=embeds)

                #     tool_result = tool_result["message"]

                # Add tool result directly to history as a system message
                history.append({
                    "role": "tool",
                    "name": tool_name,
                    "content": tool_result
                })
                return history

            except Exception as e:
                stack_trace = traceback.format_exc()
                logging.error(f"Error executing tool {tool_name}: {e}")
                logging.error(f"Stacktrace: {stack_trace}")
                logging.info(f"Tool args: {tool_args}")

                history.append({
                    "role": "tool",
                    "name": tool_name,
                    "content": stack_trace
                })
                return history
        else:
            logging.warning(f"Tool {tool_name} not found.")
            history.append({
                "role": "system", 
                "content": f"Tool {tool_name} not found."
            })
            return history

async def setup(client, cfg):
    # Define a wrapper function to pass `client` and `cfg` to `ai_chat_handler`
    async def wrapped_ai_chat_handler(msg):
        await ai_chat_handler(msg, client, cfg)

    # Register the wrapped handler for the `on_message` event
    register_event("on_message", wrapped_ai_chat_handler)
