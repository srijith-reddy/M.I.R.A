import os
import datetime
import json

LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)

def _timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_event(event: str, details: str = ""):
    """
    Logs system events (startup, wakeword detected, errors, etc.)
    """
    entry = f"[{_timestamp()}] {event}: {details}"
    print(entry)  # still print to console
    with open(os.path.join(LOG_DIR, "events.log"), "a") as f:
        f.write(entry + "\n")

def log_conversation(user_text: str, amma_reply: str):
    """
    Logs conversations into JSON + plain text for easy reading.
    """
    # Plain text
    txt_entry = f"[{_timestamp()}]\n🧑 You: {user_text}\n👩 Amma: {amma_reply}\n\n"
    with open(os.path.join(LOG_DIR, "conversation.txt"), "a") as f:
        f.write(txt_entry)

    # JSON
    json_entry = {
        "timestamp": _timestamp(),
        "user": user_text,
        "amma": amma_reply
    }
    with open(os.path.join(LOG_DIR, "conversation.json"), "a") as f:
        f.write(json.dumps(json_entry) + "\n")

import os, json

def load_conversation_log(path="logs/conversation.json"):
    """Load conversation history from a JSON array into a list of dicts."""
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            conversations = json.load(f)   # load the whole array
            if not isinstance(conversations, list):
                print("⚠️ Expected a JSON array, got:", type(conversations))
                return []
            return conversations
    except json.JSONDecodeError as e:
        print("❌ Failed to parse JSON:", e)
        return []

def log_error(e: Exception, context: str = ""):
    """
    Logs errors consistently with timestamp.
    """
    entry = f"[{_timestamp()}] ERROR in {context}: {str(e)}"
    print(entry)  # print to console
    with open(os.path.join(LOG_DIR, "errors.log"), "a") as f:
        f.write(entry + "\n")
