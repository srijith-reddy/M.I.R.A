# amma/agents/music_agent.py

import urllib.parse
import webbrowser

class MusicAgent:
    """
    Opens YouTube Music (or YouTube) for the requested track/genre.
    - No API keys required
    - Works on all platforms (opens default browser)
    """

    def play(self, query: str) -> str:
        """
        Open a YouTube Music search for the given query.
        Examples:
          "play lo-fi beats" -> opens YT Music search
          "Amma play Arijit Singh" -> same (prefix stripped)
        """
        q = (query or "").strip()
        ql = q.lower()

        # strip common voice prefixes
        if ql.startswith("amma "):
            q = q[5:].strip()
            ql = q.lower()
        if ql.startswith("play "):
            q = q[5:].strip()

        if not q:
            return "What should I play?"

        url = "https://music.youtube.com/search?q=" + urllib.parse.quote(q)
        webbrowser.open(url)
        print(f"Opening YouTube Music for “{q}”.")
        return None

    def play_on_youtube(self, query: str) -> str:
        """
        Open a regular YouTube search (fallback/alternative).
        """
        q = (query or "").strip()
        if not q:
            print("What should I play on YouTube?")
            return None
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(q)
        webbrowser.open(url)
        print(f"Searching YouTube for “{q}”.")
        return None
