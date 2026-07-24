"""
示范工具 1：获取当前时间

定位：零外部依赖、零网络、零权限的「探针工具」。
  仅用于验证 Function Calling 全链路（Router 判定 → Registry 执行 → 结果喂回 LLM），
  是接入工具系统时成本最低、最安全的第一个工具。

当用户问「现在几点」「今天几号」「星期几」等时间相关问题时，
AgentRouter 会让 LLM 选择本工具，拿到准确时间后再由 LLM 组织成自然语言回答。
"""

from datetime import datetime

from domain.tools.tool import Tool


class GetCurrentTimeTool(Tool):
    """获取当前日期、星期与时间的工具"""

    name = "get_current_time"
    description = (
        "获取当前的日期、时间和星期。当用户询问现在几点、今天几号、"
        "星期几、当前日期等与时间相关的问题时使用。"
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    async def run(self) -> str:
        """
        返回格式化后的当前时间字符串

        返回示例：
          "当前时间：2026年07月24日 周四 00:51:25（北京时间，UTC+8）"
        """
        now = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekdays[now.weekday()]
        return (
            f"当前时间：{now.strftime('%Y年%m月%d日')} {weekday} "
            f"{now.strftime('%H:%M:%S')}（北京时间，UTC+8）"
        )
