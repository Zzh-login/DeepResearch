"""
双轨记忆 · 冷层 KV 长期记忆存储（结构化事实）

架构定位：
  - 这是"双轨记忆"里【冷层】的"结构化事实"存储，对应工业记忆分层里的
    "语义长期记忆 / 知识库"。
  - 存的是"提炼后的事实"（如"用户偏好中文回答"），不存对话原文。

存储介质：本地 JSON 文件
  - 路径：data/users/{source}/long_memory.json（每个用户一份）
  - 选型理由：事实型数据、带去重/敏感过滤/排序，无需关系库的事务/检索能力，
    与项目已有的 JSON 存储风格一致，零额外依赖。

与其他模块的关系（调用链）：
  - 写入端：domain/memory/extractor.py 的 MemoryExtractor.add_memories()
  - 读出端：domain/prompt/builder.py 的 _get_kv_context() → render_context()
           拼进 system prompt 的"长期记忆"段。

关键设计：
  - 去重 UPSERT：同 fact 算 hash 作主键，已存在只更新置信度/重要性。
  - 敏感信息过滤：正则挡 api_key / 密码 / 身份证 / 手机号 / 邮箱等。
  - 落盘即按 (importance, confidence, updated_at) 降序排，重要的在前。
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).parent.parent.parent / "data"
USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_TYPES = {
    "preference",
    "profile",
    "project",
    "decision",
    "goal",
    "constraint",
}

SENSITIVE_PATTERNS = [
    re.compile(r"\b(api[_-]?key|token|secret|password|passwd|pwd)\b", re.I),
    re.compile(r"(密码|密钥|令牌|验证码|身份证|银行卡|手机号|手机号码|精确住址)"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


class JsonLongMemory:
    """JSON 长期记忆存储：以「一个来源/用户一份文件」的方式，持久化提炼后的结构化事实。

    本类对应双轨记忆里的【冷层 / 语义长期记忆】。它只关心"事实型数据"
    （如"用户偏好中文回答"），不存对话原文。写入来自 MemoryExtractor，
    读出经过 PromptBuilder 注入 system prompt。
    """

    def __init__(self, source: str = "web"):
        """初始化存储实例。

        Args:
            source: 记忆归属来源标识（如 "web" 或 "user_123"），决定落盘到
                    data/users/{source}/long_memory.json。构造时即确保目录存在。
        """
        self._source = source
        self._dir = USERS_DIR / source
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self) -> Path:
        """返回当前 source 对应的 long_memory.json 文件路径。"""
        return self._dir / "long_memory.json"

    def load(self) -> dict:
        """从磁盘读取长期记忆 JSON。

        Returns:
            解析后的 dict；若文件不存在、内容非 dict 或解析失败（损坏/权限问题），
            一律返回空 dict（fail-safe，不抛异常）。
        """
        f = self._file()
        if not f.exists():
            return {}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, data: dict) -> None:
        """把整个记忆 dict 写回磁盘（覆盖式）。

        注意：写入时 ensure_ascii=False 保留中文，indent=2 便于人工阅读/diff。
        """
        self._file().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key_name: str) -> Optional[Any]:
        """通用 KV 读：取顶层某个键的值，不存在返回 None。"""
        return self.load().get(key_name)

    def set(self, key_name: str, value: Any) -> None:
        """通用 KV 写：设置顶层某个键并落盘（读-改-写，非原子）。"""
        data = self.load()
        data[key_name] = value
        self.save(data)

    def delete(self, key_name: str) -> None:
        """通用 KV 删：删除顶层某个键（存在才删）并落盘。"""
        data = self.load()
        if key_name in data:
            del data[key_name]
            self.save(data)

    def list_keys(self) -> list[str]:
        """返回当前长期记忆中所有顶层键名列表。"""
        return list(self.load().keys())

    def add_facts(self, facts: list[str]) -> None:
        """把一串自由文本事实，统一规整为 profile 类型后再交给 add_memories 写入。

        便于调用方只持有"话术级"的事实字符串时快速入库；空串/非字符串会被过滤。
        """
        memories = [
            {
                "type": "profile",
                "fact": fact,
                "confidence": 0.75,
                "importance": 0.5,
            }
            for fact in facts
            if isinstance(fact, str) and fact.strip()
        ]
        self.add_memories(memories)

    def add_memories(self, memories: list[dict]) -> None:
        """UPSERT 一批结构化记忆项（按 fact 的归一化 hash 去重）。

        行为要点：
          - 已存在的同 id 项：用 max() 合并置信度/重要性，仅刷新 updated_at，不覆盖原文。
          - 新项：直接加入。
          - 写回前按 (importance, confidence, updated_at) 降序排序，重要事实排在前面。
        这是冷层事实的【主写入入口】，由 MemoryExtractor 调用。
        """
        if not memories:
            return

        data = self.load()
        existing = self._load_structured_items(data)
        by_id = {item["id"]: item for item in existing}
        now = datetime.now(timezone.utc).isoformat()

        for raw in memories:
            item = self._normalize_memory(raw, now)
            if item is None:
                continue

            current = by_id.get(item["id"])
            if current:
                current.update(
                    {
                        "type": item["type"],
                        "fact": item["fact"],
                        "confidence": max(
                            current.get("confidence", 0.0),
                            item["confidence"],
                        ),
                        "importance": max(
                            current.get("importance", 0.0),
                            item["importance"],
                        ),
                        "updated_at": now,
                    }
                )
            else:
                by_id[item["id"]] = item

        data["memories"] = sorted(
            by_id.values(),
            key=lambda item: (
                item.get("importance", 0.0),
                item.get("confidence", 0.0),
                item.get("updated_at", ""),
            ),
            reverse=True,
        )
        self.save(data)

    def render_context(self, max_items: int = 20) -> str:
        """把结构化记忆渲染成可注入 system prompt 的纯文本。

        Returns:
            形如 "- [preference] 用户偏好中文回答" 的多行文本（取前 max_items 条）；
            若无任何记忆返回空串 ""（供 PromptBuilder 判断"有则注入"）。
            兼容旧版非结构化字段（K/V 平铺）的渲染。
        """
        data = self.load()
        if not data:
            return ""

        structured = self._load_structured_items(data)
        if structured:
            lines = []
            for item in structured[:max_items]:
                fact = item.get("fact", "").strip()
                if fact:
                    lines.append(f"- [{item.get('type', 'memory')}] {fact}")
            legacy_context = self._render_legacy_context(
                {k: v for k, v in data.items() if k != "memories"}
            )
            if legacy_context:
                lines.append(legacy_context)
            return "\n".join(lines)

        return self._render_legacy_context(data)

    def _load_structured_items(self, data: dict) -> list[dict]:
        """从 data["memories"] 加载并规整所有结构化记忆项，按重要度降序返回。

        与 add_memories 共用 _normalize_memory；preserve_dates=True 以保留
        已有的 created_at / updated_at 时间戳（读取路径不应改动时间）。
        """
        memories = data.get("memories", [])
        if not isinstance(memories, list):
            return []

        now = datetime.now(timezone.utc).isoformat()
        items = []
        for raw in memories:
            item = self._normalize_memory(raw, now, preserve_dates=True)
            if item is not None:
                items.append(item)

        return sorted(
            items,
            key=lambda item: (
                item.get("importance", 0.0),
                item.get("confidence", 0.0),
                item.get("updated_at", ""),
            ),
            reverse=True,
        )

    def _normalize_memory(
        self,
        raw: dict,
        now: str,
        preserve_dates: bool = False,
    ) -> Optional[dict]:
        """把一条原始记忆规整为内部标准结构。

        处理链：
          1. 非 dict / fact 为空 → 丢弃（返回 None）。
          2. fact 命中敏感正则 → 丢弃（防泄露密钥/身份证等）。
          3. type 不在白名单 → 回退 "profile"。
          4. confidence/importance 归一到 [0,1]。
          5. id 缺失则用 fact 的归一化 sha256 前 16 位作主键（去重关键）。
        preserve_dates=True 时沿用原始时间戳，否则用 now 填充。
        """
        if not isinstance(raw, dict):
            return None

        fact = str(raw.get("fact", "")).strip()
        if not fact:
            return None
        if self._looks_sensitive(fact):
            return None

        memory_type = str(raw.get("type", "profile")).strip()
        if memory_type not in MEMORY_TYPES:
            memory_type = "profile"

        confidence = self._clamp_float(raw.get("confidence", 0.75))
        importance = self._clamp_float(raw.get("importance", 0.5))
        memory_id = str(raw.get("id", "")).strip() or self._make_id(fact)

        created_at = raw.get("created_at") if preserve_dates else None
        updated_at = raw.get("updated_at") if preserve_dates else None

        return {
            "id": memory_id,
            "type": memory_type,
            "fact": fact,
            "confidence": confidence,
            "importance": importance,
            "source": str(raw.get("source", "conversation")),
            "status": str(raw.get("status", "active")),
            "created_at": created_at or now,
            "updated_at": updated_at or now,
        }

    def _make_id(self, fact: str) -> str:
        """对 fact 做归一化（小写、压缩空白）后取 sha256 前 16 位，作为去重主键。"""
        normalized = " ".join(fact.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _looks_sensitive(self, fact: str) -> bool:
        """用 SENSITIVE_PATTERNS（密钥/证件/手机号/邮箱等正则）判断 fact 是否含敏感信息。"""
        return any(pattern.search(fact) for pattern in SENSITIVE_PATTERNS)

    def _clamp_float(self, value: Any) -> float:
        """把任意值安全地转成 [0.0, 1.0] 区间内的浮点；非法输入回退 0.0。"""
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        return max(0.0, min(1.0, number))

    def _render_legacy_context(self, data: dict) -> str:
        """把旧版"非 memories 结构"的 K/V 平铺字段渲染成可读文本。

        兼容历史数据：dict 值展开为缩进列表、list 值用逗号/顿号连接，
        其余非空标量直接 "key: value"。空值跳过。
        """
        lines: list[str] = []

        for key, value in data.items():
            if isinstance(value, dict):
                lines.append(f"{key}:")
                for k, v in value.items():
                    if isinstance(v, list):
                        lines.append(f"  - {k}: {'、'.join(str(x) for x in v)}")
                    elif v is not None and v != "":
                        lines.append(f"  - {k}: {v}")
            elif isinstance(value, list):
                joined = ", ".join(str(v) for v in value)
                lines.append(f"{key}: {joined}")
            elif value is not None and value != "":
                lines.append(f"{key}: {value}")

        return "\n".join(lines)