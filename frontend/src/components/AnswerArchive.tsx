"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { Answer } from "@/lib/types";

const BADGE_EMOJI: Record<string, string> = { 沧海遗珠: "🔦", 潜力新声: "🌱", 内容待更新: "⏰" };

export function Badge({ name }: { name: string }) {
  const cls =
    name === "沧海遗珠"
      ? "border border-gold/60 bg-gold/10 text-gold"
      : name === "潜力新声"
        ? "border border-[#5BA694]/50 bg-[#5BA694]/10 text-[#5BA694]"
        : "border border-line bg-panel text-dim";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs ${cls}`}>
      {BADGE_EMOJI[name] ?? ""} {name}
    </span>
  );
}

/** 四维雷达：信息增量 / 证据 / 印证 / 新鲜度 */
function Radar({ a, maxV }: { a: Answer; maxV: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const evidence = a.claim_count > 0 ? a.anchored_claims / a.claim_count : 0;
    chart.setOption({
      radar: {
        radius: "62%",
        indicator: [
          { name: "信息增量", max: 1 },
          { name: "证据", max: 1 },
          { name: "印证", max: 1 },
          { name: "新鲜度", max: 1 },
        ],
        axisName: { color: "#7A8499", fontSize: 10 },
        splitArea: { areaStyle: { color: ["transparent"] } },
        splitLine: { lineStyle: { color: "#232A38" } },
        axisLine: { lineStyle: { color: "#232A38" } },
      },
      series: [
        {
          type: "radar",
          data: [
            {
              value: [
                Math.min(1, a.info_score / (maxV || 1)),
                evidence,
                Math.min(1, Math.max(0, a.E_answer)),
                Math.min(1, Math.max(0, a.freshness)),
              ],
              areaStyle: { color: "rgba(232,184,75,0.18)" },
              lineStyle: { color: "#E8B84B", width: 1.5 },
              itemStyle: { color: "#E8B84B" },
            },
          ],
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [a, maxV]);
  return <div ref={ref} className="h-44 w-full" />;
}

interface Props {
  a: Answer;
  maxV: number;
  onClose: () => void;
}

/** 原文阅读档案：深色宇宙中「取出一份档案」——浅底纸感弹出层 */
export default function AnswerArchive({ a, maxV, onClose }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="paper-scroll flex max-h-[86vh] w-full max-w-2xl flex-col overflow-y-auto rounded-xl bg-paper p-6 text-paperink shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 档案头 */}
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <b className="text-lg">{a.author || "匿名"}</b>
              <span className="font-mono text-xs text-[#8a8577]">
                {a.votes_unknown ? "赞同数未知" : `${a.votes} 赞`} · {a.comments} 评论
              </span>
              {(a.badges ?? []).map((b) => <Badge key={b} name={b} />)}
            </div>
            <div className="mt-1 font-mono text-xs text-[#8a8577]">
              V={a.info_score.toFixed(3)} · F={a.freshness.toFixed(2)}
              {a.votes_unknown
                ? " · L∞=未知（枚举通道无曝光数据，不计 U）"
                : ` · L∞=${a.L_inf}${a.age_days !== null ? ` · 发布 ${a.age_days.toFixed(0)} 天` : ""}${a.underestimate_index !== null ? ` · U=${a.underestimate_index >= 0 ? "+" : ""}${a.underestimate_index.toFixed(2)}` : ""}`}
            </div>
          </div>
          <button onClick={onClose} className="shrink-0 text-[#8a8577] hover:text-paperink">✕</button>
        </div>

        {a.excavation_reason && (
          <div className="mt-3 rounded-r-lg border-l-4 border-gold bg-gold/10 p-3 text-sm">
            💡 {a.excavation_reason}
          </div>
        )}

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-[1fr_200px]">
          {/* 主张列表 */}
          <div>
            <div className="mb-1.5 text-xs font-semibold text-[#8a8577]">
              共提取 {a.claim_count} 条主张 · 其中 {a.unique_claims} 条为本文独占 · {a.anchored_claims} 条含可自查锚点
              {a.detailed_claims ? ` · ${a.detailed_claims} 条一手经历含具体细节` : ""}
            </div>
            <ul className="space-y-1.5">
              {(a.claims ?? []).map((c, i) => (
                <li
                  key={i}
                  className={`flex items-start gap-1.5 rounded-r px-1 text-xs leading-5 ${
                    c.unique ? "border-l-2 border-gold bg-gold/[0.07]" : ""
                  }`}
                >
                  <span
                    className={`mt-0.5 shrink-0 rounded px-1 py-0.5 font-mono text-[10px] ${
                      c.verifiable
                        ? "bg-[#5B7FA6]/15 text-[#3d5a7a]"
                        : c.type === "sensitive"
                          ? "bg-[#A6875B]/20 text-[#7a6238]"
                          : c.type === "personal"
                            ? "bg-[#8A6BA6]/15 text-[#6b4f85]"
                            : "bg-black/5 text-[#8a8577]"
                    }`}
                  >
                    {c.verifiable
                      ? "可核验"
                      : c.type === "sensitive"
                        ? "时效敏感"
                        : c.type === "personal"
                          ? c.has_detail
                            ? "个例·具体"
                            : "个例·空泛"
                          : "常青"}
                  </span>
                  {c.unique && (
                    <span className="mt-0.5 shrink-0 rounded bg-gold/15 px-1 py-0.5 font-mono text-[10px] text-[#8a6d1f]">
                      ✦ 独占
                    </span>
                  )}
                  <span>{c.claim}</span>
                </li>
              ))}
            </ul>
          </div>
          {/* 四维雷达 */}
          <div className="rounded-lg bg-panel p-1">
            <div className="pt-1 text-center text-[10px] text-dim">信息增量 / 证据 / 印证 / 新鲜度</div>
            <Radar a={a} maxV={maxV} />
          </div>
        </div>

        {/* 原文 */}
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold text-[#8a8577]">回答正文（公开搜索摘要，可能被截断）</div>
          <p className="max-h-52 overflow-y-auto paper-scroll whitespace-pre-wrap rounded-lg bg-white/70 p-3 text-sm leading-6">
            {a.text}
          </p>
        </div>

        <a
          href={a.url}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex w-fit items-center gap-1 rounded-lg bg-gold px-4 py-2 text-sm font-medium text-[#1F2328]"
        >
          在知乎查看原文 →
        </a>
      </div>
    </div>
  );
}
