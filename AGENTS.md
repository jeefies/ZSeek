# AGENTS.md

本文件面向 AI 编码代理。项目协作语言为中文。原则：每条信息都回答「没有它代理一定会踩坑吗」，否则不写。

## 项目概览

「知寻 ZhiSeek」（目录名「沧海遗珠」）：知乎黑客松 2026 参赛作品——**不做质量裁判，做低估探测器**，挖出知乎上「信息独特却零曝光」的回答，授 🔦「沧海遗珠」徽章。

指标体系唯一权威定义在 `知寻-框架手册.md` 二.4，实现即 `pipeline/score.py` 的模块 docstring：

```
v(主张) = rarity × [1 − (1−E)(1−C)]        # 稀有度 × 至少一根支柱（E=可核验锚点 / C=簇内独立答主印证）
V(回答) = Σrarity·v / Σrarity × (0.4+0.6F) # F=新鲜度（仅统计 sensitive 主张：过期×0.7 + 未核验×0.3）
L∞     = L / (1 − e^{−t/τ})，封顶 5×，τ=365d   # 曝光时间修正；发布 <7 天进观察池，不做低估判定
U(回答) = percentile(V) − percentile(L∞)   # 问题内百分位差 = 低估指数（旧文档「价值÷曝光」表述已废弃）
M(问题) = Top3 曝光占比 − Top3 稀有度加权主张占比   # 垄断度
徽章：🔦 U>0.6 / 🌱 U>0.3 且<90天 / ⏰ F<0.4
```

经历类（personal）支柱走 D（细节密度：含具体细节=1.0 / 空泛=0.5，见 `config.D_DETAIL/D_VAGUE`），且 personal 主张整体降权 `PERSONAL_WEIGHT=0.6`。

## 环境与命令

- **conda 环境 `ZhiSeek`（python 3.12）**：`conda activate ZhiSeek`。所有 python 命令从仓库根目录跑（模块路径 `pipeline.*`，脚本自行把根目录插入 `sys.path`）。系统 python 没有 numpy/umap/hdbscan，跑不了。
- 分析 pipeline：`python -m pipeline.run "近视手术安全吗" [--question-url URL] [--topn 10] [--force] [--no-llm]`（`--no-llm` 的主张=整段摘要，仅调试用）
- 战役脚本：`python scripts/collect_stage0.py`（18 议题阶段 1 采集，断点续跑）→ `python scripts/run_campaign.py [--only 关键词]`（全量跑+垄断榜）
- 测试：`pytest tests/`（全部合成数据，**不耗 API 额度**）；单测：`pytest tests/test_smoke.py::test_score_synthetic`
- 后端：`python -m uvicorn backend.app:app --port 8000`；前端：`cd frontend && npm run dev`（3000 端口）
- `data/` 整个目录 gitignored（可再生缓存+战役产物，git 里没有）；`.env` 在 `.gitignore`

## pipeline 结构（`pipeline/`）

`fetch → claims → embed(bge-small-zh) → cluster(UMAP+HDBSCAN) → freshness → score → report`，`run.py` 编排。关键工程事实：

- **分层缓存** `data/cache/<key>/<stage>`，层名：`1_search / 2_claims / 3_embeddings.npy / 4_cluster / 4b_freshness / 5_score /6a_names / 6_report`。改某阶段逻辑就删对应层（`--force` 全删）；缓存命中重跑 0 API 调用。已跑过的议题不要 `--force` 重跑。
- 搜索通道多查询变体**线程池并行**（`SEARCH_WORKERS` 默认 4；撞 30001 限速由退避兜底），按变体顺序合并，输出与串行确定性一致。枚举通道深分页只能串行，`QA_MAX_ITEMS` 默认 100。
- **文章级拆主张缓存** `data/cache/_articles/<sha1(content_id)>.json`：同一篇回答跨议题/中断重跑零消耗复用。
- 拆主张**一篇一次 LLM 调用不复用不打包**；调用预算 `CLAIMS_MAX_CALLS`（默认 150，env 可调），预算耗尽跳过剩余篇；解析失败跳过该篇。注入的本人回答（`mine_` 前缀 content_id）排最前且 LLM 零主张时整段兜底为 1 条 personal。
- embedding 离线：`config.py` 强制 `HF_HUB_OFFLINE=1`（墙内联网检查会卡死），模型缓存在 `$HF_HOME`。
- 时效判定三级：①相对时间词+发布时间纯规则判「锚点失效」②晚近回答反驳旧主张的内部冲突（复用 embedding+便宜 LLM）③外部核验=**桩**（MVP 未实现，诚实降级「疑似过期」）。
- 后验确定性规则（有单测锁定，改动需同步测试）：personal 含数字/时长/学段碎片→detail 强制置真；sensitive 无时间锚点→降级 evergreen；evergreen 含相对时间词→升级 sensitive。

## 知乎接口硬事实（2026-09-11 实测，细节见框架手册二.7）

- 中文请求**必须 Python httpx**，shell curl 的 GBK 编码触发错误码 90001。
- Code 30001=突发限速，fetch.py 自动退避 15s×3 重试；其余错误码立即抛。
- `zhihu_search`（5000/日）：每查询 ≤10 条，`Offset` **不支持翻页**（HasMore 恒 false）；~1000 字截断摘要 + 赞同数/作者/时间 → 低估判定的曝光样本来源。召回扩大靠 LLM 扩展子查询+模板变体（~8 查询/议题）。
- `question_answers`（100/日，**官方文档未记载**，从回答 URL 反推 `/question/xxx` 启用）：深分页，短摘要**无赞同数** → 标 `votes_unknown`，只参与聚类/观点覆盖，**不进 U 判定**。配额要紧：`QA_MAX_ITEMS` 默认 100。
- `hot_list`（100/日）：仅 Demo 选题入口。
- **没有**「按问题拉全量回答+赞同数」的接口。额度自查：`quota` 接口（`ZhihuClient.quota()`），禁止高频重试。

## LLM 路由与 env（`.env`，凭证不入库/不进日志）

- 结构化抽取（拆主张/冲突判定/查询扩展）：**qwen3.7-flash 无思考优先**（`DASHSCOPE_*`，`enable_thinking=false`）→ GLM-4.7-Flash（`ZHIPU_*`）回退 → 知乎直答 `zhida-fast-1p5` 兜底。生成质量（簇命名/挖掘理由）：GLM→qwen→`zhida-thinking-1p5`，单议题 ≤`REPORT_MAX_CALLS=10` 次。
- 注意 `.env` 里智谱 key 是历史拼写 **`ZHIPU_APK_KEY`**，代码 `ZHIPU_API_KEY` 优先、回落兼容该拼写——别当 typo「修正」。
- 百炼 Batch File（JSONL→/files→/batches→轮询，半价）已实现但**默认关**（`DASHSCOPE_BATCH=0`，非实时不适合演示；大流量跑批才设 1）。断点续等靠 `data/cache/<key>/2_batch.json`。
- `OPENAI_NEXT_*`（glm-4-air）额度已弃用，代码保留备用。
- 另：`ZSEEK_ANALYZE_CONCURRENCY`（后端 `/api/analyze` 全局并发闸，默认 2）；`SEARCH_WORKERS`/`CLAIMS_WORKERS`（搜索变体/拆主张线程池并发，均默认 4）。

## 后端与前端

- `backend/app.py`（FastAPI）：`POST /api/analyze`（后台任务，返回缓存 key 作 job_id；可带 `include_answers` 注入本人回答）、`GET /api/jobs/{key}/events`（SSE 阶段进度）、`GET /api/jobs/{key}/status`（轻量轮询，个人遗珠用）、`GET /api/result/{key}`、`GET /api/topics`、`GET /api/pearls`（跨议题低估榜 Top12）、`GET /api/hot-board`（读 `data/campaign/monopoly_rank.json`）。CORS 只放行 localhost:3000。
- `frontend/`：Next.js **16.3.4** + React 19 + Tailwind v4 + ECharts。**Next 16 与训练数据中的 Next.js 有破坏性差异——动手前先读 `frontend/AGENTS.md`（next dev 自动生成，勿手改）并查 `node_modules/next/dist/docs/`**。
- 前端 API 基址 `NEXT_PUBLIC_API_BASE`，默认 `http://localhost:8000`；**生产构建必须 `NEXT_PUBLIC_API_BASE=""`**（同源走 nginx 反代，否则打到访问者本机）。
- UI 唯一权威 `知寻ZhiSeek-UI设计规范.md`（深色星图+遗珠金 #E8B84B 唯一高饱和色；全站仅 3 个动效；明确不做 3D/力导向图）。页面前端细节不抄进本文件。

## 生产部署（git 同步，2026-09-15 起）

- **约定：每次代码修改后立即同步部署，一键命令**：`python scripts/deploy.py -m "提交信息"`（commit+push+服务器 fetch/reset+按改动范围重启+健康检查；`-n` 只演习）。手动流程作为兜底：
  本地 `git push origin main` → 服务器 `cd /srv/zseek && git fetch origin && git reset --hard origin/main` → 按改动重启。
- 线上 https://zseek.jeefy.top/（36.151.145.113，Ubuntu，root SSH）；仓库 git@github.com:jeefies/ZSeek.git（main 分支），服务器 `/srv/zseek/` 即该仓库的 checkout。
- **`.env` 永不进 git**（.gitignore 已排除）：服务器 `/srv/zseek/.env` 为服务器本地维护的凭证文件，改凭证直接上服务器改；`oauth-demo.env`（OAuth demo 密钥）同理。服务器独有文件靠 `.git/info/exclude` 排除（venv/、oauth-demo.env、*.log）。
- `data/` 不进 git：缓存与战役产物以服务器本地为准（`monopoly_rank.json` 等已在服务器）。
- 重启规则：`pipeline/`、`backend/` 改动 → `systemctl restart zseek-backend`；`frontend/` 改动 → 服务器上 `NEXT_PUBLIC_API_BASE="" npm run build` 后 `systemctl restart zseek-frontend`；**勿重启 zseek-demo**（OAuth token 在内存，重启即登出用户）。
- 端口全绑 127.0.0.1 由 nginx 反代：后端 8010、前端 3100、OAuth demo 4173（**勿用 8000/3000**，被占）。验证：`curl https://zseek.jeefy.top/api/health`。
- `zseek-oauth-demo/`：`POST /api/oauth/personal-u {"analyze":[qid...]}` 触发所选问题分析（缓存命中免费；未授权返回 LOGIN_REQUIRED）。

## 产品哲学红线（改代码不得违背）

1. 不判断回答好坏，只算两个可客观信号：信息增量（稀有度）与可核验性（锚点）
2. 排序是可切换双视图，不替换知乎原排序；挖掘理由必须透明展示
3. 诚实降级：核验不了就标「⏰ 时效敏感但未核验」，系统承认不知道
4. 不验证引用对错，只验证「敢不敢让你查」

## 当前验证状态（决定能否动指标权重）

- 42 篇人工盲标分层单调（遗珠层值得率 67% vs 头部 44%），LLM 第二标注人 κ=0.60；**扩样 112 篇（9 议题）后分层信号消失（KW p=0.63）**，候选解释=评审口径不认一手经历 vs 议题泛化性，待人工仲裁——**仲裁前不要调权重**（42 篇上调参有过拟合风险）。详见框架手册「数据战役实测结果」节。

## 文档索引

- `知寻-框架手册.md`：指标体系、接口实测、战役结果、路演 Q&A（技术唯一权威）
- `知寻ZhiSeek-产品说明计划书.md`：**旧版**技术方案，已被框架手册取代——勿引用其中现状描述
- `知寻ZhiSeek-UI设计规范.md`：UI 唯一权威
- `知寻-数据战役计划.md` / `相关通知.md`：战役方案与赛事通知
