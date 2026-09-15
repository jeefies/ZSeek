"""枚举通道后补：对 run_hotlist --search-only 跑出的议题，用剩余 question_answers 额度补枚举样本。

背景：question_answers 100 次/日且响应慢，热榜批量先 --search-only 出结果（不耗枚举额度），
本脚本随后把枚举摘要（无赞数 → votes_unknown）补进样本，扩大聚类/观点覆盖。

工作原理（零浪费）：
- 文章级拆主张缓存（data/cache/_articles/）使旧样本重拆 0 LLM 消耗，只有新枚举篇花调用
- 下游阶段（2→6）作废重跑；embedding 缓存按主张数自动失效、本地 CPU 重算秒级
- 补完即更新垄断榜，前台即时可见

用法：
    python scripts/enrich_enumeration.py [--pages-per-topic N] [--budget-calls N] [--dry-run]

- 候选议题来自 data/campaign/hotlist_meta.jsonl（run_hotlist 的产出），只需 6_report 存在且枚举为空
- 默认额度自适应：pages = clamp((剩余 − 10) // 待补数, 0, 5)；额度不够就停，改天重跑同命令即可续补
- 已补过（枚举非空）的议题自动跳过
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from pipeline import config
from pipeline.fetch import (
    ZhihuClient, check_quota, topic_key, load_cache, save_cache,
    enumerate_question, merge_enumeration_samples,
)
from pipeline.run import run

RANK_PATH = config.DATA_DIR / "campaign" / "monopoly_rank.json"
META_PATH = config.DATA_DIR / "campaign" / "hotlist_meta.jsonl"
ENRICH_META_PATH = config.DATA_DIR / "campaign" / "hotlist_enrich_meta.jsonl"
QA_MARGIN = 10  # 给次日/手工操作留的枚举额度余量
# 下游作废阶段（与 run.py 主动注入路径同一清单；3_embeddings.npy 按主张数自动失效，不用删）
INVALIDATE = ("2_claims", "4_cluster", "4b_freshness", "5_score", "6a_names", "6_report")


def load_candidates() -> list[tuple[str, str | None, str]]:
    """从 hotlist 元数据里挑出：有完整报告、但枚举通道为空的热榜议题。"""
    if not META_PATH.exists():
        sys.exit(f"[退出] 找不到 {META_PATH}，先跑 run_hotlist.py")
    out: list[tuple[str, str | None, str]] = []
    seen: set[str] = set()
    for line in META_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not rec.get("ok"):
            continue
        title, key, url = rec.get("title"), rec.get("key"), rec.get("url")
        if not title or not key or key in seen:
            continue
        seen.add(key)
        stage1 = load_cache(key, "1_search")
        if not stage1 or not load_cache(key, "6_report"):
            continue
        enum = stage1.get("enumeration")
        if enum and enum.get("items"):
            continue  # 已补过或原本就有枚举
        out.append((title, url or stage1.get("question_url"), key))
    return out


def update_rank(title: str, key: str, result: dict) -> None:
    """重跑完成后同步垄断榜条目（没有则追加）。"""
    rank = json.loads(RANK_PATH.read_text(encoding="utf-8")) if RANK_PATH.exists() else []
    mono = result.get("monopoly") or {}
    answers = result.get("answers", [])
    entry = {
        "title": title, "key": key,
        "monopoly_gap": mono.get("monopoly_gap"),
        "exposure_share_top_k": mono.get("exposure_share_top_k"),
        "info_increment_share_top_k": mono.get("info_increment_share_top_k"),
        "sample_size": result.get("sample_size"),
        "claim_total": result.get("claim_total"),
        "pearl_count": sum(1 for a in answers
                           if a.get("underestimate_index") is not None
                           and a["underestimate_index"] > config.BADGE_UNDISCOVERED),
    }
    for i, r in enumerate(rank):
        if r.get("key") == key:
            rank[i] = entry
            break
    else:
        rank.append(entry)
    rank.sort(key=lambda r: -(r["monopoly_gap"] if r.get("monopoly_gap") is not None else -9))
    RANK_PATH.write_text(json.dumps(rank, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-per-topic", type=int, default=None, help="强制每议题枚举页数（默认按额度自适应 0–5）")
    ap.add_argument("--budget-calls", type=int, default=None, help="本次运行 question_answers 调用预算（默认全部可用额度）")
    ap.add_argument("--dry-run", action="store_true", help="只打印待补清单和额度分配，不执行")
    args = ap.parse_args()

    candidates = load_candidates()
    print(f"待补议题：{len(candidates)} 个")
    for t, _, _ in candidates:
        print(f"  - {t[:50]}")

    client = ZhihuClient()
    check_quota(client)
    remaining = next((it.get("RemainingQuota", 0) for it in client.quota()
                      if it.get("APIID") == "question_answers"), 0)

    if args.pages_per_topic is not None:
        pages = max(0, min(5, args.pages_per_topic))
    else:
        pages = max(0, min(5, (remaining - QA_MARGIN) // len(candidates))) if candidates else 0
    calls_per_topic = pages  # 每页 1 次调用
    budget = args.budget_calls if args.budget_calls is not None else remaining
    print(f"额度：剩余 {remaining} 次；每议题 {pages} 页（{pages * config.QA_PAGE_LIMIT} 条），"
          f"单次调用预算 {budget} 次")

    if args.dry_run:
        print("[dry-run] 不执行。")
        return
    if pages == 0:
        print("[退出] 额度不足（或待补数为 0），改天重跑同命令即可续补。")
        return

    config.QA_MAX_ITEMS = pages * config.QA_PAGE_LIMIT
    ENRICH_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_f = ENRICH_META_PATH.open("a", encoding="utf-8")
    spent = ok = fail = skip = 0
    try:
        for i, (title, url, key) in enumerate(candidates, 1):
            if spent + calls_per_topic > budget:
                print(f"[停] 调用预算用尽（已用 {spent}/{budget}），剩余议题改天再补。")
                break
            if not url:
                print(f"[{i}/{len(candidates)}] {title[:40]} 无问题链接，无法枚举，跳过")
                skip += 1
                continue
            print(f"\n{'=' * 70}\n[{i}/{len(candidates)}] 补枚举：{title}\n{'=' * 70}")
            rec = {"title": title, "key": key, "url": url, "pages": pages,
                   "ran_at": datetime.now(timezone.utc).isoformat()}
            try:
                stage1 = load_cache(key, "1_search")
                enum = enumerate_question(client, url)
                spent += calls_per_topic
                before = len(stage1["samples"])
                stage1["samples"] = merge_enumeration_samples(stage1["samples"], enum)
                stage1["enumeration"] = enum
                save_cache(key, "1_search", stage1)
                added = len(stage1["samples"]) - before
                rec.update(ok=True, enum_fetched=enum.get("fetched", 0), added=added)
                print(f"      枚举 {enum.get('fetched', 0)} 条，新并入 {added} 篇；重跑下游…")
                if added > 0:
                    for st in INVALIDATE:
                        (config.CACHE_DIR / key / f"{st}.json").unlink(missing_ok=True)
                    result = run(title, url, 10, False, False)
                    update_rank(title, key, result)
                    rec["sample_size"] = result.get("sample_size")
                    rec["pearl_count"] = sum(1 for a in result.get("answers", [])
                                             if "沧海遗珠" in (a.get("badges") or []))
                    ok += 1
                else:
                    skip += 1
            except Exception as e:
                print(f"[失败] {title}: {e}")
                rec.update(ok=False, error=str(e))
                fail += 1
            finally:
                log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                log_f.flush()
            time.sleep(2)  # 温和节流，避免突发限速 30001
    finally:
        log_f.close()
        client.close()

    print(f"\n完成：重跑 {ok}，无新增/跳过 {skip}，失败 {fail}，枚举调用 {spent} 次。元数据 → {ENRICH_META_PATH}")


if __name__ == "__main__":
    main()
