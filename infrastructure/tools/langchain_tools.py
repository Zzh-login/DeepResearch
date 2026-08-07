"""
把项目自研的 Tool 实例包装成 LangChain 工具，供 LangGraph StateGraph 使用。

设计：
  - 复用 domain.tools.tool.Tool 接口，不重写任何工具逻辑
  - 通过 StructuredTool.from_function + pydantic args_schema 动态生成工具签名，
    与工具原有的 parameters JSON Schema 对齐（覆盖 get_current_time /
    recall_memory / web_search 三种签名差异）
  - 包装后的工具内部仍调用原 Tool.run(**kwargs)，失败降级由原 Tool/Registry 负责

与 DeepResearch 路线对齐：原 AgentRunner 的「Router 判定 + 工具循环」被 LangGraph
原生的 function calling + tool 节点取代，工具实现本身零改动。
"""

from typing import Any, Dict, Optional

from pydantic import Field, create_model

from domain.tools.tool import Tool
from infrastructure.tools.registry import ToolRegistry


# JSON Schema 基础类型 → Python 类型
_PY_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_type_to_py(json_type: str) -> type:
    return _PY_TYPES.get(json_type, str)


def _wrap_tool(tool: Tool):
    """把单个自研 Tool 包成 LangChain StructuredTool。"""
    params = tool.parameters or {"type": "object", "properties": {}, "required": []}
    props: Dict[str, Any] = params.get("properties", {}) or {}
    required = set(params.get("required", []) or [])

    fields = {}
    for pname, pspec in props.items():
        ptype = _json_type_to_py(pspec.get("type", "string"))
        desc = pspec.get("description", "")
        if pname in required:
            # 必填：Field(...) 不带默认值
            fields[pname] = (ptype, Field(..., description=desc))
        else:
            # 选填：取 schema 里的 default（没有则 None）
            default = pspec.get("default", None)
            fields[pname] = (Optional[ptype], Field(default=default, description=desc))

    args_schema = create_model(f"{tool.name}_args", **fields)

    async def _run(**kwargs) -> str:
        # 内部仍走原 Tool.run，逻辑零改动；失败由原 Tool 返回可读错误串
        return await tool.run(**kwargs)

    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool.name,
        description=tool.description,
        args_schema=args_schema,
    )


def build_graph_tools(registry: ToolRegistry) -> list:
    """把注册表里的全部自研 Tool 包装成 LangChain 工具列表。"""
    return [_wrap_tool(t) for t in registry.all()]
