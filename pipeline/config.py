"""全局配置：阈值参数、模型名、配额预算、路径。"""
import os
from pathlib import Path

# 离线优先：模型已在本地（HF_HOME），禁止连 huggingface.co 检查更新——墙内会无限退避卡死 embedding
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

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
API_HOT_LIST = "/api/v1/content/hot_list"
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
CLAIMS_BATCH_SIZE = 6          # 兼容保留：现已改为逐篇调用（一篇一次）
CLAIMS_BATCH_CHARS = 2600      # 兼容保留：现已改为逐篇调用
CLAIMS_MAX_CALLS = int(os.environ.get("CLAIMS_MAX_CALLS", "150"))  # 逐篇调用预算：须 ≥ 样本数，防超支截断
BATCH_POLL_INTERVAL = 10       # 百炼 Batch 轮询间隔（秒）
BATCH_POLL_TIMEOUT = 600        # 百炼 Batch 最长等待（秒）：队列拥堵超时后自动回退实时链（GLM→qwen）；
                               # 已提交的批任务仍在服务端，下次 force 重跑会续等其结果
BATCH_ENABLED = os.environ.get("DASHSCOPE_BATCH", "0") == "1"  # 百炼 Batch File 已实现但默认关闭（非实时，不适合 demo 现场）；
                               # 大流量跑批时设 DASHSCOPE_BATCH=1 开启，等 10 分钟不动自动回退实时链
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
D_VAGUE = 0.5              # 空泛感慨（「我当年很努力」）——与具体的差距收到 0.5，防止细节溢价过高
PERSONAL_WEIGHT = 0.6      # 个人经历主张在 V 中的整体权重（降低轶事型内容的占比）


def zhihu_token() -> str:
    token = os.environ.get("ZHIHU_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 ZHIHU_ACCESS_TOKEN，请在 .env 中配置")
    return token
