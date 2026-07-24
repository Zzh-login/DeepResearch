"""
提示词子包（domain.prompt）。

负责把领域配置转换为 LLM 可消费的 system prompt，并在 LLM 返回后做强制兜底：
  - builder.py：读取 persona / rules / format 配置，拼装完整 system prompt
  - validator.py：Validator 类，按 rules.yaml 的 check 字段逐条校验并修正输出

设计原则：prompt 是软约束（尽量遵守），validator 是硬约束（代码兜底底线）。
"""

# domain.prompt package
