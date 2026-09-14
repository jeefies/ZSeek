"""知寻后端：FastAPI + SSE 进度推送（比赛规格）。

- POST /api/analyze {title, question_url?} → 启动后台分析任务（job_id = 议题缓存 key）
- GET  /api/jobs/{key}/events → SSE 流：stage 进度事件 → done/error
- GET  /api/result/{key} → 6_report 最终 JSON
- GET  /api/topics → 已有缓存结果的议题列表（保底案例入口）

启动：python -m uvicorn backend.app:app --port 8000
前端（Next.js dev）走 CORS 白名单 http://localhost:3000。
"""
import asyncio
import itertools
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from pipeline import config
from pipeline.fetch import topic_key, load_cache
from pipeline.run import run as pipeline_run

app = FastAPI(title="知寻 ZhiSeek API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STAGE_FILES = ["1_search.json", "2_claims.json", "3_embeddings.npy", "4_cluster.json", "4b_freshness.json", "5_score.json", "6a_names.json", "6_report.json"]
STAGE_NAMES = {
    "1_search.json": "搜索抽样",
    "2_claims.json": "LLM 拆主张",
    "3_embeddings.npy": "本地 embedding",
    "4_cluster.json": "观点聚类",
    "4b_freshness.json": "时效判定",
    "5_score.json": "指标计算",
    "6a_names.json": "簇命名",
    "6_report.json": "生成报告",
}

JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()
# 全局并发闸：同时跑的 pipeline 上限（知乎突发限速 30001 的保险），超限任务排队等位
ANALYZE_CONCURRENCY = max(1, int(os.environ.get("ZSEEK_ANALYZE_CONCURRENCY", "2")))
JOB_SEM = threading.Semaphore(ANALYZE_CONCURRENCY)
JOB_SEQ = itertools.count(1)


class AnalyzeReq(BaseModel):
    title: str
    question_url: str | None = None
    include_answers: list[dict] | None = None  # 主动注入的回答（个人遗珠：低赞回答不进搜索样本）


def _stage_progress(key: str) -> tuple[int, str | None]:
    """返回 (已完成阶段数, 当前进行中的阶段名)。"""
    done = 0
    current = None
    for s in STAGE_FILES:
        if (config.CACHE_DIR / key / s).exists():
            done += 1
        elif current is None:
            current = STAGE_NAMES[s]
    return done, current


def _job_status(key: str) -> str:
    j = JOBS.get(key)
    if j:
        return j["status"]
    return "done" if (config.CACHE_DIR / key / "6_report.json").exists() else "idle"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/topics")
def topics() -> list[dict]:
    out = []
    if config.CACHE_DIR.exists():
        for d in sorted(config.CACHE_DIR.iterdir()):
            r = load_cache(d.name, "6_report")
            if r:
                pearls = sum(1 for a in r.get("answers", []) if "沧海遗珠" in (a.get("badges") or []))
                out.append({"key": d.name, "title": r.get("title", ""), "sample_size": r.get("sample_size", 0), "pearl_count": pearls})
    return out


@app.get("/api/pearls")
def pearls() -> list[dict]:
    """跨议题最被低估的回答 Top 12：沧海遗珠徽章优先，其余按低估指数 U 降序补位。"""
    flagged: list[dict] = []
    rest: list[dict] = []
    if config.CACHE_DIR.exists():
        for d in sorted(config.CACHE_DIR.iterdir()):
            r = load_cache(d.name, "6_report")
            if not r:
                continue
            title = r.get("title", "")
            for a in r.get("answers", []):
                u = a.get("underestimate_index")
                if u is None:
                    continue
                item = {
                    "key": d.name,
                    "q_title": title,
                    "author": a.get("author") or "匿名",
                    "reason": a.get("excavation_reason") or "",
                    "u": u,
                    "votes": a.get("votes"),
                    "badges": a.get("badges") or [],
                }
                (flagged if "沧海遗珠" in item["badges"] else rest).append(item)
    flagged.sort(key=lambda x: x["u"], reverse=True)
    rest.sort(key=lambda x: x["u"], reverse=True)
    return (flagged + rest)[:12]


@app.get("/api/hot-board")
def hot_board() -> list[dict]:
    """垄断度热榜：数据战役排行榜（点击直达缓存结果，秒开）。"""
    rank_file = config.DATA_DIR / "campaign" / "monopoly_rank.json"
    if not rank_file.exists():
        return []
    try:
        rank = json.loads(rank_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out = []
    for r in rank:
        key = r.get("key")
        if not key:
            continue
        out.append({
            "key": key,
            "title": r.get("title", ""),
            "monopoly_gap": r.get("monopoly_gap"),
            "exposure_share_top_k": r.get("exposure_share_top_k"),
            "info_increment_share_top_k": r.get("info_increment_share_top_k"),
            "sample_size": r.get("sample_size"),
            "pearl_count": r.get("pearl_count"),
            "ready": (config.CACHE_DIR / key / "6_report.json").exists(),
        })
    return out


@app.post("/api/analyze")
def analyze(req: AnalyzeReq) -> dict:
    key = topic_key(req.title)
    with JOB_LOCK:
        job = JOBS.get(key)
        if job and job["status"] in ("queued", "running"):
            return {"job_id": key, "status": job["status"]}
        JOBS[key] = {"status": "queued", "title": req.title, "error": None, "seq": next(JOB_SEQ)}

    def work() -> None:
        with JOB_SEM:  # 排队等并发槽位
            with JOB_LOCK:
                if JOBS.get(key, {}).get("status") != "queued":
                    return
                JOBS[key]["status"] = "running"
            try:
                pipeline_run(req.title, req.question_url or None, topn=10, force=False, no_llm=False,
                             include_answers=req.include_answers or None)
                JOBS[key]["status"] = "done"
            except BaseException as e:  # noqa: BLE001 - 含 SystemExit
                JOBS[key]["status"] = "error"
                JOBS[key]["error"] = str(e) or e.__class__.__name__

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": key, "status": "queued"}


@app.get("/api/jobs/{key}/status")
def job_status(key: str) -> dict:
    """轻量任务状态查询（供 personal-u 轮询：queued/running/done/error/none，queued 附排队位次）。"""
    with JOB_LOCK:
        job = JOBS.get(key)
        if not job:
            return {"status": "none"}
        pos = None
        if job["status"] == "queued":
            pos = sum(1 for j in JOBS.values()
                      if j.get("status") == "queued" and j.get("seq", 0) < job.get("seq", 0))
        done, current = _stage_progress(key)
        # 拆主张阶段才把「当前文章」透给前端（1_search 已落、2_claims 未落时才有效，防早期误显示）
        current_article = None
        article_done = None
        article_total = None
        if job["status"] == "running" and (config.CACHE_DIR / key / "1_search.json").exists() \
                and not (config.CACHE_DIR / key / "2_claims.json").exists():
            prog_path = config.CACHE_DIR / key / "2_progress.json"
            if prog_path.exists():
                try:
                    prog = json.loads(prog_path.read_text(encoding="utf-8"))
                    current_article = prog.get("current")
                    article_done = prog.get("done")
                    article_total = prog.get("total")
                except (json.JSONDecodeError, OSError):
                    pass
        return {"status": job["status"], "error": job.get("error"), "position": pos,
                "stage_done": done, "stage_total": len(STAGE_FILES), "stage_current": current,
                "current_article": current_article, "article_done": article_done, "article_total": article_total}


@app.get("/api/jobs/{key}/events")
def job_events(key: str) -> EventSourceResponse:
    """SSE：阶段进度事件。事件名 stage / done / error，数据为 JSON。"""

    async def stream():
        emitted: set[str] = set()
        last_prog: dict | None = None
        for _ in range(600):  # 最长 10 分钟
            status = _job_status(key)
            done, current = _stage_progress(key)
            # 拆主张逐篇进度：文件在变就推（前端显示「正在拆哪篇」）
            prog_path = config.CACHE_DIR / key / "2_progress.json"
            if prog_path.exists():
                try:
                    prog = json.loads(prog_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    prog = None
                if prog and prog != last_prog:
                    last_prog = prog
                    yield {"event": "progress", "data": json.dumps(
                        {"article": prog.get("current"), "done": prog.get("done"), "total": prog.get("total")},
                        ensure_ascii=False)}
            for s in STAGE_FILES:
                if s not in emitted and (config.CACHE_DIR / key / s).exists():
                    emitted.add(s)
                    yield {"event": "stage", "data": json.dumps(
                        {"stage": s, "name": STAGE_NAMES[s], "done": len(emitted), "total": len(STAGE_FILES)},
                        ensure_ascii=False)}
            if status == "error":
                yield {"event": "error", "data": json.dumps({"message": JOBS.get(key, {}).get("error") or "分析失败"}, ensure_ascii=False)}
                return
            if status == "done":
                # 任务标记完成即可下发 done（旧缓存可能缺少 6a_names 等进度标记文件）
                yield {"event": "done", "data": json.dumps({"key": key}, ensure_ascii=False)}
                return
            await asyncio.sleep(0.7)
        yield {"event": "error", "data": json.dumps({"message": "分析超时"}, ensure_ascii=False)}

    return EventSourceResponse(stream())


@app.get("/api/result/{key}")
def result(key: str) -> dict:
    data = load_cache(key, "6_report")
    if data is None:
        raise HTTPException(status_code=404, detail="结果不存在或尚未生成")
    return data


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
