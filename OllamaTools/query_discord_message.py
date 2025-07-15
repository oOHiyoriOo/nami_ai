import logging, discord
from langchain.tools import Tool
from pydantic import BaseModel, Field

class QueryInput(BaseModel):
    message_id : str = Field(description="ID of message to query.")
    channel_id : str = Field(description="ID of channel where message was sent.")

async def query_discord_message(client: discord.Client, source_user, channel_id: str, message_id: str):
    try:
        channel = client.get_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))

        message_info = {
          "content": message.content,
          "author": {
            "name": message.author.name,
            "discriminator": message.author.discriminator,
            "display_name": message.author.display_name if hasattr(message.author, 'display_name') else message.author.name,
            "id": message.author.id,
            "avatar_url": message.author.avatar_url if hasattr(message.author, 'avatar_url') else None,
            "created_at": message.author.created_at.strftime("%B %d, %Y"),
            "is_bot": message.author.bot
          },
          "created_at": message.created_at.strftime("%B %d, %Y %H:%M:%S"),
          "edited_at": message.edited_at.strftime("%B %d, %Y %H:%M:%S") if message.edited_at else None,
          "id": message.id,
          "channel_id": message.channel.id
        }

        response_text = (
            f"Message Content: {message_info['content']}\n"
            f"Author: {message_info['author']['name']}#{message_info['author']['discriminator']}\n"
            f"Display Name: {message_info['author']['display_name']}\n"
            f"Author ID: {message_info['author']['id']}\n"
            f"Message ID: {message_info['id']}\n"
            f"Channel ID: {message_info['channel_id']}\n"
            f"Created At: {message_info['created_at']}\n"
            f"Edited At: {message_info['edited_at']}\n"
            f"Bot: {'Yes' if message_info['author']['is_bot'] else 'No'}\n"
        )

        if message_info['author']['avatar_url']:
            response_text += f"Avatar URL: {message_info['author']['avatar_url']}\n"

        return response_text

    except Exception as e:
        logging.error(f"Error querying user: {e}")
        return f"Error: {str(e)}"

def get_tool():
    return {
      "type": "function",
      "function": {
        "args_schema": QueryInput,
        "name": "query_discord_message",
        "description": "Get's a Discord Message by its Channel- and Message- ID.",
        "parameters": {
          "type": "object",
          "properties": {
            "channel_id": {
              "type": "string",
              "description": "The Discords Channel of the message"
            },
            "message_id": {
              "type": "string",
              "description": "The Discords message ID to query."
            }
          },
          "required": ["input"]
        }
      }
    }