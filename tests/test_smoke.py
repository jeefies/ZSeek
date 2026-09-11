"""知寻 pipeline 冒烟测试（不消耗 API 额度）。

test_score_synthetic：合成数据验证指标体系
  - 低赞+独占+有锚点 → 高估 U、🔦 徽章
  - 高赞+共识 → 低/负 U
  - 独占但零锚点 → V 低于有独占有锚点的回答
  - 发布 <7 天 → 观察池，U=None
test_cluster_shapes：聚类输出形状与回答级坐标
"""
import time

from pipeline.score import score_answers, monopoly_metrics
from pipeline.cluster import cluster_claims, answer_positions
from pipeline.freshness import detect_freshness


def _fixture():
    now = time.time()
    answers = [
        {"content_id": "a1", "votes": 5000, "comments": 100, "author": "大V",
         "edit_time": now - 3 * 365 * 86400, "text": "x"},
        {"content_id": "a2", "votes": 15, "comments": 2, "author": "小透明",
         "edit_time": now - 2 * 365 * 86400, "text": "x"},
        {"content_id": "a3", "votes": 8, "comments": 0, "author": "路人",
         "edit_time": now - 400 * 86400, "text": "x"},
        {"content_id": "a4", "votes": 30, "comments": 1, "author": "新人",
         "edit_time": now - 3 * 86400, "text": "x"},
    ]
    claims = [
        {"content_id": "a1", "claim": "共识观点一", "type": "evergreen", "verifiable": True},
        {"content_id": "a1", "claim": "共识观点二", "type": "evergreen", "verifiable": False},
        {"content_id": "a2", "claim": "独占观点甲", "type": "evergreen", "verifiable": True},
        {"content_id": "a2", "claim": "共识观点一", "type": "evergreen", "verifiable": True},
        {"content_id": "a3", "claim": "独占观点乙", "type": "evergreen", "verifiable": False},
        {"content_id": "a4", "claim": "独占观点丙", "type": "sensitive", "verifiable": True},
        {"content_id": "a4", "claim": "独占观点丁", "type": "sensitive", "verifiable": False},
        {"content_id": "a1", "claim": "共识观点二补充", "type": "evergreen", "verifiable": True},
    ]
    # 簇0={a1,a2 的"共识观点一"}, 簇1={a1 两条}, 噪声={a2甲,a3乙,a4丙,a4丁}
    labels = [0, 1, -1, 0, -1, -1, -1, 1]
    return answers, claims, labels


def test_score_synthetic():
    answers, claims, labels = _fixture()
    scored = {s["content_id"]: s for s in score_answers(answers, claims, labels)}
    now = time.time()

    # 低赞独占+有锚点（a2）应显著高于高赞共识（a1）
    assert scored["a2"]["info_score"] > scored["a1"]["info_score"]
    # 独占+有锚点（a2）> 独占+零锚点（a3）：支柱效应
    assert scored["a2"]["info_score"] > scored["a3"]["info_score"]
    # 低赞高价值（a2/a3）的 U 应高于高赞共识（a1）
    assert scored["a2"]["underestimate_index"] > scored["a1"]["underestimate_index"]
    assert scored["a3"]["underestimate_index"] > scored["a1"]["underestimate_index"]
    # a2 U 应为全场最高（样本仅 4 篇，百分位粒度粗，达不到 0.6 徽章阈值属正常）
    assert scored["a2"]["underestimate_index"] >= scored["a3"]["underestimate_index"]
    assert scored["a2"]["underestimate_index"] > 0.3
    # a4 发布 <7 天 → 观察池，不做低估判定
    assert scored["a4"]["in_observation_pool"] is True
    assert scored["a4"]["underestimate_index"] is None
    assert "沧海遗珠" not in scored["a4"]["badges"]
    # 时间修正：3 年老回答修正倍数在 (1, 5] 之间
    assert 1.0 < scored["a1"]["expo_correction"] <= 5.0
    # a4 新鲜度：2 条敏感都未核验 → F = 1 - 0.3 = 0.7
    assert abs(scored["a4"]["freshness"] - 0.7) < 1e-6
    # 垄断度结构完整
    m = monopoly_metrics(list(scored.values()))
    assert {"exposure_share_top_k", "info_increment_share_top_k", "monopoly_gap"} <= set(m)


def test_freshness_levels():
    import numpy as np
    import time as _t
    now = _t.time()
    answers = [
        {"content_id": "old", "edit_time": now - 400 * 86400},   # 400 天前
        {"content_id": "new", "edit_time": now - 10 * 86400},    # 10 天前
    ]
    claims = [
        {"content_id": "old", "claim": "目前的政策是 X", "type": "sensitive", "verifiable": True},
        {"content_id": "old", "claim": "房价处于低位", "type": "sensitive", "verifiable": False},
        {"content_id": "new", "claim": "政策已改为 Y，房价处于低位", "type": "sensitive", "verifiable": True},
        {"content_id": "new", "claim": "常青知识", "type": "evergreen", "verifiable": False},
    ]
    emb = np.eye(4, 16, dtype=np.float32)  # 归一化的正交向量
    statuses = detect_freshness(claims, answers, emb, now=now)
    # 一级：400 天前的回答里「目前」→ 锚点失效
    assert statuses.get("0") == "expired"
    # 常青不参与
    assert "3" not in statuses
    # 二级/三级：无相对时间词的主张要么 suspected，要么被晚近冲突判 expired
    assert statuses.get("1") in ("expired", "suspected")


def test_badges():
    from pipeline.score import _badges
    assert "沧海遗珠" in _badges({"underestimate_index": 0.7, "age_days": 300, "freshness": 1.0})
    assert "潜力新声" in _badges({"underestimate_index": 0.5, "age_days": 30, "freshness": 1.0})
    assert "沧海遗珠" not in _badges({"underestimate_index": 0.5, "age_days": 30, "freshness": 1.0})  # U 不够
    assert "内容待更新" in _badges({"underestimate_index": 0.0, "age_days": 900, "freshness": 0.3})
    assert _badges({"underestimate_index": None, "age_days": 3, "freshness": 1.0}) == []  # 观察池


def test_personal_detail_pillar():
    """经历类双路径（手册类型—支柱对照）：personal 靠 D（细节密度），不靠 E。
    相同稀有度下，含不可复制细节的亲历叙述应显著高于空泛感慨。"""
    now = time.time()
    answers = [
        {"content_id": "p1", "votes": 10, "comments": 0, "author": "亲历者甲",
         "edit_time": now - 400 * 86400, "text": "x"},
        {"content_id": "p2", "votes": 12, "comments": 0, "author": "亲历者乙",
         "edit_time": now - 400 * 86400, "text": "x"},
    ]
    claims = [
        # 具体细节经历（D=1），零锚点
        {"content_id": "p1", "claim": "我2021年3月在同仁做的全飞秒，术前角膜厚度532微米，术后第三天复查视力1.2",
         "type": "personal", "detail": True, "verifiable": False},
        # 空泛感慨（D=0.2），零锚点
        {"content_id": "p2", "claim": "我当年也很纠结，后来想开就好了", "type": "personal", "detail": False, "verifiable": False},
    ]
    labels = [-1, -1]  # 均为噪声 → 各自独立成簇，稀有度相同，唯一变量是 D
    scored = {s["content_id"]: s for s in score_answers(answers, claims, labels)}
    assert scored["p1"]["info_score"] > scored["p2"]["info_score"]
    assert scored["p1"]["detailed_claims"] == 1
    assert scored["p2"]["detailed_claims"] == 0


def test_detail_override_rule():
    """确定性升级规则：数字/时长/学段碎片 → detail 置真；纯情绪/笼统状态不触发。"""
    from pipeline.claims import _detail_override
    assert _detail_override("最后我高考考的比我对象多6分")          # 阿拉伯数字
    assert _detail_override("作者的恋爱对象复读了一年")            # 中文数词+量词
    assert _detail_override("去年冬天我们分手了")                  # 明确时间锚点
    assert _detail_override("本人是高三开始恋爱的")                # 学段
    assert not _detail_override("觉得自己很愚笨")                  # 纯情绪
    assert not _detail_override("我们几乎不秀恩爱")                # 笼统状态
    assert not _detail_override("三思而后行")                      # 数词后非量词，不误伤


def test_sensitive_settle_rule():
    """sensitive 后验校验：无时间锚点/时效话题词 → 降级 evergreen，防 F 误罚。"""
    from pipeline.claims import _settle_type
    assert _settle_type("sensitive", "我们从大数据来看，这还真是影响的") == "evergreen"
    assert _settle_type("sensitive", "成年人的感情可能还有约的419") == "evergreen"
    assert _settle_type("sensitive", "目前的政策是 X") == "sensitive"      # 相对时间词
    assert _settle_type("sensitive", "全飞秒最新价格 1.2 万") == "sensitive"  # 时效话题词
    assert _settle_type("sensitive", "2023 年千人手术量 1.5 人") == "sensitive"  # 年份锚点
    assert _settle_type("personal", "任何话") == "personal"              # 非 sensitive 原样


def test_evergreen_time_upgrade():
    """evergreen 含相对时间词/年份锚点 → 升级 sensitive（时效判定覆盖）。"""
    from pipeline.claims import _TIME_WORDS, _YEAR_PATTERN
    text = "现在高中谈恋爱的很多"
    assert any(w in text for w in _TIME_WORDS)
    text2 = "2023 年我们结婚了"
    assert _YEAR_PATTERN.search(text2)


def test_cluster_shapes():
    import numpy as np
    rng = np.random.default_rng(0)
    embeddings = rng.normal(size=(40, 16)).astype(np.float32)
    out = cluster_claims(embeddings, umap_components=8, min_cluster_size=3)
    assert len(out["labels"]) == 40
    assert len(out["embedding_2d"]) == 40
    assert all(len(xy) == 2 for xy in out["embedding_2d"])
    claims = [{"content_id": f"a{i % 4}"} for i in range(40)]
    pos = answer_positions(claims, out["labels"], out["embedding_2d"])
    assert set(pos) == {"a0", "a1", "a2", "a3"}
