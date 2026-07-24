"""
工具抽象基类 —— 所有 Agent 可调用的能力都实现此接口

设计说明：
  这是「工具系统」的核心抽象（位于 domain 层）。
  - 名称、描述、参数结构：供 LLM 理解何时、如何调用（OpenAI function schema）
  - run()：实际执行逻辑，由基础设施层的具体工具类实现

  为什么用 ABC 而不是普通函数 / dict：
    - 统一接口：AgentRunner / ToolRegistry 面向 Tool 编程，不关心具体实现
    - 可扩展：新增能力只需写一个继承 Tool 的类并注册，符合开闭原则
    - 可测试：工具可独立单测，不依赖 LLM / Session

  to_openai_schema() 把工具声明成 DeepSeek/OpenAI 兼容的 function 格式，
  直接塞进 LLM 调用的 tools 参数即可。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class Tool(ABC):
    """
    工具抽象基类

    子类必须定义三个类属性与一个协程方法：
      name        工具名（英文，唯一，如 "get_current_time"）
      description 工具用途描述（给 LLM 看，决定何时调用）
      parameters  JSON Schema 风格的参数定义（无参则 {"type":"object","properties":{}}）
      run(**kwargs) 执行逻辑，返回结果字符串（会原样喂回 LLM）
    """

    # —— 子类必填 ——
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """
        执行工具，返回结果字符串

        约定：
          - 成功：返回对 LLM 有用的结果文本
          - 失败：捕获异常并返回人类可读的错误字符串
            （由 ToolRegistry 统一兜底，但工具自身也应尽量返回可读错误，
             避免把原始 traceback 直接丢给用户）
        """
        raise NotImplementedError

    def to_openai_schema(self) -> Dict[str, Any]:
        """
        转换为 OpenAI / DeepSeek function calling 的 tools 元素

        返回形如：
          {"type": "function",
           "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
