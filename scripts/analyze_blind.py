"""数据战役 · 任务 3b：盲标分析。

输入盲标结果 labels.jsonl：{"id": "...", "label": "worth"|"meh"|"not_worth"}
（值得更多人看到 / 一般 / 不值得）
与议题报告中的 U 值合并，算 spearman 相关 + 分层命中率，出散点图（PPT 核心页）。

用法：python scripts/analyze_blind.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from pipeline import config
from pipeline.fetch import load_cache

LABEL_SCORE = {"worth": 2, "meh": 1, "not_worth": 0}
CAMPAIGN = config.DATA_DIR / "campaign"


def main() -> None:
    labels = {}
    for line in (CAMPAIGN / "blind_labels.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        labels[r["id"]] = LABEL_SCORE[r["label"]]
    strata = json.loads((CAMPAIGN / "blind_strata.json").read_text(encoding="utf-8"))

    # 合并 U 值：id 形如 {key6}-{j}-{content8}
    rows = []
    for sid, score in labels.items():
        key6 = sid.split("-", 2)[0]
        cid8 = sid.split("-", 2)[2]  # content_id 本身可能以「-」开头，不能用 split("-")[-1]
        for d in config.CACHE_DIR.iterdir():
            if not d.name.startswith(key6):
                continue
            report = load_cache(d.name, "6_report")
            if not report:
                continue
            a = next((x for x in report["answers"]
                      if x["content_id"].startswith(cid8) and x.get("underestimate_index") is not None), None)
            if a:
                rows.append({"id": sid, "human": score, "U": a["underestimate_index"],
                             "V": a["info_score"],
                             "stratum": strata.get(sid, "?"), "votes": a.get("votes", 0)})
                break

    if len(rows) < 10:
        print(f"有效配对太少（{len(rows)}），检查 id 与缓存")
        sys.exit(1)

    human = [r["human"] for r in rows]
    us = [r["U"] for r in rows]
    n = len(rows)

    # 分层抽样下池化 spearman 会混淆层间与层内信号，主指标用分层统计：
    # 1) 各 U 层人工价值均值与「值得」率（方向性检验，Kruskal-Wallis）
    # 2) 遗珠候选层内 V 与人工判断的相关（质量维度检验）
    from scipy.stats import kruskal
    strata_order = ("pearl", "mid", "head")
    groups = [[r["human"] for r in rows if r["stratum"] == s] for s in strata_order]
    kw = kruskal(*groups)
    tier_stats = {}
    for s, g in zip(strata_order, groups):
        cells = [r for r in rows if r["stratum"] == s]
        tier_stats[s] = {
            "n": len(cells),
            "human_mean": round(float(np.mean(g)), 3) if cells else None,
            "worth_rate": round(sum(1 for x in g if x == 2) / len(g), 3) if cells else None,
            "U_mean": round(float(np.mean([r["U"] for r in cells])), 3) if cells else None,
            "V_mean": round(float(np.mean([r["V"] for r in cells])), 3) if cells else None,
        }
    pearl_cells = [r for r in rows if r["stratum"] == "pearl"]
    rho_pearl_v = spearmanr([r["human"] for r in pearl_cells], [r["V"] for r in pearl_cells]).statistic \
        if len(pearl_cells) >= 8 else None
    rho_pool_u = spearmanr(human, us).statistic
    print(f"N={n}")
    print(f"  各 U 层人工价值：" + "  ".join(
        f"{s}(n={tier_stats[s]['n']}) 均值 {tier_stats[s]['human_mean']} / 值得率 {tier_stats[s]['worth_rate']:.0%}"
        for s in strata_order))
    print(f"  Kruskal-Wallis：H={kw.statistic:.3f}，p={kw.pvalue:.4f}")
    print(f"  遗珠候选层内 ρ(human, V)={rho_pearl_v:.3f}（质量维度）")
    print(f"  参考：池化 ρ(human, U)={rho_pool_u:.3f}（分层抽样下仅作参考，层间/层内信号混淆）")
    pearl_hit = sum(1 for r in pearl_cells if r["human"] == 2)
    head_cells = [r for r in rows if r["stratum"] == "head"]
    head_hit = sum(1 for r in head_cells if r["human"] == 2)
    if pearl_cells and head_cells:
        print(f"  区分度：遗珠候选层「值得」率 {pearl_hit/len(pearl_cells):.0%} vs 头部层 {head_hit/len(head_cells):.0%}")

    # 图（PPT 核心页）：左=各 U 层「值得」率柱状，右=人工评分 vs U 散点
    rng = np.random.default_rng(7)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    names = {"pearl": "遗珠候选层\n(U 最高 1/3)", "mid": "中腰部", "head": "头部高赞层\n(U 最低 1/3)"}
    colors = {"pearl": "#E8B84B", "mid": "#7BA7C7", "head": "#8A93A0"}
    xs = np.arange(3)
    rates = [tier_stats[s]["worth_rate"] or 0 for s in strata_order]
    bars = ax1.bar(xs, rates, color=[colors[s] for s in strata_order], width=0.55)
    for x, r in zip(xs, rates):
        ax1.text(x, r + 0.02, f"{r:.0%}", ha="center", fontsize=11)
    ax1.set_xticks(xs, [names[s] for s in strata_order], fontsize=9)
    ax1.set_ylabel("人工盲标「值得更多人看到」比例")
    ax1.set_title(f"知寻 U 分层 vs 人工判断（N={n}）")
    ax1.set_ylim(0, 0.85)
    ax1.grid(alpha=0.3, axis="y")
    for s in strata_order:
        xs2 = [r["U"] for r in rows if r["stratum"] == s]
        ys2 = [r["human"] + rng.uniform(-0.12, 0.12) for r in rows if r["stratum"] == s]
        ax2.scatter(xs2, ys2, c=colors[s], s=55, alpha=0.8,
                    label={"pearl": "遗珠候选", "mid": "中腰部", "head": "头部高赞"}[s])
    ax2.set_xlabel("知寻低估指数 U（系统排序依据）")
    ax2.set_yticks([0, 1, 2], ["不值得", "一般", "值得更多人看到"])
    ax2.set_title(f"散点视图（候选层内 ρ(V,人工)={rho_pearl_v:.2f}）" if rho_pearl_v is not None else "散点视图")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    out_png = CAMPAIGN / "blind_validation.png"
    fig.savefig(out_png, dpi=150, bbox_inches="tight")

    out = {"n": n, "tier_stats": tier_stats, "kruskal_H": round(float(kw.statistic), 3),
           "kruskal_p": round(float(kw.pvalue), 4),
           "rho_pearl_within_V": round(float(rho_pearl_v), 3) if rho_pearl_v is not None else None,
           "rho_pooled_U": round(float(rho_pool_u), 3),
           "note": "分层抽样下池化 spearman 混淆层间/层内信号，主指标为分层 worth_rate 与 KW 检验",
           "rows": rows}
    (CAMPAIGN / "blind_analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {CAMPAIGN / 'blind_analysis.json'}\n→ {out_png}")


if __name__ == "__main__":
    main()
