import re
from pathlib import Path

from infrastructure.knowledge.loaders.base import DocumentLoader
from domain.knowledge.models import ParsedSection


class TextLoader(DocumentLoader):
    """TXT / Markdown 解析器，按 Markdown 标题保留章节结构。"""

    _HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    _LIST_ITEM_PATTERN = re.compile(r"^(\d+)[.、]\s+(.+?)\s*$")
    _LIST_SPLIT_THRESHOLD = 400

    async def load(self, path: str) -> list[ParsedSection]:
        content = Path(path).read_text(encoding="utf-8-sig")
        return self._parse_sections(content)

    @classmethod
    def _parse_sections(cls, content: str) -> list[ParsedSection]:
        """按标题切分文本；没有标题的普通 TXT 仍作为一个 section。"""
        sections: list[ParsedSection] = []
        heading_path: dict[int, tuple[str, str]] = {}
        current_title: str | None = None
        current_level: int | None = None
        body_lines: list[str] = []
        in_fenced_block = False
        fence_marker: str | None = None

        def split_list_blocks(lines: list[str]) -> list[tuple[str | None, list[str]]]:
            """Split a multi-item top-level numbered list into atomic blocks."""
            if len("\n".join(lines).strip()) <= cls._LIST_SPLIT_THRESHOLD:
                return [(None, lines)]

            item_starts: list[tuple[int, re.Match[str]]] = []
            inside_fence = False
            active_fence: str | None = None

            for index, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(("```", "~~~")):
                    marker = stripped[:3]
                    if not inside_fence:
                        inside_fence = True
                        active_fence = marker
                    elif marker == active_fence:
                        inside_fence = False
                        active_fence = None
                    continue
                if inside_fence:
                    continue
                match = cls._LIST_ITEM_PATTERN.match(line)
                if match is not None:
                    item_starts.append((index, match))

            if len(item_starts) < 2:
                return [(None, lines)]

            blocks: list[tuple[str | None, list[str]]] = []
            first_index = item_starts[0][0]
            if any(line.strip() for line in lines[:first_index]):
                blocks.append((None, lines[:first_index]))

            for position, (start, match) in enumerate(item_starts):
                end = (
                    item_starts[position + 1][0]
                    if position + 1 < len(item_starts)
                    else len(lines)
                )
                item_title = f"{match.group(1)}. {match.group(2)}"
                blocks.append((item_title, lines[start:end]))
            return blocks

        def flush_section() -> None:
            nonlocal body_lines
            body_text = "\n".join(body_lines).strip()
            if current_level is not None and not body_text:
                body_lines = []
                return

            metadata = {}
            if current_level is not None:
                metadata = {
                    "heading_level": current_level,
                    "heading_path": [
                        title for _, title in heading_path.values()
                    ],
                }

            for item_title, item_lines in split_list_blocks(body_lines):
                item_body = "\n".join(item_lines).strip()
                if not item_body:
                    continue
                section_title = current_title
                item_metadata = dict(metadata)
                if item_title:
                    section_title = (
                        f"{current_title} > {item_title}"
                        if current_title
                        else item_title
                    )
                    item_metadata["list_item"] = item_title

                text = item_body
                if section_title:
                    text = f"章节：{section_title}\n{item_body}"
                sections.append(
                    ParsedSection(
                        text=text,
                        section_title=section_title,
                        metadata=item_metadata,
                    )
                )
            body_lines = []

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if not in_fenced_block:
                    in_fenced_block = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fenced_block = False
                    fence_marker = None
                body_lines.append(line)
                continue

            heading_match = None if in_fenced_block else cls._HEADING_PATTERN.match(line)
            if heading_match is None:
                body_lines.append(line)
                continue

            flush_section()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()

            for old_level in [item for item in heading_path if item >= level]:
                del heading_path[old_level]
            heading_path[level] = (line.strip(), title)

            current_title = " > ".join(
                heading_title
                for _, heading_title in heading_path.values()
            )
            current_level = level

        flush_section()
        return sections
