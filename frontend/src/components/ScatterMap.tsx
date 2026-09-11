"use client";

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { CLUSTER_COLORS, STAR_PATH } from "@/lib/theme";
import type { Answer, ClusterView } from "@/lib/types";

export type SortLens = "info" | "votes";

interface Props {
  answers: Answer[];
  clusters: ClusterView[];
  /** 左栏图例点击聚焦的观点簇；null = 全部 */
  focusCluster: number | null;
  /** 排序镜头：影响点大小映射（增量=L∞，热度=votes），切换时 300ms 重排 */
  lens: SortLens;
  onSelect: (a: Answer) => void;
}

const MONO = "'JetBrains Mono', ui-monospace, monospace";

/** 中央星图：普通回答灰蓝小点（大小∝L∞/赞同），沧海遗珠为金色星形+微光晕。滚轮/框选缩放 */
export default function ScatterMap({ answers, clusters, focusCluster, lens, onSelect }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current = echarts.init(ref.current, null, { renderer: "canvas" });
    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chartRef.current?.dispose();
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const sizedBy = (a: Answer) => (lens === "votes" ? a.votes : a.L_inf || a.votes);
    const maxSize = Math.max(...answers.map(sizedBy), 1);
    const points = answers.map((a) => {
      const clusterIdx =
        a.dominant_cluster >= 0 ? clusters.findIndex((c) => c.cluster === a.dominant_cluster) : -1;
      const isPearl = (a.badges ?? []).includes("沧海遗珠");
      const dimmed = focusCluster !== null && a.dominant_cluster !== focusCluster;
      const base = {
        name: a.author || "匿名",
        value: [a.position.x, a.position.y, sizedBy(a)],
        symbolSize: isPearl ? 14 + 16 * Math.sqrt(sizedBy(a) / maxSize) : 5 + 18 * Math.sqrt(sizedBy(a) / maxSize),
        itemStyle: {
          color: clusterIdx >= 0 ? CLUSTER_COLORS[clusterIdx % CLUSTER_COLORS.length] : "#3A4356",
          opacity: dimmed ? 0.08 : 0.9,
        },
        _answer: a,
      };
      if (isPearl) {
        // 遗珠：金色星形 + 微光晕（全站唯一高饱和元素）
        return {
          ...base,
          symbol: STAR_PATH,
          itemStyle: {
            ...base.itemStyle,
            color: "#E8B84B",
            borderColor: "#E8B84B",
            shadowBlur: 14,
            shadowColor: "rgba(232,184,75,0.55)",
            opacity: dimmed ? 0.15 : 1,
          },
        };
      }
      return base;
    });
    chart.setOption({
      animationDurationUpdate: 300,
      animationEasingUpdate: "cubicOut",
      grid: { left: 16, right: 16, top: 16, bottom: 16 },
      xAxis: { type: "value", show: false, min: (v: { min: number; max: number }) => v.min - (v.max - v.min) * 0.08, max: (v: { min: number; max: number }) => v.max + (v.max - v.min) * 0.08 },
      yAxis: { type: "value", show: false, min: (v: { min: number; max: number }) => v.min - (v.max - v.min) * 0.08, max: (v: { min: number; max: number }) => v.max + (v.max - v.min) * 0.08 },
      dataZoom: [
        { type: "inside", xAxisIndex: 0, filterMode: "none" },
        { type: "inside", yAxisIndex: 0, filterMode: "none" },
      ],
      toolbox: {
        right: 8,
        top: 6,
        itemSize: 13,
        iconStyle: { borderColor: "#7A8499" },
        feature: {
          dataZoom: { yAxisIndex: "none", title: { zoom: "框选放大", back: "回退" } },
          restore: { title: "还原" },
        },
      },
      tooltip: {
        // 与纸感弹出层一致：浅底墨字（规范七「原文阅读弹出层为浅底纸感」）
        backgroundColor: "#FAF7F2",
        borderColor: "#d8d2c4",
        textStyle: { color: "#1F2328", fontSize: 12 },
        formatter: (p: { data: { _answer: Answer } }) => {
          const a = p.data._answer;
          const u = a.underestimate_index;
          return `<b>${a.author || "匿名"}</b> · ${a.votes_unknown ? "赞同数未知" : a.votes + " 赞"}<br/>V=${a.info_score.toFixed(2)} U=${u === null ? "观察池" : (u >= 0 ? "+" : "") + u.toFixed(2)}<br/><span style="color:#8a8577">${a.text.slice(0, 80)}…</span>`;
        },
      },
      series: [{ type: "scatter", data: points }],
    });
    chart.off("click");
    chart.on("click", (p) => onSelect((p as unknown as { data: { _answer: Answer } }).data._answer));
  }, [answers, clusters, focusCluster, lens, onSelect]);

  return <div ref={ref} className="h-full w-full" />;
}
