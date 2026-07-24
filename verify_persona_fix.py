"""
角色切换修复验证脚本
验证：PromptBuilder.build() 的 persona_override 参数能正确覆盖 YAML 默认角色，
且不影响默认行为（回归保护）。

运行：
    venv/Scripts/python.exe verify_persona_fix.py
"""
import os
import sys
import asyncio

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from domain.prompt.builder import PromptBuilder


async def main():
    """验证 PromptBuilder.build() 的 persona_override 修复是否成立。

    做法：构造一个 PromptBuilder（source="web"），分别对同一句用户输入
    构建「不传 persona_override」与「传入自定义角色」两种 system prompt，
    断言自定义角色被正确注入且保留了规则/格式段，且默认行为不被污染。

    返回值：无返回值（通过 assert 在失败时报错，成功时打印通过信息）。
    值得注意：本脚本只读不写，不会触碰数据库或生产记忆数据。
    """
    print("=" * 60)
    print("验证：PromptBuilder.build() 的 persona_override 修复")
    print("=" * 60)

    b = PromptBuilder(source="web")

    # 1) 默认行为：不传 override，应使用 YAML 默认角色
    _, msgs_default = await b.build(user_input="你好", history=[])
    default_sys = msgs_default[0]["content"]
    print("\n[默认角色] system prompt 首段：")
    print("  " + default_sys.splitlines()[0])

    # 2) 修复行为：传 override，应包含自定义角色，且保留规则/格式
    custom = "你是一个语文老师，专门讲解文言文与古诗词。"
    _, msgs_override = await b.build(
        user_input="你好", history=[], persona_override=custom
    )
    override_sys = msgs_override[0]["content"]
    print("\n[自定义角色] system prompt 首段：")
    print("  " + override_sys.splitlines()[0])

    # 3) 断言
    assert "语文老师" in override_sys, "FAIL: 自定义角色未注入 system prompt"
    assert "语文老师" not in default_sys, "FAIL: 默认角色不应含自定义内容"
    assert "输出格式" in override_sys, "FAIL: 输出格式（规则段）丢失"

    print("\n" + "=" * 60)
    print("✅ 验证通过")
    print("  - persona_override 正确覆盖 YAML 默认角色")
    print("  - 不传 override 时退回默认（回归安全）")
    print("  - 规则/格式段保留，未丢约束")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
