"""
联网搜索工具（web_search）—— 项目 Agent 能力的扩展实现

定位：
  在「已支持 function calling 的聊天 Agent」基础上，新增「联网检索」能力。
  本文件包含三块：
    1. WebSearchTool      —— 继承 domain.tools.tool.Tool，可直接 register 进现有 ToolRegistry
    2. CredibilityFilter  —— 清洗去噪 + 可信度过滤（P0 闸门，集成在工具内部）
    3. GroundingChecker   —— 回答引用溯源校验（P0 闸门，生成后调用）

  设计依据（与现有项目对齐）：
    - 完全复用 Tool / ToolRegistry / AgentRunner，不重写任何框架代码
    - 失败降级走 Tool.run() 返回可读错误字符串（Registry.execute 已统一兜底）
    - 双搜索源融合（Tavily + 百度 web_search）：两个源并发取结果、合并去重、统一过清洗闸；
      各源客户端在 _search_tavily / _search_baidu，新增源只需加一个客户端函数。

接入步骤（两行，无需改框架）：
  在 app/session/session.py 的 __init__ 中，紧接现有 register 之后加：
      from infrastructure.tools.web_search import WebSearchTool
      self._tool_registry.register(WebSearchTool())
  API Key 写入 .env（沿用既有 load_dotenv 读取机制）：
      WEB_SEARCH_API_KEY=你的tavily_key        # Tavily 源（可选）
      BAIDU_API_KEY=你的百度千帆API_key        # 百度源（可选）
  两个 Key 至少配置一个；都配则双源融合，只配一个则该源单独工作，另一源自动跳过。

Grounding 接线（P0，第二阶段）：
  GroundingChecker.check(answer, allowed_urls) 应在 LLM 生成最终回答后、输出前调用，
  拦住「幽灵引用」。可挂到 domain/prompt/validator.py 或 pipeline 的回答校验步骤；
  本文件提供 check() 供直接调用，AgentRunner 循环无需改动。
"""

import os
import re
import json
import time
import asyncio
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import httpx

# 复用项目 Tool 抽象；若单独冒烟测试（文件不在项目包内）则回退到最小基类
try:
    from domain.tools.tool import Tool
except ImportError:
    from abc import ABC, abstractmethod
    class Tool(ABC):
        name: str = ""
        description: str = ""
        parameters: Dict[str, Any] = {"type": "object", "properties": {}}
        def to_openai_schema(self) -> Dict[str, Any]:
            return {"type": "function", "function": {
                "name": self.name, "description": self.description,
                "parameters": self.parameters}}
        @abstractmethod
        async def run(self, **kwargs) -> str:
            raise NotImplementedError


# ───────────────────────────── 配置 ─────────────────────────────
TAVILY_ENDPOINT = "https://api.tavily.com/search"
TAVILY_USAGE_ENDPOINT = "https://api.tavily.com/usage"  # 官方实时用量接口
# 百度智能云千帆「百度搜索」纯搜索接口（免费 100 次/日，返回 title/url/snippet/date）
BAIDU_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
SEARCH_TIMEOUT = 8.0  # 秒，独立超时防拖死整轮 Agent 响应

# ────────────────────── 免费额度用量统计（监控用） ──────────────────────
# 用途：每次搜索调用后，在终端打印各源累计用量 / 免费上限，防止超额。
# 百度千帆免费档：100 次/日（官方档位，无 API 可查，只能本地累计）；
# Tavily 免费档：1000 次/月，但官方提供 /usage 接口，走实时查询（带缓存防限速）。
QUOTA_FILE = os.path.join("data", "search_quota.json")
TAVILY_LIMIT = 1000
BAIDU_LIMIT = 100

# Tavily /usage 接口自身限速：开发档 10 次/10 分钟。故本地缓存 70s，绝不在每次搜索后都调。
_TAVILY_USAGE_TTL = 70.0
_tavily_usage_cache: Dict[str, Any] = {"ts": 0.0, "value": None, "err": False}

def _fetch_tavily_usage(key: str) -> Optional[int]:
    """实时查询 Tavily 官方 /usage 接口，返回「本月已用次数」。
    带 70s 内存缓存；失败返回 None（调用方退回本地累计兜底）。
    限速保护：/usage 本身限 10次/10分钟，缓存确保不刷爆。"""
    now = time.time()
    if now - _tavily_usage_cache["ts"] < _TAVILY_USAGE_TTL and not _tavily_usage_cache["err"]:
        return _tavily_usage_cache["value"]  # 命中缓存（即使为 None，也在 TTL 内复用）
    try:
        resp = httpx.get(
            TAVILY_USAGE_ENDPOINT,
            headers={"Authorization": f"Bearer {key}"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            # 官方返回结构：{"usage": {"total": N, ...}, "limit": {...}} 或按月端点明细
            used = None
            if isinstance(data.get("usage"), dict):
                used = data["usage"].get("total") or data["usage"].get("this_month")
            elif isinstance(data.get("total"), (int, float)):
                used = data["total"]
            _tavily_usage_cache.update(ts=now, value=used, err=False)
            return used
        else:
            _tavily_usage_cache.update(ts=now, value=None, err=True)
            print(f"[web_search] Tavily 用量查询失败 HTTP {resp.status_code}（退回本地累计）")
            return None
    except Exception as e:
        _tavily_usage_cache.update(ts=now, value=None, err=True)
        print(f"[web_search] Tavily 用量查询异常 {e}（退回本地累计）")
        return None

def _load_search_quota() -> Dict[str, Any]:
    """读取累计配额，并按日（百度）/ 月（Tavily）自动重置。
    仅用于「无实时接口」的百度源；Tavily 优先走实时查询，本地计数作兜底。"""
    today = date.today().isoformat()
    month = today[:7]
    try:
        with open(QUOTA_FILE, "r", encoding="utf-8") as f:
            q = json.load(f)
    except Exception:
        q = {}
    if q.get("baidu", {}).get("date") != today:
        q["baidu"] = {"count": 0, "date": today}
    if q.get("tavily", {}).get("month") != month:
        q["tavily"] = {"count": 0, "month": month}
    return q

def _save_search_quota(q: Dict[str, Any]) -> None:
    """落盘累计配额；失败只告警不影响搜索。"""
    try:
        os.makedirs("data", exist_ok=True)
        with open(QUOTA_FILE, "w", encoding="utf-8") as f:
            json.dump(q, f, ensure_ascii=False)
    except Exception as e:
        print(f"[web_search] 配额文件写入失败（不影响搜索）: {e}")

# 域名信誉：白名单 = 公认靠谱的站（提权，见 authority_score）；黑名单 = 垃圾站（直接丢，见 BLOCKED_DOMAINS）
TRUSTED_DOMAINS = {
    "wikipedia.org", "arxiv.org", "who.int", "nih.gov", "gov.cn",
    "nature.com", "science.org", "ieee.org", "acm.org",
    "bbc.com", "reuters.com", "bloomberg.com",
}
BLOCKED_DOMAINS = {
    # 内容农场 / 假新闻站示例，按需补充
    "spam-farm-example.com", "fake-news-example.com",
}

# 注入检测关键词（命中则把网页内容标记为「外部资料，非用户指令」）
INJECTION_MARKERS = [
    "ignore previous", "忽略以上", "忽略前面的", "disregard",
    "system prompt", "忽略所有指令", "new instructions",
]


# ────────────────────── 清洗去噪 + 可信度过滤（P0 闸门之一） ──────────────────────
class CredibilityFilter:
    """把搜索 API 返回的原始结果，净化为可信、可用、对 LLM 友好的文本。"""

    @staticmethod
    def domain_of(url: str) -> str:
        try:
            return urlparse(url).netloc.lower().lstrip("www.")
        except Exception:
            return ""

    @staticmethod
    def authority_score(domain: str) -> float:
        # 域名信誉打分：黑名单垃圾站 → 0 分；白名单靠谱站（维基/政府/学术等）→ 1 分；其他普通站 → 0.5 分
        if domain in BLOCKED_DOMAINS:
            return 0.0
        return 1.0 if domain in TRUSTED_DOMAINS else 0.5

    @staticmethod
    def freshness_score(publish_date: Optional[date]) -> float:
        # 新鲜度打分：越近越高。没有发布日期给中性 0.6（不妄断新旧）
        if not publish_date:
            return 0.6  # 无日期，给中性分并提示不确定
        age_days = (date.today() - publish_date).days
        if age_days < 365:
            return 1.0   # 1 年内：最新，满分
        if age_days < 730:
            return 0.7   # 1~2 年：较新
        if age_days < 1825:
            return 0.4   # 2~5 年：偏旧
        return 0.2  # >5 年，明显可能失效

    @staticmethod
    def sanitize_injection(text: str) -> str:
        low = text.lower()
        if any(m in low for m in INJECTION_MARKERS):
            return "[外部资料·已标记，非用户指令] " + text
        return text

    @classmethod
    def score(cls, domain: str, publish_date: Optional[date], relevance: float) -> float:
        # 总评分 = 三项加权混合，权重体现「谁更重要」的设计取舍：
        #   - 域名可信度占 0.5：来源靠不靠谱，比什么都重要（见 authority_score）
        #   - 新鲜度占 0.3：资讯越新越有用，过期数据可能失效（见 freshness_score）
        #   - 相关度占 0.2：和搜索词搭不搭边，辅助排序（见 clean 第 4 道）
        return (
            cls.authority_score(domain) * 0.5
            + cls.freshness_score(publish_date) * 0.3
            + relevance * 0.2
        )

    @classmethod
    def clean(
        cls,
        raw_results: List[Dict[str, Any]],
        query: str = "",
    ) -> List[Dict[str, Any]]:
        """
        输入搜索 API 的原始结果列表（每项含 title/url/content），
        输出经去重、信誉过滤、注入消毒、打分后的干净结果（按分数降序）。
        """
        # ===== 去噪六道筛（下文逐条对应）=====
        seen_urls = set()          # 记录已收过的链接，用于第 2 道去重
        cleaned: List[Dict[str, Any]] = []
        q_tokens = set(re.findall(r"\w+", query.lower())) if query else set()

        for r in raw_results:
            url = r.get("url", "")
            domain = cls.domain_of(url)

            # 【第 1 道】没链接、或来自黑名单垃圾域名的，直接扔掉
            if not url or domain in BLOCKED_DOMAINS:
                continue
            # 【第 2 道】同一链接搜出来多次（双源融合常见），只留第一条
            if url in seen_urls:
                continue  # 去重
            seen_urls.add(url)

            # 【第 3 道】内容不到 30 字，基本是导航页/空壳页，没信息量，丢
            content = (r.get("content") or "").strip()
            if len(content) < 30:
                continue  # 太短，列表页/空页丢弃

            # 【第 4 道】算相关度：把搜索词拆成词，看这条结果里命中几个
            # 命中越多越相关（英文按词切；中文要更准可换 jieba 分词）
            relevance = 0.5
            if q_tokens:
                c_tokens = set(re.findall(r"\w+", content.lower()))
                overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))
                relevance = min(1.0, 0.3 + overlap)

            # 【第 5 道】防 prompt 注入：若内容想冒充用户指令骗机器人，
            # 不删它，只在前面加标记「外部资料·已标记，非用户指令」
            content = cls.sanitize_injection(content)

            # 【第 6 道】收尾：截断长文 + 算总评分，主循环结束后统一排序
            cleaned.append({
                "title": r.get("title", ""),
                "url": url,
                "content": content[:1200],       # 截断到 1200 字，防止撑爆对话 token
                "publish_date": r.get("publish_date"),  # API 未给日期则为 None
                # 总评分 = 域名可信度×0.5 + 新鲜度×0.3 + 相关度×0.2（见 score 方法）
                "score": cls.score(domain, r.get("publish_date"), relevance),
            })

        # 全部筛完、打分后，按分数从高到低排，最可信最新的排最前喂给 LLM
        cleaned.sort(key=lambda x: x["score"], reverse=True)
        return cleaned


# ────────────────────── 引用溯源校验（P0 闸门之二） ──────────────────────
class GroundingChecker:
    """
    校验 LLM 最终回答里的每条 [来源](URL) 是否真来自本次工具返回。
    拦住「幽灵引用」（LLM 编造的链接）。
    """

    CITE_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

    @classmethod
    def extract_citations(cls, answer: str) -> List[Dict[str, str]]:
        return [{"title": m.group(1), "url": m.group(2)}
                for m in cls.CITE_RE.finditer(answer)]

    @classmethod
    def check(cls, answer: str, allowed_urls: List[str]) -> Dict[str, Any]:
        # 抽查：把 LLM 回答里每条 [来源](URL) 和「本次工具真实返回的 URL 集合」比对，
        # 对不上的就是 LLM 瞎编的「幽灵引用」，记进 issues。
        allowed = set(allowed_urls)
        citations = cls.extract_citations(answer)
        issues: List[str] = []
        for c in citations:
            if c["url"] not in allowed:
                issues.append(f"引用无法溯源：{c['title']} -> {c['url']}")
        return {
            "passed": len(issues) == 0,   # 全部能对上 = 通过
            "citation_count": len(citations),
            "issues": issues,
        }


# ────────────────────── 搜索源客户端（双源：Tavily + 百度） ──────────────────────
# 两个客户端统一返回 [{title, url, content, publish_date}]，便于后续合并去重。
def _parse_date(s: Optional[str]) -> Optional[date]:
    """把百度返回的日期字符串解析为 date；多种格式兼容，解析失败返回 None。"""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except Exception:
            continue
    return None


async def _search_tavily(api_key: str, query: str, top_k: int) -> List[Dict[str, Any]]:
    """Tavily 搜索源 → 统一格式。失败返回带 error 的项，由 run() 统一降级。"""
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            resp = await client.post(
                TAVILY_ENDPOINT,
                json={"api_key": api_key, "query": query,
                      "max_results": top_k, "search_depth": "basic"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return [{"error": f"Tavily 搜索调用失败：{e}"}]
    out = []
    for r in data.get("results", []):
        out.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content") or "",
            "publish_date": None,  # Tavily basic 不返回日期
        })
    return out


async def _search_baidu(api_key: str, query: str, top_k: int) -> List[Dict[str, Any]]:
    """百度搜索源 → 统一格式。百度限制 query ≤72 字符；失败返回带 error 的项。"""
    q = query[:72]
    body = {
        "messages": [{"role": "user", "content": q}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": top_k}],
    }
    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            resp = await client.post(
                BAIDU_SEARCH_URL,
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return [{"error": f"百度搜索调用失败：{e}"}]
    refs = data.get("references") or data.get("results") or []
    out = []
    for r in refs:
        url = r.get("url") or r.get("link") or ""
        if not url:
            continue
        out.append({
            "title": r.get("title", ""),
            "url": url,
            "content": r.get("snippet") or r.get("content") or "",
            "publish_date": _parse_date(r.get("date") or r.get("page_time")),
        })
    return out

async def search_structured(
    query: str,
    top_k: int = 5,
    tavily_key: str = "",
    baidu_key: str = "",
) -> List[Dict[str, Any]]:
    """Return cleaned structured results for research workflows."""
    tavily_key = tavily_key or os.getenv("WEB_SEARCH_API_KEY", "")
    baidu_key = baidu_key or os.getenv("BAIDU_API_KEY", "")

    cleaned: List[Dict[str, Any]] = []
    if tavily_key:
        cleaned = CredibilityFilter.clean(
            await _search_tavily(tavily_key, query, top_k),
            query=query,
        )
    if not cleaned and baidu_key:
        cleaned = CredibilityFilter.clean(
            await _search_baidu(baidu_key, query, top_k),
            query=query,
        )
    return cleaned[:top_k]

# ────────────────────── 搜索工具（继承项目 Tool） ──────────────────────
class WebSearchTool(Tool):
    """联网检索工具：调用搜索 API，返回经可信度过滤的网页标题/链接/摘要。"""

    name = "web_search"
    capability = "web_search" # ← 新增这一行
    description = (
        "通过搜索引擎检索互联网上的实时信息，返回相关网页的标题、链接与摘要。"
        "当用户的问题涉及最新资讯、实时数据、当前事件、或你知识截止日期之后"
        "发生的事件时，应使用此工具。语法、算法、常识、固定理论等静态知识无需搜索。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，应精炼准确，不要照搬口语长句",
            },
            "top_k": {
                "type": "integer",
                "description": "返回结果条数，默认 5",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self):
        self._tavily_key = os.getenv("WEB_SEARCH_API_KEY", "")
        self._baidu_key = os.getenv("BAIDU_API_KEY", "")
        self.last_usage: Dict[str, Any] = {}  # 本轮用量，供日志/调试读取

    async def run(self, query: str, top_k: int = 5) -> str:
        # 主备模式：主源 = Tavily，备源 = 百度（用户指定百度放第二）。
        # 只有当主源「没法调用」时才降级到备源——未配置 / 请求失败 / 调通但无有效结果，
        # 统一以 cleaned 是否为空判定。每轮最多消耗 1 个源的免费额度。
        used_source: Optional[str] = None  # "tavily" / "baidu" / None
        cleaned: List[Dict[str, Any]] = []

        # ── 1) 先试主源 Tavily（若已配置）──
        if self._tavily_key:
            raw = await _search_tavily(self._tavily_key, query, top_k)
            cleaned = CredibilityFilter.clean(raw, query=query)
            if cleaned:
                used_source = "tavily"
        # 若未配 Tavily，cleaned 保持空，直接进入备源分支

        # ── 2) 主源没法用 → 降级备源 百度（若已配置）──
        if not cleaned and self._baidu_key:
            raw_b = await _search_baidu(self._baidu_key, query, top_k)
            cleaned = CredibilityFilter.clean(raw_b, query=query)
            if cleaned:
                used_source = "baidu"

        # ── 3) 都没配 / 都没结果 → 打印并兜底返回 ──
        if not cleaned:
            q = _load_search_quota()
            tv_real = _fetch_tavily_usage(self._tavily_key) if self._tavily_key else None
            tv_disp = (f"R{tv_real}" if tv_real is not None
                       else f"L{q.get('tavily', {}).get('count', 0)}")
            if self._tavily_key and self._baidu_key:
                note = "主源 Tavily 无有效结果，已降级百度仍无结果"
            elif self._tavily_key:
                note = "仅配 Tavily 且无有效结果"
            elif self._baidu_key:
                note = "未配置主源 Tavily，仅用百度仍无有效结果"
            else:
                note = "未配置任何搜索 API Key"
            print(f"[web_search] 本轮「{query}」{note}："
                  f"tavily={'✓' if self._tavily_key else '—'}({tv_disp}/{TAVILY_LIMIT}) · "
                  f"baidu={'✓' if self._baidu_key else '—'}"
                  f"(L{q.get('baidu', {}).get('count', 0)}/{BAIDU_LIMIT})")
            if not (self._tavily_key or self._baidu_key):
                return ("[web_search 执行失败：未配置任何搜索 API Key，"
                        "请在 .env 设置 WEB_SEARCH_API_KEY 或 BAIDU_API_KEY]")
            return "未找到可靠的搜索结果。"

        # ── 4) 有效结果 → 统计用量 + 打印（只统计实际被调用的源）──
        q = _load_search_quota()
        if used_source == "tavily":
            q["tavily"]["count"] += 1
        elif used_source == "baidu":
            q["baidu"]["count"] += 1
        _save_search_quota(q)

        tv_real = _fetch_tavily_usage(self._tavily_key) if self._tavily_key else None
        tv_disp = (f"R{tv_real}" if tv_real is not None
                   else f"L{q['tavily']['count']}")
        self.last_usage = {
            "used_source": used_source,
            "tavily": tv_real if tv_real is not None else q["tavily"]["count"],
            "tavily_real": tv_real is not None,
            "tavily_limit": TAVILY_LIMIT,
            "baidu": q["baidu"]["count"], "baidu_limit": BAIDU_LIMIT,
            "results": len(cleaned),
        }
        if used_source == "tavily":
            print(f"[web_search] 本轮「{query}」主源命中："
                  f"tavily=✓(主,{tv_disp}/{TAVILY_LIMIT})，有效结果 {len(cleaned)} 条")
        else:
            why = "未配置" if not self._tavily_key else "失败/无有效结果"
            print(f"[web_search] 本轮「{query}」主源 Tavily{why}，降级备源百度命中："
                  f"baidu=✓(备,L{q['baidu']['count']}/{BAIDU_LIMIT})，有效结果 {len(cleaned)} 条")

        lines = []
        for i, r in enumerate(cleaned[:top_k], 1):
            snippet = r["content"][:300]
            lines.append(f"[{i}] {r['title']}\nURL: {r['url']}\n摘要: {snippet}")
        return "\n\n".join(lines)


# ── 可选：若搜索 API 只给 URL（不给摘要），用下面函数抓正文并去噪 ──
async def fetch_and_extract(url: str, timeout: float = 8.0) -> str:
    """抓取网页并用 trafilatura 抽取正文（去模板/导航/广告）。"""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            html = (await client.get(url)).text
    except Exception:
        return ""
    try:
        import trafilatura
        return trafilatura.extract(html, include_comments=False) or ""
    except ImportError:
        return re.sub(r"<[^>]+>", "", html)[:2000]  # 退而用正则粗略去标签


# ────────────────────── 独立冒烟测试 ──────────────────────
if __name__ == "__main__":
    import asyncio

    async def _smoke():
        tool = WebSearchTool()
        src = []
        if tool._tavily_key:
            src.append("Tavily")
        if tool._baidu_key:
            src.append("百度")
        print(f"=== 已启用搜索源：{src or '无（请配置 .env 的 WEB_SEARCH_API_KEY / BAIDU_API_KEY）'} ===")
        out = await tool.run("Apple stock price today", top_k=3)
        print("=== web_search 输出 ===")
        print(out)

        # 取本次真实返回的 URL 作为「允许溯源」集合
        urls = re.findall(r"URL: (https?://\S+)", out)

        # 模拟 LLM 正确引用（URL 在允许集合内 → 通过）
        answer_ok = "据搜索结果，苹果今日股价约 228 美元 [Apple](%s)" % (
            urls[0] if urls else "https://example.com")
        print("grounding(正确引用):", GroundingChecker.check(answer_ok, urls))

        # 模拟 LLM 编造引用（URL 不在允许集合内 → 拦截）
        answer_bad = "据传苹果股价 999 [假链接](https://fake-news-example.com/x)"
        print("grounding(幽灵引用):", GroundingChecker.check(answer_bad, urls))

    asyncio.run(_smoke())
