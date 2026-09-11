import type { Result, TopicInfo } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function startAnalyze(title: string): Promise<{ job_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`启动分析失败: ${res.status}`);
  return res.json();
}

export async function fetchResult(key: string): Promise<Result> {
  const res = await fetch(`${API_BASE}/api/result/${key}`);
  if (!res.ok) throw new Error(`获取结果失败: ${res.status}`);
  return res.json();
}

export async function fetchTopics(): Promise<TopicInfo[]> {
  const res = await fetch(`${API_BASE}/api/topics`);
  if (!res.ok) return [];
  return res.json();
}

export function jobEventsUrl(key: string): string {
  return `${API_BASE}/api/jobs/${key}/events`;
}
