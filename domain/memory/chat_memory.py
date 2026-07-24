"""
对话记忆抽象接口

架构定位：双轨记忆【热层】的抽象契约（load / save / add_message / trim），
具体实现见 infrastructure/storage/json_repo.py（JSON 版）。
Session 只依赖此抽象类型，换存储后端（JSON→PostgreSQL）无需改 session 代码。

技术栈说明：
  - Python ABC（抽象基类）：定义接口契约，强制子类实现指定方法
  - 依赖倒置原则（DIP）：Domain 层定义"记忆能做什么"，
    Infrastructure 层（json_repo.py / 未来的 pg_repo.py）负责"怎么做"
  - 策略模式：运行时注入不同实现（JSON / PostgreSQL / Redis），
    Session 层的代码一行不改

设计原则：
  ChatMemory 只定义行为（load / save / add / trim），
  不绑定任何存储介质。这是"开闭原则"的核心——对扩展开放，对修改关闭。
"""

from abc import ABC, abstractmethod
from ..types.message import Message


class ChatMemory(ABC):
    """
    对话记忆抽象基类

    定义记忆系统必须实现的四个能力：

      load()   → 启动时从存储介质加载历史对话
      save()   → 每次对话结束后持久化
      add()    → 运行时追加一条消息到内存历史
      trim()   → 裁剪超长历史，防止 token 溢出

    为什么用抽象类而不是直接写死 JSON：
      如果 ChatMemory 直接 import json 和 open()，
      换成 PostgreSQL 就要改 ChatMemory → 违反开闭原则。
      现在定义好 load/save 签名，JSON 和 PG 分别实现，
      Session 只依赖 ChatMemory 类型，不关心底层是什么。

    子类：
      infrastructure/storage/json_repo.py → JsonChatMemory
      未来：infrastructure/storage/pg_repo.py → PgChatMemory
    """

    @abstractmethod
    def load(self, persona: str, source: str = "web") -> list[Message]:
        """
        加载历史对话

        参数：
          persona：当前生效的角色文本，作为 messages[0] 注入
          source：数据源标识（"web" 或 "cli"），用于多端数据隔离

        返回：
          含 system 消息在内的完整 Message 列表，
          system 消息始终在索引 [0] 位置

        调用时机：system 启动 / ChatSession.__init__()
        """
        ...

    @abstractmethod
    def save(self, history: list[Message], source: str = "web") -> None:
        """
        保存对话历史

        参数：
          history：完整 Message 列表（含 system 头部）
          source：数据源标识

        调用时机：每次 chat() 返回后，在返回 reply 给用户之前

        注意：
          实现层负责截断超长历史（MAX_ROUNDS），
          确保文件 / 数据库不会无限膨胀。
        """
        ...

    @abstractmethod
    def add_message(self, history: list[Message], message: Message) -> None:
        """
        向内存中的历史追加一条消息

        参数：
          history：当前会话的 Message 列表（原地修改）
          message：要追加的 Message（UserMessage / AssistantMessage）

        调用时机：用户发消息后、LLM 返回后各调用一次

        为什么单独抽象而不是直接 history.append()：
          未来 PG 实现可能需要同时写数据库（WAL 先写日志），
          不能只靠一个 append。抽象方法给了实现层额外的操作空间。
        """
        ...

    @abstractmethod
    def trim(
        self, history: list[Message], max_rounds: int = 20
    ) -> list[Message]:
        """
        裁剪超长历史

        参数：
          history：当前 Message 列表
          max_rounds：最大保留轮数（一轮 = user + assistant）

        返回：
          裁剪后的新列表，system 消息始终保留

        调用时机：save() 内部自动调用，由实现层决定裁剪策略

        注意：
          裁剪时 system 消息不能丢（角色设定必须始终在 [0]），
          只裁 user/assistant 对。
        """
        ...


def create_chat_memory(backend: str = "json", **kwargs) -> ChatMemory:
    """
    工厂函数：根据后端标识创建对应的 ChatMemory 实例

    参数：
      backend："json" 用 JSON 文件，"pg" 用 PostgreSQL（未来）

    为什么用工厂函数而不是直接 new：
      Session 层只需要调 create_chat_memory("json")，
      不需要知道具体类名。换后端时只改这一个字符串。
    """
    if backend == "json":
        # 延迟导入，避免循环依赖
        from infrastructure.storage.json_repo import JsonChatMemory
        return JsonChatMemory(**kwargs)
    # elif backend == "pg":
    #     from infrastructure.storage.pg_repo import PgChatMemory
    #     return PgChatMemory(**kwargs)
    raise ValueError(f"Unknown memory backend: {backend}")
