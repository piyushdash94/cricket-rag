"""Embedding and reranking models behind small protocols. CPU only: ONNX through fastembed, no torch."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    name: str
    dim: int

    def passages(self, texts: list[str]) -> np.ndarray: ...
    def query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    name: str

    def scores(self, query: str, docs: list[str]) -> list[float]: ...


def _local_kwargs(model: str, model_dir: Path | None) -> dict:
    """Load from data/models/<org>__<name> when present (offline, survives temp cleanup)."""
    if model_dir and (path := model_dir / model.replace("/", "__")).is_dir():
        return {"specific_model_path": str(path), "local_files_only": True}
    return {"cache_dir": str(model_dir)} if model_dir else {}


class FastEmbedder:
    """bge-small-en-v1.5 (384-d, MIT). fastembed applies bge's query instruction in query_embed."""

    def __init__(self, model: str, threads: int | None = None, model_dir: Path | None = None):
        from fastembed import TextEmbedding

        self.name = model
        self._m = TextEmbedding(model, threads=threads, **_local_kwargs(model, model_dir))
        self.dim = int(self.passages(["probe"]).shape[1])

    def passages(self, texts: list[str]) -> np.ndarray:
        return np.asarray(list(self._m.passage_embed(texts, batch_size=64)), dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        return np.asarray(next(iter(self._m.query_embed([text]))), dtype=np.float32)


class FastReranker:
    """ms-marco-MiniLM-L-6-v2 cross-encoder (Apache-2.0), ONNX. Kept only if the eval says it helps."""

    def __init__(self, model: str, threads: int | None = None, model_dir: Path | None = None):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = model
        self._m = TextCrossEncoder(model, threads=threads, **_local_kwargs(model, model_dir))

    def scores(self, query: str, docs: list[str]) -> list[float]:
        return [float(s) for s in self._m.rerank(query, docs, batch_size=32)] if docs else []


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder for offline tests. Not for real use."""

    def __init__(self, dim: int = 64):
        self.name, self.dim = f"hash-{dim}", dim

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for w in re.findall(r"[a-z0-9]+", text.casefold()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._one(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def query(self, text: str) -> np.ndarray:
        return self._one(text)


class OverlapReranker:
    """Word-overlap reranker for offline tests."""

    name = "overlap"

    def scores(self, query: str, docs: list[str]) -> list[float]:
        q = set(re.findall(r"[a-z0-9]+", query.casefold()))
        return [float(len(q & set(re.findall(r"[a-z0-9]+", d.casefold())))) for d in docs]


@lru_cache(maxsize=2)
def default_embedder() -> FastEmbedder:
    from cricrag.config import settings

    return FastEmbedder(settings().embed_model, model_dir=settings().model_dir)


@lru_cache(maxsize=2)
def default_reranker() -> FastReranker:
    from cricrag.config import settings

    return FastReranker(settings().rerank_model, model_dir=settings().model_dir)
