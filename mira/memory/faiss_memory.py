"""
Semantic memory with embeddings + FAISS (if available).
Falls back to a NumPy brute-force index when FAISS isn't installed.

Usage:
    mem = SemanticMemory()  # loads index if present
    mem.add("Sriju likes filter coffee", {"type":"fact"})
    hits = mem.search("what coffee do I like?", k=3)
"""

import os
import json
import math
import numpy as np
from typing import List, Dict, Any, Optional

from openai import OpenAI
from mira.core.config import cfg
from mira.utils import logger

# Try FAISS, fall back to NumPy
try:
    import faiss  # type: ignore
    _FAISS_OK = True
except Exception:
    faiss = None
    _FAISS_OK = False

DATA_DIR = "./data"
INDEX_PATH = os.path.join(DATA_DIR, "vectors.faiss")
META_PATH  = os.path.join(DATA_DIR, "vectors.jsonl")
NPZ_PATH   = os.path.join(DATA_DIR, "vectors_fallback.npz")

EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

class SemanticMemory:
    def __init__(self, index_path: str = INDEX_PATH, meta_path: str = META_PATH):
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        self.client = OpenAI(api_key=cfg.OPENAI_API_KEY)
        self.index_path = index_path
        self.meta_path = meta_path

        self.texts: List[str] = []
        self.metas: List[Dict[str, Any]] = []
        self.dim: Optional[int] = None

        if _FAISS_OK:
            self.index = None  # type: ignore
        else:
            self.index = None
            self._vecs = None  # numpy fallback

        self._load()

    # ---------- persistence ----------

    def _load(self):
        # load metadata
        if os.path.exists(self.meta_path):
            with open(self.meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        self.texts.append(obj["text"])
                        self.metas.append(obj.get("meta", {}))
                    except Exception:
                        continue

        # load vectors
        if _FAISS_OK and os.path.exists(self.index_path):
            try:
                self.index = faiss.read_index(self.index_path)
                self.dim = self.index.d
                logger.log_event("SemanticMemory.load", f"FAISS index loaded d={self.dim} n={self.index.ntotal}")
            except Exception as e:
                logger.log_error(e, context="SemanticMemory.load_faiss")
                self.index = None

        if not _FAISS_OK and os.path.exists(NPZ_PATH):
            try:
                data = np.load(NPZ_PATH)
                self._vecs = data["vecs"]
                self.dim = self._vecs.shape[1]
                logger.log_event("SemanticMemory.load", f"NumPy index loaded d={self.dim} n={self._vecs.shape[0]}")
            except Exception as e:
                logger.log_error(e, context="SemanticMemory.load_numpy")
                self._vecs = None

    def _save_meta(self, text: str, meta: Dict[str, Any]):
        with open(self.meta_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "meta": meta}, ensure_ascii=False) + "\n")

    def _save_index(self):
        if _FAISS_OK and self.index is not None:
            faiss.write_index(self.index, self.index_path)
        elif not _FAISS_OK and self._vecs is not None:
            np.savez_compressed(NPZ_PATH, vecs=self._vecs)

    # ---------- embeddings ----------

    def _embed(self, texts: List[str]) -> np.ndarray:
        """
        Returns (n, d) float32 matrix. Normalized for cosine similarity.
        """
        resp = self.client.embeddings.create(model=EMBED_MODEL, input=texts)
        vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
        # normalize for cosine = dot product
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-8
        vecs = vecs / norms
        if self.dim is None:
            self.dim = vecs.shape[1]
        return vecs

    # ---------- public API ----------

    def add(self, text: str, meta: Optional[Dict[str, Any]] = None):
        """
        Add a single text + metadata to the index.
        """
        try:
            v = self._embed([text])
            self.texts.append(text)
            self.metas.append(meta or {})

            if _FAISS_OK:
                if self.index is None:
                    # Cosine via inner product on normalized vectors
                    self.index = faiss.IndexFlatIP(int(self.dim))
                self.index.add(v)
            else:
                if self._vecs is None:
                    self._vecs = v
                else:
                    self._vecs = np.vstack([self._vecs, v])

            self._save_meta(text, meta or {})
            self._save_index()
            logger.log_event("SemanticMemory.add", f"n={len(self.texts)}")
        except Exception as e:
            logger.log_error(e, context="SemanticMemory.add")

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """
        Returns top-k matches with scores (cosine).
        """
        try:
            if (self.index is None and self._vecs is None) or len(self.texts) == 0:
                return []

            qv = self._embed([query])  # (1, d)

            if _FAISS_OK and self.index is not None:
                D, I = self.index.search(qv, k)
                scores = D[0].tolist()
                idxs = I[0].tolist()
            else:
                # brute-force cosine (dot with normalized vectors)
                M = self._vecs  # (n, d)
                sims = (M @ qv.T).reshape(-1)  # (n,)
                idxs = np.argsort(-sims)[:k].tolist()
                scores = sims[idxs].tolist()

            results: List[Dict[str, Any]] = []
            for rank, (i, s) in enumerate(zip(idxs, scores)):
                if i < 0 or i >= len(self.texts):
                    continue
                results.append({
                    "rank": rank + 1,
                    "score": float(s),
                    "text": self.texts[i],
                    "meta": self.metas[i],
                })
            return results
        except Exception as e:
            logger.log_error(e, context="SemanticMemory.search")
            return []
