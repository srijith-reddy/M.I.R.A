# amma/agents/music_worker.py

from mira.utils.bgloop import BgLoop
from mira.agents.music_agent_ytautoplay import MusicAgentYTAutoplayAsync

class MusicWorker:
    def __init__(self, timeout_start: float = 10.0):
        self.bg = BgLoop()
        self._timeout_start = timeout_start
        self.agent = None

    async def _init(self):
        agent = MusicAgentYTAutoplayAsync()
        return agent

    def _ensure_agent(self):
        if self.agent is None:
            agent = self.bg.run(self._init(), timeout=self._timeout_start)
            if agent is None:
                raise RuntimeError("MusicWorker: failed to initialize MusicAgent")
            self.agent = agent

    # -------------------- Sync-friendly wrappers --------------------
    def play(self, query: str):
        self._ensure_agent()
        return self.bg.run(self.agent.play(query))

    def pause(self):
        self._ensure_agent()
        return self.agent.pause()

    def resume(self):
        self._ensure_agent()
        return self.agent.resume()

    def stop(self):
        self._ensure_agent()
        return self.agent.stop()
    def available(self):
        self._ensure_agent()
        return self.agent.available()

