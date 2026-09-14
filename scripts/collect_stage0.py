"""数据战役 · 阶段 0：共享数据采集。

对人工筛选的 15–20 个分层议题，只跑 pipeline 阶段 1（搜索 + 枚举抓取），
缓存到 data/cache/<key>/1_search.json，供 τ 拟合 / 垄断度 / 验证三任务复用。

用法：
    python scripts/collect_stage0.py [--no-llm] [--only "议题标题"]
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
    ZhihuClient, topic_key, load_cache, save_cache,
    default_variants, fetch_search_samples, enumerate_question,
    merge_enumeration_samples,
)
from pipeline.claims import expand_queries

# 战役采集：枚举通道单日 100 次调用，18 议题 × 5 次 = 90 次，留 10 次余量
config.QA_MAX_ITEMS = 100

# ---- 人工筛选议题（2026-09-12 热榜 + 常青补充），分两层 ----
TOPICS_CURRENT = [  # 时事类（τ 预期小）
    "网友称把前置摄像头关掉刷手机可以保护眼睛，这是真的吗？如果属实，是因为哪些原理？",
    "一天一瓶啤酒，对身体有害吗？",
    "美国 8 月 CPI 同比增长 3.4%，市场预期到年底前美联储将加息两次，如何解读？",
    "为啥现在很多做饭教程都是「两勺生抽一勺老抽一勺蚝油」？这是什么万能公式吗？能不能把它们也做成一种调料？",
    "一设计师称中国客厅已失去意义，反映了当下怎样的家庭生活变化？你家还有客厅吗，是怎样的？",
    "月之暗面Kimi K2.8 Preview模型9月11日上线kimi code，如何评价其表现？",
    "为什么皮鞋、手表、西装、酒这些行业崩溃了？",
    "打假网红铁头敲诈勒索案一审被判八年，伙同他人威胁曝黑料，向带货主播索要数百克黄金，哪些信息值得关注？",
    "曝优衣库一线员工上厕所只给 5 分钟，具体规定是怎样的？类似要求在一些行业岗位是普遍存在的吗？",
    "香港留学现在性价比是不是越来越低了？",
]
TOPICS_EVERGREEN = [  # 常青长尾类（τ 预期大）
    "亲兄妹之间长大后关系为什么会变差呢？",
    "如果孩子这辈子注定考不上985/211，只能做个普通体力劳动者，那我拼命鸡娃，买学区房的意义是什么？",
    "对于领导提出的不合理的要求，你们会「直接反驳吗」？",
    "觉得上海不好玩，是因为我没钱吗？",
    "我有套房子，同事一直要求我卖掉或者租出去，我该怎么办？",
    "孩子一年级从同学那学脏话怎么纠正？需要转学换个环境吗？",
    "古代没有电灯，晚上过了8点，古人都怎么打发时间？",
    "野外河沟里的蚊子吸谁的血？",
]

META_PATH = config.DATA_DIR / "campaign" / "stage0_meta.jsonl"


def age_bucket(ts: int, now: float) -> str | None:
    if not ts:
        return None
    d = (now - ts) / 86400
    for hi, name in [(7, "0-7d"), (30, "7-30d"), (90, "30-90d"), (365, "90-365d"), (1095, "1-3y")]:
        if d <= hi:
            return name
    return "3y+"


def collect_one(client: ZhihuClient, title: str, use_llm: bool) -> dict:
    key = topic_key(title)
    cached = load_cache(key, "1_search")
    if cached:
        print(f"[跳过] {title[:40]}…（缓存 {len(cached['samples'])} 篇）")
        return meta_of(title, key, cached, skipped=True)

    print(f"[采集] {title}")
    variants_llm = expand_queries(title) if use_llm else None
    variants = list(dict.fromkeys([title, *(variants_llm or []), *default_variants(title)]))
    print(f"       搜索通道 {len(variants)} 个变体")
    samples, question_guess = fetch_search_samples(client, title, variants)
    print(f"       搜索样本 {len(samples)} 篇" + (f"，识别问题：{question_guess}" if question_guess else ""))

    enumeration = None
    if question_guess:
        enumeration = enumerate_question(client, question_guess)
        before = len(samples)
        samples = merge_enumeration_samples(samples, enumeration)
        print(f"       枚举并入 {len(samples) - before} 篇（共取 {enumeration['fetched']} 条）")

    stage1 = {"title": title, "question_url": question_guess, "samples": samples,
              "enumeration": enumeration, "variants": variants}
    save_cache(key, "1_search", stage1)
    return meta_of(title, key, stage1, skipped=False)


def meta_of(title: str, key: str, stage1: dict, skipped: bool) -> dict:
    now = time.time()
    samples = stage1["samples"]
    search_samples = [s for s in samples if not s.get("votes_unknown")]
    ages = [(now - s["edit_time"]) / 86400 for s in search_samples if s.get("edit_time")]
    buckets: dict[str, int] = {}
    for s in search_samples:
        b = age_bucket(s.get("edit_time", 0), now)
        if b:
            buckets[b] = buckets.get(b, 0) + 1
    return {
        "title": title, "key": key,
        "layer": "current" if title in TOPICS_CURRENT else "evergreen",
        "question_url": stage1.get("question_url"),
        "n_samples": len(samples),
        "n_search_samples": len(search_samples),
        "votes_median": sorted(s["votes"] for s in search_samples)[len(search_samples) // 2] if search_samples else None,
        "age_min_days": round(min(ages), 1) if ages else None,
        "age_max_days": round(max(ages), 1) if ages else None,
        "age_buckets": buckets,
        "enum_fetched": (stage1.get("enumeration") or {}).get("fetched", 0),
        "skipped": skipped,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="不调用 glm 扩展子查询（省额度）")
    ap.add_argument("--only", default=None, help="只跑指定标题的议题")
    args = ap.parse_args()

    topics = [*TOPICS_CURRENT, *TOPICS_EVERGREEN]
    if args.only:
        topics = [t for t in topics if args.only in t]

    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    client = ZhihuClient()
    metas = []
    try:
        for i, title in enumerate(topics, 1):
            print(f"\n=== [{i}/{len(topics)}] ===")
            try:
                metas.append(collect_one(client, title, use_llm=not args.no_llm))
            except Exception as e:
                print(f"[失败] {title}: {e}")
                metas.append({"title": title, "error": str(e)})
            # 温和节流，避免 30001
            time.sleep(2)
    finally:
        client.close()
        with META_PATH.open("a", encoding="utf-8") as f:
            for m in metas:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"\n完成：{sum(1 for m in metas if not m.get('error'))} 个议题，元数据 → {META_PATH}")


if __name__ == "__main__":
    main()
