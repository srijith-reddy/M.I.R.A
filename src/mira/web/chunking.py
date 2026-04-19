from __future__ import annotations

import re
from dataclasses import dataclass

# Rough token budgets. A dedicated tokenizer would be more accurate but
# every chunk is re-scored downstream anyway — the exact boundary doesn't
# change outcomes, it just controls how much context each chunk carries.
# Word-count approximation: 1 token ≈ 0.75 words for English prose.
_WORDS_PER_CHUNK = 375          # ~500 tokens
_WORDS_OVERLAP = 50             # ~65 tokens of overlap
_MIN_CHUNK_CHARS = 120          # drop chunks smaller than this — usually nav/fragments


@dataclass(frozen=True)
class Chunk:
    text: str
    url: str
    title: str
    source_rank: int            # rank of source document in the search result list
    chunk_idx: int              # position within the source document

    def short_preview(self, n: int = 180) -> str:
        t = self.text.replace("\n", " ").strip()
        return t if len(t) <= n else t[:n] + "..."


_PARA_SPLIT = re.compile(r"\n{2,}|\r\n{2,}")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def _paragraphs(text: str) -> list[str]:
    """Split on blank lines first (keeps authorial paragraph boundaries),
    then flatten oversized paragraphs into sentences so no single unit is
    larger than a chunk budget."""
    paras = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    out: list[str] = []
    for p in paras:
        # Cheap length gate — only bother sentence-splitting if the paragraph
        # alone would blow past the chunk budget.
        if len(p.split()) <= _WORDS_PER_CHUNK:
            out.append(p)
            continue
        for s in _SENT_SPLIT.split(p):
            s = s.strip()
            if s:
                out.append(s)
    return out


def chunk_document(
    text: str,
    *,
    url: str,
    title: str,
    source_rank: int,
    words_per_chunk: int = _WORDS_PER_CHUNK,
    words_overlap: int = _WORDS_OVERLAP,
) -> list[Chunk]:
    """Greedy pack paragraphs/sentences into ~word-budget chunks with a
    small tail overlap so answers that span a paragraph boundary still
    land in at least one chunk's window."""
    if not text:
        return []
    units = _paragraphs(text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_words = 0
    idx = 0
    for u in units:
        uw = len(u.split())
        # Emit when adding this unit would exceed the budget AND we already
        # have something buffered; otherwise at least accept the (oversize)
        # unit alone so we don't drop content.
        if buf and buf_words + uw > words_per_chunk:
            joined = "\n\n".join(buf).strip()
            if len(joined) >= _MIN_CHUNK_CHARS:
                chunks.append(Chunk(
                    text=joined, url=url, title=title,
                    source_rank=source_rank, chunk_idx=idx,
                ))
                idx += 1
            # Seed next buffer with a tail slice of the prior chunk for overlap.
            if words_overlap > 0 and joined:
                tail = " ".join(joined.split()[-words_overlap:])
                buf = [tail]
                buf_words = len(tail.split())
            else:
                buf = []
                buf_words = 0
        buf.append(u)
        buf_words += uw
    if buf:
        joined = "\n\n".join(buf).strip()
        if len(joined) >= _MIN_CHUNK_CHARS:
            chunks.append(Chunk(
                text=joined, url=url, title=title,
                source_rank=source_rank, chunk_idx=idx,
            ))
    return chunks
