"""本地 embedding：BAAI/bge-small-zh-v1.5（sentence-transformers，离线可用）。

embedding 顺序与 claims JSON 列表顺序一一对应，缓存为 .npy。
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import config

_NPY_NAME = "3_embeddings.npy"


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(config.EMBED_MODEL, device="cpu")


def embeddings_path(cache_dir: Path) -> Path:
    return cache_dir / _NPY_NAME


def embed_claims(claims: list[dict[str, Any]], cache_dir: Path) -> np.ndarray:
    """主张列表 → L2 归一化向量 (n, 512)。有缓存直接读。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = embeddings_path(cache_dir)
    if path.exists():
        arr = np.load(path)
        if arr.shape[0] == len(claims):
            return arr
    texts = [c["claim"] for c in claims]
    model = _model()
    arr = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    arr = np.asarray(arr, dtype=np.float32)
    np.save(path, arr)
    return arr
