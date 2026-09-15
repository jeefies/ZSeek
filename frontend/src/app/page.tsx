"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ScatterMap, { type SortLens } from "@/components/ScatterMap";
import AnalyzingView from "@/components/AnalyzingView";
import AnswerArchive, { Badge } from "@/components/AnswerArchive";
import PersonalBoard from "@/components/PersonalBoard";
import { CLUSTER_COLORS } from "@/lib/theme";
import { fetchHotBoard, fetchPearls, fetchResult, fetchTopics, jobEventsUrl, startAnalyze } from "@/lib/api";
import type { HotBoardItem, PearlItem } from "@/lib/api";
import type { Answer, Result, StageEvent, TopicInfo } from "@/lib/types";

type View = "home" | "analyzing" | "dashboard" | "topics";

export default function Home() {
  const [title, setTitle] = useState("");
  const [view, setView] = useState<View>("home");
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageEvent[]>([]);
  const [goldLine, setGoldLine] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [claimProgress, setClaimProgress] = useState<{ phase?: string; article: string; done: number; total: number } | null>(null);
  const [claimEta, setClaimEta] = useState<{ t0: number; d0: number } | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [topics, setTopics] = useState<TopicInfo[]>([]);
  const [hotBoard, setHotBoard] = useState<HotBoardItem[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [pearls, setPearls] = useState<PearlItem[]>([]);
  const [pageIdx, setPageIdx] = useState(0);
  const homeRef = useRef<HTMLElement | null>(null);
  const [oauth, setOauth] = useState<{ authorized: boolean; profile?: { name?: string | null } | null } | null>(null);
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
    fetchHotBoard().then(setHotBoard).catch(() => {});
    fetchPearls().then(setPearls).catch(() => {});
    fetch("/api/oauth/status")
      .then((r) => r.json())
      .then((s) => setOauth(s))
      .catch(() => {}); // demo 服务不可用时保持「测测我的遗珠」
  }, [view]);

  const runAnalyze = async (rawTitle: string) => {
    const t = rawTitle.trim();
    if (!t || running) return;
    stopEvents();
    if (timerRef.current) clearTimeout(timerRef.current);
    setRunning(true);
    setStages([]);
    setGoldLine(null);
    setClaimProgress(null);
    setClaimEta(null);
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
      es.addEventListener("progress", (e) => {
        const p = JSON.parse((e as MessageEvent).data) as { phase?: string; article: string; done: number; total: number };
        setClaimProgress(p);
        if (p.phase !== "fetch") setClaimEta((prev) => prev ?? { t0: Date.now(), d0: p.done ?? 0 });
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

  const analyze = () => runAnalyze(title);

  const loadTopic = async (key: string) => {
    try {
      setResult(await fetchResult(key));
      setFocusCluster(null);
      setOpenMinor(null);
      setArchiveId(null);
      setLens("info");
      setView("dashboard");
      return true;
    } catch {
      /* 缓存损坏则忽略 */
      return false;
    }
  };

  /** 个人报告点击话题标题：缓存命中直达报告，否则走分析流程 */
  const openTopic = async (t: string, key: string) => {
    if (!(await loadTopic(key))) await runAnalyze(t);
  };

  /** 个人报告点击「我的回答」：载入话题报告并弹出该回答的主张拆解 */
  const openAnswer = async (t: string, key: string, aid: string) => {
    try {
      const r = await fetchResult(key);
      setResult(r);
      setFocusCluster(null);
      setOpenMinor(null);
      setLens("info");
      setView("dashboard");
      const hit = r.answers.find((a) => String(a.content_id).includes(aid) || (a.url || "").includes(`/answer/${aid}`));
      setArchiveId(hit ? hit.content_id : null);
    } catch {
      await runAnalyze(t);
    }
  };

  const scrollToPage = (i: number) => {
    const el = homeRef.current;
    if (!el) return;
    el.scrollTo({ top: i * el.clientHeight, behavior: "smooth" });
  };
  const onHomeScroll = () => {
    const el = homeRef.current;
    if (!el) return;
    setPageIdx(Math.min(2, Math.max(0, Math.round(el.scrollTop / el.clientHeight))));
  };

  const byId = new Map(result?.answers.map((a) => [a.content_id, a]) ?? []);
  const maxV = Math.max(...(result?.answers.map((a) => a.info_score) ?? [1]), 0.0001);
  // 价值百分位
  const vSorted = result ? [...result.answers].map((a) => a.info_score).sort((x, y) => x - y) : [];
  const valuePct = (a: Answer) =>
    vSorted.length ? Math.round((vSorted.filter((v) => v <= a.info_score).length / vSorted.length) * 100) : 0;

  const archive = archiveId ? byId.get(archiveId) ?? null : null;
  // 拆主张实测速率 → 预计剩余（claims 阶段才有数据）
  const claimEtaSeconds =
    claimEta && claimProgress && claimProgress.done > claimEta.d0
      ? Math.round(((claimProgress.total - claimProgress.done) * (Date.now() - claimEta.t0)) / (claimProgress.done - claimEta.d0))
      : null;
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
        <button
          onClick={() => setView("home")}
          className="text-sm font-semibold tracking-wide hover:text-gold"
          title="回到主页"
        >
          知寻 ZhiSeek
        </button>
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
        <a
          href="/login/"
          title={oauth?.authorized ? "知乎账号已授权" : "知乎登录，测量你的回答被埋没了多少"}
          className={`max-w-44 shrink-0 truncate rounded-lg border border-gold/60 px-3 py-1 text-xs text-gold hover:bg-gold/10 ${
            view === "dashboard" ? "" : "ml-auto"
          }`}
        >
          {oauth?.authorized ? `✓ 已登录${oauth.profile?.name ? ` · ${oauth.profile.name}` : ""}` : "测测我的遗珠"}
        </a>
      </header>

      {/* ============ 页面 1：首页（scroll-snap 翻页：寻找 → 遗珠 → 垄断榜） ============ */}
      {view === "home" && (
        <>
          {/* 右侧圆点导航：悬停出名称，点击平滑锁定到对应页 */}
          <nav className="fixed right-4 top-1/2 z-20 flex -translate-y-1/2 flex-col items-center gap-3">
            {["寻找", "遗珠", "垄断榜"].map((name, i) => (
              <button
                key={name}
                title={name}
                onClick={() => scrollToPage(i)}
                className={`h-2.5 w-2.5 rounded-full border border-gold/60 transition-all hover:scale-125 ${
                  pageIdx === i ? "bg-gold" : "bg-transparent"
                }`}
              />
            ))}
          </nav>
          <main
            ref={homeRef}
            onScroll={onHomeScroll}
            className="flex-1 snap-y snap-mandatory overflow-y-auto scroll-smooth"
          >
            {/* ── 第 1 页：询问 + 我的报告 ── */}
            <section className="flex min-h-full snap-start flex-col items-center px-6 pt-[13vh]">
              <div className="text-center">
                <h1 className="text-[28px] font-semibold leading-9 text-[#E6E9EF]">
                  每个问题里，都有没被看见的好回答
                </h1>
              </div>
              <div className="mt-6 flex w-[min(560px,92%)] gap-2">
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && analyze()}
                  placeholder="输入一个知乎问题…"
                  autoFocus
                  className="flex-1 rounded-xl border border-line bg-panel px-4 py-3 text-[16px] text-ink outline-none placeholder:text-dim focus:border-gold"
                />
                <button
                  onClick={analyze}
                  disabled={running}
                  className="rounded-xl bg-gold px-8 text-[16px] font-semibold text-[#1F2328] disabled:opacity-40"
                >
                  寻
                </button>
              </div>
              {topics.length >= 2 && (
                <div className="mt-4 text-xs text-dim">
                  试试：
                  <button onClick={() => setTitle(topics[0].title)} className="hover:text-gold hover:underline underline-offset-2">
                    {topics[0].title}
                  </button>
                  <span className="mx-1.5 text-line">·</span>
                  <button onClick={() => setTitle(topics[1].title)} className="hover:text-gold hover:underline underline-offset-2">
                    {topics[1].title}
                  </button>
                </div>
              )}
              <button
                onClick={() => setView("topics")}
                title="浏览全部已分析问题"
                className="mt-4 font-mono text-xs text-dim underline-offset-4 hover:text-gold hover:underline"
              >
                已分析 {topics.length} 个问题 · 挖出 {pearlTotal} 篇遗珠 →
              </button>
              {/* 个人遗珠：仅已登录用户可见（未授权时静默隐藏，入口在页头按钮） */}
              <div className="mt-10 flex w-full flex-col items-center pb-10">
                <PersonalBoard onOpenTopic={openTopic} onOpenAnswer={openAnswer} />
              </div>
            </section>

            {/* ── 第 2 页：最被低估的回答（跨议题 U 值榜） ── */}
            <section className="flex min-h-full snap-start flex-col items-center px-6 py-10">
              <div className="w-[min(720px,94%)]">
                <h2 className="text-xl font-medium text-[#E6E9EF]">💎 最被低估的回答</h2>
                <p className="mt-1 text-xs text-dim">
                  整个知识库里，价值与曝光落差最大的回答——点卡片直达该问题的观点星图
                </p>
                {pearls.length > 0 ? (
                  <div className="mt-4 space-y-2">
                    {pearls.map((p) => (
                      <button
                        key={`${p.key}-${p.author}`}
                        onClick={() => loadTopic(p.key)}
                        className="block w-full rounded-lg border border-gold/30 bg-panel px-3 py-2 text-left transition-colors hover:border-gold"
                      >
                        <div className="truncate text-[10px] text-dim">📍 {p.q_title}</div>
                        <div className="mt-1 flex items-center gap-2 text-sm">
                          <b className="min-w-0 truncate text-[#E6E9EF]">{p.author}</b>
                          {p.badges.map((b) => <Badge key={b} name={b} />)}
                          <span className="ml-auto shrink-0 font-mono text-xs text-gold">
                            U{p.u != null && p.u >= 0 ? "+" : ""}{p.u?.toFixed(2) ?? "—"}
                          </span>
                        </div>
                        {p.reason && <p className="mt-1 text-xs leading-5 text-dim">💡 {p.reason}</p>}
                        <div className="mt-1 font-mono text-[10px] text-dim">
                          {p.votes == null ? "赞同数未知" : `${p.votes} 赞`}
                        </div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="mt-6 text-center text-xs text-dim">分析完成的问题会在这里出现被埋没的好回答</p>
                )}
              </div>
            </section>

            {/* ── 第 3 页：今日最被垄断的问题 ── */}
            <section className="flex min-h-full snap-start flex-col items-center px-6 py-10">
              <div className="w-[min(720px,94%)]">
                <h2 className="text-xl font-medium text-[#E6E9EF]">🔥 今日最被垄断的问题</h2>
                <p className="mt-1 text-xs text-dim">热度 Top3 拿走了多少曝光，又贡献了多少信息增量？</p>
                {hotBoard.length > 0 ? (
                  <>
                    <div className="mt-4 space-y-2">
                      {(showAll ? hotBoard : hotBoard.slice(0, 3)).map((h, i) => {
                        const expo = (h.exposure_share_top_k ?? 0) * 100;
                        const incr = (h.info_increment_share_top_k ?? 0) * 100;
                        return (
                          <button
                            key={h.key}
                            onClick={() => h.ready && loadTopic(h.key)}
                            disabled={!h.ready}
                            className={`block w-full rounded-lg border border-line bg-panel px-3 py-2 text-left transition-colors ${
                              h.ready ? "hover:border-gold" : "opacity-50"
                            }`}
                          >
                            <div className="flex items-center gap-2 text-sm">
                              <span className="shrink-0 font-mono text-xs text-dim">{i + 1}.</span>
                              <span className="min-w-0 flex-1 truncate text-[#E6E9EF]">{h.title}</span>
                              {!h.ready && <span className="shrink-0 font-mono text-[10px] text-dim">分析中…</span>}
                            </div>
                            <div className="mt-1.5 flex items-center gap-2">
                              <span className="w-8 shrink-0 text-[9px] text-dim">曝光</span>
                              <div className="h-1 flex-1 bg-line">
                                <div className="h-full bg-[#5B7FA6]" style={{ width: `${expo}%` }} />
                              </div>
                              <span className="w-8 shrink-0 text-right font-mono text-[9px] text-dim">{expo.toFixed(0)}%</span>
                            </div>
                            <div className="mt-1 flex items-center gap-2">
                              <span className="w-8 shrink-0 text-[9px] text-dim">增量</span>
                              <div className="h-1 flex-1 bg-line">
                                <div className="h-full bg-[#5BA694]" style={{ width: `${incr}%` }} />
                              </div>
                              <span className="w-8 shrink-0 text-right font-mono text-[9px] text-dim">{incr.toFixed(0)}%</span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                    {!showAll && hotBoard.length > 3 && (
                      <button onClick={() => setShowAll(true)} className="mt-3 text-xs text-dim hover:text-gold">
                        查看全部 {hotBoard.length} 个问题 →
                      </button>
                    )}
                  </>
                ) : (
                  <p className="mt-6 text-center text-xs text-dim">榜单生成中…</p>
                )}
              </div>
            </section>
          </main>
        </>
      )}

      {/* ============ 问题库：全部已分析议题（点统计行进入） ============ */}
      {view === "topics" && (
        <main className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto w-[min(720px,94%)]">
            <div className="flex items-baseline gap-3">
              <h2 className="text-xl font-medium text-[#E6E9EF]">📚 问题库</h2>
              <span className="font-mono text-xs text-dim">
                {topics.length} 个问题 · {pearlTotal} 篇遗珠
              </span>
              <button onClick={() => setView("home")} className="ml-auto shrink-0 text-xs text-dim hover:text-gold">
                ← 返回首页
              </button>
            </div>
            <p className="mt-1 text-xs text-dim">点击任意问题，直达它的观点星图与遗珠清单</p>
            <div className="mt-4 space-y-2">
              {[...topics]
                .sort((a, b) => (b.pearl_count ?? 0) - (a.pearl_count ?? 0))
                .map((t) => (
                  <button
                    key={t.key}
                    onClick={() => loadTopic(t.key)}
                    className="block w-full rounded-lg border border-line bg-panel px-3 py-2 text-left transition-colors hover:border-gold"
                  >
                    <div className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate text-[#E6E9EF]">{t.title}</span>
                      {(t.pearl_count ?? 0) > 0 && (
                        <span className="shrink-0 rounded-full border border-gold/60 bg-gold/10 px-2 py-0.5 text-[10px] text-gold">
                          🔦 {t.pearl_count} 遗珠
                        </span>
                      )}
                      <span className="shrink-0 font-mono text-[10px] text-dim">{t.sample_size} 篇样本</span>
                    </div>
                  </button>
                ))}
            </div>
          </div>
        </main>
      )}

      {/* ============ 页面 2：分析中 ============ */}
      {view === "analyzing" && (
        <AnalyzingView stages={stages} goldLine={goldLine} error={error} claimProgress={claimProgress} claimEtaSeconds={claimEtaSeconds} />
      )}

      {/* ============ 页面 3：结果仪表盘（三栏） ============ */}
      {view === "dashboard" && result && m && (
        <main className="flex min-h-0 flex-1 gap-3 p-3">
          {/* 左栏 20%：问题档案 */}
          <aside className="flex w-[20%] min-w-[210px] shrink-0 flex-col gap-4 overflow-y-auto rounded-xl border border-line bg-panel p-4">
            <div>
              <h2 className="font-semibold leading-6 text-[#E6E9EF]">{result.title}</h2>
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
