"""
角色系统实体定义 + YAML 配置加载器

技术栈说明：
  - Python dataclasses：结构化数据容器
  - PyYAML：解析 persona.yaml / rules.yaml / format.yaml
  - 配置驱动设计（Configuration-Driven）：
    角色行为不写在代码里，全部外挂到 YAML 文件。
    修改角色不需要改代码 → 非程序员可直接编辑

数据流向：
  persona.yaml + rules.yaml + format.yaml
       ↓ YAML 解析
  PersonaConfig + list[Rule] + FormatTemplate（Python 对象）
       ↓
  prompt/builder.py 读取 → 拼装 system prompt
       ↓
  LLM 收到完整角色设定
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 配置文件路径（相对于项目根目录）
DATA_DIR = Path(__file__).parent.parent.parent / "data"


# ════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════

@dataclass
class PersonaConfig:
    """
    角色身份配置（对应 persona.yaml）

    字段：
      name       → 角色名称（如"星程"）
      role       → 角色定位（如"AI机器人开发助手"）
      principles → 核心原则（不可违反的底线）
      style      → 说话风格（语言、语气、篇幅）
      behavior   → 行为约束（是否简洁、是否主动提问等）

    与老的 SYSTEM_PROMPT 的区别：
      老：一段自由文本 → LLM 行为不可控
      新：结构化字段 → 每个维度独立定义，可单独验证和修改
    """
    name: str = ""
    role: str = ""
    principles: list[str] = field(default_factory=list)
    style: dict = field(default_factory=dict)
    behavior: dict = field(default_factory=dict)

    def to_prompt_text(self) -> str:
        """
        将结构化配置编译为一段 system prompt 文本

        为什么保留 to_prompt_text 而不是直接传 YAML 给 LLM：
          LLM 能理解 YAML 但没有 prompt 工程上的人类语言效果好。
          编译成自然语言后，LLM 的遵从度更高。
        """
        parts = [f"你是{self.name}，{self.role}。"]

        if self.principles:
            parts.append("你必须遵守以下原则：")
            parts.extend(f"- {p}" for p in self.principles)

        style = self.style
        if style:
            tone = style.get("tone", "")
            verbosity = style.get("verbosity", "")
            if tone or verbosity:
                parts.append(f"说话风格：{tone}，篇幅{verbosity}。")

        behavior = self.behavior
        if behavior.get("concise"):
            parts.append("回答务必简洁。")

        return "\n".join(parts)


@dataclass
class Rule:
    """
    单条行为规则（对应 rules.yaml 中的每一条）

    id    → 规则唯一标识（如 R4），用于 Validator 精准匹配
    text  → 规则内容，直接注入 system prompt
    check → 可选：Validator 校验函数名（如 "ends_with"）
    args  → 校验函数的参数（如 {"suffix": "我的回答完毕。"}）

    为什么 rules 和 persona 分开放：
      persona 定义"我是谁"（身份）
      rules 定义"我必须怎么做"（约束）
      分开后可以给两个角色复用同一套规则，也可以给同一角色随时增删规则。
    """
    id: str = ""
    text: str = ""
    check: str = ""
    args: dict = field(default_factory=dict)


@dataclass
class FormatTemplate:
    """
    输出格式模板（对应 format.yaml）

    template  → 输出结构，如 "【结论】\n{conclusion}\n\n【分析】\n{analysis}"
    suffix    → 固定后缀，Validator 兜底追加，如 "我的回答完毕。"

    为什么格式和规则分开：
      规则是"不能做什么"（禁止项），格式是"必须长什么样"（结构约束）。
      格式变更频繁（老板今天要三段式、明天要两段式），
      独立出来只改一个 YAML 文件。
    """
    template: str = ""
    suffix: str = ""


# ════════════════════════════════════════════════════════
# 配置加载器（对外接口）
# ════════════════════════════════════════════════════════

def load_persona() -> PersonaConfig:
    """
    从 data/persona.yaml 加载角色配置

    为什么用 YAML 而不是 JSON：
      YAML 支持注释，配置文件中可以写说明文字，
      方便非技术人员理解每个字段的含义。
    """
    path = DATA_DIR / "persona.yaml"
    if not path.exists():
        return PersonaConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PersonaConfig(
        name=data.get("name", ""),
        role=data.get("role", ""),
        principles=data.get("principles", []),
        style=data.get("style", {}),
        behavior=data.get("behavior", {}),
    )


def load_rules() -> list[Rule]:
    """
    从 data/rules.yaml 加载行为规则

    返回规则列表，按 id 排序。
    prompt/builder.py 把它们拼入 system prompt，
    validator.py 按 check 字段逐条执行校验。
    """
    path = DATA_DIR / "rules.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules_data = data.get("rules", [])
    return [
        Rule(
            id=r.get("id", ""),
            text=r.get("text", ""),
            check=r.get("check", ""),
            args=r.get("args", {}),
        )
        for r in rules_data
    ]


def load_format() -> FormatTemplate:
    """
    从 data/format.yaml 加载输出格式模板
    """
    path = DATA_DIR / "format.yaml"
    if not path.exists():
        return FormatTemplate()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    template = ""
    if "template" in data:
        template = "\n".join(data["template"]) if isinstance(data["template"], list) else data["template"]

    return FormatTemplate(
        template=template,
        suffix=data.get("suffix", ""),
    )