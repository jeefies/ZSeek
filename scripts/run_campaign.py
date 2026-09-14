"""数据战役 · 任务 2a：18 个议题全量跑通（阶段 2–6）。

阶段 1 缓存复用（阶段 0 已采集），逐议题断点续跑；
汇总垄断度排行榜 → data/campaign/monopoly_rank.json。

用法：python scripts/run_campaign.py [--only "关键词"]
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
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from pipeline import config
from pipeline.run import run

CAMPAIGN_TOPICS = [
    # 时事类
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
    # 常青类
    "亲兄妹之间长大后关系为什么会变差呢？",
    "如果孩子这辈子注定考不上985/211，只能做个普通体力劳动者，那我拼命鸡娃，买学区房的意义是什么？",
    "对于领导提出的不合理的要求，你们会「直接反驳吗」？",
    "觉得上海不好玩，是因为我没没钱吗？",  # 占位防错：下面用真实标题
    "觉得上海不好玩，是因为我没钱吗？",
    "我有套房子，同事一直要求我卖掉或者租出去，我该怎么办？",
    "孩子一年级从同学那学脏话怎么纠正？需要转学换个环境吗？",
    "古代没有电灯，晚上过了8点，古人都怎么打发时间？",
    "野外河沟里的蚊子吸谁的血？",
]
# 去掉占位项
CAMPAIGN_TOPICS = [t for t in CAMPAIGN_TOPICS if "没没钱" not in t]

RANK_PATH = config.DATA_DIR / "campaign" / "monopoly_rank.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    topics = CAMPAIGN_TOPICS
    if args.only:
        topics = [t for t in topics if args.only in t]

    rank = []
    if RANK_PATH.exists():
        rank = json.loads(RANK_PATH.read_text(encoding="utf-8"))

    done_keys = {r["key"] for r in rank}
    from pipeline.fetch import topic_key, load_cache
    for i, title in enumerate(topics, 1):
        key = topic_key(title)
        if key in done_keys:
            print(f"[{i}/{len(topics)}] 跳过（已入榜）：{title[:40]}")
            continue
        print(f"\n{'='*70}\n[{i}/{len(topics)}] {title}\n{'='*70}")
        try:
            result = run(title, None, 10, False, False)
        except SystemExit as e:
            print(f"[跳过] pipeline 退出：{e}")
            continue
        except Exception as e:
            print(f"[失败] {title}: {e}")
            continue
        mono = result.get("monopoly") or {}
        rank.append({
            "title": title, "key": key,
            "monopoly_gap": mono.get("monopoly_gap"),
            "exposure_share_top_k": mono.get("exposure_share_top_k"),
            "info_increment_share_top_k": mono.get("info_increment_share_top_k"),
            "sample_size": result.get("sample_size"),
            "claim_total": result.get("claim_total"),
            "pearl_count": sum(1 for a in result["answers"]
                               if a.get("underestimate_index") is not None
                               and a["underestimate_index"] > config.BADGE_UNDISCOVERED),
        })
        rank.sort(key=lambda r: -(r["monopoly_gap"] if r["monopoly_gap"] is not None else -9))
        RANK_PATH.write_text(json.dumps(rank, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[入榜 {len(rank)}] 当前榜首：{rank[0]['title'][:36]}（gap {rank[0]['monopoly_gap']:+.0%}）")

    print(f"\n排行榜（{len(rank)} 议题）→ {RANK_PATH}")
    for j, r in enumerate(rank, 1):
        if r.get("monopoly_gap") is not None:
            print(f"  {j:>2}. {r['title'][:42]}  gap {r['monopoly_gap']:+.1%}  曝光 {r['exposure_share_top_k']:.0%} vs 增量 {r['info_increment_share_top_k']:.0%}")


if __name__ == "__main__":
    main()
