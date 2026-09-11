"""三级过期判定（依据作战手册：外部核验只用在刀刃上）。

第一级【时间锚点失效】零成本纯规则：
    主张含相对时间词（「目前/最新/今年…」）→ 「目前」锚定的是回答发布时间；
    回答超过阈值天数后，这类主张自动标「已过期」。覆盖过期主张大头。

第二级【语料库内部交叉检测】低成本复用：
    本问题的全部回答本身是个带时间戳的知识库——新回答常反驳旧回答。
    时效敏感主张与「发布时间更晚」的主张做语义比对（复用聚类 embedding），
    高相似候选交给便宜 LLM 判冲突，实锤的标「已过期」。零外部调用。

第三级【外部核验】贵、只抽查（MVP 为桩）：
    「时效敏感 + 无内部冲突 + 高稀有度」才值得查外部；查不到的诚实降级。

输出（claim 级三档）："expired"（一/二级实锤）/ "suspected"（三级未核验）/ None（常青）。
F 公式：过期比例只计 expired，未核验比例计 suspected —— 与作战手册兼容。
"""
import time
from typing import Any

import numpy as np

from .claims import judge_conflicts

# 相对时间词：出现即表示主张的时效锚定在回答发布时刻
RELATIVE_TIME_WORDS = [
    "目前", "现在", "当前", "如今", "现今", "现阶段", "当下",
    "今年", "去年", "明年", "最新", "最近", "近期", "近来",
    "这两年", "这几年", "本月", "上个月", "本周", "现在市面上",
    "现如今", "眼下",
]

# 回答发布超过该天数后，含相对时间词的时效主张判「锚点失效」
ANCHOR_EXPIRE_DAYS = 180
# 二级判定：晚近主张至少比旧主张晚这么多天才有资格「更新」它
CONFLICT_MIN_AGE_GAP_DAYS = 30
# 语义相似候选阈值与上限（控制 LLM 调用）
CONFLICT_SIM_THRESHOLD = 0.8
CONFLICT_MAX_CANDIDATES = 15


def level1_anchor_expire(
    claims: list[dict[str, Any]],
    edit_time_by_id: dict[str, float],
    now: float | None = None,
) -> dict[int, str]:
    """第一级：时间锚点失效（纯规则）。返回 {claim_idx: 'expired'}。"""
    now = now or time.time()
    expired: dict[int, str] = {}
    for idx, c in enumerate(claims):
        if c["type"] != "sensitive":
            continue
        text = c["claim"]
        if not any(w in text for w in RELATIVE_TIME_WORDS):
            continue
        edit = edit_time_by_id.get(c["content_id"], 0) or 0
        if edit <= 0:
            continue
        age_days = (now - edit) / 86400.0
        if age_days > ANCHOR_EXPIRE_DAYS:
            expired[idx] = "expired"
    return expired


def level2_internal_conflict(
    claims: list[dict[str, Any]],
    embeddings: np.ndarray,
    edit_time_by_id: dict[str, float],
    skip: set[int],
) -> dict[int, str]:
    """第二级：晚近主张反驳旧主张（embedding 选候选 + 便宜 LLM 判定）。"""
    n = len(claims)
    sim = embeddings @ embeddings.T  # 已 L2 归一化，点积即余弦
    candidates: list[tuple[float, int, int]] = []  # (sim, old_idx, new_idx)
    for i in range(n):
        if i in skip or claims[i]["type"] != "sensitive":
            continue
        t_old = edit_time_by_id.get(claims[i]["content_id"], 0) or 0
        if t_old <= 0:
            continue
        for j in range(n):
            if i == j or claims[j]["content_id"] == claims[i]["content_id"]:
                continue
            t_new = edit_time_by_id.get(claims[j]["content_id"], 0) or 0
            if t_new - t_old < CONFLICT_MIN_AGE_GAP_DAYS * 86400:
                continue
            s = float(sim[i, j])
            if s >= CONFLICT_SIM_THRESHOLD:
                candidates.append((s, i, j))
    candidates.sort(reverse=True)
    candidates = candidates[:CONFLICT_MAX_CANDIDATES]
    if not candidates:
        return {}

    old_ids = {}
    new_block_items = {}
    old_block_lines = []
    new_block_lines = []
    for rank, (_, i, j) in enumerate(candidates):
        oid = f"old{rank}"
        old_ids[oid] = i
        old_block_lines.append(f"[{oid}] {claims[i]['claim']}")
        # 同一旧主张可能配多个晚近主张，收集去重
        if j not in new_block_items:
            new_block_items[j] = f"new{len(new_block_items)}"
            new_block_lines.append(f"[{new_block_items[j]}] （发布更晚）{claims[j]['claim']}")
    judgments = judge_conflicts("\n".join(old_block_lines), "\n".join(new_block_lines))

    out: dict[int, str] = {}
    for oid, status in judgments.items():
        if status == "conflict" and oid in old_ids:
            out[old_ids[oid]] = "expired"
    return out


def detect_freshness(
    claims: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    embeddings: np.ndarray,
    now: float | None = None,
) -> dict[str, str]:
    """三级判定入口。返回 {claim_idx(str): 'expired'|'suspected'}（仅时效敏感主张）。"""
    now = now or time.time()
    edit_time_by_id = {a["content_id"]: a.get("edit_time", 0) or 0 for a in answers}

    statuses: dict[int, str] = {}
    # 第一级
    statuses.update(level1_anchor_expire(claims, edit_time_by_id, now))
    # 第二级（跳过一级已判的）
    try:
        statuses.update(level2_internal_conflict(claims, embeddings, edit_time_by_id, set(statuses)))
    except Exception as e:  # noqa: BLE001 - 二级失败不阻塞主流程
        print(f"[警告] 二级过期判定失败，跳过: {e}")
    # 第三级：其余时效敏感 → 疑似过期（未核验）
    for idx, c in enumerate(claims):
        if c["type"] == "sensitive" and idx not in statuses:
            statuses[idx] = "suspected"

    return {str(k): v for k, v in statuses.items()}
