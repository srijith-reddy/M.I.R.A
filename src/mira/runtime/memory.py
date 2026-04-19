from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from mira.config.settings import get_settings
from mira.obs.logging import log_event
from mira.runtime.store import connect, vec_available
from mira.runtime.tracing import span


_OPENAI_EMBED_DIM = 1536
_OPENAI_EMBED_MODEL = "text-embedding-3-small"

# Local embedder specs. Dim must match the model — bge-small-en-v1.5 outputs
# 384-d vectors. If you swap the local model, update _LOCAL_EMBED_DIM too.
_LOCAL_EMBED_DIM = 384


# Transcript normalization for dedup. Same spirit as reply_cache._normalize
# but applied to the STORED transcript so we can do an indexed lookup.
_DEDUP_FILLER = re.compile(
    r"^(um+|uh+|hmm+|hey mira|okay mira|mira|please|so|like|well|just)\b[,\s]*",
    re.IGNORECASE,
)
_DEDUP_PUNCT = re.compile(r"[^\w\s]")
_DEDUP_WS = re.compile(r"\s+")


def _normalize_transcript(text: str) -> str:
    s = text.strip().lower()
    s = _DEDUP_FILLER.sub("", s)
    s = _DEDUP_PUNCT.sub(" ", s)
    s = _DEDUP_WS.sub(" ", s).strip()
    return s


@dataclass(frozen=True)
class Episode:
    id: int
    turn_id: str
    ts: float
    transcript: str
    reply: str
    status: str
    via: str
    score: float = 0.0  # populated by recall(), else 0


class MemoryStore:
    """Durable memory backing for episodes + long-lived profile facts.

    Two tiers live here:

      * Episodes — every completed turn is captured with transcript, reply,
        and (optionally) a 1536-d OpenAI embedding. Recall is cosine top-k
        over the in-user-id slice, falling back to LIKE-based substring
        match when no embeddings are available (no OpenAI key, or the
        vector is unbuilt on an older row).

      * Profile — flat key/value store for stable facts ("user_name",
        "home_city", "preferred_coffee"). Intended to be read on every
        turn; kept separate from episodes because the access pattern and
        retention policy are different.

    Recall uses sqlite-vec's `vec_distance_cosine` in-SQL when the
    extension loads (10-50× faster than the Python loop, and the scan
    runs entirely in C). If the platform sqlite refuses to load
    extensions we transparently fall back to the numpy path below.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._openai: Any | None = None
        self._local: Any | None = None
        self._local_failed: bool = False

    # ---- Embeddings ----------------------------------------------------

    def _openai_client(self) -> Any | None:
        if self._openai is not None:
            return self._openai
        if not self._settings.openai_api_key:
            return None
        from openai import OpenAI

        self._openai = OpenAI(api_key=self._settings.openai_api_key)
        return self._openai

    def _local_embedder(self) -> Any | None:
        """Lazy fastembed init. Returns None if fastembed isn't installed or
        the model failed to load — callers then fall back to OpenAI. One-shot
        failure flag so we don't keep retrying on each turn."""
        if self._local is not None:
            return self._local
        if self._local_failed:
            return None
        try:
            from fastembed import TextEmbedding

            # fastembed caches the ONNX file under ~/.cache/fastembed on
            # first use (~80MB). Subsequent inits are millisecond-cheap.
            self._local = TextEmbedding(
                model_name=self._settings.local_embedding_model
            )
            log_event(
                "memory.local_embedder_ready",
                model=self._settings.local_embedding_model,
            )
            return self._local
        except Exception as exc:
            self._local_failed = True
            log_event(
                "memory.local_embedder_unavailable", error=repr(exc)
            )
            return None

    def _active_embed_model(self) -> str:
        """Name recorded alongside each row — so recall() can select only
        vectors from the same embedder (dim + geometry must match)."""
        if self._settings.local_embeddings and self._local_embedder() is not None:
            return self._settings.local_embedding_model
        return _OPENAI_EMBED_MODEL

    def _active_embed_dim(self) -> int:
        if self._settings.local_embeddings and self._local_embedder() is not None:
            return _LOCAL_EMBED_DIM
        return _OPENAI_EMBED_DIM

    def embed(self, text: str) -> np.ndarray | None:
        """Return a unit-norm float32 vector, or None if embedding is
        unavailable. Provider is picked per-call: local fastembed when the
        user has opted in AND fastembed loaded; else OpenAI; else None.

        Called off the hot path (bus subscriber), so a network blip here
        degrades recall quality, not user-visible correctness."""
        if not text.strip():
            return None
        trimmed = text[:8000]

        # Local path — preferred when enabled. Zero network, ~10ms on CPU.
        if self._settings.local_embeddings:
            emb = self._local_embedder()
            if emb is not None:
                try:
                    with span("memory.embed", chars=len(trimmed), provider="local"):
                        # fastembed yields a generator of numpy arrays.
                        vecs = list(emb.embed([trimmed]))
                    if vecs:
                        vec = np.asarray(vecs[0], dtype=np.float32)
                        norm = np.linalg.norm(vec)
                        if norm > 0:
                            vec = vec / norm
                        return vec
                except Exception as exc:
                    # One-shot failure → mark the embedder dead for this
                    # process and fall through to OpenAI. Next process boot
                    # will retry.
                    self._local_failed = True
                    self._local = None
                    log_event("memory.local_embed_error", error=repr(exc))

        client = self._openai_client()
        if client is None:
            return None
        try:
            with span("memory.embed", chars=len(trimmed), provider="openai"):
                resp = client.embeddings.create(
                    model=_OPENAI_EMBED_MODEL, input=trimmed
                )
            vec = np.asarray(resp.data[0].embedding, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec
        except Exception as exc:
            log_event("memory.embed_error", error=repr(exc))
            return None

    # ---- Episodes ------------------------------------------------------

    def record_episode(
        self,
        *,
        turn_id: str,
        transcript: str,
        reply: str,
        status: str,
        via: str,
        user_id: str = "local",
    ) -> int:
        """Persist one turn. Returns the new episode id, or 0 if the turn was
        discarded as a near-duplicate of a recent episode.

        Dedup: if any episode in the last `episode_dedup_hours` window shares
        the same normalized transcript (lowercase, filler + punct stripped),
        we drop the new one. This prevents "what time is it" asked 50× over
        an afternoon from drowning out the useful episodes at recall time.
        Embedding is best-effort — a None vector still stores the row so
        LIKE-based fallback recall can find it later.
        """
        norm = _normalize_transcript(transcript)
        window_s = max(0.0, float(self._settings.episode_dedup_hours)) * 3600.0
        if norm and window_s > 0:
            with connect() as conn:
                dup = conn.execute(
                    """
                    SELECT id FROM episodes
                    WHERE user_id = ?
                      AND norm_transcript = ?
                      AND ts > ?
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    (user_id, norm, time.time() - window_s),
                ).fetchone()
            if dup is not None:
                log_event(
                    "memory.episode_deduped",
                    turn_id=turn_id,
                    dup_of=int(dup["id"]),
                )
                return 0

        embedding = self.embed(f"{transcript}\n\n{reply}")
        blob = embedding.tobytes() if embedding is not None else None
        model_name = self._active_embed_model() if blob is not None else None
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO episodes
                    (turn_id, user_id, ts, transcript, reply, status, via,
                     embedding, embedding_model, norm_transcript)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id, user_id, time.time(), transcript, reply, status, via,
                    blob, model_name, norm,
                ),
            )
            episode_id = int(cur.lastrowid or 0)
        log_event(
            "memory.episode_recorded",
            episode_id=episode_id,
            turn_id=turn_id,
            has_embedding=blob is not None,
            embedding_model=model_name,
        )
        return episode_id

    def recall(
        self,
        query: str,
        *,
        k: int = 5,
        user_id: str = "local",
    ) -> list[Episode]:
        """Top-k episodes for `query`. Cosine over embeddings when available;
        falls back to substring match (ordered by recency) otherwise."""
        query = query.strip()
        if not query:
            return []

        q_vec = self.embed(query)
        active_model = self._active_embed_model()
        active_dim = self._active_embed_dim()
        # Rows written before the embedding_model column existed are NULL;
        # treat them as legacy OpenAI vectors so old data keeps working.
        legacy_openai = (active_model == _OPENAI_EMBED_MODEL)

        # Fast path: sqlite-vec loaded → cosine + sort + LIMIT all happen
        # in C. We pass the query vector as a float32 blob; `vec_f32(?)`
        # interprets the bytes. Filter to rows written by the SAME embedder —
        # cosine between OpenAI-1536 and BGE-384 is meaningless (different
        # dim + different geometry).
        if q_vec is not None and vec_available():
            q_blob = q_vec.astype(np.float32).tobytes()
            model_filter = (
                "(embedding_model = ? OR embedding_model IS NULL)"
                if legacy_openai
                else "embedding_model = ?"
            )
            sql = f"""
                SELECT id, turn_id, ts, transcript, reply, status, via,
                       vec_distance_cosine(embedding, vec_f32(?)) AS dist
                FROM episodes
                WHERE user_id = ?
                  AND embedding IS NOT NULL
                  AND length(embedding) = ?
                  AND {model_filter}
                ORDER BY dist ASC
                LIMIT ?
            """
            with connect() as conn:
                rows = conn.execute(
                    sql,
                    (q_blob, user_id, active_dim * 4, active_model, int(k)),
                ).fetchall()
            if rows:
                return [
                    _episode_from_row(r, score=1.0 - float(r["dist"]))
                    for r in rows
                ]
            # Fall through to LIKE if no embeddings existed yet.

        # Slow path: Python cosine over recent slice. Kept as a fallback
        # for platforms where sqlite-vec won't load.
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, turn_id, ts, transcript, reply, status, via,
                       embedding, embedding_model
                FROM episodes
                WHERE user_id = ?
                ORDER BY ts DESC
                LIMIT 2000
                """,
                (user_id,),
            ).fetchall()

        if not rows:
            return []

        if q_vec is not None:
            scored: list[tuple[float, Any]] = []
            for row in rows:
                blob = row["embedding"]
                if not blob:
                    continue
                # Row written by a different embedder — skip. `embedding_model`
                # may be missing on legacy rows; treat NULL as OpenAI.
                row_model = row["embedding_model"] if "embedding_model" in row.keys() else None
                if row_model is None:
                    row_model = _OPENAI_EMBED_MODEL
                if row_model != active_model:
                    continue
                vec = np.frombuffer(blob, dtype=np.float32)
                if vec.shape[0] != active_dim:
                    continue
                score = float(np.dot(q_vec, vec))  # both unit-norm → cosine
                scored.append((score, row))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                return [_episode_from_row(r, score=s) for s, r in scored[:k]]

        like = f"%{query}%"
        out: list[Episode] = []
        for row in rows:
            if like.lower().strip("%") in (row["transcript"] or "").lower() or like.lower().strip(
                "%"
            ) in (row["reply"] or "").lower():
                out.append(_episode_from_row(row))
                if len(out) >= k:
                    break
        return out

    def recent_episodes(
        self, *, limit: int = 5, user_id: str = "local"
    ) -> list[Episode]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT id, turn_id, ts, transcript, reply, status, via,
                       embedding, embedding_model
                FROM episodes
                WHERE user_id = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            ).fetchall()
        return [_episode_from_row(r) for r in rows]

    def forget_episode(self, episode_id: int, *, user_id: str = "local") -> bool:
        """Delete a single episode. Returns True if a row was removed.
        Scoped by user_id so a cross-user id can't accidentally delete
        another user's history."""
        with connect() as conn:
            cur = conn.execute(
                "DELETE FROM episodes WHERE id = ? AND user_id = ?",
                (int(episode_id), user_id),
            )
            removed = cur.rowcount > 0
        log_event(
            "memory.episode_forgotten",
            episode_id=int(episode_id),
            user_id=user_id,
            removed=removed,
        )
        return removed

    def reembed_stale(self, *, batch_size: int = 50) -> dict[str, int]:
        """Rebuild embeddings for episodes written under a different embedder.

        The active embedder is whatever `_active_embed_model()` currently
        returns (env toggles between OpenAI-1536 and BGE-384). Rows whose
        `embedding_model` doesn't match — including NULL rows, which are
        legacy pre-column writes — get their `embedding` blob replaced
        in-place so recall can include them again.

        Why: recall() filters on `embedding_model` (cross-dim cosine is
        meaningless), so swapping embedders silently buries old turns
        until they age out. This walks them back into the active slice.
        """
        active_model = self._active_embed_model()
        active_dim = self._active_embed_dim()
        counts = {"scanned": 0, "reembedded": 0, "skipped": 0, "errors": 0}
        # Cursor on id so rows that can't be re-embedded (embedder offline,
        # empty text) don't get re-selected on the next batch forever.
        last_id = 0

        while True:
            with connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, transcript, reply
                    FROM episodes
                    WHERE id > ?
                      AND (embedding_model IS NOT ? OR embedding_model IS NULL)
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (last_id, active_model, int(batch_size)),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                counts["scanned"] += 1
                last_id = int(row["id"])
                text = f"{row['transcript'] or ''}\n\n{row['reply'] or ''}".strip()
                if not text:
                    counts["skipped"] += 1
                    with connect() as conn:
                        conn.execute(
                            "UPDATE episodes SET embedding = NULL, embedding_model = ? WHERE id = ?",
                            (active_model, int(row["id"])),
                        )
                    continue
                vec = self.embed(text)
                if vec is None or vec.shape[0] != active_dim:
                    counts["errors"] += 1
                    continue
                with connect() as conn:
                    conn.execute(
                        "UPDATE episodes SET embedding = ?, embedding_model = ? WHERE id = ?",
                        (vec.astype(np.float32).tobytes(), active_model, int(row["id"])),
                    )
                counts["reembedded"] += 1
        log_event("memory.reembed_done", model=active_model, **counts)
        return counts

    def prune_old_episodes(self, *, max_age_days: int | None = None) -> int:
        """Delete episodes older than `max_age_days` (defaults to
        `settings.episode_retention_days`). Returns the row count removed.
        Safe to call at startup — O(1) with the ts index."""
        days = max_age_days if max_age_days is not None else self._settings.episode_retention_days
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400.0
        with connect() as conn:
            cur = conn.execute("DELETE FROM episodes WHERE ts < ?", (cutoff,))
            removed = int(cur.rowcount or 0)
        if removed:
            log_event("memory.pruned", removed=removed, older_than_days=days)
        return removed

    # ---- Profile -------------------------------------------------------

    def set_profile(self, key: str, value: str) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO profile (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, time.time()),
            )
        log_event("memory.profile_set", key=key)

    def get_profile(self, key: str) -> str | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT value FROM profile WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def list_profile(self) -> dict[str, str]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM profile ORDER BY key"
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}


def _episode_from_row(row: Any, *, score: float = 0.0) -> Episode:
    return Episode(
        id=int(row["id"]),
        turn_id=row["turn_id"],
        ts=float(row["ts"]),
        transcript=row["transcript"] or "",
        reply=row["reply"] or "",
        status=row["status"] or "",
        via=row["via"] or "",
        score=score,
    )


_store: MemoryStore | None = None


def memory() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store
