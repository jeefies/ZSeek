"""知乎官方 API 客户端：搜索（曝光通道）+ 问题回答枚举（枚举通道）+ 额度查询。

实测注意事项（详见 技术方案.md）：
- 中文查询必须走 Python httpx，禁止 shell curl（GBK 编码会触发 90001）
- zhihu_search 每次最多 10 条、HasMore 固定 false，ContentText 为 ~1000 字截断摘要
- question_answers 文档未记载但可用，深分页，Summary ~200 字，无赞同数
"""
import json
import time
import hashlib
from pathlib import Path
from typing import Any

import httpx

from . import config


class ZhihuClient:
    def __init__(self) -> None:
        self._client = httpx.Client(base_url=config.ZHIHU_BASE, timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {config.zhihu_token()}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            resp = self._client.get(path, params=params, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            if data.get("Code") == 0:
                return data
            last_err = RuntimeError(f"知乎接口错误 Code={data.get('Code')} Message={data.get('Message')} path={path}")
            # 30001 为突发限速：退避重试；其余错误立即抛
            if data.get("Code") != 30001:
                raise last_err
            if attempt < retries:
                time.sleep(15)
        raise last_err  # type: ignore[misc]

    # ---- 曝光通道 ----
    def zhihu_search(self, query: str, count: int = config.SEARCH_COUNT) -> list[dict[str, Any]]:
        data = self._get(config.API_SEARCH, {"Query": query, "Count": count})
        return (data.get("Data") or {}).get("Items", [])

    # ---- 枚举通道 ----
    def question_answers(self, question_url: str, limit: int, offset: int) -> dict[str, Any]:
        return self._get(
            config.API_QUESTION_ANSWERS,
            {"QuestionUrl": question_url, "Limit": limit, "Offset": offset},
        )

    # ---- 额度 ----
    def quota(self) -> list[dict[str, Any]]:
        data = self._get(config.API_QUOTA, {})
        return data.get("Data", [])

    def close(self) -> None:
        self._client.close()


def topic_key(title: str) -> str:
    """议题缓存 key。"""
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]


def _cache_path(key: str, stage: str) -> Path:
    path = config.CACHE_DIR / key / f"{stage}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_cache(key: str, stage: str) -> Any | None:
    path = _cache_path(key, stage)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_cache(key: str, stage: str, data: Any) -> Path:
    path = _cache_path(key, stage)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------- 抓取逻辑 ----------------

def default_variants(title: str, n: int = config.SEARCH_VARIANTS_DEFAULT) -> list[str]:
    """生成子查询变体：原标题 + 规则改写，尽量打散搜索结果的头部重叠。"""
    variants: list[str] = []

    def add(v: str) -> None:
        v = v.strip()
        if v and v not in variants:
            variants.append(v)

    add(title)
    add(f"如何看待{title.rstrip('？?')}")
    add(f"{title} 知乎")
    # 去掉疑问尾词后的核心词查询（召回不同子集）
    core = title.rstrip("？?吗呢吧嘛").strip()
    if core and core != title.rstrip("？?"):
        add(core)
    add(f"{title} 经验 靠谱吗")
    return variants[: max(n, 3)]


def fetch_search_samples(client: ZhihuClient, title: str, variants: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    """曝光通道：多查询聚合 → 去重 → 只留回答。每个元素是标准化回答 dict。

    同时从搜索结果中识别原问题链接（Question 类型条目），供枚举通道使用。
    返回 (回答样本, 问题链接猜测)。
    """
    seen: dict[str, dict[str, Any]] = {}
    question_candidates: list[dict[str, str]] = []
    for query in variants:
        for item in client.zhihu_search(query):
            url = str(item.get("Url") or "")
            if "/question/" in url and "/answer/" not in url:
                question_candidates.append({"title": str(item.get("Title") or ""), "url": url})
                continue
            if item.get("ContentType") != config.KEEP_CONTENT_TYPE:
                continue
            cid = str(item.get("ContentID") or "")
            if not cid or cid in seen:
                continue
            seen[cid] = {
                "content_id": cid,
                "question_title": item.get("Title", ""),
                "text": item.get("ContentText", ""),
                "votes": item.get("VoteUpCount", 0) or 0,
                "comments": item.get("CommentCount", 0) or 0,
                "author": item.get("AuthorName", ""),
                "edit_time": item.get("EditTime", 0) or 0,
                "url": url,
            }
    # 问题链接猜测：优先标题完全匹配，其次互相包含
    core = title.rstrip("？?").strip()
    guess = None
    for c in question_candidates:
        if c["title"].rstrip("？?").strip() == core:
            guess = c["url"]
            break
    if guess is None:
        for c in question_candidates:
            ct = c["title"].rstrip("？?").strip()
            if ct and (ct in core or core in ct):
                guess = c["url"]
                break
    if guess is None and question_candidates:
        guess = question_candidates[0]["url"]
    # 回退：从回答 URL 反推问题链接（/question/xxx/answer/yyy → /question/xxx），
    # 优先选取与议题标题相匹配的问题
    if guess is None:
        groups: dict[str, dict[str, Any]] = {}
        for a in seen.values():
            u = str(a.get("url") or "").split("?")[0]
            if "/answer/" not in u:
                continue
            qurl = u.split("/answer/")[0]
            g = groups.setdefault(qurl, {"count": 0, "match": False})
            g["count"] += 1
            qt = str(a.get("question_title") or "").rstrip("？?").strip()
            if qt and (qt == core or qt in core or core in qt):
                g["match"] = True
        if groups:
            best = sorted(groups.items(), key=lambda kv: (kv[1]["match"], kv[1]["count"]), reverse=True)[0]
            guess = best[0]
    return list(seen.values()), guess


def enumerate_question(client: ZhihuClient, question_url: str) -> dict[str, Any]:
    """枚举通道：拉取问题回答清单（含短摘要），用于全量叙事与 join 去重。"""
    items: list[dict[str, Any]] = []
    offset = 0
    calls = 0
    is_end = False
    while not is_end and len(items) < config.QA_MAX_ITEMS and calls < config.QA_MAX_ITEMS // config.QA_PAGE_LIMIT + 2:
        data = client.question_answers(question_url, config.QA_PAGE_LIMIT, offset)
        page = data.get("Data") or {}
        batch = page.get("Items", [])
        items.extend(batch)
        paging = page.get("Paging") or {}
        is_end = paging.get("IsEnd", True)
        offset = paging.get("NextOffset", offset + len(batch))
        calls += 1
        if not batch:
            break
    return {
        "question_url": question_url,
        "is_end": is_end,
        "fetched": len(items),
        "items": [
            {
                "content_token": str(it.get("ContentToken") or ""),
                "url": it.get("Url", ""),
                "summary": it.get("Summary", ""),
            }
            for it in items
        ],
    }


def merge_enumeration_samples(samples: list[dict[str, Any]], enumeration: dict[str, Any]) -> list[dict[str, Any]]:
    """把枚举通道的回答（仅 200 字摘要、无赞同数）并入样本，按 url 去重。

    枚举样本 votes_unknown=True：参与聚类与主张分析，但不进低估判定（无曝光数据）。
    """
    known_urls = {s["url"].split("?")[0].rstrip("/") for s in samples}
    out = list(samples)
    for it in enumeration.get("items", []):
        url = (it.get("url") or "").split("?")[0].rstrip("/")
        if not url or url in known_urls:
            continue
        known_urls.add(url)
        summary = (it.get("summary") or "").strip()
        if len(summary) < 20:
            continue
        out.append({
            "content_id": f"enum_{it.get('content_token') or len(out)}",
            "question_title": samples[0]["question_title"] if samples else "",
            "text": summary,
            "votes": 0,
            "comments": 0,
            "author": "",
            "edit_time": 0,
            "url": it.get("url", ""),
            "votes_unknown": True,
        })
    return out


def check_quota(client: ZhihuClient) -> dict[str, int]:
    """运行前检查关键额度，返回 {api_id: remaining}。"""
    watch = {"zhida_openai", "question_answers", "zhihu_search"}
    remaining = {}
    for item in client.quota():
        api_id = item.get("APIID", "")
        if api_id in watch:
            remaining[api_id] = item.get("RemainingQuota", 0)
            total = item.get("TotalQuota", 0) or 1
            if remaining[api_id] < 0.2 * total:
                print(f"[警告] {api_id} 额度不足 20%：剩余 {remaining[api_id]}/{total}")
    return remaining
