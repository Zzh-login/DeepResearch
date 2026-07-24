"""
消息类型定义：替代裸 dict，提供类型安全的结构化消息

技术栈说明：
  - Python dataclasses（标准库）：提供结构化数据容器，替代手写 dict
  - OpenAI Chat Completion 格式兼容：role/content 结构对齐 OpenAI API，
    可直接序列化为 messages 参数传入 openai.ChatCompletion.create()
  - 与 LangChain 消息体系可互换：SystemMessage / HumanMessage / AIMessage
    三件套对齐 LangChain 的 schema，未来接 LangChain 时只需一行适配
  - 依赖倒置：Domain 层不 import OpenAI / LangChain，
    Infrastructure 层的 llm/client.py 负责序列化调用
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SystemMessage:
    """
    系统消息（角色设定 / 全局指令）

    OpenAI API 规范：messages[0] 通常为 system role，
    用于设定 AI 的行为边界、回答风格、规则约束。
    当前系统的 persona.yaml + rules.yaml 拼装后写入此结构的 content。
    """
    content: str
    role: Literal["system"] = field(default="system", init=False)


@dataclass
class UserMessage:
    """
    用户消息

    对应 OpenAI API 的 user role。
    来源：Web 端文字输入 / 语音识别结果 / CLI 的 input()
    """
    content: str
    role: Literal["user"] = field(default="user", init=False)


@dataclass
class AssistantMessage:
    """
    AI 回复消息

    对应 OpenAI API 的 assistant role。
    由 LLM 返回后，经 Validator 兜底校验，再写入 History。
    """
    content: str
    role: Literal["assistant"] = field(default="assistant", init=False)


# 联合类型，方便函数签名约束入参类型
Message = SystemMessage | UserMessage | AssistantMessage


# ════════════════════════════════════════════════════════════
# 序列化 vs 反序列化：什么时候用哪个？
#
# 反序列化（from_dict）：系统启动时
#   json_repo 从 chat_history_web.json 读出 dict → from_dict()
#   转成 Message 对象列表 → self._history = [SystemMessage, UserMessage, ...]
#
# 序列化（messages_to_dicts）：LLM 调用前 + 保存历史时
#   · builder.py 组装好 Message 列表 → messages_to_dicts() → 传给 LLM API
#   · json_repo 保存时 → messages_to_dicts() → json.dump 写入文件
#
# 其他时刻全程用 Message 对象，不碰裸 dict。
# ════════════════════════════════════════════════════════════


def to_dict(msg: Message) -> dict:
    """
    序列化：单条 Message → dict

    用在哪：messages_to_dicts 内部逐条调用，不直接对外使用。
    """
    return {"role": msg.role, "content": msg.content}


def messages_to_dicts(messages: list[Message]) -> list[dict]:
    """
    序列化：批量 Message → dict 列表

    用在哪（两个场景）：
      1. LLM 调用前：prompt/builder.py 组装好消息列表，调此函数转为
         OpenAI API 的 messages 参数格式
      2. 保存历史时：json_repo 将内存中的 Message 列表转为 dict
         再 json.dump 写入 chat_history_web.json

    为什么必须序列化：
      OpenAI API 只认 {"role":"...","content":"..."} 格式的 dict，
      不认 Python dataclass 对象。Message 是 Domain 层的内部表示，
      对外通信时必须翻译成 API 协议格式。
    """
    return [to_dict(m) for m in messages]


def from_dict(data: dict) -> Message:
    """
    反序列化：dict → Message 对象

    用在哪：系统启动时
      json_repo 从 chat_history_web.json 读到原始 dict 列表，
      逐条调 from_dict() 转为类型安全的 Message 对象再交给上层。
      相当于 ORM 的反序列化——JSON 文件是"数据库"，Message 是"实体"。

    为什么必须反序列化：
      dict 无类型约束，msg["rol"] 拼错不会报错直到 LLM 返回 400。
      在"进入系统"那一刻转成 Message 对象，之后全程用 msg.role，
      IDE 自动补全 + 拼错当场红线。
    """
    role = data["role"]
    content = data.get("content", "")
    if role == "system":
        return SystemMessage(content=content)
    elif role == "user":
        return UserMessage(content=content)
    elif role == "assistant":
        return AssistantMessage(content=content)
    raise ValueError(f"Unknown role: {role}")
