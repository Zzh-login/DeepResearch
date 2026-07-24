"""
类型定义子包（domain.types）。

提供替代裸 dict 的结构化数据类型，使领域层在全流程中享受类型安全与 IDE 补全：
  - message.py：SystemMessage / UserMessage / AssistantMessage 及序列化工具，
    对齐 OpenAI Chat Completion 的 role/content 协议
  - memory.py  ：MemoryItem（KV 记忆条目）与 ChatTurn（一轮问答）数据结构

这些类型是 Domain 层内部表示；与外部通信（调 LLM、存 JSON）时才序列化为 dict。
"""

# domain.types package
