"""
滚动摘要归档存储 —— 双轨记忆的第二轨（温层）

技术栈说明：
  - Python json 标准库（与 JsonChatMemory 同选型，零额外依赖）
  - 数据隔离：每个 source（用户）一个独立文件
    data/users/{source}/archive_memory.json

为什么用 JSON 而不是数据库：
  - 滚动摘要是"单块、低频读写"的文本，本质是配置型数据，不需要关系库的
    并发 / 事务 / 检索能力。
  - 与现有 JsonChatMemory / JsonLongMemory 完全同构，不引入新基础设施，
    也不偏离项目已有的存储风格。
  - 若未来要上生产多实例，平移到 PostgreSQL 一张 archive_memory 表即可
    （schema 就是 {source, digest} 两列），改动极小。

职责边界（与 reducer 分工）：
  - JsonArchiveMemory 只管"存 / 取 / 渲染"这一块摘要文本。
  - MemoryReducer 只管"怎么把片段压成摘要"。
  两者组合 = 双轨记忆的温层。
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
USERS_DIR = DATA_DIR / "users"
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR.mkdir(parents=True, exist_ok=True)


class JsonArchiveMemory:
    """每个 source 一份滚动摘要的读写器。

    存储格式（archive_memory.json）：
        {"digest": "这里是一段不断演进的中文滚动摘要……"}

    digest 字段始终只有一块文本——这就是"滚动"的核心：
    无论对话多长，归档只占一块，token 占用基本恒定。
    """

    def __init__(self, source: str = "web"):
        """初始化滚动摘要读写器。

        Args:
            source: 记忆归属来源（如 "web" 或 "user_123"），
                    决定落盘到 data/users/{source}/archive_memory.json。
        构造时确保目录存在，并预存 _file 路径引用。
        """
        self._source = source
        self._dir = USERS_DIR / source
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "archive_memory.json"

    def _load(self) -> dict:
        """安全读取归档文件，损坏 / 缺失都降级为空摘要。"""
        if not self._file.exists():
            return {"digest": ""}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"digest": ""}

    def get_digest(self) -> str:
        """读取当前滚动摘要，空文件返回空串（不污染 prompt）。"""
        return self._load().get("digest", "") or ""

    def set_digest(self, text: str) -> None:
        """写入新的滚动摘要（整块覆盖，实现"滚动折叠"）。

        注意：不是 append 而是覆盖——因为 reducer 返回的已经是
        "旧摘要 + 新片段"融合后的完整结果，直接存这份融合结果即可。
        """
        self._file.write_text(
            json.dumps({"digest": text}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def render_context(self) -> str:
        """返回可直接注入 system prompt 的摘要文本。

        空摘要返回空串，调用方据此决定是否注入，
        避免往 prompt 里塞无意义的"早期对话摘要："。
        """
        return self.get_digest()
