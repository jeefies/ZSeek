"""知寻后端：FastAPI + SSE 进度推送（比赛规格）。

- POST /api/analyze {title, question_url?} → 启动后台分析任务（job_id = 议题缓存 key）
- GET  /api/jobs/{key}/events → SSE 流：stage 进度事件 → done/error
- GET  /api/result/{key} → 6_report 最终 JSON
- GET  /api/topics → 已有缓存结果的议题列表（保底案例入口）

启动：python -m uvicorn backend.app:app --port 8000
前端（Next.js dev）走 CORS 白名单 http://localhost:3000。
"""
import asyncio
import json
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


class AnalyzeReq(BaseModel):
    title: str
    question_url: str | None = None


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


@app.post("/api/analyze")
def analyze(req: AnalyzeReq) -> dict:
    key = topic_key(req.title)
    with JOB_LOCK:
        job = JOBS.get(key)
        if job and job["status"] == "running":
            return {"job_id": key, "status": "running"}
        JOBS[key] = {"status": "running", "title": req.title, "error": None}

    def work() -> None:
        try:
            pipeline_run(req.title, req.question_url or None, topn=10, force=False, no_llm=False)
            JOBS[key]["status"] = "done"
        except BaseException as e:  # noqa: BLE001 - 含 SystemExit
            JOBS[key]["status"] = "error"
            JOBS[key]["error"] = str(e) or e.__class__.__name__

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": key, "status": "running"}


@app.get("/api/jobs/{key}/events")
def job_events(key: str) -> EventSourceResponse:
    """SSE：阶段进度事件。事件名 stage / done / error，数据为 JSON。"""

    async def stream():
        emitted: set[str] = set()
        for _ in range(600):  # 最长 10 分钟
            status = _job_status(key)
            done, current = _stage_progress(key)
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
