# AGENTS.md

本文件面向 AI 编码代理，介绍当前项目的状态、结构与约定。项目文档使用中文，所有协作内容请使用中文。

## 项目概览

「知寻 ZhiSeek」（原名「沧海遗珠」）是 **知乎黑客松 2026 校园新锐季 · 知识炼金场赛道** 的参赛项目。

一句话定位：**不做质量裁判，做低估探测器**——把知乎上「信息独特却零曝光」的回答挖出来。被系统挖掘出来的回答授予 🔦「沧海遗珠」徽章。

指标体系（以 `知寻-框架手册.md` 第二节第 4 条为唯一权威定义）：

```
v(主张) = rarity × [1 − (1−E)(1−C)]        # 稀有度 × 至少一根支柱（证据或印证）
V(回答) = Σrarity·v / Σrarity × (0.4+0.6F) # 稀有度加权 × 新鲜度惩罚
L∞     = L / (1 − e^{−t/τ})，封顶 5×        # 曝光时间修正，<7 天进观察池
U(回答) = percentile(V) − percentile(L∞)   # 问题内百分位差 = 低估指数
M(问题) = Top3 曝光占比 − Top3 稀有度加权主张占比   # 信息垄断度
徽章：🔦 沧海遗珠 U>0.6 / 🌱 潜力新声 U>0.3 且<90天 / ⏰ 内容待更新 F<0.4
```

注意：旧文档中「低估指数 = 信息价值分 ÷ 曝光量」的表述已废弃，一律以百分位差为准。

## 当前仓库状态

已有一个可跑的 Python 分析 pipeline（conda 环境 **ZhiSeek**，python 3.12）：

| 路径 | 说明 |
| --- | --- |
| `pipeline/` | 分析核心：`fetch`（双通道抓取：LLM×模板多查询搜索 + 从回答 URL 反推问题链接自动启用枚举通道）→ `claims`（拆主张+三分类+冲突判定，**一篇一次 LLM 调用不复用**；GLM-4.7-Flash→qwen→直答分层路由，解析失败跳过该篇；**文章级缓存** `data/cache/_articles/<sha1(content_id)>.json`：同一篇回答跨话题/中断重跑零消耗复用）→ `embed`（本地 bge-small-zh）→ `cluster`（UMAP+HDBSCAN）→ `freshness`（三级过期判定）→ `score`（指标体系：事实类 E 或 C 支柱、经历类 D（细节密度，含细节→1/空泛→**0.5**，**personal 整体降权 0.6** 压轶事占比）或 C 支柱）→ `report`（命名/理由）→ `run`（CLI 编排，分层缓存于 `data/cache/`） |
| `backend/app.py` | FastAPI：POST /api/analyze 启动分析；GET /api/jobs/{key}/events SSE 分阶段进度；GET /api/result/{key} 结果；GET /api/topics 已分析议题列表；GET /api/hot-board 垄断度热榜（读 data/campaign/monopoly_rank.json，前端首页「今日最被垄断的问题」） |
| `frontend/` | Next.js + Tailwind v4 + ECharts，按 `知寻ZhiSeek-UI设计规范.md`（深色知识星图 + 遗珠金 #E8B84B，唯一高饱和色）实现三页状态机：首页（scroll-snap 整屏翻页三页：①寻找=输入+我的遗珠报告 ②最被低估的回答=`/api/pearls` 跨议题 U 值榜 ③垄断度热榜只露 3 条+查看全部；右侧圆点导航锁定页）→ 分析中仪器日志页（星点渐亮）→ 结果三栏仪表盘（左 20% 问题档案+垄断度仪表+簇图例降序可聚焦、微阵营(≤2 条)与未入簇主张并入可展开的少数派报告 / 中央 55% 可缩放星图，沧海遗珠为金色星形+光晕，hover 为浅底纸感摘要卡 / 右 25% 遗珠清单+按增量/热度切换）。点击任意点/卡片弹出纸感原文档案（四维雷达 + 主张列表，独占主张金色 `✦ 独占` 标记）。组件：ScatterMap / AnalyzingView / AnswerArchive / PersonalBoard（个人遗珠：仅已登录用户渲染，未授权静默隐藏，入口为页头「测测我的遗珠」金色描边按钮；登录后拉取「我的创作」，逐问题查/触发知寻分析，把本人回答映射回 U/V/徽章/曝光-价值分位）/ theme.ts。首页结构以 `知寻ZhiSeek-UI设计规范.md` 第四节为准：一屏一事（标题→输入框→社会证明→垄断度榜单只露 3 条+查看全部），不放登录墙、不放问题标签云。`npm run dev`（端口 3000，后端 8000） |
| `tests/` | 冒烟测试 |
| `scripts/` | 数据战役脚本：`collect_stage0.py`（议题阶段 1 批量采集）/ `fit_tau.py`（τ 分桶拟合）/ `sensitivity_tau.py`（τ 敏感性）/ `run_campaign.py`（18 议题全量跑通+垄断榜）/ `sample_blind.py` + `analyze_blind.py`（盲标抽样与验证）/ `llm_blind.py`（LLM 第二标注人盲标+一致性分析，产物 blind_llm_*） |
| `data/campaign/` | 数据战役产物（gitignore）：stage0_meta.jsonl、tau_fit.json/.png、tau_sensitivity.json/.png、monopoly_rank.json、blind_*（盲标样本/标签/分析/图） |
| `data/fallback/` | 赛前预跑的保底议题数据 |
| `知寻-框架手册.md` | 核心文档（原赛前作战手册）：痛点、指标体系、接口现状、排期、路演脚本、Q&A、风险预案 + 「数据战役实测结果」节（τ 拟合/敏感性、18 议题垄断榜、盲标验证） |
| `知寻-数据战役计划.md` | 数据战役作战计划（τ 拟合 + 垄断度热榜 + 指标验证三线复用一次采集） |
| `知寻ZhiSeek-UI设计规范.md` | UI 唯一权威：设计令牌（色板/字号/字体）、三页结构、动效规范（只做 3 个）、自检清单 |
| `相关通知.md` | 赛事群通知：报名链接、必读资料、官方 skill 下载地址 |
| `environment.yml` | conda 环境定义（ZhiSeek） |
| `.env` | 凭证（gitignore）：`ZHIHU_ACCESS_TOKEN`（开放平台 Access Secret）+ `ZHIPU_BASE_URL/ZHIPU_APK_KEY/ZHIPU_MODEL`（GLM-4.7-Flash，优先）+ `DASHSCOPE_BASE_URL/API_KEY/MODEL`（qwen3.7-flash，回退+拆主张 Batch File）+ `OPENAI_NEXT_*`（glm-4-air，额度已弃用保留） |

常用命令：

```bash
conda activate ZhiSeek
python -m pipeline.run "近视手术安全吗" [--question-url URL] [--topn 10] [--force] [--no-llm]
python scripts/collect_stage0.py   # 阶段 0 采集（复用缓存，断点续跑）
python scripts/run_campaign.py     # 战役全量跑通（18 议题，逐议题入垄断榜）
pytest tests/
```

## 生产部署（2026-09-13 起）

- **线上地址**：https://zseek.jeefy.top/ （服务器 36.151.145.113，Ubuntu 22.04，root SSH）
- **代码位置**：`/srv/zseek/`（backend + pipeline + data + frontend + zseek-oauth-demo + venv + .env[600]）
- **端口**（全部 127.0.0.1，nginx 反代对外）：后端 8010、前端 3100、OAuth demo 4173（服务器 8000 被 jeefy-tools 占用、3000 被旧 nginx 站点占用，勿用）
- **systemd**：`zseek-backend` / `zseek-frontend` / `zseek-demo`（`systemctl status|restart`）
- **nginx**：`/etc/nginx/sites-available/zseek`——`/`→3100、`/api/`→8010（SSE 已关 buffering）、`/login/`+`/auth/`+`/api/oauth/`→4173；HTTPS 由 certbot 管理（`zseek.jeefy.top` 证书，HTTP 301 到 HTTPS）
- **OAuth demo**：`zseek-oauth-demo/`（官方 hello-world OAuth 版 + 个人遗珠扩展，挂载在 /login/，回调 `https://zseek.jeefy.top/auth/callback`）；密钥在服务器 `/srv/zseek/oauth-demo.env`（600，ZHIHU_OAUTH_APP_KEY + ZHIHU_ACCESS_SECRET），不入库、不进命令行；`POST /api/oauth/personal-u` 返回个人遗珠报告（未授权返回 LOGIN_REQUIRED）
- **前端构建**：`NEXT_PUBLIC_API_BASE="" npm run build`（同源走 nginx，默认值的 localhost:8000 只适用本地开发）
- **嵌入模型**：bge-small-zh 缓存在服务器 `/root/.cache/huggingface`（venv 已设 HF_HOME）
- **LLM 路由（2026-09-13 切换）**：实时链 GLM-4.7-Flash（ZHIPU_*，优先）→ qwen3.7-flash（DASHSCOPE_*，回退）→ 知乎直答（额度见底，最后兜底）；百炼 Batch File 已实现（JSONL→/files→/batches→轮询→下载，半价，断点续等靠 `data/cache/<key>/2_batch.json`），但**默认关闭**（`DASHSCOPE_BATCH=0`，非实时不适合 demo 现场；智谱也有 batch file 同理不用），大流量跑批时设 `DASHSCOPE_BATCH=1`；openai-next（glm-4-air）额度已弃用
- **个人遗珠不自动烧 token**：`/api/oauth/personal-u` 仅 `{"analyze":[qid...]}` 触发所选问题分析，缓存命中免费直出，前端勾选后轮询
- **改动同步**：本地改代码后 `tar czf - --exclude=frontend/node_modules --exclude=frontend/.next backend pipeline data frontend | ssh root@36.151.145.113 "tar xzf - -C /srv/zseek"`，前端需重新 build，再 `systemctl restart zseek-*`

## 数据接口事实（2026-09-11 实测，细节见作战手册二.7）

- **没有**「按问题拉全量回答+赞同数」的组合接口（`hot_list`：`GET /api/v1/content/hot_list?Limit≤30`，Bearer 鉴权）。双通道：
  - 曝光通道 `zhihu_search`（5000/日）：每查询 ≤10 条，**`Offset` 实测不支持翻页**（返回相同结果），返回 ~1000 字截断摘要 + 赞同数/作者/时间 → 低估指数的计算样本（实测 ~10 篇/议题）
  - 枚举通道 `question_answers`（100/日，**文档未记载**）：按问题 URL 深分页（实测 520+），短摘要无赞同数 → 并入样本补充观点覆盖（votes_unknown 不进低估判定）；问题链接由搜索 Question 条目匹配或从回答 URL 反推
  - 召回扩大靠「LLM 扩展 5 子查询 + 模板变体并行」（共 ~8 查询），实测样本 7 → 51 篇
- LLM **分层路由**：结构化抽取（拆主张/时效/锚点/冲突判定）走 openai-next 的 **glm-4-air**（便宜，失败回退直答 fast）；生成质量任务（簇命名/挖掘理由）走**知乎直答 thinking**（100/日，批量打包），单议题 ≤10 次
- 拆主张输出三分类（glm-4-air 在阶段 2 逐条判定，**与聚类无关**）：`evergreen` 普遍知识 / `sensitive` 时效锚点 / `personal` 个人经历个例（不计时效惩罚）；`verifiable` 按是否含可自查锚点（具体数据/法规/文献/机构名）判定
- 时效判定**三级**：① 相对时间词+回答发布时间纯规则判「锚点失效」② 晚近回答反驳旧主张的内部冲突检测（复用 embedding + 便宜 LLM）③ 外部核验只抽查高稀有候选（MVP 为桩，未核验诚实降级「疑似过期」）
- 中文请求**必须用 Python httpx**，shell curl 的 GBK 编码会触发 90001
- embedding 用本地 `BAAI/bge-small-zh-v1.5`（sentence-transformers，离线）

## 关键设计立场（不可违背的产品哲学）

1. **不判断回答好坏**，只测两个可客观计算的信号：信息增量（别处没有的内容）与可核验性（敢不敢给读者自查的锚点）
2. 排序做成**可切换的双视图**，不替换知乎原排序；挖掘理由必须透明展示
3. **诚实降级**：核验不了的信息标注「⏰ 时效敏感但未核验」，系统承认不知道
4. 不验证引用对错，只验证「敢不敢让你查」；不输出「对错判决」，只输出「新鲜度衰减分」
5. 明确不做的：3D 图、力导向图、花哨动画

## 开发约定与流程

- 48 小时排期与验收标准详见作战手册第五节，铁律：**D1 结束时必须有端到端能跑的丑版本**
- pipeline 分层缓存（`data/cache/<key>/1_search…6_report`），任何阶段失败可断点续跑；改指标公式时删掉对应缓存层重跑
- API key 等凭据只在 `.env`，不写入代码/文档/日志
- 调用官方接口注意配额（`quota` 接口可查），禁止高频重试

## 测试与验证策略

- `tests/test_smoke.py`：单议题端到端冒烟（优先走缓存，不耗 API 额度）
- 人工复核标准：拆主张无明显脑补（对照原文）、低估榜确实出现「低赞+独特」回答、每个 Demo 议题至少 2–3 篇 🔦 遗珠
- 指标有效性验证（2026-09-12）：3 议题分层盲标 42 篇人工分层单调（遗珠层值得率 67% vs 头部 44%，KW p=0.17），LLM 第二标注人 deepseek-v4-flash 与人工 κ=0.60；**扩样 112 篇（9 议题）后分层信号消失（KW p=0.63，徽章级同样不成立）**，候选解释=评审口径不认一手经历 vs 指标议题泛化性，待人工标注仲裁；详见框架手册「数据战役实测结果」节

## 外部资源

- 参赛流程指南：https://pcnsiq9mmnww.feishu.cn/wiki/Pd1UwIIBriW0DBk8qlIczBAVnJc
- 参赛规则文档：https://my.feishu.cn/docx/Mc80dR5XvoPaYDxcTasc04POnjd
- 官方 skill 下载：https://developer-cdn.zhihu.com/zhihu-cli/releases/beta/skill/0.5.3-beta.20260904115023/zhihu-cli-skill-0.5.3-beta.20260904115023.zip

## 安全与合规注意事项

- 项目处理知乎用户生成内容，生成「低估报告」涉及答主，注意仅在授权范围内使用数据
- 官方接口有配额与频率限制，应用层必须缓存与去重
- 凭证不出现在仓库、日志、截图、录屏中
