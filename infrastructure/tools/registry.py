"""
工具注册表 —— Agent 可用工具的中央登记与执行中枢

职责：
  1. 注册（register）：启动时把各工具实例登记进来
  2. 查找（get / all / schemas）：供 Router 生成 tools 参数、供 Runner 取用
  3. 执行（execute）：按工具名 + 参数执行，统一做失败降级

设计要点：
  - 失败降级：execute() 捕获工具异常，返回人类可读错误字符串，
    而非抛出。错误字符串会作为 tool 结果喂回 LLM，让模型向用户解释，
    绝不让工具崩溃导致整个对话中断（沿用温层/冷层 fire-and-forget 的稳健哲学）。
  - 与 domain/tools/tool.py 的 Tool 抽象解耦：注册表只认 Tool 接口，
    工具具体是谁、怎么实现它不关心。
"""

from typing import Dict, List, Any, Optional

from domain.tools.tool import Tool


class ToolRegistry:
    """工具注册表：登记、查找、执行 Agent 工具"""

    def __init__(self):
        """初始化空注册表"""
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """
        注册一个工具

        参数：
          tool  实现了 Tool 抽象基类的实例
        异常：
          ValueError  tool.name 为空（无法作为查找键）
        """
        if not tool.name:
            raise ValueError("工具 name 不能为空，注册失败")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """按名称查找工具，不存在返回 None"""
        return self._tools.get(name)

    def all(self) -> List[Tool]:
        """返回所有已注册工具实例列表"""
        return list(self._tools.values())

    def schemas(self) -> List[Dict[str, Any]]:
        """返回所有工具的 OpenAI function schema 列表（供 LLM tools 参数）"""
        return [t.to_openai_schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        执行指定工具

        参数：
          name      工具名（对应 tool.name）
          arguments  LLM 解析出的参数字典
        返回：
          工具执行结果字符串（成功结果 或 降级错误说明）

        降级策略：
          - 工具未注册 → 返回提示字符串
          - 工具抛异常 → 捕获并返回 "[工具 X 执行失败：...]"
        两种情况都把信息作为 tool 结果正常返回，不向上抛异常。
        """
        tool = self.get(name)
        if tool is None:
            return f"[工具 '{name}' 不存在或未注册]"
        try:
            return await tool.run(**(arguments or {}))
        except Exception as e:
            # 工具执行失败不崩溃：把错误喂回 LLM，让它向用户解释
            return f"[工具 '{name}' 执行失败：{e}]"
