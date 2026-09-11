"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ScatterMap, { type SortLens } from "@/components/ScatterMap";
import AnalyzingView from "@/components/AnalyzingView";
import AnswerArchive, { Badge } from "@/components/AnswerArchive";
import { CLUSTER_COLORS } from "@/lib/theme";
import { fetchResult, fetchTopics, jobEventsUrl, startAnalyze } from "@/lib/api";
import type { Answer, Result, StageEvent, TopicInfo } from "@/lib/types";

type View = "home" | "analyzing" | "dashboard";

export default function Home() {
  const [title, setTitle] = useState("");
  const [view, setView] = useState<View>("home");
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageEvent[]>([]);
  const [goldLine, setGoldLine] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [topics, setTopics] = useState<TopicInfo[]>([]);
  const [lens, setLens] = useState<SortLens>("info");
  const [focusCluster, setFocusCluster] = useState<number | null>(null);
  const [openMinor, setOpenMinor] = useState<number | "noise" | null>(null);
  const [archiveId, setArchiveId] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopEvents = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  useEffect(() => () => {
    stopEvents();
    if (timerRef.current) clearTimeout(timerRef.current);
  }, [stopEvents]);

  useEffect(() => {
    fetchTopics().then(setTopics).catch(() => {});
  }, [view]);

  const analyze = async () => {
    const t = title.trim();
    if (!t || running) return;
    stopEvents();
    if (timerRef.current) clearTimeout(timerRef.current);
    setRunning(true);
    setStages([]);
    setGoldLine(null);
    setError(null);
    setResult(null);
    setFocusCluster(null);
    setOpenMinor(null);
    setArchiveId(null);
    setLens("info");
    setView("analyzing");
    try {
      const { job_id } = await startAnalyze(t);
      const es = new EventSource(jobEventsUrl(job_id));
      esRef.current = es;
      es.addEventListener("stage", (e) => {
        const evt = JSON.parse((e as MessageEvent).data) as StageEvent;
        setStages((prev) => [...prev.filter((s) => s.stage !== evt.stage), evt]);
      });
      es.addEventListener("done", async () => {
        stopEvents();
        try {
          const r = await fetchResult(job_id);
          setResult(r);
          const pearls = r.answers.filter((a) => (a.badges ?? []).includes("沧海遗珠")).length;
          setGoldLine(`> ✦ 发现 ${pearls} 篇沧海遗珠 · ${r.noise_claim_count} 条少数派主张`);
          timerRef.current = setTimeout(() => {
            setView("dashboard");
            setRunning(false);
          }, 1600);
        } catch (err) {
          setError(err instanceof Error ? err.message : "获取结果失败");
          setRunning(false);
        }
      });
      es.addEventListener("error", (e) => {
        if (e instanceof MessageEvent) {
          try {
            const data = JSON.parse(e.data);
            setError(data.message || "分析失败");
          } catch {
            setError("样本过少或接口异常，换个更热门的议题试试");
          }
          stopEvents();
          setRunning(false);
        }
        // 裸网络错误（无 data）：交给浏览器自动重连，不关闭流
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动失败");
      setRunning(false);
    }
  };

  const loadTopic = async (key: string) => {
    try {
      setResult(await fetchResult(key));
      setFocusCluster(null);
      setOpenMinor(null);
      setArchiveId(null);
      setLens("info");
      setView("dashboard");
    } catch {
      /* 缓存损坏则忽略 */
    }
  };

  const byId = new Map(result?.answers.map((a) => [a.content_id, a]) ?? []);
  const maxV = Math.max(...(result?.answers.map((a) => a.info_score) ?? [1]), 0.0001);
  // 价值百分位
  const vSorted = result ? [...result.answers].map((a) => a.info_score).sort((x, y) => x - y) : [];
  const valuePct = (a: Answer) =>
    vSorted.length ? Math.round((vSorted.filter((v) => v <= a.info_score).length / vSorted.length) * 100) : 0;

  const archive = archiveId ? byId.get(archiveId) ?? null : null;
  const m = result?.monopoly;
  const pearlTotal = topics.reduce((s, t) => s + (t.pearl_count ?? 0), 0);
  // 观点簇：降序排列；≤2 条的微阵营归入少数派报告
  const sortedClusters = result ? [...result.clusters].sort((a, b) => b.claim_count - a.claim_count) : [];
  const mainClusters = sortedClusters.filter((c) => c.claim_count > 2);
  const tinyClusters = sortedClusters.filter((c) => c.claim_count <= 2);
  // 颜色与星图一致：按原始簇序取色
  const clusterColor = (clusterId: number) =>
    result
      ? CLUSTER_COLORS[Math.max(0, result.clusters.findIndex((c) => c.cluster === clusterId)) % CLUSTER_COLORS.length]
      : CLUSTER_COLORS[0];

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-base text-ink">
      {/* 页头：固定 Slogan */}
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-4">
        <span className="text-sm font-semibold tracking-wide">知寻 ZhiSeek</span>
        <span className="text-xs text-dim">知寻 —— 寻找被低估的声音</span>
        {view === "dashboard" && (
          <div className="ml-auto flex items-center gap-2">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && analyze()}
              placeholder="再寻一个问题…"
              className="w-52 rounded-lg border border-line bg-base px-3 py-1 text-xs text-ink outline-none placeholder:text-dim focus:border-gold"
            />
            <button
              onClick={analyze}
              disabled={running}
              className="rounded-lg bg-gold px-3 py-1 text-xs font-semibold text-[#1F2328] disabled:opacity-40"
            >
              寻
            </button>
          </div>
        )}
      </header>

      {/* ============ 页面 1：首页 ============ */}
      {view === "home" && (
        <main className="flex flex-1 flex-col items-center justify-center gap-8 overflow-y-auto px-6 py-8">
          <div className="text-center">
            <h1 className="text-xl font-semibold">每个问题里，都有没被看见的好回答</h1>
            <p className="mt-2 text-sm text-dim">
              一片深色的知识海洋里，大多数星挤在银河中——知寻帮你找到那些独自发光的星
            </p>
          </div>
          <div className="flex w-[min(560px,92%)] gap-2">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && analyze()}
              placeholder="输入一个知乎问题，如：近视手术安全吗"
              autoFocus
              className="flex-1 rounded-xl border border-line bg-panel px-4 py-3 text-base text-ink outline-none placeholder:text-dim focus:border-gold"
            />
            <button
              onClick={analyze}
              disabled={running}
              className="rounded-xl bg-gold px-8 text-base font-semibold text-[#1F2328] disabled:opacity-40"
            >
              寻
            </button>
          </div>
          <div className="font-mono text-xs text-dim">
            已分析 {topics.length} 个问题 · 挖出 {pearlTotal} 篇遗珠
          </div>
          {topics.length > 0 && (
            <div className="flex max-w-[80%] flex-wrap justify-center gap-2">
              {topics.map((t) => (
                <button
                  key={t.key}
                  onClick={() => loadTopic(t.key)}
                  className="rounded-full border border-line bg-panel px-3 py-1 text-xs text-dim hover:border-gold hover:text-ink"
                >
                  {t.title}
                </button>
              ))}
            </div>
          )}
        </main>
      )}

      {/* ============ 页面 2：分析中 ============ */}
      {view === "analyzing" && <AnalyzingView stages={stages} goldLine={goldLine} error={error} />}

      {/* ============ 页面 3：结果仪表盘（三栏） ============ */}
      {view === "dashboard" && result && m && (
        <main className="flex min-h-0 flex-1 gap-3 p-3">
          {/* 左栏 20%：问题档案 */}
          <aside className="flex w-[20%] min-w-[210px] shrink-0 flex-col gap-4 overflow-y-auto rounded-xl border border-line bg-panel p-4">
            <div>
              <h2 className="text-base font-semibold leading-6">{result.title}</h2>
              <div className="mt-2 font-mono text-xs text-dim">
                <div>样本 {result.sample_size} 篇回答</div>
                <div>{result.claim_total} 条主张 · {result.cluster_count} 个阵营</div>
                {m.question_total_answers ? <div>全问题 {m.question_total_answers}+ 回答</div> : null}
              </div>
            </div>

            {/* 垄断度仪表 */}
            <div>
              <div className="text-xs text-dim">议题垄断度</div>
              <div className="mt-1 font-mono text-xl text-ink">{(m.monopoly_gap ?? 0).toFixed(2)}</div>
              <div className="mt-2 space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="w-8 shrink-0 text-[10px] text-dim">曝光</span>
                  <div className="h-1 flex-1 bg-line">
                    <div className="h-full bg-[#5B7FA6]" style={{ width: `${(m.exposure_share_top_k ?? 0) * 100}%` }} />
                  </div>
                  <span className="w-9 shrink-0 text-right font-mono text-[10px] text-dim">
                    {((m.exposure_share_top_k ?? 0) * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-8 shrink-0 text-[10px] text-dim">增量</span>
                  <div className="h-1 flex-1 bg-line">
                    <div className="h-full bg-[#5BA694]" style={{ width: `${(m.info_increment_share_top_k ?? 0) * 100}%` }} />
                  </div>
                  <span className="w-9 shrink-0 text-right font-mono text-[10px] text-dim">
                    {((m.info_increment_share_top_k ?? 0) * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
              <p className="mt-1.5 text-[10px] leading-4 text-dim">
                热度 Top{m.top_k} 占 {((m.exposure_share_top_k ?? 0) * 100).toFixed(0)}% 曝光，仅贡献{" "}
                {((m.info_increment_share_top_k ?? 0) * 100).toFixed(0)}% 信息增量
              </p>
            </div>

            {/* 观点簇图例：点击聚焦 */}
            <div>
              <div className="mb-1.5 text-xs text-dim">观点簇（点击聚焦 · 按主张数降序）</div>
              <div className="space-y-1">
                {mainClusters.map((c) => {
                  const active = focusCluster === c.cluster;
                  return (
                    <button
                      key={c.cluster}
                      onClick={() => setFocusCluster(active ? null : c.cluster)}
                      className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs ${
                        active ? "border-gold/60 bg-gold/5 text-ink" : "border-transparent text-dim hover:bg-base hover:text-ink"
                      }`}
                    >
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: clusterColor(c.cluster) }} />
                      <span className="min-w-0 flex-1 truncate">{c.label}</span>
                      <span className="shrink-0 font-mono text-[10px]">{c.claim_count}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* 少数派报告：未入簇主张 + 微阵营（≤2 条主张，可展开） */}
            <div>
              <div className="mb-1.5 text-xs text-dim">⚡ 少数派报告</div>
              <p className="mb-2 text-[10px] leading-4 text-dim">
                {result.noise_claim_count} 条主张未入任何阵营 · {tinyClusters.length} 个微阵营（≤2 条主张）
              </p>
              <div className="space-y-1">
                {tinyClusters.map((c) => {
                  const open = openMinor === c.cluster;
                  return (
                    <div key={c.cluster} className="rounded-md bg-base text-xs">
                      <button
                        onClick={() => setOpenMinor(open ? null : c.cluster)}
                        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-dim hover:text-ink"
                      >
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: clusterColor(c.cluster) }} />
                        <span className="min-w-0 flex-1 truncate">{c.label}</span>
                        <span className="shrink-0 font-mono text-[10px]">
                          {c.claim_count} {open ? "▾" : "▸"}
                        </span>
                      </button>
                      {open && (
                        <ul className="space-y-1 border-t border-line px-2 py-1.5">
                          {(c.claims ?? []).map((cl, j) => (
                            <li key={j}>
                              <div className="leading-4 text-dim">{cl.claim}</div>
                              <button onClick={() => setArchiveId(cl.content_id)} className="text-[10px] text-gold hover:underline">
                                — {cl.author || "匿名"}（看这篇回答）
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
                {(result.noise_claims?.length ?? 0) > 0 && (
                  <div className="rounded-md bg-base text-xs">
                    <button
                      onClick={() => setOpenMinor(openMinor === "noise" ? null : "noise")}
                      className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-dim hover:text-ink"
                    >
                      <span className="h-2 w-2 shrink-0 rounded-full bg-dot" />
                      <span className="min-w-0 flex-1 truncate">未入簇主张（独树一帜）</span>
                      <span className="shrink-0 font-mono text-[10px]">
                        {result.noise_claim_count}
                        {result.noise_claims!.length < result.noise_claim_count ? "+" : ""} {openMinor === "noise" ? "▾" : "▸"}
                      </span>
                    </button>
                    {openMinor === "noise" && (
                      <ul className="space-y-1 border-t border-line px-2 py-1.5">
                        {result.noise_claims!.map((cl, j) => (
                          <li key={j}>
                            <div className="leading-4 text-dim">{cl.claim}</div>
                            <button onClick={() => setArchiveId(cl.content_id)} className="text-[10px] text-gold hover:underline">
                              — {cl.author || "匿名"}（看这篇回答）
                            </button>
                          </li>
                        ))}
                        {result.noise_claims!.length < result.noise_claim_count && (
                          <li className="text-[10px] text-dim">仅展示前 {result.noise_claims!.length} 条</li>
                        )}
                      </ul>
                    )}
                  </div>
                )}
                {result.minority_report.slice(0, 3).map((id) => {
                  const a = byId.get(id);
                  if (!a) return null;
                  return (
                    <button
                      key={id}
                      onClick={() => setArchiveId(id)}
                      className="block w-full truncate rounded-md px-2 py-1 text-left text-xs text-dim hover:bg-base hover:text-ink"
                    >
                      ★ {a.author || "匿名"} · 独占主张 {a.unique_claims}
                    </button>
                  );
                })}
              </div>
            </div>
          </aside>

          {/* 中央 55%：星图 */}
          <section className="flex min-w-0 flex-1 flex-col rounded-xl border border-line bg-panel">
            <div className="flex shrink-0 items-center gap-3 px-4 pt-2.5 text-xs text-dim">
              <span className="font-semibold text-ink">观点星图</span>
              <span>点大小∝{lens === "info" ? "时间修正曝光 L∞" : "赞同"}</span>
              <span className="text-gold">★ 沧海遗珠</span>
              <span className="ml-auto hidden sm:inline">滚轮 / 框选缩放 · 点击取出档案</span>
            </div>
            <div className="min-h-0 flex-1">
              <ScatterMap
                answers={result.answers}
                clusters={result.clusters}
                focusCluster={focusCluster}
                lens={lens}
                onSelect={(a) => setArchiveId(a.content_id)}
              />
            </div>
          </section>

          {/* 右栏 25%：遗珠清单 */}
          <aside className="flex w-[25%] min-w-[260px] shrink-0 flex-col rounded-xl border border-line bg-panel">
            <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
              <span className="text-sm font-semibold">🔦 遗珠清单</span>
              {/* 排序切换（segmented control） */}
              <div className="ml-auto flex rounded-md border border-line font-mono text-xs">
                {(
                  [
                    ["info", "按增量"],
                    ["votes", "按热度"],
                  ] as [SortLens, string][]
                ).map(([k, name]) => (
                  <button
                    key={k}
                    onClick={() => setLens(k)}
                    className={`px-2.5 py-1 ${lens === k ? "bg-gold font-semibold text-[#1F2328]" : "text-dim hover:text-ink"}`}
                  >
                    {name}
                  </button>
                ))}
              </div>
            </div>
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
              {(lens === "info"
                ? result.top_undervalued.map((id) => byId.get(id)).filter((a): a is Answer => !!a)
                : [...result.answers].sort((a, b) => b.votes - a.votes)
              ).map((a, i) => {
                const isPearl = (a.badges ?? []).includes("沧海遗珠");
                return (
                  <button
                    key={a.content_id}
                    onClick={() => setArchiveId(a.content_id)}
                    className={`block w-full rounded-lg border bg-base p-3 text-left transition-all duration-300 ${
                      isPearl ? "animate-breathe" : "border-line hover:border-dim"
                    }`}
                  >
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-xs text-dim">#{i + 1}</span>
                      <b className="min-w-0 flex-1 truncate">{a.author || "匿名"}</b>
                      {(a.badges ?? []).map((b) => <Badge key={b} name={b} />)}
                    </div>
                    {a.excavation_reason && (
                      <p className="mt-1.5 text-xs leading-5 text-dim">💡 {a.excavation_reason}</p>
                    )}
                    <div className="mt-1 font-mono text-[10px] text-dim">
                      {a.votes_unknown ? "赞同数未知" : `${a.votes} 赞`} · 价值超过 {valuePct(a)}% 回答
                      {a.underestimate_index !== null && ` · U=${a.underestimate_index >= 0 ? "+" : ""}${a.underestimate_index.toFixed(2)}`}
                    </div>
                  </button>
                );
              })}
              <p className="pt-1 text-center font-mono text-[10px] text-dim">{result.metrics_note}</p>
            </div>
          </aside>
        </main>
      )}

      {/* 原文档案弹层（纸感） */}
      {archive && <AnswerArchive a={archive} maxV={maxV} onClose={() => setArchiveId(null)} />}
    </div>
  );
}
