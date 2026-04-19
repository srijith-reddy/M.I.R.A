from __future__ import annotations

import re
from typing import Any

from mira.obs.logging import log_event
from mira.web.chunking import Chunk

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


# Process-wide lazy embedder — fastembed caches model weights under
# ~/.cache/fastembed, so first call downloads ~80MB then becomes fast.
# We intentionally share with memory.py's embedder when the model matches;
# they both use BAAI/bge-small-en-v1.5 by default.
_embedder: Any | None = None
_embedder_failed = False


def _get_embedder():  # type: ignore[no-untyped-def]
    global _embedder, _embedder_failed
    if _embedder is not None:
        return _embedder
    if _embedder_failed:
        return None
    try:
        from fastembed import TextEmbedding
        from mira.config.settings import get_settings

        model_name = get_settings().local_embedding_model
        _embedder = TextEmbedding(model_name=model_name)
        log_event("rerank.embedder_ready", model=model_name)
        return _embedder
    except Exception as exc:
        _embedder_failed = True
        log_event("rerank.embedder_unavailable", error=repr(exc))
        return None


def _rrf_merge(*rankings: list[int], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: for each item's position p in each ranking,
    add 1/(k+p). Works across rankings of different score scales (BM25's
    raw score is not comparable with cosine sim, so we can't just weight-sum).
    Returns (idx, fused_score) sorted high→low."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for pos, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + pos)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _bm25_ranking(query: str, chunks: list[Chunk]) -> list[int]:
    from rank_bm25 import BM25Okapi

    corpus = [_tokenize(c.text) for c in chunks]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tokenize(query))
    # Stable sort: chunks with the same score keep their original order (which
    # roughly follows source rank × chunk position — a sensible tiebreaker).
    return sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)


def _embed_ranking(query: str, chunks: list[Chunk]) -> list[int] | None:
    emb = _get_embedder()
    if emb is None:
        return None
    try:
        import numpy as np

        # fastembed returns a generator of np.ndarray rows (already L2-normed
        # for bge-small). Materialize into a matrix so we can cosine-score in
        # one matmul rather than in a Python loop.
        texts = [c.text for c in chunks]
        query_vec = next(iter(emb.embed([query])))
        chunk_mat = np.vstack(list(emb.embed(texts)))
        sims = chunk_mat @ query_vec
        return sorted(range(len(chunks)), key=lambda i: float(sims[i]), reverse=True)
    except Exception as exc:
        log_event("rerank.embed_error", error=repr(exc))
        return None


def hybrid_rerank(
    query: str,
    chunks: list[Chunk],
    *,
    top_k: int = 5,
) -> list[Chunk]:
    """Return the top-k chunks under a BM25 ⊕ dense-embedding RRF merge.
    Falls back to BM25-only when fastembed isn't available. Both rankers
    operate on the same candidate pool — there's no pre-filter, so nothing
    is lost to a tight first-pass cutoff."""
    if not chunks:
        return []
    if len(chunks) <= top_k:
        return list(chunks)

    rankings: list[list[int]] = [_bm25_ranking(query, chunks)]
    dense = _embed_ranking(query, chunks)
    if dense is not None:
        rankings.append(dense)
    fused = _rrf_merge(*rankings)
    picks = [chunks[idx] for idx, _score in fused[:top_k]]
    log_event(
        "rerank.done",
        total=len(chunks), kept=len(picks),
        dense=dense is not None,
    )
    return picks
