class ChatHelper:
    @staticmethod
    async def build_history(sql_history, msg, client):
        """
        Build the chat history for the AI, including system, user, assistant, and tool messages.
        Tool messages are stored with user_id 0 and role 'tool'.
        Each user message is formatted as: "{username} [timestamp] : {content}"
        """
        history = []
        for entry in sql_history:
            # entry is a dict with keys: content, user_id, name, timestamp
            content = entry.get("content", "")
            user_id = int(entry.get("user_id", 0))
            name = entry.get("name", "Unknown")
            timestamp = entry.get("timestamp", "")
            if user_id == client.user.id:
                history.append({"role": "assistant", "content": content})
            elif user_id == 0:
                # Tool message
                history.append({"role": "tool", "content": content})
            else:
                # Only format if content does not already start with "{name} ["
                if content.startswith(f"{name} ["):
                    history.append({"role": "user", "content": content})
                else:
                    history.append({"role": "user", "content": f"{name} [{timestamp}] : {content}"})
        return history
