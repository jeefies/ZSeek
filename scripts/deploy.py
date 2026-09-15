"""一键 commit + push + 部署（本地 Windows / 服务器 Ubuntu 均可用）。

用法（在仓库根目录，任意 python ≥3.9，无需 conda——只用标准库）：
    python scripts/deploy.py -m "提交信息"        # 提交+推送+服务器同步+按需重启+健康检查
    python scripts/deploy.py -m "..." --dry-run   # 只打印将执行的命令，不执行
    python scripts/deploy.py                      # 无改动且与 origin 同步时直接退出；有改动必须给 -m

流程：
    git add -A → commit → push origin main → 服务器 git fetch + reset --hard
    → 按改动范围重启：pipeline/ 或 backend/ → zseek-backend；frontend/ → 服务器重新 build + zseek-frontend
    → 健康检查 https://zseek.jeefy.top/api/health

铁律：
    - 永不重启 zseek-demo（OAuth token 在内存，重启即登出用户）；zseek-oauth-demo/ 有改动时只告警。
    - .env / data/ / *.log / oauth-demo.env 均被 git 忽略，不会进 git，服务器凭证文件不受影响。
    - 若本地有未推送提交（未 commit 的新改动为空），会把这些提交一并推送部署。
"""
import argparse
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一改为 UTF-8 输出，防止打印 Unicode 崩溃/乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
SERVER = "root@36.151.145.113"
REPO_DIR = "/srv/zseek"
HEALTH_URL = "https://zseek.jeefy.top/api/health"
BRANCH = "main"

DRY_RUN = False


def run(cmd: list[str], *, timeout: int | None = None, tail: int | None = None) -> str:
    """执行（可能改变状态的）命令；dry-run 时只打印。失败即退出并保留输出尾部。"""
    if DRY_RUN:
        print(f"  [dry-run] {' '.join(cmd)}")
        return ""
    print(f"  $ {' '.join(cmd[:3])}{' ...' if len(cmd) > 3 else ''}")
    return _exec(cmd, timeout=timeout, tail=tail)


def query(cmd: list[str]) -> str:
    """只读 git 查询：不受 dry-run 影响，永远真实执行。"""
    return _exec(["git", "-C", str(ROOT), *cmd])


def _exec(cmd: list[str], *, timeout: int | None = None, tail: int | None = None) -> str:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"[失败] 命令超时（{timeout}s）：{cmd[0]}")
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        shown = "\n".join(out.splitlines()[-30:])
        sys.exit(f"[失败] 退出码 {p.returncode}：{' '.join(cmd)}\n{shown}")
    if tail:
        for line in out.splitlines()[-tail:]:
            print(f"    {line}")
    return p.stdout or ""


def git(*args: str, tail: int | None = None) -> str:  # 有状态的 git 操作（commit/push/fetch）
    return run(["git", "-C", str(ROOT), *args], tail=tail)


def ssh(remote_cmd: str, *, timeout: int | None = None, tail: int | None = None) -> str:
    return run(["ssh", SERVER, remote_cmd], timeout=timeout, tail=tail)


def health_check() -> None:
    out = run(["curl", "-sS", "--max-time", "15", HEALTH_URL])
    if DRY_RUN:
        return
    if '"ok":true' in out or '"ok": true' in out:
        print(f"[健康] {HEALTH_URL} → {out.strip()}")
    else:
        sys.exit(f"[警告] 健康检查异常：{out.strip() or '无响应'}（部署已完成，请人工排查）")


def main() -> None:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="一键 commit + push + 部署 ZhiSeek")
    ap.add_argument("-m", "--message", help="提交信息（有改动时必填）")
    ap.add_argument("-n", "--dry-run", action="store_true", help="只打印将执行的命令")
    args = ap.parse_args()
    DRY_RUN = args.dry_run

    branch = query(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if branch != BRANCH:
        sys.exit(f"[退出] 当前分支 {branch!r}，请在 {BRANCH} 分支运行")

    # ---- 1. 提交（如有改动） ----
    old_ref = query(["rev-parse", "HEAD"]).strip()
    git("add", "-A")
    staged = query(["diff", "--cached", "--name-only"]).split()
    committed = False
    if staged:
        if not args.message:
            sys.exit("[退出] 有未提交改动，必须用 -m 提供提交信息")
        print(f"[1/4] 提交 {len(staged)} 个文件…")
        git("commit", "-m", args.message, tail=3)
        committed = True
    else:
        print("[1/4] 工作区无新增改动，检查是否有未推送提交…")
        git("fetch", "origin", "--quiet")
        ahead = query(["rev-list", "--count", f"origin/{BRANCH}..HEAD"]).strip()
        if ahead == "0" and not DRY_RUN:
            print("      与 origin 完全同步，无需部署。")
            return

    new_ref = query(["rev-parse", "HEAD"]).strip()
    if committed:
        changed = query(["diff", "--name-only", old_ref, new_ref]).split()
    else:
        changed = query(["diff", "--name-only", f"origin/{BRANCH}..HEAD"]).split()

    # ---- 2. 推送 ----
    print(f"[2/4] 推送 {old_ref[:7]}..{new_ref[:7]} → origin/{BRANCH}…")
    git("push", "origin", BRANCH, tail=3)

    # ---- 3. 服务器同步 + 按需重启 ----
    print(f"[3/4] 服务器同步（{SERVER}:{REPO_DIR}）…")
    ssh(f"cd {REPO_DIR} && git fetch origin && git reset --hard origin/{BRANCH}", tail=3)

    need_backend = any(p.startswith(("pipeline/", "backend/")) for p in changed)
    need_frontend = any(p.startswith("frontend/") for p in changed)
    oauth_touched = any(p.startswith("zseek-oauth-demo/") for p in changed)
    if oauth_touched:
        print("  [告警] zseek-oauth-demo/ 有改动：请择期人工重启 zseek-demo（会登出当前 OAuth 用户）")

    if need_frontend:
        print("  frontend/ 有改动：服务器构建（约 1–2 分钟）…")
        ssh(f"cd {REPO_DIR}/frontend && set -o pipefail && NEXT_PUBLIC_API_BASE='' npm run build 2>&1 | tail -8",
            timeout=600)
        ssh("systemctl restart zseek-frontend")
        print("  zseek-frontend 已重启")
    if need_backend:
        ssh("systemctl restart zseek-backend")
        print("  zseek-backend 已重启")
    if not need_backend and not need_frontend:
        print("  仅文档/脚本改动，无需重启服务")

    # ---- 4. 健康检查 ----
    print("[4/4] 健康检查…")
    if not DRY_RUN:
        import time
        time.sleep(2)  # 等服务就绪
    health_check()
    print("部署完成。")


if __name__ == "__main__":
    main()
