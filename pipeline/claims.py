"""LLM 客户端：分层路由。

- 结构化抽取（拆主张/时效/锚点/冲突判定）：openai-next 的 glm-4-air（便宜，
  调用结构成本大头在这层）；环境变量缺失时回退知乎直答 zhida-fast-1p5
- 生成质量任务（簇命名/挖掘理由）：知乎直答 zhida-thinking-1p5（少量调用）

实测注意：
- zhida 输出包 ```json 代码块，需剥离后解析
- 模型会脑补原文没有的信息，prompt 必须强约束 + 输出校验
- 直答额度 100/日，openai-next 按量计费（拆主张约个位数人民币/千回答问题）
"""
import json
import os
import re
import time
from typing import Any

import httpx

from . import config

# 防脑补约束：反复强调只提取原文信息
CLAIM_PROMPT = """你是信息提取器。把给定回答拆成原子主张（一句一义），并对每条主张做分类。

背景：这些回答都来自同一个知乎问题「{question_title}」。只有与这个问题直接相关、能回应这个问题的内容才值得提取。

铁律（违反即错误）：
1. 只提取原文明确表达的信息，不得补充原文没有的数字、文号、日期、实体
2. 主张必须是陈述句：疑问句/反问句要么改写成等义陈述句（如「谁说眼科医生不做近视手术？」→「眼科医生也会做近视手术」），要么丢弃
3. 丢弃与议题无关的内容（如聊戴框架眼镜的遭遇、题外故事）和纯情绪吐槽（如「真的气死了」「关键最近还把我鼻梁刮破了」）
4. 作者讲述自己的具体遭遇/感受/恢复过程（如「我术后一个月还有眩光」）是个人经历，不是普遍结论
5. 文本是公开摘要，可能截断，按现有内容提取即可

每条主张输出：
- claim: 原子主张，必须是陈述句（可轻度去口语化，但不许新增信息）
- type:
  - "evergreen"：普遍成立的知识/共识/机制/专业建议，不随时间失效
  - "sensitive"：含时效锚点或会随时间变化（价格、政策、技术版本、「目前/最新/今年」、时效数据）
  - "personal"：作者个人经历或个例感受，不代表普遍结论
- detail: 仅 "personal" 类型需要判断：true（含不可复制的具体细节：具体时间/地点/数字/对话/过程转折）或 false（空泛感慨，如「我当年很努力」）；其他类型一律 false
  判定示例：「我高考比对象多6分」「对象复读了一年」「每天给对象讲题」→ true（数字/时长/具体 recurring 行为）；「觉得自己很愚笨」「我们互不打扰」→ false（纯情绪/笼统状态）
- verifiable: true（含可自查锚点：具体数据/数字、法规条款、文献报告引用、医院/医生/机构名等可查证实体）或 false

注意："evergreen" 仅指普遍成立的知识/规律/机制；个人的立场表态（「我不支持也不反对」）按 personal（detail=false）处理；对答题行为本身的元评论（「这个问题应该提建议」）与议题无关内容直接丢弃不提取。

输出严格 JSON（不要输出任何 JSON 以外内容）：
{{"results":[{{"id":"<回答编号>","claims":[{{"claim":"...","type":"evergreen","detail":false,"verifiable":true}}]}}]}}

待拆回答：
{answers_block}"""

CONFLICT_PROMPT = """下面是同一问题下不同时间的回答主张。晚近回答可能反驳/更新了早期回答。

任务：判断「旧主张」是否被「晚近主张」实锤反驳或更新（数值变了、政策改了、结论反了）。
只有晚近主张明确表达冲突时才判 conflict；互补、补充、无关都判 ok。

输出严格 JSON：
{{"judgments":[{{"old":"<旧主张编号>","status":"conflict|ok"}}]}}

【旧主张】（来自较早发布的回答）：
{old_block}

【晚近主张】（来自更晚发布的回答，可能比旧主张晚数月或数年）：
{new_block}"""


# 数字/时长/学段等可核查碎片：命中即视为细节密度为真（只升不降，防模型漏判中文数词）
_DETAIL_SIGNAL = re.compile(
    r"[0-9０-９]"
    r"|[一二三四五六七八九十百千万两]+\s*(?:年|月|日|天|周|星期|次|分|块|岁|只|杯|件|门|个)"
    r"|去年|前年|上个月|上学期|下学期|小学|初中|高一|高二|高三|大[一二三四]"
)


def _detail_override(claim_text: str) -> bool:
    """确定性升级规则：personal 主张含数字/时长/学段碎片 → 细节密度直接置真。"""
    return bool(_DETAIL_SIGNAL.search(claim_text))


# sensitive 降级规则的词表（与 freshness.RELATIVE_TIME_WORDS 保持一致）
_TIME_WORDS = (
    "目前", "现在", "当前", "如今", "现今", "现阶段", "当下",
    "今年", "去年", "明年", "最新", "最近", "近期", "近来",
    "这两年", "这几年", "本月", "上个月", "本周", "现在市面上", "现如今", "眼下",
)
# 随时间变化的典型话题词（价格/政策/版本类）
_SENSITIVE_SIGNAL = (
    "价格", "费用", "多少钱", "收费", "薪资", "工资", "利率", "汇率", "房价", "政策",
    "法规", "规定", "条例", "版本", "补贴", "税", "分数线", "录取", "招生", "行情",
)
_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}\s*年")


def _settle_type(ctype: str, claim_text: str) -> str:
    """sensitive 后验校验：无相对时间词、无时效话题词、无年份锚点 → 降级 evergreen。
    防空泛内容误标 sensitive 造成的 F 新鲜度误罚。"""
    if ctype != "sensitive":
        return ctype
    if _YEAR_PATTERN.search(claim_text) or any(w in claim_text for w in _TIME_WORDS) or any(
        w in claim_text for w in _SENSITIVE_SIGNAL
    ):
        return "sensitive"
    return "evergreen"


def _extract_json(text: str) -> Any:
    """从模型输出中剥离 ```json 围栏并解析，失败抛异常。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("输出中无 JSON")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    return obj


class ZhidaClient:
    """知乎直答客户端（thinking 级任务 + 回退通道）。"""

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=config.ZHIHU_BASE, timeout=120.0)

    def chat(self, model: str, prompt: str, retries: int = 1) -> str:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._client.post(
                    config.API_CHAT,
                    headers={
                        "Authorization": f"Bearer {config.zhihu_token()}",
                        "X-Request-Timestamp": str(int(time.time())),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "stream": False,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001 - 重试所有可恢复错误
                last_err = e
                if attempt < retries:
                    time.sleep(2)
        raise RuntimeError(f"直答调用失败: {last_err}")

    def close(self) -> None:
        self._client.close()


def _chat_openai_next(prompt: str, retries: int = 1) -> str:
    """openai-next（GLM-4-Air 等 OpenAI 兼容接口），用于结构化抽取。"""
    base = os.environ.get("OPENAI_NEXT_BASE_URL", "").rstrip("/")
    key = os.environ.get("OPENAI_NEXT_API_KEY", "").strip()
    model = os.environ.get("OPENAI_NEXT_MODEL", "glm-4-air").strip()
    if not base or not key:
        raise RuntimeError("未配置 OPENAI_NEXT_BASE_URL / OPENAI_NEXT_API_KEY")
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.0,
                    "max_tokens": 8192,  # 大批量拆主张的 JSON 输出需要足够长度，防截断
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"openai-next 调用失败: {last_err}")


def chat_extract(prompt: str, zhida_fallback: ZhidaClient | None = None) -> str:
    """抽取级调用：优先 glm-4-air（便宜），失败回退 zhida-fast。"""
    try:
        return _chat_openai_next(prompt)
    except RuntimeError as e:
        print(f"  [提示] glm-4-air 不可用，回退直答: {e}")
        client = zhida_fallback or ZhidaClient()
        return client.chat(config.ZHIDA_FAST, prompt)


def split_claims(
    answers: list[dict[str, Any]],
    batch_chars: int = config.CLAIMS_BATCH_CHARS,
    max_calls: int = config.CLAIMS_MAX_CALLS,
) -> dict[str, list[dict[str, Any]]]:
    """批量拆主张（按字符预算打包，防长输出截断）。返回 {content_id: [claim...]}。claim 含 claim/type/verifiable。"""
    zhida = ZhidaClient()
    results: dict[str, list[dict[str, Any]]] = {}
    # 贪心打包：每批累计文本不超过 batch_chars（枚举摘要短，单批可装更多篇）
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_chars = 0
    for a in answers:
        t = len(a.get("text", ""))
        if cur and cur_chars + t > batch_chars:
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(a)
        cur_chars += t
    if cur:
        batches.append(cur)
    calls = 0
    question_title = answers[0].get("question_title", "") if answers else ""

    def _ingest(batch: list[dict[str, Any]], parsed: Any) -> None:
        id_map = {f"回答 {i + 1}": a["content_id"] for i, a in enumerate(batch)}
        id_map.update({str(i + 1): a["content_id"] for i, a in enumerate(batch)})
        for r in parsed.get("results", []):
            cid = id_map.get(str(r.get("id", "")), str(r.get("id", "")))
            claims = []
            for c in r.get("claims", []):
                claim_text = str(c.get("claim", "")).strip()
                if not claim_text or len(claim_text) < 4:
                    continue
                ctype_raw = str(c.get("type", "evergreen"))
                ctype = ctype_raw if ctype_raw in ("sensitive", "personal") else "evergreen"
                ctype = _settle_type(ctype, claim_text)
                # 逆向升级：evergreen 含相对时间词/年份锚点 → sensitive
                # （「现在高中谈恋爱的很多」锚定回答发布时间，应受时效判定；personal 不受影响——经历不过期）
                if ctype == "evergreen" and (
                    _YEAR_PATTERN.search(claim_text) or any(w in claim_text for w in _TIME_WORDS)
                ):
                    ctype = "sensitive"
                claims.append({
                    "claim": claim_text,
                    "type": ctype,
                    "detail": (bool(c.get("detail")) or _detail_override(claim_text)) and ctype == "personal",
                    "verifiable": bool(c.get("verifiable")),
                })
            results[cid] = claims

    def _process(batch: list[dict[str, Any]], tag: str) -> None:
        nonlocal calls
        if not batch:
            return
        if calls >= max_calls:
            print(f"[警告] 拆主张调用达预算上限 {max_calls}，{tag} 共 {len(batch)} 篇未处理")
            return
        calls += 1
        answers_block = "\n\n".join(
            f"[回答 {i + 1} | id={a['content_id']}]\n{a['text']}" for i, a in enumerate(batch)
        )
        prompt = CLAIM_PROMPT.format(answers_block=answers_block, question_title=question_title)
        parsed = None
        for attempt in range(2):
            try:
                parsed = _extract_json(chat_extract(prompt, zhida))
                break
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                if attempt == 1:
                    if len(batch) > 1:
                        # 输出被接口截断：拆半递归重试
                        mid = len(batch) // 2
                        print(f"  [提示] {tag} 输出截断，拆半重试")
                        _process(batch[:mid], f"{tag}.1")
                        _process(batch[mid:], f"{tag}.2")
                        return
                    print(f"[警告] {tag} 拆主张 JSON 解析失败，跳过该篇: {e}")
        if parsed:
            _ingest(batch, parsed)
            print(f"  拆主张 {tag} 完成（{len(batch)} 篇）")

    for bi, batch in enumerate(batches):
        _process(batch, f"批次 {bi + 1}/{len(batches)}")
    zhida.close()
    return results


QUERY_EXPAND_PROMPT = """用户想在知乎上收集「{title}」这个问题下尽量多不同的回答做分析。
请生成 5 个差异最大的搜索查询，覆盖不同侧重点（如：风险/体验/专业建议/亲身经历/数据对比）和不同措辞习惯，
让 5 个查询的搜索结果重叠尽可能小。每个查询 4-14 字，像真实用户在知乎搜索框输入的，不带书名号/引号。

输出严格 JSON：{{"queries": ["...", "...", "...", "...", "..."]}}"""


def expand_queries(title: str, n: int = 5) -> list[str] | None:
    """LLM 扩展子查询（glm-4-air）；失败返回 None 由调用方回退规则变体。"""
    try:
        parsed = _extract_json(chat_extract(QUERY_EXPAND_PROMPT.format(title=title)))
        queries = [str(q).strip() for q in parsed.get("queries", []) if str(q).strip()]
        seen, out = set(), []
        for q in queries:
            if q not in seen:
                seen.add(q)
                out.append(q)
        return out[:n] or None
    except Exception as e:  # noqa: BLE001
        print(f"[提示] 查询扩展失败，回退规则变体: {e}")
        return None


def judge_conflicts(old_block: str, new_block: str, max_pairs: int = 20) -> dict[str, str]:
    """二级过期判定：LLM 判断旧主张是否被晚近主张实锤反驳。返回 {old_id: 'conflict'|'ok'}。"""
    prompt = CONFLICT_PROMPT.format(old_block=old_block, new_block=new_block)
    try:
        parsed = _extract_json(chat_extract(prompt))
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 冲突判定失败，视为无冲突: {e}")
        return {}
    return {str(j.get("old")): str(j.get("status", "ok")) for j in parsed.get("judgments", [])}
