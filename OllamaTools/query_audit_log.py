import logging, discord, json
from langchain.tools import Tool
from pydantic import BaseModel, Field

async def query_audit_log(
    client: discord.Client,
    source_user : discord.Member, # the id of the user who is currently talking.
    action: str = None,
):
    """
    Query the Discord server's audit log with optional filters.
    """
    # Determine guild and user from source_user
    if hasattr(source_user, "guild") and hasattr(source_user, "id"):
        guild = source_user.guild
        user = source_user
    elif hasattr(source_user, "guild") and hasattr(source_user, "author"):
        guild = source_user.guild
        user = source_user.author
    else:
        return "Could not determine guild or user from source_user."

    logging.info(f"Querying audit log for guild {guild.id} with filters: user_id={user.id}, action={action}, limit=5")
    try:
        filters = {"user": user}
        if action:
            try:
                filters['action'] = getattr(discord.AuditLogAction, action)
            except AttributeError:
                return f"Invalid action type: {action}"

        entries = []
        async for entry in guild.audit_logs(limit=5, **filters):
            entry_info = {
                "action": str(entry.action),
                "user": str(entry.user),
                "target": str(entry.target),
                "reason": entry.reason,
                "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "changes": [str(change) for change in getattr(entry, "changes", [])] if hasattr(entry, "changes") else None,
            }
            entries.append(entry_info)
        return json.dumps(entries)

    except Exception as e:
        logging.error(f"Error querying audit log: {e}")
        logging.exception(e)
        return f"Error: {str(e)}"

def get_tool():
    audit_log_actions = [
        "guild_update", "channel_create", "channel_update", "channel_delete",
        "overwrite_create", "overwrite_update", "overwrite_delete", "kick",
        "member_prune", "ban", "unban", "member_update", "member_role_update",
        "member_move", "member_disconnect", "bot_add", "role_create",
        "role_update", "role_delete", "invite_create", "invite_update",
        "invite_delete", "webhook_create", "webhook_update", "webhook_delete",
        "emoji_create", "emoji_update", "emoji_delete", "message_delete",
        "message_bulk_delete", "message_pin", "message_unpin",
        "integration_create", "integration_update", "integration_delete",
        "stage_instance_create", "stage_instance_update", "stage_instance_delete",
        "sticker_create", "sticker_update", "sticker_delete",
        "scheduled_event_create", "scheduled_event_update", "scheduled_event_delete",
        "thread_create", "thread_update", "thread_delete",
        "app_command_permission_update", "soundboard_sound_create",
        "soundboard_sound_update", "soundboard_sound_delete", "automod_rule_create",
        "automod_rule_update", "automod_rule_delete", "automod_block_message",
        "automod_flag_message", "automod_timeout_member",
        "creator_monetization_request_created", "creator_monetization_terms_accepted"
    ]
    action_description = (
        "Optional. Filter by action type. "
        f"Available actions: {', '.join(audit_log_actions)}."
    )

    return {
      "type": "function",
      "function": {
        "name": "query_audit_log",
        "description": "Query the Discord server's audit log for the current user with an optional action type filter. Always returns up to 5 entries.",
        "parameters": {
          "type": "object",
          "properties": {
            "action": {
              "type": "string",
              "description": action_description
            }
          },
          "required": []
        }
      }
    }