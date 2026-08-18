"""量化拆书 — 文档分段 → 原文块（V0.2 主链路「解析与分段 → 观点」黏合）。

把 `services.parser.ParsedDocument`（含 passages：heading/text/page）平面化为可
交给 `bind_evidence`/`refine` 的 `(chapter, text)` 块序列，补齐主链路上游：
    书籍/文档导入 → 解析与分段 → (本章) 章节/段落 → 观点 → 证据绑定 → …
纯函数、确定性；不改变既有 parser 结构。
"""
from __future__ import annotations

from typing import Any


def default_chapter(passage: Any, index: int, fallback: str = "正文") -> str:
    """从 passage 提取章节标题；缺省回退到「第 N 段」。"""
    heading = (passage.heading or "").strip() if passage else ""
    if heading:
        return heading
    page = getattr(passage, "page_start", None)
    if page:
        return f"{fallback}·p{page}"
    return f"{fallback}·#{index + 1}"


def document_to_blocks(doc: Any, fallback_chapter: str = "正文") -> list[tuple[str, str]]:
    """ParsedDocument → [(chapter, text), ...]（供 bind_evidence/refine 使用）。

    跳过空段；无章节标题时用页号/序号生成占位标题。text 仅取 passage 正文。
    """
    blocks: list[tuple[str, str]] = []
    for index, passage in enumerate(getattr(doc, "passages", []) or []):
        text = (getattr(passage, "text", "") or "").strip()
        if not text:
            continue
        blocks.append((default_chapter(passage, index, fallback_chapter), text))
    return blocks


def document_text(doc: Any) -> str:
    """拼接全文（保留章节标题行），便于需要整篇文本的入口。"""
    lines: list[str] = []
    for chapter, text in document_to_blocks(doc):
        lines.append(f"## {chapter}\n{text}")
    return "\n\n".join(lines)


__all__ = ["document_text", "document_to_blocks"]