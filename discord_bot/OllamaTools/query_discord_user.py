import logging, discord, json
from langchain.tools import Tool
from pydantic import BaseModel, Field

async def query_discord_user(client: discord.Client, source_user, user_id : str|int ):
    logging.info(f"Querying user with ID {user_id}")
    try:
        user = client.get_user(int(user_id))

        if not user:
          user = await client.fetch_user(int(user_id))

        if not user:
          return f"User not found with ID {user_id}."

        member = user.mutual_guilds[0].get_member(user.id)
        if not member:
            member = await user.mutual_guilds[0].fetch_member(user.id)
          
        if not member and user:
          user_info = {
              "name": user.name,
              "display_name": user.display_name if hasattr(user, 'display_name') else user.name,
              "id": user.id,
              "avatar_url": user.avatar_url if hasattr(user, 'avatar_url') else None,
              "created_at": user.created_at.strftime("%B %d, %Y"),
              "is_bot": user.bot
          }

          return json.dumps(user_info)

##############################################################################################################################
        
        member_info = {field: str(getattr(member, field, None)) for field in [
            "accent_color", "activity", "avatar_url", "color", "created_at", "display_name", "global_name", "id", "name", "nick", "premium_since", "status"
        ] if getattr(member, field, None) is not None}
        
        return json.dumps(member_info)

    except Exception as e:
        logging.error(f"Error querying user: {e}")
        logging.exception(e)
        return f"Error: {str(e)}"

def get_tool():
    return {
      "type": "function",
      "function": {
        "name": "query_discord_user",
        "description": "Call client.fetch_user to query a Discord user by their ID.",
        "parameters": {
          "type": "object",
          "properties": {
            "user_id": {
              "type": "string",
              "description": "The Discords user ID to query."
            }
          },
          "required": ["user_id"]
        }
      }
    }