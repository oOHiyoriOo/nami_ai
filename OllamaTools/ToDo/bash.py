import logging
import discord
import paramiko
import asyncio

from langchain.tools import Tool
from colorama import Fore
import paramiko.win_openssh

async def bash(client: discord.Client, source_user, command: str, close_session: bool = False):
    """
    Execute a terminal command and return the snapshot of the output after stabilization.
    """
    logging.info(f"Executing Bash command: {command}")
    try:
        # SSH connection details
        ssh_host = "192.168.188.159"
        ssh_port = 22
        ssh_user = "ahri"
        ssh_key_path = "C:\\Users\\Wolfs\\.ssh\\nami_rsa"

        def run_ssh_command():
            if not hasattr(client, "ssh_client"):
                client.ssh_client = {}

            # Reuse or create a session
            if source_user not in client.ssh_client:
                ssh_client = paramiko.SSHClient()
                ssh_client.load_system_host_keys()
                ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # Connect to the server
                ssh_client.connect(
                    hostname=ssh_host,
                    port=ssh_port,
                    username=ssh_user,
                    key_filename=ssh_key_path,
                )

                # Create a PTY channel
                channel = ssh_client.invoke_shell()
                client.ssh_client[source_user] = {
                    "client": ssh_client,
                    "channel": channel,
                    "buffer": ""
                }
            else:
                channel = client.ssh_client[source_user]["channel"]

            return channel

        # Run the SSH connection setup in a separate thread
        channel = await asyncio.to_thread(run_ssh_command)

        # Send the command to the terminal
        channel.send(command.strip() + "\n")

        # Monitor output for stabilization
        output = []
        snapshot_timeout = 1  # Time in seconds to consider output stabilized
        last_data_time = asyncio.get_event_loop().time()
        max_timeout = 300  # Maximum time in seconds
        start_time = asyncio.get_event_loop().time()

        while True:
            # Check if there's data to read
            if channel.recv_ready():
                data = channel.recv(1024).decode()
                logging.info(f"[{Fore.GREEN}SSH{Fore.RESET}] {data.strip()}")
                output.append(data)

                # Update the last time we received data
                last_data_time = asyncio.get_event_loop().time()

            # Check if output has stabilized
            if asyncio.get_event_loop().time() - last_data_time > snapshot_timeout:
                break
            
            if asyncio.get_event_loop().time() - start_time > max_timeout:
              output.append("\n[Error: Command timed out]")
              break

            # Yield control to the event loop while waiting for new data
            await asyncio.sleep(0.1)

        # Optionally close the session
        if close_session:
            await asyncio.to_thread(channel.close)
            await asyncio.to_thread(client.ssh_client[source_user]["client"].close)
            del client.ssh_client[source_user]

        # Return the snapshot of the terminal's output
        return "".join(output)

    except Exception as e:
        logging.error(f"Error executing SSH command: {e}")
        logging.exception(e)
        return f"Error executing SSH command: {e}"


def get_tool():
    return {
      "type": "function",
      "function": {
        "name": "bash",
        "description": "Your own Bash access, use as you please!",
        "parameters": {
          "type": "object",
          "properties": {
            "command": {
              "type": "string",
              "description": "The terminal command to execute over SSH."
            },
            "close_session": {
              "type": "boolean",
              "description": "Close the SSH session after the command is executed."
            }
          }
        }
      }
    }
