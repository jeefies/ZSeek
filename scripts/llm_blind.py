"""数据战役 · 任务 3c：LLM 盲标（第二标注人）。

用 openai-next 的模型（默认 deepseek-v4-flash，--model 可换）对 blind_sample.jsonl
逐篇盲标。协议与人工盲标完全一致：模型只看到「问题 + 回答原文 + 作者名」，
看不到任何系统分数、赞同数、分层信息。

产出（均在 data/campaign/）：
  - blind_llm_labels.jsonl  ：{"id", "label", "reason"}，断点续跑
  - blind_llm_analysis.json ：LLM vs 人工一致性（粗一致率、Cohen's κ）、
                              LLM 标注下的分层 worth_rate 与 KW 检验、候选层内 ρ(LLM, V)
  - blind_llm_validation.png：左=人工/LLM 分层值得率对比，右=人工 vs LLM 评分散点

用法：
  python scripts/llm_blind.py                # 标注（断点续跑）+ 分析
  python scripts/llm_blind.py --model glm-4-air
  python scripts/llm_blind.py --analyze-only # 只重跑分析
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import kruskal, spearmanr

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from pipeline import config
from pipeline.fetch import load_cache

LABEL_SCORE = {"worth": 2, "meh": 1, "not_worth": 0}
SCORE_LABEL = {2: "worth", 1: "meh", 0: "not_worth"}
CAMPAIGN = config.DATA_DIR / "campaign"

PROMPT = """你是一名严格的内容评审。下面是一个知乎问题下的一篇回答。
请只依据回答本身的信息价值与可核验性，判断它是否「值得被更多人看到」。

评判标准：
- worth：提供了大多数读者不知道的信息增量（独到经验、数据、分析角度），或内容扎实、可核验
- meh：信息量一般，正确但平庸，看了等于没看
- not_worth：低信息（情绪宣泄、玩梗、重复常识、纯表态），或事实存疑且无法核验

注意：不要依据文笔、立场、是否讨喜来评判；只看信息增量与可核验性。
不要输出其他内容。

问题：{topic}
回答作者：{author}
回答正文：
{text}

只输出一行 JSON：{{"label": "worth"|"meh"|"not_worth", "reason": "一句话理由"}}"""

MAX_TEXT_CHARS = 5000  # 盲标不需要全文，截断防超长


def chat(base: str, key: str, model: str, prompt: str, retries: int = 2) -> str:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.0,
                    "max_tokens": 512,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=90.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 - 与 pipeline/claims.py 同一策略
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"openai-next 调用失败: {last_err}")


def parse_label(content: str) -> dict:
    """从模型输出中稳健抽取 {"label", "reason"}。"""
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError(f"输出无 JSON: {content[:80]!r}")
    r = json.loads(m.group(0))
    label = str(r.get("label", "")).strip().lower()
    if label not in LABEL_SCORE:
        raise ValueError(f"非法 label: {label!r}")
    return {"label": label, "reason": str(r.get("reason", ""))[:200]}


def run_labeling(model: str) -> Path:
    base = os.environ.get("OPENAI_NEXT_BASE_URL", "").rstrip("/")
    key = os.environ.get("OPENAI_NEXT_API_KEY", "").strip()
    if not base or not key:
        raise RuntimeError("未配置 OPENAI_NEXT_BASE_URL / OPENAI_NEXT_API_KEY")

    out_path = CAMPAIGN / "blind_llm_labels.jsonl"
    done: dict[str, dict] = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r

    samples = [json.loads(l) for l in (CAMPAIGN / "blind_sample.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = [s for s in samples if s["id"] not in done]
    print(f"模型 {model}：共 {len(samples)} 篇，已完成 {len(done)}，待标 {len(todo)}")

    with out_path.open("a", encoding="utf-8") as f:
        for i, s in enumerate(todo, 1):
            text = s["text"][:MAX_TEXT_CHARS]
            if len(s["text"]) > MAX_TEXT_CHARS:
                text += "\n……（后略）"
            prompt = PROMPT.format(topic=s["topic"], author=s.get("author", "匿名"), text=text)
            try:
                r = parse_label(chat(base, key, model, prompt))
            except Exception as e:  # noqa: BLE001 - 单篇失败不中断，分析阶段按缺失处理
                print(f"  [{i}/{len(todo)}] {s['id']} 标注失败：{e}")
                continue
            rec = {"id": s["id"], "model": model, **r}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            done[s["id"]] = rec
            print(f"  [{i}/{len(todo)}] {s['id']} → {r['label']}（{r['reason'][:30]}）")
            time.sleep(0.3)  # 温和限速

    print(f"→ {out_path}（{len(done)}/{len(samples)}）")
    return out_path


def cohens_kappa(a: list[int], b: list[int]) -> float:
    """无序 Cohen's κ（三档标签足够；注意它忽略 worth/meh 的有序接近性，偏保守）。"""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca.get(c, 0) / n) * (cb.get(c, 0) / n) for c in set(ca) | set(cb))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def merge_scores(labels: dict[str, int]) -> list[dict]:
    """与 analyze_blind.py 同一合并逻辑：盲标 id → 缓存里的 U/V/徽章/议题。"""
    strata = json.loads((CAMPAIGN / "blind_strata.json").read_text(encoding="utf-8"))
    topics = {}
    if (CAMPAIGN / "blind_sample.jsonl").exists():
        for line in (CAMPAIGN / "blind_sample.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                topics[r["id"]] = r["topic"]
    rows = []
    for sid, score in labels.items():
        key6 = sid.split("-", 2)[0]
        cid8 = sid.split("-", 2)[2]
        for d in config.CACHE_DIR.iterdir():
            if not d.name.startswith(key6):
                continue
            report = load_cache(d.name, "6_report")
            if not report:
                continue
            a = next((x for x in report["answers"]
                      if x["content_id"].startswith(cid8) and x.get("underestimate_index") is not None), None)
            if a:
                rows.append({"id": sid, "score": score, "U": a["underestimate_index"],
                             "V": a["info_score"], "stratum": strata.get(sid, "?"),
                             "votes": a.get("votes", 0), "badges": a.get("badges", []),
                             "topic": topics.get(sid, "?")})
                break
    return rows


def tier_stats(rows: list[dict]) -> dict:
    out = {}
    for s in ("pearl", "mid", "head"):
        cells = [r for r in rows if r["stratum"] == s]
        out[s] = {
            "n": len(cells),
            "mean": round(float(np.mean([r["score"] for r in cells])), 3) if cells else None,
            "worth_rate": round(sum(1 for r in cells if r["score"] == 2) / len(cells), 3) if cells else None,
        }
    return out


def run_analysis(model: str) -> None:
    llm = {}
    for line in (CAMPAIGN / "blind_llm_labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            llm[r["id"]] = LABEL_SCORE[r["label"]]
    human = {}
    for line in (CAMPAIGN / "blind_labels.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            human[r["id"]] = LABEL_SCORE[r["label"]]

    common = sorted(set(llm) & set(human))
    if not common:
        print("LLM 与人工标注没有共同 id，无法比对")
        sys.exit(1)

    # ---- 1) LLM vs 人工一致性（第二标注人角色）----
    agree = sum(1 for i in common if llm[i] == human[i])
    po = agree / len(common)
    kappa = cohens_kappa([human[i] for i in common], [llm[i] for i in common])
    # 有序三档下 ±1 档也算「方向一致」
    close = sum(1 for i in common if abs(llm[i] - human[i]) <= 1) / len(common)
    print(f"\n=== LLM（{model}）vs 人工：N={len(common)} ===")
    print(f"  完全一致率 {po:.1%}，Cohen's κ={kappa:.3f}，±1 档内一致率 {close:.1%}")

    # 混淆矩阵
    conf = Counter((SCORE_LABEL[human[i]], SCORE_LABEL[llm[i]]) for i in common)
    for h in ("worth", "meh", "not_worth"):
        row = "  ".join(f"{l}:{conf.get((h, l), 0)}" for l in ("worth", "meh", "not_worth"))
        print(f"  人工={h:<9} {row}")

    # ---- 2) LLM 标注下的系统指标审查（人工口径的复现检验）----
    rows = merge_scores(llm)
    stats = tier_stats(rows)
    groups = [[r["score"] for r in rows if r["stratum"] == s] for s in ("pearl", "mid", "head")]
    kw = kruskal(*groups)
    pearl = [r for r in rows if r["stratum"] == "pearl"]
    rho_pv = spearmanr([r["score"] for r in pearl], [r["V"] for r in pearl]).statistic if len(pearl) >= 8 else None
    print(f"\n=== LLM 标注下的分层审查（N={len(rows)}）===")
    for s in ("pearl", "mid", "head"):
        print(f"  {s}(n={stats[s]['n']})：均值 {stats[s]['mean']} / 值得率 {stats[s]['worth_rate']:.0%}")
    print(f"  Kruskal-Wallis：H={kw.statistic:.3f}，p={kw.pvalue:.4f}")
    print(f"  遗珠候选层内 ρ(LLM, V)={rho_pv:.3f}" if rho_pv is not None else "  遗珠候选层样本不足，跳过层内相关")

    # ---- 2b) 议题级分解： pooled 分层统计在议题异质/基线差异大时会互相抵消 ----
    by_topic: dict[str, list[dict]] = {}
    for r in rows:
        by_topic.setdefault(r["topic"], []).append(r)
    per_topic = {}
    print("\n=== 议题级分解（每格 n=4–6，只看方向）===")
    for t, cells in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        line = f"  {t[:30]}"
        pt = {}
        for s in ("pearl", "mid", "head"):
            g = [r for r in cells if r["stratum"] == s]
            wr = sum(1 for r in g if r["score"] == 2) / len(g) if g else None
            pt[s] = {"n": len(g), "worth_rate": round(wr, 3) if wr is not None else None}
            line += f"  {s} {wr:.0%}" if wr is not None else f"  {s}  —"
        per_topic[t] = pt
        print(line)

    # ---- 2c) 徽章级审查：产品真正推的是「沧海遗珠」徽章，而非 U Top1/3 分层 ----
    def badge_group(r: dict) -> str:
        joined = "".join(r["badges"])
        if "遗珠" in joined:
            return "pearl_badge"
        if r["badges"]:
            return "other_badge"
        return "no_badge"
    badge_stats = {}
    print("\n=== 徽章级审查（LLM 值得率）===")
    for g in ("pearl_badge", "other_badge", "no_badge"):
        cells = [r for r in rows if badge_group(r) == g]
        if not cells:
            continue
        wr = sum(1 for r in cells if r["score"] == 2) / len(cells)
        badge_stats[g] = {"n": len(cells), "worth_rate": round(wr, 3)}
        print(f"  {g:<12} n={len(cells):>3}  值得率 {wr:.0%}")

    # ---- 3) 图：人工 vs LLM 分层值得率 + 评分散点 ----
    h_rows = merge_scores(human)
    h_stats = tier_stats(h_rows)
    rng = np.random.default_rng(7)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5))
    names = {"pearl": "遗珠候选层", "mid": "中腰部", "head": "头部高赞层"}
    xs = np.arange(3)
    w = 0.36
    ax1.bar(xs - w / 2, [h_stats[s]["worth_rate"] or 0 for s in ("pearl", "mid", "head")],
            width=w, color="#7BA7C7", label="人工盲标")
    ax1.bar(xs + w / 2, [stats[s]["worth_rate"] or 0 for s in ("pearl", "mid", "head")],
            width=w, color="#E8B84B", label=f"LLM 盲标（{model}）")
    for x, s in zip(xs, ("pearl", "mid", "head")):
        ax1.text(x - w / 2, (h_stats[s]["worth_rate"] or 0) + 0.02, f"{h_stats[s]['worth_rate']:.0%}", ha="center", fontsize=9)
        ax1.text(x + w / 2, (stats[s]["worth_rate"] or 0) + 0.02, f"{stats[s]['worth_rate']:.0%}", ha="center", fontsize=9)
    ax1.set_xticks(xs, [names[s] for s in ("pearl", "mid", "head")], fontsize=10)
    ax1.set_ylabel("「值得更多人看到」比例")
    ax1.set_title(f"人工 vs LLM 盲标的分层值得率（N={len(common)}）")
    ax1.set_ylim(0, 0.9)
    ax1.legend(fontsize=9)
    ax1.grid(alpha=0.3, axis="y")
    ax2.scatter([human[i] for i in common], [llm[i] + rng.uniform(-0.12, 0.12) for i in common],
                c="#8A93A0", s=55, alpha=0.8)
    for x in (0, 1, 2):
        for y in (0, 1, 2):
            c = sum(1 for i in common if human[i] == x and llm[i] == y)
            if c:
                ax2.text(x, y, str(c), ha="center", va="center", fontsize=11,
                         color="#B33A3A" if x == y else "#666666",
                         fontweight="bold" if x == y else "normal")
    ax2.set_xlabel("人工评分")
    ax2.set_ylabel("LLM 评分")
    ax2.set_xticks([0, 1, 2], ["不值得", "一般", "值得"])
    ax2.set_yticks([0, 1, 2], ["不值得", "一般", "值得"])
    ax2.set_title(f"标注一致性：κ={kappa:.2f}，一致率 {po:.0%}")
    ax2.grid(alpha=0.3)
    out_png = CAMPAIGN / "blind_llm_validation.png"
    fig.savefig(out_png, dpi=150, bbox_inches="tight")

    out = {
        "model": model, "n_pair": len(common),
        "agreement": round(po, 3), "cohens_kappa": round(kappa, 3),
        "within_one_rate": round(close, 3),
        "confusion": {f"{h}->{l}": c for (h, l), c in sorted(conf.items())},
        "llm_tier_stats": stats,
        "human_tier_stats": h_stats,
        "kruskal_H": round(float(kw.statistic), 3), "kruskal_p": round(float(kw.pvalue), 4),
        "rho_pearl_within_V": round(float(rho_pv), 3) if rho_pv is not None else None,
        "per_topic_tiers": per_topic, "badge_stats": badge_stats,
        "rows": rows,
        "note": "κ 为无序口径偏保守；LLM 与人工协议一致（只看原文，不看分数），"
                "κ 衡量第二标注人可信度，LLM 分层统计用于复现人工口径的结论；"
                "pooled 分层在议题异质大时会抵消，需结合 per_topic_tiers 与 badge_stats 一起看",
    }
    (CAMPAIGN / "blind_llm_analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {CAMPAIGN / 'blind_llm_analysis.json'}\n→ {out_png}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("BLIND_LLM_MODEL", "deepseek-v4-flash"),
                    help="openai-next 上的模型名（默认 deepseek-v4-flash，可用 BLIND_LLM_MODEL 覆盖）")
    ap.add_argument("--analyze-only", action="store_true", help="跳过标注，只重跑分析")
    args = ap.parse_args()

    if not args.analyze_only:
        run_labeling(args.model)
    run_analysis(args.model)


if __name__ == "__main__":
    main()
