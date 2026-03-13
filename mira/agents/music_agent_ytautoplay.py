import asyncio
import shutil
import subprocess
import sounddevice as sd
import numpy as np
import os, signal


class MusicAgentYTAutoplayAsync:
    def __init__(self):
        self.yt_proc = None
        self.ffmpeg_proc = None
        self.stream = None
        self.playing = False
        self.paused = False

    def available(self):
        return shutil.which("yt-dlp") and shutil.which("ffmpeg")

    async def play(self, query: str):
        if not self.available():
            return "yt-dlp or ffmpeg not installed."

        if self.playing:
            return None  # already playing

        self.playing = True
        self.paused = False

        ffmpeg_path = shutil.which("ffmpeg")

        self.yt_proc = subprocess.Popen(
            ["yt-dlp", "-f", "bestaudio", "-o", "-", f"ytsearch1:{query}"],
            stdout=subprocess.PIPE,
            preexec_fn=os.setsid  # new process group
        )
        self.ffmpeg_proc = subprocess.Popen(
            [ffmpeg_path, "-i", "pipe:0",
             "-f", "s16le", "-acodec", "pcm_s16le",
             "-ac", "2", "-ar", "48000", "pipe:1"],
            stdin=self.yt_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )

        try:
            self.stream = sd.OutputStream(samplerate=48000, channels=2, dtype="int16")
            self.stream.start()

            while self.playing:
                if self.paused:
                    await asyncio.sleep(0.1)
                    continue

                chunk = await asyncio.to_thread(self.ffmpeg_proc.stdout.read, 4096)
                if not chunk:
                    break

                audio = np.frombuffer(chunk, dtype=np.int16)
                if audio.size > 0:
                    try:
                        audio = audio.reshape(-1, 2)
                    except ValueError:
                        audio = np.column_stack([audio, audio])
                    self.stream.write(audio)

        except KeyboardInterrupt:
            print("[MusicAgent] CTRL+C pressed → stopping playback only.")
            self.stop()
        finally:
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None

    def stop(self):
        self.playing = False

        # stop audio stream immediately
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        # kill yt-dlp & ffmpeg groups
        if self.yt_proc and self.yt_proc.poll() is None:
            os.killpg(os.getpgid(self.yt_proc.pid), signal.SIGTERM)
        if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
            os.killpg(os.getpgid(self.ffmpeg_proc.pid), signal.SIGTERM)

        self.yt_proc = None
        self.ffmpeg_proc = None
        return "⏹️ Stopped."

    def pause(self):
        self.paused = True
        return "⏸️ Paused."

    def resume(self):
        if self.paused:
            self.paused = False
            return "▶️ Resumed."
        return "Nothing is paused right now."
