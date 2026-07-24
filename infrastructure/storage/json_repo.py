"""
JSON 文件存储实现 —— ChatMemory 接口的 JSON 版本

架构定位：双轨记忆【热层】的工作记忆存储（最近 20 轮对话原文 + 角色设定）。
（接口定义见 domain/memory/chat_memory.py，本文件是其 JSON 实现）

技术栈说明：
  - Python json 模块（标准库）：无需额外依赖
  - 实现 ChatMemory 抽象接口（abc 多态）：
    继承 domain/memory/chat_memory.py 的 ChatMemory 抽象基类，
    实现 load / save / add_message / trim 四个方法
  - 数据隔离：每个 source（用户）一个独立子目录
    data/users/{source}/chat_history.json
    data/users/{source}/persona_history.json
    data/users/{source}/persona.txt
    （web 用户的 source = "user_{user_id}"，cli 的 source = "cli"）

数据流向（以一次 chat 为例）：
  1. 启动 → load() → json.load → from_dict() → [Message, Message, ...] → session
  2. 用户发消息 → add_message() → history.append(UserMessage)
  3. LLM 返回 → add_message() → history.append(AssistantMessage)
  4. save() → trim() → messages_to_dicts() → json.dump → 文件

为什么用 json 而不是 pickle / sqlite：
  - JSON 人类可读，方便调试
  - 跨语言通用，将来迁移到 PostgreSQL 数据可直接导出
  - 当前阶段不需要数据库的并发 / 事务能力
"""

import json
from pathlib import Path

from domain.memory.chat_memory import ChatMemory
from domain.types.message import Message, messages_to_dicts, from_dict, SystemMessage

DATA_DIR = Path(__file__).parent.parent.parent / "data"
USERS_DIR = DATA_DIR / "users"
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR.mkdir(parents=True, exist_ok=True)

MAX_ROUNDS = 20


class JsonChatMemory(ChatMemory):
    """
    JSON 文件实现 ChatMemory 接口

    每个 source（用户）一个独立子目录，按文件夹隔离：
      - data/users/{source}/chat_history.json    → 对话历史
      - data/users/{source}/persona_history.json → 角色设定历史列表
      - data/users/{source}/persona.txt          → 当前角色持久化

    一个实例只服务一个 source。
    """

    def __init__(self, source: str = "web"):
        """初始化 JSON 文件存储，按 source 隔离到独立子目录。

        Args:
            source: 用户/会话标识；落盘到 data/users/{source}/ 下各 JSON 文件。
        构造时确保目录存在。一个实例只服务一个 source。
        """
        self._source = source
        # 每个 source 一个独立子目录，实现"按用户文件夹管理数据"
        self._dir = USERS_DIR / source
        self._dir.mkdir(parents=True, exist_ok=True)

    # ════════════════════════════════════════════════════════
    # 文件路径
    # ════════════════════════════════════════════════════════

    def _history_file(self) -> Path:
        """返回对话历史文件路径 chat_history.json。"""
        return self._dir / "chat_history.json"

    def _persona_history_file(self) -> Path:
        """返回角色历史列表文件路径 persona_history.json。"""
        return self._dir / "persona_history.json"

    def _persona_file(self) -> Path:
        """返回当前角色持久化文件路径 persona.txt。"""
        return self._dir / "persona.txt"

    # ════════════════════════════════════════════════════════
    # ChatMemory 接口实现
    # ════════════════════════════════════════════════════════

    def load(self, persona: str, source: str = "web") -> list[Message]:
        """
        加载历史对话

        流程：
          1. 检查文件是否存在 → 不存在则返回只含 system 的列表
          2. 读取 JSON → from_dict() 逐条反序列化为 Message
          3. 用传入的 persona 替换文件中旧的 system 消息
             （因为角色可能在两次启动之间被修改了）

        为什么用 persona 参数覆盖文件里的 system：
          文件里存的可能是上一次启动时的角色，当前生效的角色可能已经变了。
          启动时由外部传入当前实际的 persona，保证 messages[0] 是最新的。
        """
        fpath = self._history_file()
        if not fpath.exists():
            # 首次启动：只有 system 消息，还没有对话记录
            return [SystemMessage(content=persona)]

        try:
            raw = json.loads(fpath.read_text(encoding="utf-8"))
            messages: list[Message] = []

            for item in raw:
                msg = from_dict(item)
                if isinstance(msg, SystemMessage):
                    # 用当前 persona 替换旧的 system 消息
                    messages.append(SystemMessage(content=persona))
                else:
                    messages.append(msg)

            # 如果文件里没有 system 消息，在头部补一个
            if not messages or not isinstance(messages[0], SystemMessage):
                messages.insert(0, SystemMessage(content=persona))

            return messages

        except Exception:
            # JSON 损坏或格式错误 → 降级为全新历史
            return [SystemMessage(content=persona)]

    def save(self, history: list[Message], source: str = "web") -> None:
        """
        保存对话历史到 JSON 文件

        流程：
          1. 裁剪超长轮数（保留 system + 最近 N 轮的 user/assistant）
          2. messages_to_dicts() 序列化
          3. json.dump 写入文件

        为什么先裁剪再保存：
          20 轮对话约 4000 token，加上 system prompt 约 100 token，
          总共 4100 token，在 DeepSeek 的 128K 窗口内但 tokens 越长
          API 调用越慢越贵。20 轮是经验值——能记住足够上下文，
          又不拖慢响应速度。
        """
        trimmed = self.trim(history, MAX_ROUNDS)
        data = messages_to_dicts(trimmed)

        self._history_file().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_message(
        self, history: list[Message], message: Message
    ) -> None:
        """
        向内存历史追加一条消息

        JSON 存储模式下：直接 history.append(message)，
        因为 JSON 文件不需要"双写"（先写日志再写数据）。

        换成 PostgreSQL 时：这里需要同时写 chat_messages 表，
        保证崩溃恢复时数据不丢。
        """
        history.append(message)

    def trim(
        self, history: list[Message], max_rounds: int = 20
    ) -> list[Message]:
        """
        裁剪超长历史

        策略：
          - system 消息始终保留在 [0] 位置，不参与裁剪
          - 只裁剪 user/assistant 对，保留最近 max_rounds 轮

        为什么 system 不裁：
          system 消息是角色设定，丢了之后 LLM 会"失忆"——忘记自己是星程，
          回答风格回到通用模式。这是最核心的一条消息，绝对不能裁。
        """
        if not history:
            return []

        system = history[0] if isinstance(history[0], SystemMessage) else None
        ua = [m for m in history if not isinstance(m, SystemMessage)]

        # 一轮 = user + assistant，保留最近 max_rounds 轮
        keep = ua[-(max_rounds * 2):]

        return [system] + keep if system else keep

    # ════════════════════════════════════════════════════════
    # 角色相关（非 ChatMemory 接口，但归属存储层）
    # ════════════════════════════════════════════════════════

    def load_persona(self, default_persona: str) -> str:
        """
        从文件加载当前角色

        读取 persona_{source}.txt，不存在则返回默认角色。
        这是持久化角色的核心方法——重启后自动恢复上次设定的角色。
        """
        fpath = self._persona_file()
        if fpath.exists():
            return fpath.read_text(encoding="utf-8").strip()
        return default_persona

    def save_persona(self, text: str) -> None:
        """
        保存当前角色到文件

        每次 set_persona() 时调用，角色持久化存储。
        """
        self._persona_file().write_text(text, encoding="utf-8")

    def load_persona_history(self) -> list[str]:
        """
        加载角色历史列表

        从 persona_history_{source}.json 读取，返回角色文本列表。
        用于前端下拉框展示所有设置过的角色。
        """
        fpath = self._persona_history_file()
        if not fpath.exists():
            return []
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def add_persona_history(self, text: str) -> None:
        """
        添加一条角色设定到历史列表

        策略：
          - 去重：如果已存在，移除旧位置，插入头部
          - 上限：最多保留 50 条
          - 排序：最新设定的在最前面
        """
        text = text.strip()
        if not text:
            return
        items = self.load_persona_history()
        if text in items:
            items.remove(text)
        items.insert(0, text)
        items = items[:50]
        self._persona_history_file().write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def delete_persona_history(self, index: int) -> None:
        """删除指定索引的角色历史"""
        items = self.load_persona_history()
        if 0 <= index < len(items):
            items.pop(index)
            self._persona_history_file().write_text(
                json.dumps(items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )