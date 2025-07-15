import asyncio, json, sys, traceback, logging

from lib.comfy_api_wrapper import ComfyUIWrapper
from lib.global_registry import g_data

# --- Configuration ---
COMFYUI_SERVER_ADDRESS = g_data.get("cfg").data['comfyui']['server']
WORKFLOW_FILE_PATH = g_data.get("cfg").data['comfyui']['workflow']
OUTPUT_IMAGE_DIR = g_data.get("cfg").data['comfyui']['output']

POSITIVE_PROMPT_NODE_ID_STR = g_data.get("cfg").data['comfyui']['positive_node']
NEGATIVE_PROMPT_NODE_ID_STR = g_data.get("cfg").data['comfyui']['negative_node']
SAMPLER_NODE_ID_STR = g_data.get("cfg").data['comfyui']['sampler_node']
# ---

def _prepare_and_execute_workflow(positive_prompt: str, negative_prompt: str = "") -> list[str]:
    """
    Loads workflow, modifies prompts/seed, and triggers execution via ComfyUIWrapper.
    Returns list of image paths or empty list on failure.
    """
    saved_image_paths = []

    # 1. Load Workflow JSON
    try:
        logging.info(f"Tool Sync Helper: Loading workflow from {WORKFLOW_FILE_PATH}")
        with open(WORKFLOW_FILE_PATH, 'r') as f:
            workflow = json.load(f)
        logging.info("Tool Sync Helper: Workflow loaded.")
    except FileNotFoundError:
        logging.info(f"Tool Error: Workflow file '{WORKFLOW_FILE_PATH}' not found.", file=sys.stderr)
        return []
    except json.JSONDecodeError:
        logging.info(f"Tool Error: Could not decode JSON from '{WORKFLOW_FILE_PATH}'. Ensure it's API Format.", file=sys.stderr)
        return []
    except Exception as e:
        logging.info(f"Tool Error: Unexpected error loading workflow: {e}", file=sys.stderr)
        traceback.logging.info_exc()
        return []

    # 2. Modify Workflow with Prompts and Seed
    try:
        logging.info(f"Tool Sync Helper: Modifying workflow prompts. Pos ID: {POSITIVE_PROMPT_NODE_ID_STR}, Neg ID: {NEGATIVE_PROMPT_NODE_ID_STR}")
        if POSITIVE_PROMPT_NODE_ID_STR in workflow:
            workflow[POSITIVE_PROMPT_NODE_ID_STR]["inputs"]["text"] = positive_prompt
        else:
            logging.info(f"Tool Error: Positive prompt Node ID '{POSITIVE_PROMPT_NODE_ID_STR}' not found in workflow!", file=sys.stderr)
            return [] # Cannot proceed

        if negative_prompt and NEGATIVE_PROMPT_NODE_ID_STR:
            if NEGATIVE_PROMPT_NODE_ID_STR in workflow:
                workflow[NEGATIVE_PROMPT_NODE_ID_STR]["inputs"]["text"] = negative_prompt
            else:
                logging.info(f"Tool Warning: Negative prompt Node ID '{NEGATIVE_PROMPT_NODE_ID_STR}' not found, skipping.")

        # # Set random seed
        import random
        if SAMPLER_NODE_ID_STR in workflow:
            seed = random.randint(0, 2**32 - 1)
            workflow[SAMPLER_NODE_ID_STR]["inputs"]["seed"] = seed
            logging.info(f"Tool Sync Helper: Set random seed {seed} for node {SAMPLER_NODE_ID_STR}")
        else:
             logging.info(f"Tool Warning: Sampler Node ID '{SAMPLER_NODE_ID_STR}' not found, skipping seed set.")

    except KeyError as e:
         logging.info(f"Tool Error: KeyError accessing node/input for ID '{e}'. Check workflow structure/IDs.", file=sys.stderr)
         return []
    except Exception as e:
         logging.info(f"Tool Error: Unexpected error modifying workflow parameters: {e}", file=sys.stderr)
         traceback.logging.info_exc()
         return []

    try:
        logging.info("Tool Sync Helper: Instantiating ComfyUIWrapper...")
        wrapper = ComfyUIWrapper(server_address=COMFYUI_SERVER_ADDRESS, output_dir=OUTPUT_IMAGE_DIR)

        logging.info("Tool Sync Helper: Calling wrapper.execute_workflow...")
        saved_image_paths = asyncio.run(wrapper.execute_workflow(workflow=workflow))
        logging.info(f"Tool Sync Helper: Wrapper execution finished. Paths: {saved_image_paths}")

    except Exception as e:
        logging.info(f"Tool Error: Exception calling ComfyUIWrapper: {e}", file=sys.stderr)
        traceback.logging.info_exc()
        return [] # Return empty list on wrapper error

    return saved_image_paths


# Async function that the AI Tool framework will call
# This function now primarily delegates the work to the sync helper via to_thread
async def generate_comfy_image(client, source_user, positive_prompt: str, negative_prompt: str = ""):
    """
    Returns:
        str: A JSON string containing the status ('success' or 'error') and either
             a list of absolute image file paths under 'image_paths' on success,
             or an error message under 'message' on failure.
    """
    logging.info(f"Tool Function: Received request. Positive='{positive_prompt}' Negative='{negative_prompt}'")

    try:
        logging.info("Tool Function: Delegating to sync helper via asyncio.to_thread...")
        image_paths = await asyncio.to_thread(
            _prepare_and_execute_workflow,
            positive_prompt,
            negative_prompt
        )
        logging.info(f"Tool Function: Received paths from sync helper: {image_paths}")

        if image_paths:
            logging.info(f"Tool Function: Generation successful.")
            return json.dumps({"status": "success", "image_paths": image_paths})
        else:
            logging.info("Tool Function: Generation failed or produced no output files.")
            return json.dumps({"status": "error", "message": "ComfyUI image generation failed or produced no output files."})

    except Exception as e:
        logging.info(f"Tool Function Error: Exception in async wrapper: {e}", file=sys.stderr)
        traceback.logging.info_exc()
        return json.dumps({"status": "error", "message": f"An unexpected error occurred in the tool function: {str(e)}"})


def get_tool():
    """Returns the tool definition dictionary for the generate_comfy_image function."""
    # Default Node IDs used by the tool function
    default_pos_id = POSITIVE_PROMPT_NODE_ID_STR
    default_neg_id = NEGATIVE_PROMPT_NODE_ID_STR

    return {
      "type": "function",
      "function": {
        "name": "generate_comfy_image",
        "description": f"Generates an image using a pre-defined ComfyUI workflow located at '{WORKFLOW_FILE_PATH}'. It modifies the text inputs of Node ID '{default_pos_id}' (positive prompt) and Node ID '{default_neg_id}' (negative prompt), and sets a random seed for Node ID '{SAMPLER_NODE_ID_STR}'. Returns a JSON string containing the status ('success' or 'error') and absolute file path(s) of the generated image(s) saved in '{OUTPUT_IMAGE_DIR}' upon success, or an error status/message upon failure.",
        "parameters": {
          "type": "object",
          "properties": {
            "positive_prompt": {
              "type": "string",
              "description": "A detailed description of the desired image content and style. This will be inserted into the text input of the positive prompt node."
            },
            "negative_prompt": {
              "type": "string",
              "description": "Optional. A description of elements, styles, or concepts to avoid in the image. This will be inserted into the text input of the negative prompt node. Defaults to empty."
            }
          },
          "required": ["positive_prompt"]
        }
      }
    }
