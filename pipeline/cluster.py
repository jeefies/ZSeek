"""聚类：embedding → UMAP 中间维度 → HDBSCAN（观点阵营）。

- 噪声点（label=-1）的每条主张视为独立簇 → 天然支持「少数派报告」
- 回答级 2D 坐标 = 其主张 2D 坐标的质心（UMAP 对主张做 2D 投影后聚合）
"""
from typing import Any

import numpy as np


def cluster_claims(
    embeddings: np.ndarray,
    umap_components: int,
    min_cluster_size: int,
    random_state: int = 42,
) -> dict[str, Any]:
    """返回 {labels, embedding_2d}：labels[i] 为主张 i 的簇号（-1=噪声）。"""
    import umap
    import hdbscan

    n = len(embeddings)
    # 样本太少时跳过 UMAP 降维，直接聚类
    if n <= max(umap_components * 2, min_cluster_size * 2):
        reduced = embeddings
    else:
        reduced = umap.UMAP(
            n_components=umap_components,
            metric="cosine",
            n_neighbors=min(15, max(2, n // 4)),
            random_state=random_state,
        ).fit_transform(embeddings)

    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean")
    labels = clusterer.fit_predict(reduced).tolist()

    # 2D 投影（可视化 + 回答级坐标）
    if n > 4:
        embedding_2d = umap.UMAP(
            n_components=2, metric="cosine", n_neighbors=min(15, max(2, n // 4)),
            random_state=random_state,
        ).fit_transform(embeddings)
    else:
        embedding_2d = reduced[:, :2] if reduced.shape[1] >= 2 else np.pad(reduced, ((0, 0), (0, 2 - reduced.shape[1])))

    return {"labels": labels, "embedding_2d": embedding_2d.astype(float).tolist()}


def answer_positions(
    claims: list[dict[str, Any]],
    labels: list[int],
    embedding_2d: list[list[float]],
) -> dict[str, dict[str, float]]:
    """回答级坐标 = 其主张 2D 质心。返回 {content_id: {x, y}}。"""
    acc: dict[str, list[list[float]]] = {}
    for claim, label, xy in zip(claims, labels, embedding_2d):
        acc.setdefault(claim["content_id"], []).append(xy)
    return {
        cid: {"x": sum(p[0] for p in pts) / len(pts), "y": sum(p[1] for p in pts) / len(pts)}
        for cid, pts in acc.items()
    }
