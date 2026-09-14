"""数据战役 · 任务 3a：盲标抽样。

从 2–3 个议题中抽 40 篇回答，刻意分层：遗珠候选 / 中腰部 / 头部高赞各约 1/3。
输出两份文件：
  - blind_sample.jsonl：乱序文本（无评分信息，供盲标）
  - blind_strata.json  ：id → 分层（仅分析阶段使用，盲标时勿看）

--append 模式：在已有样本上追加新议题（保留旧 id 不动，新样本乱序后接在末尾），
用于扩样；已存在的议题会被跳过，防止 id 冲突。

用法：python scripts/sample_blind.py --topics "关键词1,关键词2" [--n 40] [--append]
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pipeline import config
from pipeline.fetch import load_cache, topic_key

CAMPAIGN = config.DATA_DIR / "campaign"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", required=True, help="逗号分隔的标题关键词，2–3 个")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--append", action="store_true",
                    help="追加到已有 blind_sample.jsonl（跳过已采样议题），默认覆盖重写")
    args = ap.parse_args()

    sample_path = CAMPAIGN / "blind_sample.jsonl"
    strata_path = CAMPAIGN / "blind_strata.json"
    existing: list[dict] = []
    strata: dict[str, str] = {}
    if args.append and sample_path.exists():
        existing = [json.loads(l) for l in sample_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        strata = json.loads(strata_path.read_text(encoding="utf-8"))
    kws = [k.strip() for k in args.topics.split(",") if k.strip()]
    rank_file = CAMPAIGN / "monopoly_rank.json"
    rank = json.loads(rank_file.read_text(encoding="utf-8")) if rank_file.exists() else []
    titles = [r["title"] for r in rank if any(k in r["title"] for k in kws)]
    if not titles:
        print("关键词未匹配到议题")
        sys.exit(1)
    if args.append and existing:
        dup = [t for t in titles if t in {s["topic"] for s in existing}]
        if dup:
            print(f"[跳过已采样议题] {len(dup)} 个：" + "；".join(t[:20] for t in dup))
        titles = [t for t in titles if t not in dup]
        if not titles:
            print("没有可追加的新议题")
            sys.exit(0)

    per = args.n // len(titles)
    samples: list[dict] = []
    for t in titles:
        key = topic_key(t)
        report = load_cache(key, "6_report")
        rankable = [a for a in report["answers"] if a.get("underestimate_index") is not None]
        rankable.sort(key=lambda a: -a["underestimate_index"])
        n = len(rankable)
        if n < 9:
            print(f"[跳过] {t}：可判定样本太少（{n}）")
            continue
        # 分层：上 1/3（遗珠候选）、中 1/3、下 1/3（高曝光低增量）
        hi = rankable[: max(1, n // 3)]
        mid = rankable[max(1, n // 3): max(1, n // 3) * 2] or rankable[n // 2: n // 2 + 1]
        lo = rankable[max(1, n // 3) * 2:] or rankable[-1:]
        quota = per // 3
        picks = ([(a, "pearl") for a in hi[:quota]] +
                 [(a, "mid") for a in mid[:quota]] +
                 [(a, "head") for a in lo[: quota + (per % 3)]])
        for j, (a, s) in enumerate(picks):
            cid = f"{key[:6]}-{j}-{a['content_id'][:8]}"
            samples.append({
                "id": cid, "topic": t, "author": a.get("author", "匿名"),
                "text": a.get("text", ""), "url": a.get("url", ""),
            })
            strata[cid] = s
        print(f"{t[:40]}：可判定 {n} 篇，抽 {len(picks)}（遗珠 {quota} / 中腰 {quota} / 头部 {len(picks)-2*quota}）")

    rng = random.Random(args.seed)
    rng.shuffle(samples)

    if args.append and existing:
        merged = existing + samples  # 新样本乱序后接在旧样本之后，旧 id 不动
    else:
        merged = samples
    with sample_path.open("w", encoding="utf-8") as f:
        for s in merged:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    strata_path.write_text(json.dumps(strata, ensure_ascii=False, indent=2), encoding="utf-8")
    action = "追加" if args.append and existing else "写出"
    print(f"\n盲标样本 {action} {len(samples)} 篇（累计 {len(merged)}，乱序，无评分）→ {sample_path}")
    print(f"分层映射（勿给标注者）→ {strata_path}")


if __name__ == "__main__":
    main()
