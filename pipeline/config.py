"""全局配置：阈值参数、模型名、配额预算、路径。"""
import os
from pathlib import Path

# ---- 路径 ----
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
FALLBACK_DIR = DATA_DIR / "fallback"

# ---- 知乎官方 API ----
ZHIHU_BASE = "https://developer.zhihu.com"
API_SEARCH = "/api/v1/content/zhihu_search"
API_QUESTION_ANSWERS = "/api/v1/content/question_answers"
API_QUOTA = "/api/v1/quota"
API_CHAT = "/v1/chat/completions"

# ---- LLM（知乎直答）----
ZHIDA_FAST = "zhida-fast-1p5"          # 拆主张 + 时效/可核验分类（便宜、批量）
ZHIDA_THINKING = "zhida-thinking-1p5"  # 簇命名、挖掘理由（少量、质量优先）

# ---- 抓取参数 ----
SEARCH_COUNT = 10              # 单次搜索上限（接口硬限制 10）
SEARCH_VARIANTS_DEFAULT = 3    # 子查询变体数（含原标题）
QA_PAGE_LIMIT = 20             # question_answers 每页条数
QA_MAX_ITEMS = 200             # 单议题枚举上限（配额 100/日，≤10 次调用）
KEEP_CONTENT_TYPE = "Answer"   # 只保留回答，剔除文章

# ---- 直答调用预算 ----
CLAIMS_BATCH_SIZE = 6          # 兼容保留：现按 CLAIMS_BATCH_CHARS 打包
CLAIMS_BATCH_CHARS = 2600      # 每次拆主张调用的文本字符预算（防 JSON 输出截断）
CLAIMS_MAX_CALLS = 30          # 拆主张阶段调用预算（含截断拆半的余量，glm 按量计费可控）
REPORT_MAX_CALLS = 10          # 命名 + 理由阶段调用预算

# ---- embedding / 聚类 ----
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
UMAP_N_COMPONENTS = 8          # 聚类用中间维度
HDBSCAN_MIN_CLUSTER_SIZE = 3

# ---- 评分参数（依据作战手册「指标体系：低估指数的完整定义」）----
EXPO_TAU_DAYS = 365        # 点赞饱和积累时间常数 τ，赛前用保底数据拟合校准
EXPO_CAP = 5.0             # 曝光时间修正倍数封顶
OBSERVATION_DAYS = 7       # 发布 <7 天进观察池，不做低估判定
BADGE_UNDISCOVERED = 0.6   # 🔦 沧海遗珠：U > 0.6
BADGE_RISING = 0.3         # 🌱 潜力新声：U > 0.3 且发布 <90 天
BADGE_STALE_F = 0.4        # ⏰ 内容待更新：F < 0.4
MONOPOLY_TOP_K = 3         # 垄断度统计的头部回答数

# 经历类（personal）主张的 D 支柱（细节密度，见手册类型—支柱对照）
D_DETAIL = 1.0             # 含不可复制具体细节（时间/地点/数字/对话/转折）
D_VAGUE = 0.2              # 空泛感慨（「我当年很努力」）


def zhihu_token() -> str:
    token = os.environ.get("ZHIHU_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 ZHIHU_ACCESS_TOKEN，请在 .env 中配置")
    return token
