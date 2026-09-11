"use client";

import { useMemo } from "react";
import type { StageEvent } from "@/lib/types";

const STAGE_LINES = [
  "> 连接知乎知识接口…",
  "> 拆分主张中…（只提取原文明确表达的观点）",
  "> 计算语义坐标…（本地 embedding，不出域）",
  "> 构建观点星图…（UMAP + HDBSCAN）",
  "> 交叉印证 & 时效核验…",
  "> 计算低估指数 U = 价值百分位 − 曝光百分位…",
  "> 为观点阵营命名…",
  "> 整理遗珠清单…",
];

interface Props {
  stages: StageEvent[];
  /** 完成后追加的金色预告行（如 "> ✦ 发现 3 篇沧海遗珠"） */
  goldLine: string | null;
  error: string | null;
}

/** 确定性伪随机（避免每次渲染星点乱跳） */
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const STAR_COUNT = 110;

/** 分析中：仪器扫描日志 + 星点从中心逐点亮起（动效规范 #1） */
export default function AnalyzingView({ stages, goldLine, error }: Props) {
  const stars = useMemo(() => {
    const rand = mulberry32(20260911);
    return Array.from({ length: STAR_COUNT }, (_, i) => {
      const angle = rand() * Math.PI * 2;
      const r = Math.pow(rand(), 0.6) * 46; // 中心化分布
      return {
        left: 50 + Math.cos(angle) * r,
        top: 50 + Math.sin(angle) * r * 0.72,
        size: 1.5 + rand() * 2.5,
        order: r, // 离中心越近越早亮起
        gold: i >= STAR_COUNT - 5, // 最后 5 颗是遗珠预告
      };
    }).sort((a, b) => a.order - b.order);
  }, []);

  const progress = goldLine ? 1 : stages.length / STAGE_LINES.length;
  const litCount = Math.round(progress * STAR_COUNT);

  return (
    <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-y-auto overflow-x-hidden p-4">
      {/* 星场背景：散点逐点亮起 */}
      <div className="absolute inset-0">
        {stars.map((s, i) => {
          const lit = i < litCount;
          return (
            <span
              key={i}
              className="absolute rounded-full transition-opacity duration-700"
              style={{
                left: `${s.left}%`,
                top: `${s.top}%`,
                width: s.size,
                height: s.size,
                background: lit ? (s.gold ? "#E8B84B" : "#5B7FA6") : "#232A38",
                opacity: lit ? (s.gold ? 1 : 0.85) : 0.5,
                boxShadow: lit && s.gold ? "0 0 10px 2px rgba(232,184,75,0.5)" : "none",
              }}
            />
          );
        })}
      </div>

      {/* 仪器日志 */}
      <div className="relative z-10 w-[min(560px,90%)] rounded-xl border border-line bg-panel/90 p-6 font-mono text-sm backdrop-blur">
        <div className="mb-3 text-xs text-dim">ZHISEEK · EXCAVATION LOG</div>
        {STAGE_LINES.slice(0, Math.max(stages.length, goldLine ? STAGE_LINES.length : 0)).map((line, i) => (
          <div key={i} className="leading-6 text-ink">
            <span className="text-dim">{line.slice(0, 2)}</span>
            {line.slice(2)}
          </div>
        ))}
        {!goldLine && !error && (
          <div className="leading-6 text-gold">
            <span className="text-dim">{STAGE_LINES[Math.min(stages.length, STAGE_LINES.length - 1)].slice(0, 2)}</span>
            {STAGE_LINES[Math.min(stages.length, STAGE_LINES.length - 1)].slice(2)}
            <span className="animate-blink">▌</span>
          </div>
        )}
        {goldLine && <div className="mt-2 leading-6 text-gold">{goldLine}</div>}
        {error && <div className="mt-2 leading-6 text-warn">{`> 异常：${error}（可换个更热门的议题重试）`}</div>}
        <div className="mt-4 h-0.5 w-full bg-line">
          <div
            className="h-full bg-gold transition-all duration-500"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
      </div>
    </div>
  );
}
