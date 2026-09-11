"""生成物：簇命名、挖掘理由、低估报告（知乎直答 thinking 模型，批量调用）。"""
from typing import Any

import numpy as np

from . import config
from .claims import ZhidaClient, _extract_json

NAME_PROMPT = """下面是同一议题下多个观点簇的代表主张。为每个簇起一个一句话标签（≤15字，指出观点核心），并写一句中立概述。

输出严格 JSON：{{"names":[{{"cluster":<簇号>,"label":"...","summary":"..."}}]}}

簇与代表主张：
{clusters_block}"""

REASON_PROMPT = """根据以下结构化特征，为每个回答写一句透明的「挖掘理由」（说明它为何被低估/高估，只引用给定特征，不做质量判断，不添加特征之外的信息）。

输出严格 JSON：{{"reasons":[{{"id":"<回答id>","reason":"..."}}]}}

回答特征：
{answers_block}"""


def _cluster_representatives(
    claims: list[dict[str, Any]],
    labels: list[int],
    embeddings: np.ndarray,
) -> dict[int, str]:
    """每簇取离质心最近的主张文本。噪声簇跳过。"""
    groups: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        if label != -1:
            groups.setdefault(label, []).append(idx)
    reps: dict[int, str] = {}
    for label, idxs in groups.items():
        center = embeddings[idxs].mean(axis=0)
        best = max(idxs, key=lambda i: float(np.dot(embeddings[i], center)))
        reps[label] = claims[best]["claim"]
    return reps


def name_clusters(
    zhida: ZhidaClient,
    claims: list[dict[str, Any]],
    labels: list[int],
    embeddings: np.ndarray,
) -> dict[int, dict[str, str]]:
    """簇命名 + 概述。返回 {cluster: {label, summary}}。"""
    reps = _cluster_representatives(claims, labels, embeddings)
    if not reps:
        return {}
    clusters_block = "\n".join(f"簇 {k}：{v}" for k, v in sorted(reps.items()))
    try:
        parsed = _extract_json(zhida.chat(config.ZHIDA_THINKING, NAME_PROMPT.format(clusters_block=clusters_block)))
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 簇命名失败，使用占位标签: {e}")
        return {k: {"label": f"观点簇 {k}", "summary": v} for k, v in reps.items()}
    out = {}
    for item in parsed.get("names", []):
        try:
            k = int(item["cluster"])
        except (KeyError, ValueError):
            continue
        out[k] = {"label": str(item.get("label", f"观点簇 {k}")), "summary": str(item.get("summary", ""))}
    # 缺失簇兜底
    for k in reps:
        out.setdefault(k, {"label": f"观点簇 {k}", "summary": reps[k]})
    return out


def build_reason_features(scored: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """取低估榜候选（按 U 排序前 top_n，观察池除外），组装特征供 LLM 写理由。"""
    candidates = sorted(
        (s for s in scored if s["underestimate_index"] is not None),
        key=lambda x: -x["underestimate_index"],
    )[:top_n]
    features = []
    for a in candidates:
        features.append({
            "id": a["content_id"],
            "features": (
                f"赞同 {a['votes']}，评论 {a['comments']}，发布约 {a['age_days']:.0f} 天；"
                f"主张 {a['claim_count']} 条，其中独占观点 {a['unique_claims']} 条，"
                f"含可核验锚点 {a['anchored_claims']} 条，"
                f"含具体细节的个人经历 {a.get('detailed_claims', 0)} 条，"
                f"时效敏感未核验 {a['sensitive_unverified_claims']} 条；"
                f"信息价值分 V={a['info_score']:.2f}，新鲜度 F={a['freshness']:.2f}，"
                f"低估指数 U={a['underestimate_index']:+.3f}。"
                f"作者：{a['author']}。"
            ),
        })
    return features


def write_reasons(zhida: ZhidaClient, features: list[dict[str, Any]]) -> dict[str, str]:
    """批量生成挖掘理由。返回 {content_id: reason}。失败时退化为模板。"""
    def fallback(f: dict[str, Any]) -> str:
        return f"此回答{ f['features'] }"

    if not features:
        return {}
    answers_block = "\n".join(f"[{f['id']}] {f['features']}" for f in features)
    try:
        parsed = _extract_json(zhida.chat(config.ZHIDA_THINKING, REASON_PROMPT.format(answers_block=answers_block)))
        reasons = {str(r.get("id")): str(r.get("reason", "")) for r in parsed.get("reasons", [])}
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 挖掘理由生成失败，使用模板: {e}")
        reasons = {}
    return {f["id"]: reasons.get(f["id"]) or fallback(f) for f in features}
