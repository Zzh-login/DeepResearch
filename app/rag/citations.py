import re
from dataclasses import replace
from typing import Any

from domain.rag.models import Citation, RetrievedSource


SOURCE_PATTERN = re.compile(r"\[S([^\[\]]*)\]")
INSUFFICIENT_CONTEXT_ANSWER = "根据当前知识库资料无法确定。"
_INSUFFICIENT_CONTEXT_PATTERNS = (
    re.compile(r"根据当前知识库资料无法确定"),
    re.compile(
        r"知识库(?:证据|资料).{0,30}(?:未包含|没有|找不到|未找到).{0,30}"
        r"(?:相关|解释|信息|内容)"
    ),
)


def is_insufficient_context_answer(answer: str) -> bool:
    """识别模型明确表示知识库证据不足的回答。"""
    text = SOURCE_PATTERN.sub("", str(answer or "")).strip()
    return any(
        pattern.search(text[:500])
        for pattern in _INSUFFICIENT_CONTEXT_PATTERNS
    )


def prepare_rag_context(
    sources: list[RetrievedSource],
    max_total_chars: int,
    max_source_chars: int,
) -> tuple[str, list[RetrievedSource]]:
    """把证据放进字符预算，并返回真正放入提示词的证据。"""
    blocks: list[str] = []
    included: list[RetrievedSource] = []
    used_chars = 0

    for source in sources:
        location = [f"文件：{source.filename}", f"切片序号：{source.chunk_index}"]
        if source.page_number is not None:
            location.append(f"页码：{source.page_number}")
        if source.section_title:
            location.append(f"章节：{source.section_title}")

        prefix = f"[{source.source_id}]\n{'；'.join(location)}\n原文："
        separator_size = 2 if blocks else 0
        remaining = max_total_chars - used_chars - len(prefix) - separator_size
        if remaining <= 0:
            break

        content = source.content[: min(max_source_chars, remaining)].strip()
        if not content:
            continue

        block = prefix + content
        blocks.append(block)
        # 返回的 source.content 必须与真正进入提示词的内容一致。
        # 后续引用摘要只能展示模型实际看过的证据，不能展示被截断掉的原文。
        included.append(replace(source, content=content))
        used_chars += len(block) + separator_size

    return "\n\n".join(blocks), included


def extract_source_ids(answer: str) -> list[str]:
    """提取所有 [S...] 标记；S0、Sabc 等也会被提取并判为非法。"""
    result: list[str] = []
    for number in SOURCE_PATTERN.findall(answer):
        source_id = f"S{number}"
        if source_id not in result:
            result.append(source_id)
    return result


def build_citations(
    answer: str,
    sources: list[RetrievedSource],
) -> tuple[list[Citation], bool]:
    # 拒答不是由任何切片支持的事实；清空模型可能附加的无关引用。
    if is_insufficient_context_answer(answer):
        return [], bool(str(answer or "").strip())

    source_map = {source.source_id: source for source in sources}
    referenced_ids = extract_source_ids(answer)
    invalid_ids = [source_id for source_id in referenced_ids if source_id not in source_map]

    citations: list[Citation] = []
    for source_id in referenced_ids:
        source = source_map.get(source_id)
        if source is None:
            continue
        citations.append(
            Citation(
                source_id=source.source_id,
                chunk_id=source.chunk_id,
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                filename=source.filename,
                excerpt=source.content[:500],
                score=source.score,
                page_number=source.page_number,
                section_title=source.section_title,
            )
        )

    citation_valid = bool(answer.strip()) and bool(citations) and not invalid_ids
    return citations, citation_valid


def message_content_to_text(content: Any) -> str:
    """兼容 LangChain 返回字符串或内容块列表。"""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()
