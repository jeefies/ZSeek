"""评分：知寻（ZhiSeek）核心指标（依据作战手册「指标体系：低估指数的完整定义」实现）。

主张级：  v(主张) = rarity × [1 − (1−E)(1−C)]      # 稀有度 × 至少一根支柱（证据或印证，概率 OR）
          rarity(c) = ln(总主张数 / 簇内回答数)       # 独有满分，共识趋零，灌水自稀释
          E = 是否含可核验锚点（0/1，LLM 拆主张时输出）
          C = 1 − e^(−k/3)，k = 簇内除自己外的独立答主数

回答级：  V = Σ(rarity·v) / Σ(rarity) × (0.4 + 0.6F)  # 稀有度加权平均 × 新鲜度乘性惩罚
          E(回答) = (有锚点主张数+1)/(总主张数+2)      # 贝塔平滑（展示用）
          F = 1 − 0.7×过期比例 − 0.3×未核验比例        # 仅统计时效敏感主张

曝光：    L∞ = L / (1 − e^(−t/τ))，τ 经验常量，修正封顶 5×
          发布 <7 天进「观察池」，不做低估判定

低估：    U = percentile(V) − percentile(L∞)          # 议题内百分位差，范围 (−1,1)
垄断：    M = Top3 曝光占比 − Top3 稀有度加权主张占比

呈现：    🔦 沧海遗珠 U>0.6；🌱 潜力新声 U>0.3 且<90 天；⏰ 内容待更新 F<0.4
"""
import math
import time
from typing import Any

from . import config


def _percentile(value: float, values: list[float]) -> float:
    """百分位：(严格小于的个数 + 0.5×相等的个数) / n，范围 (0,1)。"""
    n = len(values)
    if n == 1:
        return 1.0
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + 0.5 * equal) / n


def _cluster_answer_counts(claims: list[dict[str, Any]], labels: list[int]) -> dict[int, set[str]]:
    """簇号 → 簇内不同回答集合。噪声点（-1）每条主张独立成簇（簇大小=1）。"""
    clusters: dict[int, set[str]] = {}
    for idx, (claim, label) in enumerate(zip(claims, labels)):
        key = label if label != -1 else -(idx + 2)
        clusters.setdefault(key, set()).add(claim["content_id"])
    return clusters


def score_answers(
    answers: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    labels: list[int],
    verified: dict[str, str] | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """对每个回答计算完整指标。claims 元素含 content_id/type/verifiable。"""
    now = now or time.time()
    total_claims = len(claims)
    clusters = _cluster_answer_counts(claims, labels)

    # ---- 主张级 ----
    claim_rows = []  # (content_id, rarity, v, fresh_status)
    for idx, (claim, label) in enumerate(zip(claims, labels)):
        key = label if label != -1 else -(idx + 2)
        cluster_size = len(clusters[key])  # 不同回答数
        k = cluster_size - 1                 # 除自己外的独立答主数
        rarity = math.log(total_claims / cluster_size)  # 独有=log(总数)，共识→0
        C = 1.0 - math.exp(-k / 3.0)
        # 支柱按类型切换（手册「类型—支柱对照」）：事实类靠可查证 E，经历类靠细节密度 D
        if claim.get("type") == "personal":
            pillar = config.D_DETAIL if claim.get("detail") else config.D_VAGUE
        else:
            pillar = 1.0 if claim["verifiable"] else 0.0
        v = rarity * (1.0 - (1.0 - pillar) * (1.0 - C))
        status = None
        if claim["type"] == "sensitive":
            # 三级过期判定结果：expired（锚点失效/内部冲突实锤）/ suspected（未核验）
            status = (verified or {}).get(str(idx)) or "suspected"
        claim_rows.append({
            "content_id": claim["content_id"],
            "rarity": rarity,
            "v": v,
            "verifiable": bool(claim["verifiable"]),
            "is_personal": claim.get("type") == "personal",
            "detail": bool(claim.get("detail")),
            "sensitive_status": status,  # None(常青) / current / outdated / unverified
        })

    # ---- 回答级 ----
    by_answer: dict[str, list[dict[str, Any]]] = {}
    for row in claim_rows:
        by_answer.setdefault(row["content_id"], []).append(row)

    scored = []
    for a in answers:
        cid = a["content_id"]
        rows = by_answer.get(cid, [])
        total = len(rows)
        if total == 0:
            continue
        sum_rarity = sum(r["rarity"] for r in rows)
        if sum_rarity <= 0:
            sum_rarity = 1e-9
        base = sum(r["rarity"] * r["v"] for r in rows) / sum_rarity

        # 新鲜度：仅统计时效敏感主张
        sensitive = [r for r in rows if r["sensitive_status"]]
        if sensitive:
            expired = sum(1 for r in sensitive if r["sensitive_status"] == "expired")
            suspected = sum(1 for r in sensitive if r["sensitive_status"] == "suspected")
            # 作战手册：过期比例只计实锤档（0.7 罚），未核验比例计疑似档（0.3 罚）
            F = 1.0 - 0.7 * (expired / len(sensitive)) - 0.3 * (suspected / len(sensitive))
        else:
            F = 1.0

        V = base * (0.4 + 0.6 * F)

        # E(回答)：贝塔平滑（展示用）
        anchored = sum(1 for r in rows if r["verifiable"])
        E_answer = (anchored + 1) / (total + 2)

        # ---- 曝光时间修正 ----
        votes = a.get("votes", 0)
        votes_unknown = bool(a.get("votes_unknown"))  # 枚举通道无赞同数
        edit_time = a.get("edit_time", 0) or 0
        age_days = (now - edit_time) / 86400.0 if edit_time > 0 else None
        correction = 1.0
        in_observation = False
        if votes_unknown:
            # 无曝光数据：参与聚类/主张分析，但不进低估判定（诚实降级）
            in_observation = True
        elif age_days is not None and age_days >= 0:
            if age_days < config.OBSERVATION_DAYS:
                in_observation = True
            else:
                correction = min(
                    config.EXPO_CAP,
                    1.0 / (1.0 - math.exp(-age_days / config.EXPO_TAU_DAYS)),
                )
        L_inf = votes * correction

        scored.append({
            **a,
            "claim_count": total,
            "unique_claims": sum(1 for r in rows if r["rarity"] >= math.log(max(total_claims, 2)) - 1e-6),
            "anchored_claims": anchored,
            "detailed_claims": sum(1 for r in rows if r["is_personal"] and r["detail"]),
            "sensitive_claims": len(sensitive),
            "sensitive_unverified_claims": sum(1 for r in sensitive if r["sensitive_status"] == "suspected"),
            "rarity_weighted_v": round(base, 4),
            "E_answer": round(E_answer, 4),
            "freshness": round(F, 4),
            "info_score": round(V, 4),
            "votes": votes,
            "age_days": round(age_days, 1) if age_days is not None else None,
            "expo_correction": round(correction, 2),
            "L_inf": round(L_inf, 1),
            "in_observation_pool": in_observation,
        })

    # ---- 低估指数：议题内百分位差 ----
    # 仅在有曝光数据（搜索通道）的回答之间排序：枚举样本 L∞=0，参与会稀释百分位刻度
    rankable = [s for s in scored if not s.get("votes_unknown")]
    Vs = [s["info_score"] for s in rankable]
    Ls = [s["L_inf"] for s in rankable]
    for s in scored:
        if s["in_observation_pool"] or s["age_days"] is None or s.get("votes_unknown"):
            s["underestimate_index"] = None  # 观察池/无曝光数据不做低估判定
        else:
            s["underestimate_index"] = round(_percentile(s["info_score"], Vs) - _percentile(s["L_inf"], Ls), 4)
        s["badges"] = _badges(s)

    return scored


def _badges(s: dict[str, Any]) -> list[str]:
    badges = []
    U = s.get("underestimate_index")
    if U is not None:
        if U > config.BADGE_UNDISCOVERED:  # 🔦 沧海遗珠
            badges.append("沧海遗珠")
        elif U > config.BADGE_RISING and (s.get("age_days") or 9999) < 90:  # 🌱 潜力新声
            badges.append("潜力新声")
    if s.get("freshness", 1.0) < config.BADGE_STALE_F:  # ⏰ 内容待更新
        badges.append("内容待更新")
    return badges


def monopoly_metrics(scored: list[dict[str, Any]], total_answers_fetched: int | None = None) -> dict[str, Any]:
    """M = Top3 曝光占比 − Top3 稀有度加权主张占比。"""
    if not scored:
        return {}
    by_votes = sorted(scored, key=lambda x: -x["votes"])
    top = by_votes[: config.MONOPOLY_TOP_K]
    total_votes = sum(x["votes"] for x in scored) or 1
    exposure_share = sum(x["votes"] for x in top) / total_votes
    # 稀有度加权主张占比
    sum_rarity_all = sum(x["rarity_weighted_v"] * x["claim_count"] for x in scored) or 1e-9
    sum_rarity_top = sum(x["rarity_weighted_v"] * x["claim_count"] for x in top)
    info_share = sum_rarity_top / sum_rarity_all
    return {
        "top_k": config.MONOPOLY_TOP_K,
        "exposure_share_top_k": round(exposure_share, 4),
        "info_increment_share_top_k": round(info_share, 4),
        "monopoly_gap": round(exposure_share - info_share, 4),
        "total_sample_answers": len(scored),
        "question_total_answers": total_answers_fetched,
    }
