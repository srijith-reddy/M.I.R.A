# mira/playback/player.py
import os, subprocess, tempfile

class SpeechPlayer:
    def __init__(self):
        self.proc = None
        self.thread = None

    def play_audio(self, audio_bytes: bytes, content_type: str = "audio/mpeg"):
        suffix = ".wav" if "wav" in content_type.lower() else ".mp3"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            temp_path = f.name
            f.write(audio_bytes)

        try:
            # 🔑 launch asynchronously and keep process handle
            self.proc = subprocess.Popen(["afplay", temp_path])

            # Wait for it to finish (blocking for sync playback)
            self.proc.wait()
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        self.proc = None
        return "▶️ Speaking finished."
    def stop(self) -> str:
        """Kill speech playback early."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc = None
            return "⏹️ Amma stopped talking."
        return "No speech is playing."

speech_player = SpeechPlayer()
