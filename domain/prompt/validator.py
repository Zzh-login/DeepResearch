"""
输出校验器 —— 在 LLM 返回后、展示前进行规则兜底

技术栈说明：
  - 规则驱动：读取 rules.yaml 的每条 check 字段
  - 插件化校验：每个 check 对应一个校验函数
  - 链式执行：按规则顺序逐条校验，失败则修正
  - 依赖倒置：不依赖 LLM / TTS / Storage，只依赖 domain/persona/entity.py

设计原则：
  Prompt 是软约束（LLM 尽量遵守），Validator 是硬约束（代码强制兜底）。
  两者结合：99% 靠 prompt，1% 靠 validator 兜住底线。
"""

import re
from typing import Callable, Dict, Optional
from ..persona.entity import load_rules, Rule, load_format, FormatTemplate


class Validator:
    """
    输出校验器

    职责：
      1. 加载 rules.yaml，找出带 check 字段的规则
      2. 为每个 check 找到对应的校验函数
      3. 在 LLM 返回后，逐条执行校验
      4. 校验失败时自动修正（如追加后缀、补全格式）

    为什么独立成类而不是 session.py 里的 if 判断：
      - 可扩展：新增校验规则只需在 rules.yaml 加一行，无需改代码
      - 可测试：单独测校验逻辑，不依赖 LLM 调用
      - 可复用：未来多 Agent 协作时，每个 Agent 用自己的 Validator
    """

    def __init__(self):
        """初始化校验器并构建校验函数映射。

        准备三条内部状态：
          - _rules：行为规则列表，采用惰性加载（首次调用 validate 时才读取 rules.yaml）
          - _format：输出格式模板，惰性加载（首次 validate_with_fallback 时读取 format.yaml）
          - _checkers：check 名称到校验函数的映射表，由 _register_checkers() 构建
        """
        self._rules: list[Rule] = []
        self._format: Optional[FormatTemplate] = None
        self._checkers: Dict[str, Callable] = self._register_checkers()

    def _register_checkers(self) -> Dict[str, Callable]:
        """
        注册所有校验函数

        每个函数签名：checker(text: str, **args) -> (passed: bool, fixed: str)
          - text: 待校验的 LLM 输出
          - args: rules.yaml 中该条规则的 args 字段
          - passed: 是否通过校验
          - fixed: 修正后的文本（如果通过则返回原 text）

        为什么用字典注册而不是 if-elif：
          新增校验函数时只需在字典加一行，符合开闭原则。
        """
        return {
            "ends_with": self._check_ends_with,
            "starts_with": self._check_starts_with,
            "contains": self._check_contains,
            "not_contains": self._check_not_contains,
            "length": self._check_length,
            "regex": self._check_regex,
        }

    # ════════════════════════════════════════════════════════
    # 校验函数实现（每个对应 rules.yaml 的一个 check 值）
    # ════════════════════════════════════════════════════════

    def _check_ends_with(self, text: str, **args) -> tuple[bool, str]:
        """检查文本是否以指定后缀结尾，未通过时自动追加后缀。

        参数：
          text：待校验的 LLM 输出文本
          args：规则参数，取 args["suffix"] 作为目标后缀
        返回：
          (passed, fixed) 元组；通过时 fixed 等于原 text，
          未通过且 suffix 非空时自动在结尾追加 suffix。
        注意：若 suffix 为空，视为直接通过（无需校验）。
        """
        suffix = args.get("suffix", "")
        if not suffix:
            return True, text
        if text.endswith(suffix):
            return True, text
        # 自动修正：追加后缀
        return False, text.rstrip() + "\n" + suffix

    def _check_starts_with(self, text: str, **args) -> tuple[bool, str]:
        """检查文本是否以指定前缀开头，未通过时自动插入前缀。

        参数：
          text：待校验的 LLM 输出文本
          args：规则参数，取 args["prefix"] 作为目标前缀
        返回：
          (passed, fixed) 元组；通过时 fixed 等于原 text，
          未通过且 prefix 非空时自动在开头插入 prefix。
        注意：若 prefix 为空，视为直接通过（无需校验）。
        """
        prefix = args.get("prefix", "")
        if not prefix:
            return True, text
        if text.startswith(prefix):
            return True, text
        # 自动修正：插入前缀
        return False, prefix + "\n" + text.lstrip()

    def _check_contains(self, text: str, **args) -> tuple[bool, str]:
        """检查文本是否包含指定关键词（无法自动修正）。

        参数：
          text：待校验的 LLM 输出文本
          args：规则参数，取 args["keyword"] 作为目标关键词
        返回：
          (passed, fixed) 元组。通过时 fixed 等于原 text；
          不通过时 fixed 仍为原 text（无法自动修正，因为不知道插入位置）。
        注意：若 keyword 为空，视为直接通过。
        """
        keyword = args.get("keyword", "")
        if not keyword:
            return True, text
        if keyword in text:
            return True, text
        # 无法自动修正（不知道插在哪里）
        return False, text

    def _check_not_contains(self, text: str, **args) -> tuple[bool, str]:
        """检查文本是否不包含指定关键词，未通过时删除该关键词。

        参数：
          text：待校验的 LLM 输出文本
          args：规则参数，取 args["keyword"] 作为需排除的关键词
        返回：
          (passed, fixed) 元组。通过时 fixed 等于原 text；
          不通过时 fixed 为删除 keyword 后的文本。
        注意：若 keyword 为空，视为直接通过。
        """
        keyword = args.get("keyword", "")
        if not keyword:
            return True, text
        if keyword not in text:
            return True, text
        # 自动修正：删除关键词
        return False, text.replace(keyword, "")

    def _check_length(self, text: str, **args) -> tuple[bool, str]:
        """检查文本长度（按去除首尾空白后的字符数）是否在范围内。

        参数：
          text：待校验的 LLM 输出文本
          args：规则参数，取 args["min"]（默认 0）与 args["max"]（默认 10**6）
        返回：
          (passed, fixed) 元组。通过时 fixed 等于原 text；
          不通过时 fixed 仍为原 text（无法自动修正，不知道该截断还是补全）。
        """
        min_len = args.get("min", 0)
        max_len = args.get("max", 10**6)
        length = len(text.strip())
        if min_len <= length <= max_len:
            return True, text
        # 无法自动修正（不知道截断还是补全）
        return False, text

    def _check_regex(self, text: str, **args) -> tuple[bool, str]:
        """检查文本是否匹配指定正则表达式（支持跨行 DOTALL）。

        参数：
          text：待校验的 LLM 输出文本
          args：规则参数，取 args["pattern"] 作为正则模式串
        返回：
          (passed, fixed) 元组。通过时 fixed 等于原 text；
          不通过时 fixed 仍为原 text（正则过于复杂，无法自动修正）。
        注意：若 pattern 为空，视为直接通过。
        """
        pattern = args.get("pattern", "")
        if not pattern:
            return True, text
        if re.search(pattern, text, re.DOTALL):
            return True, text
        # 无法自动修正（正则太复杂）
        return False, text

    # ════════════════════════════════════════════════════════
    # 公开接口
    # ════════════════════════════════════════════════════════

    def validate(self, text: str) -> str:
        """
        对 LLM 输出执行所有校验

        流程：
          1. 加载 rules.yaml（惰性加载）
          2. 找出带 check 字段的规则
          3. 按规则顺序逐条校验
          4. 任何一条失败则用修正后的文本继续下一条校验
          5. 返回最终修正结果

        为什么逐条校验而不是一次性：
          规则可能有依赖关系，比如先检查格式再检查后缀。
          一条修正后可能触发另一条失败，需要链式处理。
        """
        if not self._rules:
            self._rules = load_rules()

        current = text.strip()
        applied_rules = []

        for rule in self._rules:
            if not rule.check:
                continue  # 无 check 字段的规则仅用于 prompt，不校验

            checker = self._checkers.get(rule.check)
            if not checker:
                continue  # 未知的 check 类型，跳过

            passed, fixed = checker(current, **rule.args)
            if not passed:
                # 记录修正
                applied_rules.append(rule.id)
                current = fixed.strip()

        # 日志（可选）
        if applied_rules:
            print(f"[Validator] 应用了规则：{applied_rules}")

        return current

    def validate_with_fallback(self, text: str, truncated: bool = False) -> str:
        """
        校验 + 兜底追加后缀

        如果经过所有校验后，仍然没有以 format.yaml 的 suffix 结尾，
        则强制追加。

        这是最终保险：即使 prompt 和 validator 都漏了，
        代码层也要保证"我的回答完毕。"一定在结尾。

        参数：
          text       待校验的 LLM 输出文本
          truncated  LLM 返回 finish_reason=="length" 时为 True，
                     表示回复被 max_tokens 截断，会在末尾追加省略号标记，
                     避免用户看到一句戛然而止的半截话（对应 Step ⑤ 截断自检）。
        """
        validated = self.validate(text)

        # 截断标记：被 max_tokens 截断时，补一个明显的省略号提示
        if truncated and not validated.rstrip().endswith("…"):
            validated = validated.rstrip() + "…（回复可能因长度限制被截断）"

        if not self._format:
            self._format = load_format()
        suffix = self._format.suffix if self._format else ""
        if suffix and not validated.endswith(suffix):
            validated = validated.rstrip() + "\n" + suffix

        return validated.strip()