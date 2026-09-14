"""数据战役 · 任务 1：τ 拟合（点赞饱和时间常数）。

原料：data/cache/*/1_search.json 中 votes 已知的搜索样本（votes + edit_time）。
方法：
  1. 按回答年龄分桶，取每桶赞同数中位数（均值会被万赞回答带飞）
  2. 最小二乘拟合 M(t) = M∞·(1−e^{−t/τ})（scipy.optimize.curve_fit）
  3. 时事类 vs 常青类分别拟合，τ 差 >3 倍 → 建议分两档
产出：data/campaign/tau_fit.json + tau_fit_curve.png（PPT 素材）

用法：python scripts/fit_tau.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from pipeline import config

BUCKETS = [(7, "0-7d"), (30, "7-30d"), (90, "30-90d"), (365, "90-365d"), (1095, "1-3y"), (10**9, "3y+")]


def load_points() -> dict[str, list[tuple[float, int]]]:
    """返回 {layer: [(age_days, votes), ...]}，只用搜索样本（有真实赞同数）。"""
    meta_path = config.DATA_DIR / "campaign" / "stage0_meta.jsonl"
    layer_of: dict[str, str] = {}
    if meta_path.exists():
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("key") and m.get("layer"):
                layer_of[m["key"]] = m["layer"]

    now = time.time()
    points: dict[str, list[tuple[float, int]]] = {"current": [], "evergreen": []}
    for cache_dir in sorted(config.CACHE_DIR.iterdir()):
        f = cache_dir / "1_search.json"
        if not f.exists():
            continue
        stage1 = json.loads(f.read_text(encoding="utf-8"))
        layer = layer_of.get(cache_dir.name, "evergreen")
        for s in stage1.get("samples", []):
            if s.get("votes_unknown") or not s.get("edit_time"):
                continue
            age = (now - s["edit_time"]) / 86400
            if age < 0.5:  # 当天新答，edit_time 精度不足
                continue
            points[layer].append((age, int(s.get("votes") or 0)))
    return points


def bucketize(points: list[tuple[float, int]]) -> list[tuple[float, float, int]]:
    """分桶 → [(桶代表年龄=桶内中位年龄, 赞同中位数, 样本数)]，只保留 n≥3 的桶。"""
    out = []
    prev = 0.0
    for hi, _name in BUCKETS:
        cells = [(a, v) for a, v in points if prev < a <= hi]
        if len(cells) >= 3:
            ages = sorted(a for a, _ in cells)
            votes = sorted(v for _, v in cells)
            out.append((ages[len(ages) // 2], votes[len(votes) // 2], len(cells)))
        prev = hi
    return out


def model(t: np.ndarray, m_inf: float, tau: float) -> np.ndarray:
    return m_inf * (1 - np.exp(-t / tau))


def fit_layer(layer: str, points: list[tuple[float, int]]) -> dict:
    rows = bucketize(points)
    if len(rows) < 3:
        return {"layer": layer, "error": f"有效桶不足（{len(rows)}），需要 ≥3", "n_samples": len(points)}
    t = np.array([r[0] for r in rows], dtype=float)
    m = np.array([r[1] for r in rows], dtype=float)
    m_inf0 = max(m[-1] * 1.5, 10.0)
    try:
        (m_inf, tau), _ = curve_fit(model, t, m, p0=[m_inf0, 90.0], bounds=([0, 1], [np.inf, 3650]), maxfev=20000)
    except Exception as e:
        return {"layer": layer, "error": str(e), "n_samples": len(points)}
    resid = m - model(t, m_inf, tau)
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((m - m.mean()) ** 2))
    return {
        "layer": layer,
        "n_samples": len(points),
        "M_inf": round(float(m_inf), 2),
        "tau_days": round(float(tau), 1),
        "R2": round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else None,
        "buckets": [{"age_days": round(r[0], 1), "votes_median": r[1], "n": r[2]} for r in rows],
    }


def main() -> None:
    points = load_points()
    results = {layer: fit_layer(layer, pts) for layer, pts in points.items()}

    print("=== τ 拟合结果 ===")
    for layer, r in results.items():
        if "error" in r:
            print(f"[{layer}] 拟合失败：{r['error']}（样本 {r['n_samples']}）")
            continue
        print(f"[{layer}] 样本 {r['n_samples']}，M∞={r['M_inf']}，τ={r['tau_days']} 天，R²={r['R2']}")
        for b in r["buckets"]:
            print(f"    年龄中位 {b['age_days']:>7} 天 → 赞同中位 {b['votes_median']:>6}（n={b['n']}）")

    # 分档决策：拟合打到参数边界 = 横截面搜索样本不可辨识 τ，不做分档结论
    decision = {"tiers": 1, "reason": ""}
    TAU_BOUNDS = (1.0, 3650.0)
    hit_bound = {layer: TAU_BOUNDS[0] <= r["tau_days"] <= TAU_BOUNDS[1] + 1e-6 and (
        abs(r["tau_days"] - TAU_BOUNDS[0]) < 1 or abs(r["tau_days"] - TAU_BOUNDS[1]) < 1)
        for layer, r in results.items() if "tau_days" in r}
    identifiable = {layer: ("tau_days" in r) and not hit_bound.get(layer, False) and (r.get("R2") or 0) > 0.3
                    for layer, r in results.items()}
    if all(identifiable.values()):
        ratio = max(results["current"]["tau_days"], results["evergreen"]["tau_days"]) / min(
            results["current"]["tau_days"], results["evergreen"]["tau_days"])
        print(f"\n两层 τ 比值：{ratio:.1f}×（>3× 则分两档）")
        decision = {"tiers": 2, "reason": f"两层 τ 差 {ratio:.1f} 倍，建议分两档"} if ratio > 3 else \
                   {"tiers": 1, "reason": f"两层 τ 差仅 {ratio:.1f} 倍，全局单档"}
    else:
        bad = [layer for layer, ok in identifiable.items() if not ok]
        print(f"\n[{','.join(bad)}] 拟合打边界 / R² 过低：横截面搜索样本存在选择偏差（各年龄桶都是相关性排序头部），τ 不可直接辨识")
        decision = {"tiers": 1, "identifiable": False,
                    "reason": "拟合打边界，τ 不可辨识；维持单档 τ=365，以敏感性分析兜底（EXPO_CAP 封顶使年龄>0.22τ 的修正与 τ 无关）"}
    print(f"分档决策：{decision['reason']}")

    # 曲线图
    fig, ax = plt.subplots(figsize=(8, 5))
    colors = {"current": "#E8B84B", "evergreen": "#7BA7C7"}
    for layer, r in results.items():
        if "error" in r:
            continue
        bs = r["buckets"]
        ax.scatter([b["age_days"] for b in bs], [b["votes_median"] for b in bs],
                   c=colors[layer], s=60, label=f"{layer}（实测中位）", zorder=3)
        tt = np.linspace(1, max(b["age_days"] for b in bs) * 1.1, 200)
        ax.plot(tt, model(tt, r["M_inf"], r["tau_days"]), c=colors[layer], alpha=0.6,
                label=f"{layer} 拟合：τ={r['tau_days']:.0f}d, R²={r['R2']}")
    ax.set_xscale("log")
    ax.set_xlabel("回答年龄（天，log 刻度）")
    ax.set_ylabel("赞同数中位数")
    ax.set_title("知乎回答赞同数饱和曲线 · M(t)=M∞(1−e^(−t/τ))")
    ax.legend()
    ax.grid(alpha=0.3)
    out_png = config.DATA_DIR / "campaign" / "tau_fit_curve.png"
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    print(f"\n曲线图 → {out_png}")

    out = {"fitted_at": time.time(), "results": results, "decision": decision,
           "config_tau_days_current": config.EXPO_TAU_DAYS}
    out_json = config.DATA_DIR / "campaign" / "tau_fit.json"
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"拟合数据 → {out_json}")


if __name__ == "__main__":
    main()
