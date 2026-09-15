"""热榜批量分析：拉取知乎最新热榜 → 每个问题全量跑 pipeline → 并入垄断榜。

用法：
    python scripts/run_hotlist.py [--limit 30] [--only 关键词] [--qa-max-items N]

- 热榜接口 1 次调用拉全榜；每个问题自带 URL，直接启用枚举通道
- 配额自适应：跑前查 question_answers 剩余额度，按待跑议题均分页数（单议题 0–5 页，留 10 次余量）；
  额度耗尽时自动降级为纯搜索通道
- 断点续跑：已有 6_report 的议题直接跳过（同一命令可安全重跑）
- 结果：缓存进 data/cache/<key>/（前端问题库/遗珠榜自动可见），垄断度并入 data/campaign/monopoly_rank.json

生产用法（服务器后台）：
    nohup /srv/zseek/venv/bin/python scripts/run_hotlist.py > data/campaign/hotlist_run.log 2>&1 &
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
from pipeline.fetch import ZhihuClient, check_quota, topic_key, load_cache
from pipeline.run import run

RANK_PATH = config.DATA_DIR / "campaign" / "monopoly_rank.json"
META_PATH = config.DATA_DIR / "campaign" / "hotlist_meta.jsonl"
QA_MARGIN = 10  # 给次日/手工操作留的枚举额度余量


def qa_pages_allowed(client: ZhihuClient, n_topics: int) -> int:
    """按剩余额度给每个议题分枚举页数（保底 0 页=纯搜索；单议题上限 5 页）。"""
    try:
        data = client.quota()
    except Exception as e:
        print(f"[警告] 额度查询失败（{e}），按每议题 1 页保守跑")
        return 1 if n_topics else 0
    remaining = next((it.get("RemainingQuota", 0) for it in data if it.get("APIID") == "question_answers"), 0)
    pages = (remaining - QA_MARGIN) // n_topics if n_topics else 0
    return max(0, min(5, pages))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="热榜拉取条数（接口实测上限 30）")
    ap.add_argument("--only", default=None, help="只跑标题含此关键词的议题")
    ap.add_argument("--qa-max-items", type=int, default=None, help="强制单议题枚举上限（默认按额度自适应）")
    args = ap.parse_args()

    client = ZhihuClient()
    check_quota(client)
    items = client.hot_list(args.limit)
    print(f"热榜 {len(items)} 条")

    topics: list[tuple[str, str | None]] = []
    for it in items:
        title = str(it.get("Title") or "").strip()
        url = str(it.get("Url") or "").strip()
        if not title:
            continue
        if url and "/question/" not in url:
            url = None  # 非问题条目（文章等）只走搜索通道
        topics.append((title, url))
    if args.only:
        topics = [t for t in topics if args.only in t[0]]

    # 断点续跑：已有完整报告的跳过
    todo: list[tuple[str, str | None, str]] = []
    for title, url in topics:
        key = topic_key(title)
        if load_cache(key, "6_report"):
            print(f"[跳过] {title[:40]}（已有报告）")
            continue
        todo.append((title, url, key))

    if not todo:
        print("没有待跑议题。")
        return

    if args.qa_max_items is not None:
        config.QA_MAX_ITEMS = args.qa_max_items
        print(f"枚举上限：强制 {config.QA_MAX_ITEMS} 条/议题")
    else:
        pages = qa_pages_allowed(client, len(todo))
        config.QA_MAX_ITEMS = pages * config.QA_PAGE_LIMIT
        print(f"枚举上限：额度自适应 = {config.QA_MAX_ITEMS} 条/议题（{pages} 页 × {config.QA_PAGE_LIMIT} 条）")

    rank = json.loads(RANK_PATH.read_text(encoding="utf-8")) if RANK_PATH.exists() else []
    rank_keys = {r["key"] for r in rank}
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_f = META_PATH.open("a", encoding="utf-8")

    ok = fail = 0
    try:
        for i, (title, url, key) in enumerate(todo, 1):
            print(f"\n{'=' * 70}\n[{i}/{len(todo)}] {title}\n{'=' * 70}")
            rec = {"title": title, "key": key, "url": url,
                   "ran_at": datetime.now(timezone.utc).isoformat()}
            try:
                result = run(title, url, 10, False, False)
                mono = result.get("monopoly") or {}
                answers = result.get("answers", [])
                rec.update(
                    ok=True,
                    sample_size=result.get("sample_size"),
                    claim_total=result.get("claim_total"),
                    pearl_count=sum(1 for a in answers if "沧海遗珠" in (a.get("badges") or [])),
                    votes_unknown=sum(1 for a in answers if a.get("votes_unknown")),
                )
                ok += 1
                if key not in rank_keys:
                    rank.append({
                        "title": title, "key": key,
                        "monopoly_gap": mono.get("monopoly_gap"),
                        "exposure_share_top_k": mono.get("exposure_share_top_k"),
                        "info_increment_share_top_k": mono.get("info_increment_share_top_k"),
                        "sample_size": result.get("sample_size"),
                        "claim_total": result.get("claim_total"),
                        "pearl_count": sum(1 for a in answers
                                           if a.get("underestimate_index") is not None
                                           and a["underestimate_index"] > config.BADGE_UNDISCOVERED),
                    })
                    rank_keys.add(key)
                    rank.sort(key=lambda r: -(r["monopoly_gap"] if r.get("monopoly_gap") is not None else -9))
                    RANK_PATH.write_text(json.dumps(rank, ensure_ascii=False, indent=2), encoding="utf-8")
            except SystemExit as e:
                print(f"[跳过] pipeline 退出：{e}")
                rec.update(ok=False, error=f"exit: {e}")
                fail += 1
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

    print(f"\n完成：成功 {ok}，失败/跳过 {fail}。元数据 → {META_PATH}")
    print(f"垄断榜共 {len(rank)} 议题 → {RANK_PATH}")


if __name__ == "__main__":
    main()
