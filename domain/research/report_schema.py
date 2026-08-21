"""研究报告的结构化块模型。

校验分两层：
- 结构校验（本文件）：JSON 形状、块 id 格式、section/kind 枚举、table 的列行形状、
  非 table 块必须有 text、报告必须覆盖四个 section、块 id 唯一。
- 引用校验（app/research/graph.py 的 _report_issues）：evidence/comparison/table 必须有
  来源、synthesis 必须指向有来源的依据块、引用编号必须真实存在。这是“软校验”，
  走 repair 循环，不在解析阶段硬失败。
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReportSection(str, Enum):
    SUMMARY = "summary"
    FINDINGS = "findings"
    UNCERTAINTIES = "uncertainties"
    CONCLUSION = "conclusion"


class ReportBlockKind(str, Enum):
    EVIDENCE = "evidence"
    COMPARISON = "comparison"
    SYNTHESIS = "synthesis"
    LIMITATION = "limitation"
    RECOMMENDATION = "recommendation"
    TABLE = "table"


class ReportBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    section: ReportSection
    kind: ReportBlockKind
    text: str = Field(default="", max_length=1500)
    citation_ids: list[str] = Field(default_factory=list, max_length=12)
    based_on_block_ids: list[str] = Field(default_factory=list, max_length=12)
    columns: list[str] = Field(default_factory=list, max_length=12)
    rows: list[list[str]] = Field(default_factory=list, max_length=30)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("citation_ids", "based_on_block_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def validate_kind_shape(self):
        # 只校验“结构形状”。引用/依据这类“软校验”交给 _report_issues，
        # 以便缺引用时能走 repair 循环，而不是在解析阶段直接硬失败。
        if self.kind == ReportBlockKind.TABLE:
            if not self.columns or not self.rows:
                raise ValueError("table 块必须包含 columns 和 rows")
            if any(len(row) != len(self.columns) for row in self.rows):
                raise ValueError("table 的每一行必须与 columns 列数一致")
        elif not self.text:
            raise ValueError(f"{self.kind.value} 块必须包含 text")
        return self


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    blocks: list[ReportBlock] = Field(min_length=4, max_length=16)

    @model_validator(mode="after")
    def validate_report(self):
        block_ids = [block.id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("报告块 id 不能重复")

        sections = {block.section for block in self.blocks}
        required_sections = {
            ReportSection.SUMMARY,
            ReportSection.FINDINGS,
            ReportSection.UNCERTAINTIES,
            ReportSection.CONCLUSION,
        }
        if not required_sections.issubset(sections):
            raise ValueError("报告必须包含摘要、主要发现、分歧与不确定性、结论")

        return self
