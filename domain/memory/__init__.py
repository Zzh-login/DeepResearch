"""
记忆子包（domain.memory）。

封装对话记忆的抽象与处理逻辑（与具体存储无关）：
  - reducer.py    ：记忆裁剪 / 压缩策略
  - extractor.py  ：从对话中抽取长期记忆
  - chat_memory.py：对话记忆管理器（检索 / 写入 / 维护上下文窗口）

当前由 JSON 文件存储支撑，未来可平滑切换到 PostgreSQL + pgvector 向量记忆，
接口保持不变。
"""

# domain.memory package
