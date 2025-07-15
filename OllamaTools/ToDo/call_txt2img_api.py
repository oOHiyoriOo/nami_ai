import aiohttp
import discord
import base64
import os
import logging
from langchain.tools import Tool

# Function to decode and save a base64-encoded image
def decode_and_save_base64(image_base64, save_path):
    """Decode base64 image data and save to a file."""
    with open(save_path, "wb") as f:
        f.write(base64.b64decode(image_base64))

# Async function to call the txt2img API
async def call_txt2img_api(client: discord.Client, source_user, prompt: str):
    """
    Call the Stable Diffusion txt2img API asynchronously and return the image path or URL.
    """
    try:
        # Stable Diffusion API endpoint (update with your actual endpoint)
        webui_server_url = 'http://127.0.0.1:7860'

        # Hardcoded parameters for image generation
        payload = {
            "prompt": prompt,
            "negative_prompt":"Low res, error, cropped, worst quality, low quality, JPEG artifacts, out of frame, watermark, signature, deformed, ugly, mutilated, disfigured, text, extra limbs, face cut, head cut, extra fingers, extra arms, poorly drawn face, mutation, bad proportions, cropped head, malformed limbs, mutated hands, fused fingers, long neck, cropped collar, extra legs, mutated legs, mutated fingers, long fingers, more than two legs, more than two hands, more than two arms, big hands, big legs, duplicated nails, more than five nails, less than five nails, mutated nails, long arms, two thumbs, deformed keyboard, cropped limbs, deformed elbow, deformed wrist, deformed fingers, big thumb, short fingers, deformed body, big body, deformed torso, deformed breast, deformed belly, fat arm, fat torso, fat belly, deformed face, extra butt, extra thighs, deformed thighs, extra knee, deformed knee, distortion, deformed crotch, extra inner thigh, deformed inner thigh, deformed clothes, double head, extra ear, deformed mouth, deformed lips, extra eyelash, doll, dolls, 3D, portrait, medium portrait, up close, close up, low res, bad anatomy, bad face, bad hands, bad body, bad feet, bad proportions, worst quality, low quality, normal quality, gross proportions, blurry, poorly drawn face, text, error, missing fingers, missing arms, missing legs, short legs, extra digit, extra limbs, extra hands, fused hand, missing hand, disappearing arms, disappearing legs, bad ears, poorly drawn ears, extra ears, missing ears, huge thighs, bad hands, fused hand, missing hand, missing eyes, disappearing arms, watermark, signature, poorly drawn eyes, deformed hands, deformed eyes, deformed body, blemishes, low detail, fat body, chubby, animal ears on human, animal tail on human, deformed, mutilated, disfigured, human flesh tones, eyes. Blurred eyes, blurred face, disfigured, remove text, extra limbs, face cut, extra arms, head cut off, extra fingers, extra arms, poorly drawn face, mutation, bad proportions, cropped head, malformed limbs, mutated hands, deformed hands, fused fingers, illustration, painting, drawing, art, sketch, cropping of character, cropping of head cropping of arms, cropping of legs, cropping of character, disfigured hands, JPG photo, sketch drawing, cropping of legs, cropping of arms, cropping of feet, distorted weapons, deformed weapons, s, no text, floating weapons, phantom weapons, art, sketch, lowers, cropped, JPEG artifacts, out of frame, watermark, signature, deformed, ugly, mutilated, disfigured, text, extra limbs, face cut, head cut, extra fingers, extra arms, poorly drawn face, mutation, bad proportions, cropped head, malformed limbs, mutated hands, fused fingers, long neck, illustration, painting, drawing, art, sketch, weird angles, extra ears+",
            "steps": 36,
            "width": 1024,
            "height": 1024,
            "sampler": "DPM++ 2M",  # Sampling method
            "cfg_scale": 7.5,  # Classifier-Free Guidance scale
            "seed": -1,  # Random seed
            "override_settings": {
                "sd_model_checkpoint": "AnythingXL_xl",
            }
        }

        # Async POST request to the API
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{webui_server_url}/sdapi/v1/txt2img",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    # Parse the JSON response
                    data = await response.json()

                    # Process and save the images
                    images = data.get("images", [])
                    saved_paths = []

                    # Save each image locally
                    for index, image_base64 in enumerate(images):
                        output_dir = "output_images"
                        os.makedirs(output_dir, exist_ok=True)

                        save_path = os.path.join(
                            output_dir,  # Directory to save images
                            f"txt2img-{index}.png"
                        )
                        decode_and_save_base64(image_base64, save_path)
                        saved_paths.append(save_path)

                    if saved_paths:
                        return {"images": saved_paths, "message": "Picture send to the user don't link it."}
                    else:
                        return {"message": "No images generated."}
                else:
                    # Handle non-200 responses
                    error_message = await response.text()
                    return f"Error: API returned status code {response.status}. Details: {error_message}"

    except Exception as e:
        logging.error(f"Error calling txt2img API: {e}")
        logging.exception(e)
        return f"Error: {str(e)}"

# # Tool definition for LangChain AI
# def get_tool():
#     return {
#         "type": "function",
#         "function": {
#             "name": "call_txt2img_api",
#             "description": "Generate an image using Stable Diffusion's txt2img API based on a text prompt.",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "prompt": {
#                         "type": "string",
#                         "description": "A at least 512 symbole long detailed description of the image to generate."
#                     }
#                 }
#             }
#         }
#     }
