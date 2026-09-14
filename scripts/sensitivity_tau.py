"""数据战役 · 任务 1b：τ 敏感性分析（答辩弹药）。

对已完成全量分析的议题，固定阶段 2–5 缓存，仅扰动 EXPO_TAU_DAYS ∈ [0.5×, 2×] 重算评分，
观察遗珠清单 Top 10 的变动率。若变动 <20%，「τ 取不准」的质疑即被化解。

用法：python scripts/sensitivity_tau.py [--topics "标题关键词,..."]
产出：data/campaign/tau_sensitivity.json + tau_sensitivity.png
"""
import argparse
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
from pipeline.fetch import load_cache, topic_key
from pipeline.score import score_answers

BASE_TAU = 365.0
PERTURBATIONS = [0.5, 0.75, 1.0, 1.5, 2.0]
TOP_N = 10


def recompute(key: str, tau: float) -> dict[str, float]:
    """用指定 τ 重算评分，返回 {content_id: U}（仅可判定回答）。"""
    config.EXPO_TAU_DAYS = tau
    stage1 = load_cache(key, "1_search")
    stage2 = load_cache(key, "2_claims")
    stage4 = load_cache(key, "4_cluster")
    stage45 = load_cache(key, "4b_freshness")
    samples = stage1["samples"]
    claims: list[dict] = []
    for a in samples:
        for c in stage2["claims_map"].get(a["content_id"], []):
            claims.append({"content_id": a["content_id"], **c})
    scored = score_answers(samples, claims, stage4["labels"], verified=stage45["statuses"])
    return {s["content_id"]: s["underestimate_index"] for s in scored
            if s["underestimate_index"] is not None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", default=None, help="逗号分隔的标题关键词（默认取前 3 个可判定样本 ≥15 的议题）")
    args = ap.parse_args()

    # 选题
    rank_file = config.DATA_DIR / "campaign" / "monopoly_rank.json"
    rank = json.loads(rank_file.read_text(encoding="utf-8")) if rank_file.exists() else []
    if args.topics:
        kws = [k.strip() for k in args.topics.split(",") if k.strip()]
        keys = [topic_key(t) for t in [r["title"] for r in rank] if any(k in t for k in kws)]
    else:
        keys = []
        for r in rank:
            key = r["key"]
            report = load_cache(key, "6_report")
            if not report:
                continue
            n_rankable = sum(1 for a in report["answers"] if a.get("underestimate_index") is not None)
            if n_rankable >= 15:
                keys.append(key)
            if len(keys) >= 3:
                break
    if not keys:
        print("没有可用议题（先完成任务 2 全量跑通）")
        sys.exit(1)

    results = {}
    for key in keys:
        report = load_cache(key, "6_report")
        title = report.get("title", key)
        print(f"\n=== {title} ===")
        us: dict[float, dict[str, float]] = {}
        for p in PERTURBATIONS:
            tau = BASE_TAU * p
            us[p] = recompute(key, tau)
            print(f"  τ={tau:>6.0f}（{p:.2f}×）：可判定 {len(us[p])} 篇")
        config.EXPO_TAU_DAYS = BASE_TAU

        base = us[1.0]
        base_top = sorted(base, key=lambda c: -base[c])[:TOP_N]
        rows = []
        for p in PERTURBATIONS:
            if p == 1.0:
                continue
            u = us[p]
            top = sorted(u, key=lambda c: -u[c])[:TOP_N]
            overlap = len(set(base_top) & set(top)) / TOP_N
            common = [c for c in base_top if c in u]
            rho = spearmanr([base[c] for c in common], [u[c] for c in common]).statistic if len(common) >= 8 else None
            rows.append({"perturbation": p, "tau": BASE_TAU * p,
                         "top10_overlap": round(overlap, 3), "spearman_U": round(float(rho), 3) if rho is not None else None})
            print(f"  ±{(p-1)*100:+.0f}%：Top{TOP_N} 重合 {overlap:.0%}，U 排序 ρ={rho:.3f}" if rho is not None
                  else f"  ±{(p-1)*100:+.0f}%：Top{TOP_N} 重合 {overlap:.0%}")
        results[title] = {"key": key, "rows": rows}

    # 汇总图：各议题 Top10 重合率随扰动变化
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for title, r in results.items():
        xs = [row["perturbation"] for row in r["rows"]]
        ys = [row["top10_overlap"] for row in r["rows"]]
        ax.plot(xs, ys, marker="o", label=title[:18])
    ax.axvline(1.0, color="#E8B84B", ls="--", alpha=0.6, label="基准 τ=365")
    ax.set_xlabel("τ 扰动倍数")
    ax.set_ylabel(f"遗珠 Top{TOP_N} 重合率")
    ax.set_title("τ 敏感性：遗珠清单对 τ 的稳定性")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    out_png = config.DATA_DIR / "campaign" / "tau_sensitivity.png"
    fig.savefig(out_png, dpi=150, bbox_inches="tight")

    out = {"base_tau": BASE_TAU, "results": results}
    out_json = config.DATA_DIR / "campaign" / "tau_sensitivity.json"
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out_json}\n→ {out_png}")


if __name__ == "__main__":
    main()
