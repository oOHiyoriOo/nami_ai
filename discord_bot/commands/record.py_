import discord
from discord.ext import commands
from discord.ext import voice_recv
import wave
import asyncio
import whisper
import re

# Load the Whisper model
model = whisper.load_model("base")  # Use "tiny" or "base" for faster performance

class AudioRecorder(voice_recv.BasicSink):
    def __init__(self):
        self.frames = []
        self.decode = True
        self.cb_rtcp = None

    def write(self, user, data):
        # Append raw PCM audio data
        self.frames.append(data.pcm)

    def save(self, filename):
        # Save the recorded frames as a WAV file
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(2)  # Stereo audio
            wf.setsampwidth(2)  # 16-bit sample width
            wf.setframerate(48000)  # Sample rate
            wf.writeframes(b''.join(self.frames))

    def transcribe(self):
        # Save the recording temporarily
        self.save("temp_audio.wav")
        # Transcribe using Whisper
        result = model.transcribe("temp_audio.wav")
        return result["text"]

class Command:
    def __init__(self, client, cfg):
        self.tree = client.tree
        self.cfg = cfg
        self.register_commands()
        self.client = client

    def register_commands(self):
        @self.tree.command(name="record", description="Records audio from a voice channel and transcribes it.")
        async def record(interaction: discord.Interaction):
            if interaction.user.id != 210428907386699777:  # Replace with your Discord ID
                await interaction.response.send_message("❌ You are not authorized to use this command!")
                return

            if interaction.user.voice:
                channel = interaction.user.voice.channel

                # Connect to the voice channel if not already connected
                if not self.client.voice_clients:
                    vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
                else:
                    vc = self.client.voice_clients[0]

                # Start recording
                sink = AudioRecorder()
                vc.listen(sink)
                await interaction.response.send_message("🎤 Recording for 10 seconds...")
                await asyncio.sleep(10)

                # Stop recording and transcribe
                vc.stop_listening()
                text = sink.transcribe()
                
                if "nami" in text.lower():
                    text = re.sub(r'nami', f'<@{self.client.user.id}>', text, flags=re.IGNORECASE)

                await channel.send(text)

            else:
                await interaction.response.send_message("You must be in a voice channel to use this command.")