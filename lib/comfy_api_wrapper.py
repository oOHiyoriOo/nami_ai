# comfy_api_wrapper.py
import asyncio
import aiohttp
import json
import uuid
import urllib.parse
import os
import sys
import traceback
import logging

class ComfyUIWrapper:
    """
    A wrapper class for basic ComfyUI API interactions using aiohttp and WebSockets.
    Handles queuing prompts, monitoring progress, and retrieving results.
    Accepts a pre-loaded workflow dictionary.
    """

    def __init__(self, server_address="127.0.0.1:8188", output_dir="comfy_output"):
        """
        Initializes the wrapper.

        Args:
            server_address (str): The address (host:port) of the ComfyUI server.
            output_dir (str): The directory to save generated images.
        """
        if not server_address:
            raise ValueError("ComfyUI server address cannot be empty.")
        self.server_address = server_address.rstrip('/') # Remove trailing slash if present
        self.output_dir = output_dir
        self.prompt_url = f"http://{self.server_address}/prompt"
        self.ws_url_base = f"ws://{self.server_address}/ws"
        self.view_url_base = f"http://{self.server_address}/view"
        os.makedirs(self.output_dir, exist_ok=True)
        logging.info(f"ComfyUIWrapper initialized for server: {self.server_address}, output: {self.output_dir}")

    async def _queue_prompt(self, session, client_id, prompt_workflow):
        """Internal: Sends the workflow to the ComfyUI API queue."""
        # Ensure prompt_workflow is a dictionary
        if not isinstance(prompt_workflow, dict):
             logging.info(f"Wrapper Error: Invalid workflow data type passed to _queue_prompt: {type(prompt_workflow)}")
             return None
        payload = json.dumps({"prompt": prompt_workflow, "client_id": client_id}).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        logging.info(f"Wrapper: Queuing prompt to {self.prompt_url}")
        try:
            async with session.post(self.prompt_url, data=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.info(f"Wrapper Error: Failed to queue prompt. Status: {response.status}, Response: {error_text}")
                    return None
                response_json = await response.json()
                logging.info(f"Wrapper: Queue response received: {response_json}")
                return response_json
        except aiohttp.ClientError as e:
            logging.info(f"Wrapper Error: Network error during queueing: {e}")
            return None
        except json.JSONDecodeError as e:
             logging.info(f"Wrapper Error: Failed to decode queue response JSON: {e}")
             return None
        except Exception as e:
             logging.info(f"Wrapper Error: Unexpected error during queueing: {e}")
             traceback.logging.info_exc()
             return None


    async def _get_image(self, session, filename, subfolder, folder_type):
        """Internal: Gets image data from the /view endpoint."""
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url = f"{self.view_url_base}?{urllib.parse.urlencode(params)}"
        logging.info(f"Wrapper: Getting image from {url}")
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    logging.info(f"Wrapper Error: Failed to get image '{filename}'. Status: {response.status}")
                    return None
                logging.info(f"Wrapper: Successfully retrieved image data for '{filename}'.")
                return await response.read() # Return raw image bytes
        except aiohttp.ClientError as e:
            logging.info(f"Wrapper Error: Network error getting image '{filename}': {e}")
            return None
        except Exception as e:
             logging.info(f"Wrapper Error: Unexpected error getting image '{filename}': {e}")
             traceback.logging.info_exc()
             return None

    async def _handle_websocket(self, ws_url, prompt_id, ws_timeout=180):
        """Internal: Handles WebSocket connection for results."""
        logging.info(f"Wrapper: Connecting WebSocket to {ws_url} for prompt {prompt_id}...")
        session = None # Define session outside try block for cleanup
        ws = None
        try:
            session = aiohttp.ClientSession()
            async with session.ws_connect(ws_url, timeout=20) as ws:
                logging.info(f"Wrapper: WebSocket connected for prompt {prompt_id}.")
                while True:
                    try:
                        msg_bytes = await ws.receive(timeout=ws_timeout)

                        if msg_bytes.type == aiohttp.WSMsgType.TEXT:
                            msg = json.loads(msg_bytes.data)
                            msg_type = msg.get('type')

                            if msg_type == 'status':
                                data = msg.get('data', {})
                                q = data.get('status', {}).get('execinfo', {}).get('queue_remaining', None)
                                if q is not None: logging.info(f"Wrapper Status ({prompt_id}): Queue remaining: {q}")

                            elif msg_type == 'progress':
                                data = msg.get('data', {})
                                logging.info(f"Wrapper Progress ({prompt_id}): Step {data.get('value', '?')} / {data.get('max', '?')}")

                            elif msg_type == 'executing':
                                 data = msg.get('data', {})
                                 if data.get('prompt_id') == prompt_id and data.get('node') is None:
                                     logging.info(f"Wrapper Warning ({prompt_id}): Execution stopped or node is null.")

                            elif msg_type == 'executed':
                                data = msg.get('data', {})
                                if data.get('prompt_id') == prompt_id:
                                    logging.info(f"Wrapper: Execution complete message received for prompt {prompt_id}!")
                                    # Log the raw output for debugging if needed
                                    # logging.info(f"--- RAW OUTPUTS FOR PROMPT {prompt_id} ---")
                                    # logging.info(json.dumps(data.get('output', {}), indent=2, default=str))
                                    # logging.info(f"--- END RAW OUTPUTS FOR PROMPT {prompt_id} ---")
                                    return data.get('output', {}) # Return the output data

                        elif msg_bytes.type == aiohttp.WSMsgType.CLOSED:
                            logging.info(f"Wrapper Warning ({prompt_id}): WebSocket connection closed by server.")
                            break
                        elif msg_bytes.type == aiohttp.WSMsgType.ERROR:
                            ws_exception = ws.exception()
                            logging.info(f"Wrapper Error ({prompt_id}): WebSocket error occurred: {ws_exception}")
                            break

                    except asyncio.TimeoutError:
                        logging.info(f"Wrapper Error ({prompt_id}): WebSocket receive timeout after {ws_timeout} seconds.")
                        break
                    except json.JSONDecodeError:
                        logging.info(f"Wrapper Warning ({prompt_id}): Received non-JSON WebSocket message.")
                        continue
                    except Exception as e_ws:
                         logging.info(f"Wrapper Error ({prompt_id}): Error processing WebSocket message: {e_ws}")
                         traceback.logging.info_exc()
                         break

        except aiohttp.ClientError as e_conn:
            logging.info(f"Wrapper Error ({prompt_id}): WebSocket connection failed: {e_conn}")
            return None
        except asyncio.TimeoutError:
             logging.info(f"Wrapper Error ({prompt_id}): WebSocket initial connection timed out.")
             return None
        except Exception as e_outer:
             logging.info(f"Wrapper Error ({prompt_id}): Outer WebSocket handling error: {e_outer}")
             traceback.logging.info_exc()
             return None
        finally:
            if ws and not ws.closed:
                await ws.close()
                logging.info(f"Wrapper: WebSocket closed for prompt {prompt_id}.")
            if session and not session.closed:
                await session.close()
                logging.info(f"Wrapper: ClientSession closed for prompt {prompt_id}.")

        logging.info(f"Wrapper Warning ({prompt_id}): WebSocket handling finished without receiving 'executed' message.")
        return None

    async def execute_workflow(self, workflow: dict, ws_timeout: int = 180) -> list[str]:
        """
        Queues a pre-loaded/modified workflow, waits for results via WebSocket,
        saves images, and returns their absolute paths.

        Args:
            workflow (dict): The workflow dictionary (API Format), already modified with desired inputs.
            ws_timeout (int): Timeout in seconds for waiting for WebSocket messages.

        Returns:
            list[str]: List of absolute paths to generated images, or empty list on failure.
        """
        client_id = str(uuid.uuid4())
        ws_url = f"{self.ws_url_base}?clientId={client_id}"
        saved_image_paths = []
        prompt_id = None # Initialize prompt_id

        logging.info(f"Wrapper: Starting execute_workflow process. Client ID: {client_id}")

        session = None # Define session outside try block for cleanup
        try:
            session = aiohttp.ClientSession()

            # 1. Queue Prompt (workflow is now passed directly)
            queue_data = await self._queue_prompt(session, client_id, workflow)
            if not queue_data or 'prompt_id' not in queue_data:
                logging.info("Wrapper Error: Failed to queue prompt or get prompt_id.")
                return [] # Exit if queuing failed
            prompt_id = queue_data['prompt_id']
            logging.info(f"Wrapper: Prompt {prompt_id} successfully queued.")

            # 2. Wait for results via WebSocket
            final_outputs = await self._handle_websocket(ws_url, prompt_id, ws_timeout)
            if not final_outputs:
                logging.info(f"Wrapper Error: Did not receive execution results for prompt {prompt_id}.")
                return [] # Exit if execution didn't complete successfully

            # 3. Process outputs and retrieve images
            logging.info(f"Wrapper: Processing results for prompt {prompt_id}...")
            image_found = False
            # --- MODIFIED PROCESSING LOGIC ---
            # Check if final_outputs is a dictionary AND directly contains the 'images' key

            logging.debug(json.dumps(final_outputs, indent=4))

            if isinstance(final_outputs, dict) and 'images' in final_outputs:
                # Directly process the images list from final_outputs
                for image_info in final_outputs['images']:
                    if isinstance(image_info, dict) and image_info.get('type') == 'output':
                        image_found = True
                        filename = image_info.get('filename')
                        subfolder = image_info.get('subfolder', '')
                        folder_type = image_info.get('type', 'output') # Known type

                        if not filename:
                            logging.info(f"Wrapper Warning ({prompt_id}): Found image output but filename is missing. Image Info: {image_info}")
                            continue # Skip if no filename

                        logging.info(f"Wrapper: Retrieving output image: {filename}")
                        image_data = await self._get_image(session, filename, subfolder, folder_type)

                        if image_data:
                            unique_filename = f"{prompt_id}_{filename}"
                            save_path = os.path.join(self.output_dir, unique_filename)
                            try:
                                with open(save_path, 'wb') as f:
                                    f.write(image_data)
                                logging.info(f"Wrapper: Image successfully saved: {save_path}")
                                saved_image_paths.append(os.path.abspath(save_path))
                            except IOError as e:
                                logging.info(f"Wrapper Error: Failed to save image {filename}: {e}")
                        else:
                            logging.info(f"Wrapper Warning: Failed to retrieve image data for {filename}")
            # --- END MODIFIED PROCESSING LOGIC ---
            else:
                 # Log if the structure is not the expected direct output dictionary
                 logging.info(f"Wrapper Warning ({prompt_id}): Expected final_outputs to be a dict with an 'images' key, but got type {type(final_outputs)} or 'images' key missing. Full output: {final_outputs}")

            # This warning should now only trigger if the 'images' list was empty or contained no 'output' type images
            if not image_found:
                 logging.info(f"Wrapper Warning ({prompt_id}): Execution finished, but no images of type 'output' were processed from the results.")

        except aiohttp.ClientError as e_net:
            logging.info(f"Wrapper Network Error during session: {e_net}")
        except Exception as e_main:
            logging.info(f"Wrapper Error: An unexpected error occurred during execution process: {e_main}")
            traceback.logging.info_exc() # logging.info full traceback for debugging wrapper
        finally:
            if session and not session.closed:
                await session.close()
                logging.info(f"Wrapper: Final ClientSession closed.")

        logging.info(f"Wrapper: execute_workflow process finished for prompt {prompt_id}. Returning {len(saved_image_paths)} paths.")
        return saved_image_paths
