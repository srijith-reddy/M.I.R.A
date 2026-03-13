# amma/memory/unified_memory.py
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import deque
import numpy as np


class UnifiedMemory:
    """
    Hybrid conversational memory:
      - Short-term buffer (last N turns).
      - SQLite-backed long-term store.
      - FAISS (via semantic_mem) for semantic recall and re-ranking.
    """

    def __init__(self, sql_mem, semantic_mem, short_window: int = 5):
        self.sql = sql_mem            # expects add/query/delete/clear
        self.semantic = semantic_mem
        self._short = deque(maxlen=short_window)  # {"role","text"}

    # ----------------------- Write ---------------------------------
    def add(
        self,
        role: str,
        text: str,
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Add memory everywhere (short-term, SQLite, FAISS)."""
        if not text:
            return -1

        self._short.append({"role": role, "text": text})

        mem_id = self.sql.add(role, text, tags, meta)
        if mem_id > 0:
            meta_with_id = {"id": mem_id, "role": role, "tags": tags, **(meta or {})}
            self.semantic.add(text, meta_with_id)
        return mem_id

    def save_turn(self, user_text: str, assistant_text: str) -> None:
        """Store one dialogue turn (user + assistant)."""
        self.add("user", user_text, tags=["chat"])
        self.add("assistant", assistant_text, tags=["chat"])

    def add_identity(self, text: str, meta: Optional[Dict[str, Any]] = None) -> int:
        """Store permanent facts (never pruned)."""
        return self.add("user", text, tags=["identity"], meta=meta)

    # ----------------------- Read / Recall --------------------------
    def search(
        self,
        query: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        tag: Optional[str] = None,
        limit: int = 50,
        k: int = 5,
        order: str = "DESC",
    ) -> List[Dict[str, Any]]:
        """Hybrid retrieval: filter via SQLite, rank via FAISS similarity."""
        candidates = self.sql.query(
            keyword=query, since=since, until=until, tag=tag, limit=limit, order=order
        )
        if not candidates:
            return []

        texts = [c["text"] for c in candidates]
        metas = [c for c in candidates]

        qv = np.asarray(self.semantic._embed([query]), dtype=float)  # (1, d)
        M = np.asarray(self.semantic._embed(texts), dtype=float)     # (n, d)

        sims = (M @ qv.T).ravel()
        idxs = np.argsort(sims)[::-1][:k]

        results: List[Dict[str, Any]] = []
        for rank, i in enumerate(idxs, start=1):
            results.append({
                "rank": rank,
                "score": float(sims[i]),
                "text": texts[i],
                "meta": metas[i],
            })
        return results

    def build_context_messages(
        self,
        query: str,
        k: int = 2,
        include_short: bool = True,
    ) -> List[Dict[str, str]]:
        """Format recall_for_context into OpenAI-style messages."""
        ctx: List[Dict[str, Any]] = []
        if include_short:
            ctx.extend(list(self._short))

        hits = self.search(query=query, k=k)
        for h in hits:
            role = (h.get("meta") or {}).get("role", "memory")
            ctx.append({"role": role, "text": h["text"]})

        return [{"role": c["role"], "content": c["text"]} for c in ctx if c.get("text")]

    # ----------------------- Maintenance ----------------------------
    def clear_short(self) -> None:
        self._short.clear()

    def clear_all(self) -> None:
        self._short.clear()
        self.sql.clear()
        self.semantic.clear()

    def prune_chats(self, max_age_days: int = 30, keep_last: int = 1000) -> None:
        cutoff = datetime.now() - timedelta(days=max_age_days)
        old = self.sql.query(until=cutoff, tag="chat", limit=10000, order="ASC")
        old_ids = [c["id"] for c in old[:-keep_last]] if len(old) > keep_last else []
        for oid in old_ids:
            self.sql.delete(oid)

    # ----------------------- Debug helpers --------------------------
    def short_history(self) -> List[Dict[str, str]]:
        return list(self._short)
