"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** 个人遗珠报告：登录用户的回答 × 知寻 U 值（数据来自 zseek-oauth-demo 的 /api/oauth/personal-u） */

interface OauthStatus {
  configured: boolean;
  authorized: boolean;
  profile?: { name?: string | null; avatarUrl?: string | null } | null;
}

interface MyAnswer {
  aid: string;
  url: string;
  likes: number;
  matched: boolean;
  U?: number;
  V?: number;
  votes?: number;
  badges?: string[];
  reason?: string;
  expo_pct?: number;
  value_pct?: number;
  gap?: number;
}

interface PersonalQuestion {
  qid: string;
  title: string;
  url: string;
  status: "done" | "running" | "queued" | "pending" | "error";
  position?: number | null;
  stage?: { done: number; total: number; current: string | null; article?: string | null; ap?: { done: number; total: number } | null } | null;
  sample_size?: number;
  mine: MyAnswer[];
}

interface PersonalReport {
  summary: {
    answers_total: number;
    raw_contents?: number;
    questions: number;
    done: number;
    pending: number;
    queued: number;
    running: number;
    matched: number;
    pearls: number;
    buried: number;
    avg_gap: number | null;
  };
  questions: PersonalQuestion[];
}

export default function PersonalBoard() {
  const [status, setStatus] = useState<OauthStatus | null>(null);
  const [statusErr, setStatusErr] = useState(false);
  const [report, setReport] = useState<PersonalReport | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [etaSeconds, setEtaSeconds] = useState<number | null>(null);
  const etaRef = useRef<{ key: string; t: number; done: number } | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadReport = useCallback(async (analyze: string[] = [], isPoll = false) => {
    if (!isPoll) setLoading(true);
    try {
      const res = await fetch("/api/oauth/personal-u", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ analyze }),
      });
      const payload = await res.json();
      if (!res.ok || payload.ok === false) throw new Error(payload.error?.message || "报告生成失败");
      setReport(payload.report);
      setError(null);
      // 拆主张实测速率 → 预计剩余（8s 轮询对比相邻两次进度）
      const runningQ = payload.report.questions.find((x: PersonalQuestion) => x.status === "running" && x.stage?.ap);
      if (runningQ?.stage?.ap) {
        const ap = runningQ.stage.ap;
        const now = Date.now();
        const prev = etaRef.current;
        if (!prev || prev.key !== runningQ.key || ap.done < prev.done) {
          etaRef.current = { key: runningQ.key, t: now, done: ap.done };
          setEtaSeconds(null);
        } else if (ap.done > prev.done) {
          const rate = (ap.done - prev.done) / ((now - prev.t) / 1000);
          if (rate > 0) setEtaSeconds(Math.round((ap.total - ap.done) / rate));
          etaRef.current = { key: runningQ.key, t: now, done: ap.done };
        }
      } else {
        etaRef.current = null;
        setEtaSeconds(null);
      }
      if (payload.report.summary.running + (payload.report.summary.queued ?? 0) > 0) {
        pollRef.current = setTimeout(() => void loadReport([], true), 8000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "报告生成失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleSelect = (qid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(qid)) next.delete(qid);
      else next.add(qid);
      return next;
    });
  };

  const analyzeSelected = () => {
    if (selected.size === 0) return;
    const qids = Array.from(selected);
    setSelected(new Set());
    void loadReport(qids);
  };

  useEffect(() => {
    fetch("/api/oauth/status")
      .then((r) => r.json())
      .then((s: OauthStatus) => {
        setStatus(s);
        if (s.authorized && (window.location.search.includes("my=1") || true)) void loadReport();
      })
      .catch(() => setStatusErr(true));
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [loadReport]);

  if (statusErr) return null; // demo 服务不可用时静默隐藏

  // 未授权：不在首屏放登录墙（一屏一事），登录入口在页头「测测我的遗珠」按钮
  if (status && !status.authorized) return null;

  if (!status) return null;

  const s = report?.summary;
  return (
    <div className="w-[min(720px,94%)] rounded-xl border border-gold/40 bg-panel p-4">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-sm font-semibold">🔦 我的遗珠报告</span>
        <span className="text-[10px] text-dim">
          {status.profile?.name ? `${status.profile.name} · ` : ""}最近回答的
          {s ? ` ${s.questions} 个问题` : "问题"}，你的回答值多少曝光？
        </span>
        {report && (
          <button
            onClick={() => void loadReport()}
            disabled={loading}
            className="ml-auto shrink-0 rounded-md border border-line px-2 py-0.5 text-[10px] text-dim hover:border-gold hover:text-ink disabled:opacity-40"
          >
            {loading ? "测量中…" : "刷新"}
          </button>
        )}
      </div>

      {error && <div className="mb-2 rounded-lg border border-red-900/50 bg-base px-3 py-2 text-xs text-red-400">{error}</div>}

      {!report && !error && (
        <div className="py-4 text-center font-mono text-xs text-dim">
          {loading ? "正在拉取你的创作并逐题测量…（首次测量需要几分钟）" : "点击「刷新」生成报告"}
        </div>
      )}

      {report && s && s.questions === 0 && !error && (
        <p className="rounded-lg bg-base px-3 py-2 text-[11px] leading-4 text-dim">
          创作接口这次没有返回可定位的回答（原始返回 {s.raw_contents ?? 0} 条）。可能是授权 scope 不含创作读取或接口限流，点右上角「刷新」重试；也可以到 /login/ 页用「重新请求用户数据」验证创作接口本身是否可用。
        </p>
      )}

      {report && s && (
        <>
          <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 rounded-lg bg-base px-3 py-2 font-mono text-[11px] text-dim">
            <span>
              已测 <b className="text-ink">{s.matched}</b> 篇
            </span>
            <span>
              <b className="text-gold">{s.pearls}</b> 篇沧海遗珠
            </span>
            <span>
              <b className="text-gold">{s.buried}</b> 篇被埋没（价值−曝光 &gt; 30 分位）
            </span>
            {s.avg_gap !== null && (
              <span>
                平均埋没差 <b className="text-ink">{s.avg_gap >= 0 ? "+" : ""}{s.avg_gap}</b>
              </span>
            )}
            {s.queued > 0 && <span>🧮 {s.queued} 个排队中</span>}
            {s.running > 0 && <span className="text-gold">⏳ {s.running} 个问题分析中，自动刷新…</span>}
            {s.pending > 0 && (
              <button
                onClick={analyzeSelected}
                disabled={selected.size === 0 || loading}
                className="ml-auto rounded-md bg-gold px-2 py-0.5 font-semibold text-[10px] text-[#1F2328] disabled:opacity-40"
              >
                分析所选问题（{selected.size}）
              </button>
            )}
          </div>

          <div className="max-h-72 space-y-1.5 overflow-y-auto">
            {report.questions.map((q) => (
              <div key={q.qid} className="rounded-lg border border-line bg-base px-3 py-2">
                <div className="flex items-center gap-2 text-xs">
                  {q.status === "pending" && (
                    <input
                      type="checkbox"
                      checked={selected.has(q.qid)}
                      onChange={() => toggleSelect(q.qid)}
                      className="h-3.5 w-3.5 shrink-0 accent-[#E8B84B]"
                      title="勾选后分析此问题"
                    />
                  )}
                  <a
                    href={q.url}
                    target="_blank"
                    rel="noreferrer"
                    className="min-w-0 flex-1 truncate text-ink hover:text-gold"
                  >
                    {q.title}
                  </a>
                  {q.status === "running" ? (
                    <span className="shrink-0 font-mono text-[10px] text-gold">
                      分析中 · {q.stage?.current ?? "准备中"} {q.stage?.done ?? 0}/{q.stage?.total ?? 8}
                      {q.stage?.article ? `（${q.stage.article}${etaSeconds != null ? ` · 预计还需 ${etaSeconds < 90 ? `${Math.max(5, Math.round(etaSeconds / 5) * 5)} 秒` : `约 ${Math.max(1, Math.round(etaSeconds / 60))} 分钟`}` : ""}）` : ""}
                    </span>
                  ) : q.status === "queued" ? (
                    <span className="shrink-0 font-mono text-[10px] text-dim">
                      队列中
                      {q.position != null && q.position > 0
                        ? ` · 第 ${q.position + 1} 位 · 约等待 ${q.position * 4 + 2} 分钟`
                        : " · 即将开始"}
                    </span>
                  ) : q.status === "pending" ? (
                    <span className="shrink-0 font-mono text-[10px] text-dim">未分析 · 勾选测 U</span>
                  ) : q.status === "error" ? (
                    <span className="shrink-0 font-mono text-[10px] text-red-400">测量失败</span>
                  ) : (
                    <span className="shrink-0 font-mono text-[10px] text-dim">{q.sample_size} 篇样本</span>
                  )}
                </div>
                {q.mine
                  .filter((m) => m.matched)
                  .map((m) => {
                    const isPearl = (m.badges ?? []).includes("沧海遗珠");
                    const expo = Math.round((m.expo_pct ?? 0) * 100);
                    const value = Math.round((m.value_pct ?? 0) * 100);
                    return (
                      <div key={m.aid} className="mt-1.5 border-t border-line pt-1.5">
                        <div className="flex items-center gap-2 text-[11px]">
                          <a href={m.url} target="_blank" rel="noreferrer" className="shrink-0 text-gold hover:underline">
                            我的回答
                          </a>
                          {(m.badges ?? []).map((b) => (
                            <span
                              key={b}
                              className={`rounded px-1 text-[9px] ${
                                b.includes("遗珠") ? "bg-gold/15 text-gold" : "bg-panel text-dim"
                              }`}
                            >
                              {b}
                            </span>
                          ))}
                          <span className="ml-auto font-mono text-[10px] text-dim">
                            {m.votes ?? 0} 赞 · U={m.U !== undefined ? `${m.U >= 0 ? "+" : ""}${m.U.toFixed(2)}` : "—"}
                          </span>
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <span className="w-8 shrink-0 text-[9px] text-dim">曝光</span>
                          <div className="h-1 flex-1 bg-line">
                            <div className="h-full bg-[#5B7FA6]" style={{ width: `${expo}%` }} />
                          </div>
                          <span className="w-8 shrink-0 text-right font-mono text-[9px] text-dim">{expo}%</span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-2">
                          <span className="w-8 shrink-0 text-[9px] text-dim">价值</span>
                          <div className="h-1 flex-1 bg-line">
                            <div
                              className={`h-full ${isPearl ? "bg-gold" : "bg-[#5BA694]"}`}
                              style={{ width: `${value}%` }}
                            />
                          </div>
                          <span className="w-8 shrink-0 text-right font-mono text-[9px] text-dim">{value}%</span>
                        </div>
                        {m.reason && <p className="mt-1 text-[10px] leading-4 text-dim">💡 {m.reason}</p>}
                      </div>
                    );
                  })}
                {q.status === "done" && q.mine.every((m) => !m.matched) && (
                  <p className="mt-1 border-t border-line pt-1 text-[10px] text-dim">
                    你的回答不在本次样本中（抽样未覆盖或赞同数过低未入榜）
                  </p>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
