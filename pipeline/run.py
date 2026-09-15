"""知寻（ZhiSeek）分析 pipeline CLI。

用法：
    python -m pipeline.run "近视手术安全吗" [--question-url URL] [--topn 10] [--force] [--no-llm]

阶段（每阶段结果分层缓存到 data/cache/<key>/，可断点续跑）：
    1_search    搜索抽样（曝光通道，可选 + 问题回答枚举）
    2_claims    直答拆主张 + 时效/可核验分类
    3_embed     本地 embedding（bge-small-zh）
    4_cluster   UMAP + HDBSCAN 聚类
    5_score     指标计算
    6_report    簇命名 + 挖掘理由 + 垄断度 + 最终 JSON
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

# Windows 控制台默认 GBK，统一改为 UTF-8 输出，防止打印 Unicode 崩溃
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 支持从仓库根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pipeline import config
from pipeline.fetch import (
    ZhihuClient, topic_key, load_cache, save_cache,
    default_variants, fetch_search_samples, enumerate_question, check_quota,
    merge_enumeration_samples,
)
from pipeline.claims import ZhidaClient, split_claims, expand_queries
from pipeline.embed import embed_claims
from pipeline.freshness import detect_freshness
from pipeline.cluster import cluster_claims, answer_positions
from pipeline.score import score_answers, monopoly_metrics
from pipeline.report import name_clusters, build_reason_features, write_reasons


def run(title: str, question_url: str | None, topn: int, force: bool, no_llm: bool,
        include_answers: list[dict] | None = None) -> dict:
    key = topic_key(title)
    cache_dir = config.CACHE_DIR / key
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"议题：{title}（缓存 key: {key}）")

    # 整报告短路：已分析过的议题直接返回缓存（force / 有注入回答 才重跑）
    if not force and not include_answers:
        cached_report = load_cache(key, "6_report")
        if cached_report:
            print("[缓存] 该议题已分析过，直接返回缓存报告")
            return cached_report

    client = ZhihuClient()

    # ---- 阶段 1：抓取 ----
    stage1 = None if force else load_cache(key, "1_search")
    if stage1:
        print(f"[1/6] 抓取（缓存）：样本 {len(stage1['samples'])} 篇")
    else:
        check_quota(client)
        # 阶段 1 进度文件：供状态接口/SSE 展示「正在查哪个查询/枚举到哪页」（完成即删）
        fetch_prog_path = config.CACHE_DIR / key / "1_progress.json"

        def _fetch_progress(done: int, total: int, current: str) -> None:
            try:
                fetch_prog_path.write_text(json.dumps(
                    {"done": done, "total": total, "current": current}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass  # 进度文件 best-effort，绝不影响抓取主流程

        variants_llm = None
        if not no_llm:
            print("[1/6] 抓取：LLM 扩展子查询…")
            _fetch_progress(0, 0, "LLM 扩展搜索查询…")
            variants_llm = expand_queries(title)
        # 模板变体与 LLM 变体并行（搜索无翻页，只有多查询能扩大召回）
        variants = list(dict.fromkeys([title, *(variants_llm or []), *default_variants(title)]))
        print(f"[1/6] 抓取：搜索通道 {len(variants)} 个查询变体…")
        samples, question_guess = fetch_search_samples(client, title, variants, on_progress=_fetch_progress)
        print(f"      搜索去重后样本 {len(samples)} 篇回答")
        if not question_url and question_guess:
            question_url = question_guess
            print(f"      自动识别问题链接：{question_url}")
        enumeration = None
        if question_url:
            print(f"      枚举通道：{question_url}")
            try:
                enumeration = enumerate_question(client, question_url, on_progress=_fetch_progress)
            except Exception as e:  # noqa: BLE001 - 枚举是增强通道，失败仅用搜索样本继续
                print(f"      枚举通道不可用（{e}），跳过")
            if enumeration:
                before = len(samples)
                samples = merge_enumeration_samples(samples, enumeration)
                print(f"      枚举并入 {len(samples) - before} 篇（问题共取 {enumeration['fetched']} 条，"
                      f"{'已取完' if enumeration['is_end'] else '截断'}）")
        stage1 = {"title": title, "question_url": question_url, "samples": samples, "enumeration": enumeration, "variants": variants}
        save_cache(key, "1_search", stage1)
        fetch_prog_path.unlink(missing_ok=True)  # 阶段完成即清，避免下一轮误显示

    samples = stage1["samples"]

    # 主动注入回答（个人遗珠）：用户自己的低赞回答常不进搜索样本。
    # 三级策略：① 已在枚举样本（无赞数）→ 升级为曝光样本参与 U 判定；② 有全文 → 新样本；③ 无全文 → 枚举接口拉摘要补文本
    if include_answers:
        def _norm(u: str) -> str:
            return str(u or "").split("?")[0].rstrip("/")

        by_url = {_norm(s.get("url")): s for s in samples}
        enum_cache: dict[str, str] | None = None  # url → summary（懒拉取）
        injected = 0

        def _fetch_answer_text(url: str) -> str:
            """兜底：知乎回答页公开可访问，抓 HTML 内嵌 JSON 取全文（不耗 API 配额）。"""
            try:
                resp = httpx.get(str(url), headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                                 timeout=15, follow_redirects=True)
                m = re.search(r'<script id="js-initialData" type="text/json">(.*?)</script>', resp.text, re.S)
                if not m:
                    return ""
                data = json.loads(m.group(1))
                answers = (data.get("initialState", {}).get("entities", {}).get("answers") or {})
                for ans in answers.values():
                    content = str(ans.get("content") or "")
                    if len(content) > 40:
                        return re.sub(r"<[^>]+>", "", content).strip()
            except Exception:  # noqa: BLE001 - 兜底失败就跳过
                pass
            return ""

        for ia in include_answers:
            u = _norm(ia.get("url"))
            if not u:
                continue
            existing = by_url.get(u)
            if existing is not None:
                # ① 已在样本：补赞数/发布时间，升级为「曝光通道」样本（参与 U 判定）
                if existing.get("votes_unknown") or existing.get("votes", 0) == 0:
                    et = int(ia.get("edit_time") or 0)
                    if et > 10**12:
                        et //= 1000  # 毫秒→秒
                    existing["votes"] = int(ia.get("votes") or 0)
                    existing["edit_time"] = et or existing.get("edit_time", 0)
                    existing.pop("votes_unknown", None)
                    injected += 1
                continue
            text = str(ia.get("text") or "").strip()
            if not text and question_url:
                # ③ 创作接口无全文：用问题枚举通道的摘要（~200 字）做文本兜底
                if enum_cache is None:
                    enum_cache = {}
                    try:
                        for it in (enumerate_question(client, question_url).get("items") or []):
                            su = _norm(it.get("url"))
                            if su and (it.get("summary") or "").strip():
                                enum_cache[su] = it["summary"].strip()
                    except Exception as e:  # noqa: BLE001 - 兜底失败就跳过
                        print(f"      [提示] 枚举兜底拉取失败：{e}")
                text = enum_cache.get(u, "")
            if not text:
                # ④ 最终兜底：直接抓回答公开页面取全文
                text = _fetch_answer_text(u)
                if text:
                    print(f"      页面兜底取回全文 {len(text)} 字")
            if not text:
                print(f"      [提示] 注入跳过（无文本可拆）：{u[-40:]}")
                continue
            # ② 新样本：以「曝光通道」身份并入
            et = int(ia.get("edit_time") or 0)
            if et > 10**12:
                et //= 1000
            samples.append({
                "content_id": str(ia.get("content_id") or f"injected_{injected}"),
                "question_title": title,
                "text": text[:6000],
                "votes": int(ia.get("votes") or 0),
                "comments": 0,
                "author": str(ia.get("author") or "我"),
                "edit_time": et,
                "url": ia.get("url"),
            })
            by_url[u] = samples[-1]
            injected += 1
        if injected:
            save_cache(key, "1_search", stage1)
            # 样本构成变了：下游缓存全部作废重算（文章级缓存使重拆只花新注入篇的 LLM）
            for st in ("2_claims", "4_cluster", "4b_freshness", "5_score", "6a_names", "6_report"):
                (cache_dir / f"{st}.json").unlink(missing_ok=True)
            print(f"      主动注入 {injected} 篇本人回答，下游重跑")
        else:
            print("      [提示] 无有效注入（样本已覆盖或均无文本）")

    if len(samples) < 5:
        print(f"[错误] 样本过少（{len(samples)} 篇），无法分析。换个更热门的议题或提供 --question-url。")
        sys.exit(1)

    # ---- 阶段 2：拆主张 ----
    stage2 = None if force else load_cache(key, "2_claims")
    if stage2:
        total_cached = sum(len(v) for v in stage2["claims_map"].values())
        print(f"[2/6] 拆主张（缓存）：{total_cached} 条主张")
    elif no_llm:
        print("[2/6] 拆主张：--no-llm 模式下用整段文本作为单条主张（仅调试用）")
        claims_map = {a["content_id"]: [{"claim": a["text"][:200], "type": "evergreen", "verifiable": False}] for a in samples}
        stage2 = {"claims_map": claims_map}
        save_cache(key, "2_claims", stage2)
    else:
        print(f"[2/6] 拆主张：{len(samples)} 篇回答（GLM-4.7-Flash / 百炼 Batch）…")
        claims_map = split_claims(samples, cache_dir=config.CACHE_DIR / key)
        total = sum(len(v) for v in claims_map.values())
        print(f"      共 {total} 条主张")
        stage2 = {"claims_map": claims_map}
        save_cache(key, "2_claims", stage2)

    # 拍平主张列表（顺序稳定，与 embedding 对齐）
    claims: list[dict] = []
    for a in samples:
        for c in stage2["claims_map"].get(a["content_id"], []):
            claims.append({"content_id": a["content_id"], **c})
    if not claims:
        print("[错误] 没有提取到任何主张。")
        sys.exit(1)

    # ---- 阶段 3：embedding ----
    print(f"[3/6] embedding：{len(claims)} 条主张（本地 bge-small-zh）…")
    embeddings = embed_claims(claims, cache_dir)

    # ---- 阶段 4：聚类 ----
    stage4 = None if force else load_cache(key, "4_cluster")
    if stage4 and len(stage4["labels"]) == len(claims):
        print(f"[4/6] 聚类（缓存）：{len(set(l for l in stage4['labels'] if l != -1))} 个簇")
    else:
        print(f"[4/6] 聚类：UMAP({config.UMAP_N_COMPONENTS}) + HDBSCAN(min={config.HDBSCAN_MIN_CLUSTER_SIZE})…")
        stage4 = cluster_claims(embeddings, config.UMAP_N_COMPONENTS, config.HDBSCAN_MIN_CLUSTER_SIZE)
        save_cache(key, "4_cluster", stage4)
    labels = stage4["labels"]
    n_clusters = len(set(l for l in labels if l != -1))
    n_noise = sum(1 for l in labels if l == -1)
    print(f"      {n_clusters} 个观点簇，{n_noise} 条少数派主张（噪声）")

    # ---- 阶段 4.5：三级过期判定 ----
    stage45 = None if force else load_cache(key, "4b_freshness")
    if stage45 and len(stage45.get("statuses", {})) >= 0:
        print(f"[4.5/6] 过期判定（缓存）：{sum(1 for v in stage45['statuses'].values() if v=='expired')} 条已过期，"
              f"{sum(1 for v in stage45['statuses'].values() if v=='suspected')} 条疑似")
    else:
        print("[4.5/6] 过期判定：一级锚点规则 + 二级内部冲突检测…")
        stage45 = {"statuses": detect_freshness(claims, samples, embeddings)}
        save_cache(key, "4b_freshness", stage45)
        print(f"        {sum(1 for v in stage45['statuses'].values() if v=='expired')} 条已过期，"
              f"{sum(1 for v in stage45['statuses'].values() if v=='suspected')} 条疑似（未核验）")

    # ---- 阶段 5：评分 ----
    print("[5/6] 评分：主张稀有度加权 V、时间修正曝光 L∞、百分位差 U、议题垄断度…")
    scored = score_answers(samples, claims, labels, verified=stage45["statuses"])
    total_fetched = (stage1.get("enumeration") or {}).get("fetched")
    monopoly = monopoly_metrics(scored, total_fetched)
    save_cache(key, "5_score", {"scored": scored, "monopoly": monopoly})

    # ---- 阶段 6：报告 ----
    print("[6/6] 报告：簇命名 + 挖掘理由…")
    positions = answer_positions(claims, labels, stage4["embedding_2d"])
    if no_llm:
        names, reasons = {}, {}
    else:
        zhida = ZhidaClient()
        names = name_clusters(zhida, claims, labels, embeddings)
        save_cache(key, "6a_names", {"names": {str(k): v for k, v in names.items()}})  # 进度标记：命名完成
        features = build_reason_features(scored, topn)
        reasons = write_reasons(zhida, features)
        zhida.close()

    # 主张来源信息（簇展开视图用）
    meta_by_id = {a["content_id"]: a for a in samples}
    CLUSTER_CLAIM_CAP = 12
    ANSWER_CLAIM_CAP = 15

    clusters_view = []
    for label in sorted(set(l for l in labels if l != -1)):
        member_idx = [i for i, l in enumerate(labels) if l == label]
        cids = sorted({claims[i]["content_id"] for i in member_idx})
        claims_view = [
            {
                "claim": claims[i]["claim"],
                "author": meta_by_id.get(claims[i]["content_id"], {}).get("author", "匿名"),
                "content_id": claims[i]["content_id"],
                "url": meta_by_id.get(claims[i]["content_id"], {}).get("url", ""),
            }
            for i in member_idx[:CLUSTER_CLAIM_CAP]
        ]
        clusters_view.append({
            "cluster": label,
            "label": names.get(label, {}).get("label", f"观点簇 {label}"),
            "summary": names.get(label, {}).get("summary", ""),
            "claim_count": len(member_idx),
            "answer_count": len(cids),
            "representative_answer": cids[0] if cids else None,
            "claims": claims_view,
            "claims_truncated": len(member_idx) > CLUSTER_CLAIM_CAP,
        })

    minority = sorted(scored, key=lambda x: -x["unique_claims"])
    rankable = [s for s in scored if s["underestimate_index"] is not None]
    top_undervalued = sorted(rankable, key=lambda x: -x["underestimate_index"])[:topn]

    # 每篇回答的主导观点簇（散点图着色用）
    cluster_of_answer: dict[str, dict[int, int]] = {}
    for claim, label in zip(claims, labels):
        d = cluster_of_answer.setdefault(claim["content_id"], {})
        d[label] = d.get(label, 0) + 1

    def dominant(cid: str) -> int:
        counts = cluster_of_answer.get(cid, {})
        non_noise = {k: v for k, v in counts.items() if k != -1}
        if not non_noise:
            return -1
        return max(non_noise.items(), key=lambda kv: kv[1])[0]

    # 每条主张是否「独占」：所在簇内只有它一个回答（口径与 score.unique_claims 一致）
    claim_keys = [l if l != -1 else -(i + 2) for i, l in enumerate(labels)]
    members: dict[int, set[str]] = {}
    for ck, c in zip(claim_keys, claims):
        members.setdefault(ck, set()).add(c["content_id"])
    unique_flags: dict[str, list[bool]] = {}
    for i, c in enumerate(claims):
        unique_flags.setdefault(c["content_id"], []).append(len(members[claim_keys[i]]) == 1)

    def claim_items(cid: str) -> list[dict[str, Any]]:
        flags = unique_flags.get(cid, [])
        return [
            {
                "claim": c["claim"],
                "type": c.get("type", "evergreen"),
                "verifiable": bool(c.get("verifiable", False)),
                "has_detail": bool(c.get("detail", False)),
                "unique": flags[j] if j < len(flags) else False,
            }
            for j, c in enumerate(stage2["claims_map"].get(cid, [])[:ANSWER_CLAIM_CAP])
        ]

    # 未入簇主张（少数派报告展开列表用）
    NOISE_CAP = 20
    noise_claims_view = [
        {
            "claim": claims[i]["claim"],
            "author": meta_by_id.get(claims[i]["content_id"], {}).get("author", "匿名"),
            "content_id": claims[i]["content_id"],
            "url": meta_by_id.get(claims[i]["content_id"], {}).get("url", ""),
        }
        for i in range(len(claims))
        if labels[i] == -1
    ][:NOISE_CAP]

    result = {
        "title": title,
        "question_url": question_url,
        "sample_size": len(samples),
        "claim_total": len(claims),
        "cluster_count": n_clusters,
        "noise_claim_count": n_noise,
        "noise_claims": noise_claims_view,
        "clusters": clusters_view,
        "answers": [
            {
                **s,
                "dominant_cluster": dominant(s["content_id"]),
                "position": positions.get(s["content_id"], {"x": 0.0, "y": 0.0}),
                "excavation_reason": reasons.get(s["content_id"], ""),
                "claims": claim_items(s["content_id"]),
            }
            for s in scored
        ],
        "top_undervalued": [s["content_id"] for s in top_undervalued],
        "minority_report": [s["content_id"] for s in minority[:topn]],
        "monopoly": monopoly,
        "metrics_note": "U = percentile(V) − percentile(L∞)；样本为公开搜索可达回答；发布<7天进观察池不做低估判定",
    }
    out_path = save_cache(key, "6_report", result)
    print(f"\n完成。低估 Top {topn}（U = 价值百分位 - 曝光百分位）：")
    for rank, s in enumerate(top_undervalued, 1):
        badges = " ".join(f"[{b}]" for b in s["badges"]) if s["badges"] else ""
        print(f"  {rank}. [{s['votes']}赞] {s['author']} | V={s['info_score']:.3f} "
              f"U={s['underestimate_index']:+.3f} {badges} | {s['text'][:36]}…")
    if monopoly:
        print(f"议题垄断度：Top{monopoly['top_k']} 占曝光 {monopoly['exposure_share_top_k']:.0%}，"
              f"贡献信息增量 {monopoly['info_increment_share_top_k']:.0%}，差值 {monopoly['monopoly_gap']:+.0%}")
    print(f"结果已写入：{out_path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="知寻 ZhiSeek —— 知乎低估回答探测器 pipeline")
    parser.add_argument("title", help="议题标题（如：近视手术安全吗）")
    parser.add_argument("--question-url", help="对应知乎问题 URL，启用枚举通道（可选）")
    parser.add_argument("--topn", type=int, default=10, help="低估榜条数（默认 10）")
    parser.add_argument("--force", action="store_true", help="忽略缓存全量重跑")
    parser.add_argument("--no-llm", action="store_true", help="不调直答（调试用，主张=整段摘要）")
    args = parser.parse_args()
    run(args.title, args.question_url, args.topn, args.force, args.no_llm)


if __name__ == "__main__":
    main()
