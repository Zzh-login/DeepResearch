"""
示范工具 2：主动回忆（包装现有向量记忆检索）

定位：把已有的 VectorMemory.search() 包装成 Agent 可调用的工具，
等于「白送」一个能力 —— LLM 在觉得需要历史信息时，自行决定检索长期记忆，
而不必每次都依赖 PromptBuilder 的自动注入。

这是验证工具系统价值的最佳范例：
  - 复用既有基础设施（VectorMemory + pgvector + BGE-M3），零新依赖
  - 让记忆检索从「被动注入」升级为「主动按需调用」
  - 当用户问「我们上次聊过什么」「你还记得我说的 XX 吗」时尤其有用

降级：向量记忆未配置或检索异常时，返回说明字符串而非崩溃。
"""

from typing import Optional

from domain.tools.tool import Tool


class RecallMemoryTool(Tool):
    """从长期向量记忆中语义检索相关历史信息"""

    name = "recall_memory"
    description = (
        "从长期记忆中语义检索与用户查询相关的历史信息。"
        "当用户问题涉及过去聊过的内容、个人偏好、事实记录，"
        "或需要回忆此前对话时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要检索的内容关键词或问题",
            }
        },
        "required": ["query"],
    }

    def __init__(self, vector_memory=None, source: str = "web"):
        """
        初始化主动回忆工具

        参数：
          vector_memory  VectorMemory 实例（基础设施层），可为 None
          source         记忆归属来源标识，传给向量检索做用户隔离
        """
        self._vm = vector_memory
        self._source = source

    async def run(self, query: str) -> str:
        """
        执行语义检索

        参数：
          query  检索查询文本
        返回：
          匹配到的记忆摘要文本（多行），或降级说明字符串

        降级：
          - 未配置 vector_memory → "[未配置向量记忆，无法检索]"
          - 检索异常 → "[记忆检索失败：...]"
        两种情况都返回可读字符串，由 LLM 决定如何回应。
        """
        if self._vm is None:
            return "[未配置向量记忆，无法检索]"
        try:
            results = await self._vm.search(
                query=query,
                source=self._source,
                top_k=3,
                min_score=0.2,
            )
            if not results:
                return "未找到相关的长期记忆。"
            return "相关记忆：\n" + "\n".join(f"- {r['summary']}" for r in results)
        except Exception as e:
            return f"[记忆检索失败：{e}]"
