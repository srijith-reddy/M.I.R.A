

# MIRA Voice Assistant

````markdown
## Quick Start
```bash
git clone https://github.com/srijith-reddy/M.I.R.A.git mira-voice-assistant
cd mira-voice-assistant
micromamba create -f environment.yml -n mira-voice-assistant
micromamba activate mira-voice-assistant
playwright install
python main.py
````

## .env

```env
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o-mini
WHISPER_MODEL=whisper-1
CARTESIA_API_KEY=your_key
CARTESIA_VOICE=your_voice_id
PICOVOICE_ACCESS_KEY=your_key
WAKEWORD_MODEL=wakeword.ppn
WAKEWORD_SENSITIVITY=0.7
RATE=16000
CHANNELS=1
BLOCK_SIZE=320
VAD_AGGRESSIVENESS=2
STOP_MS=2000
TAIL_MS=100
DEVICE="MacBook Pro Microphone" # Change depending on hardware
USER_NAME=Name
```

## Notes

.env is ignored, keep screenshots/audio out of git, deps pinned in environment.yml.

```
