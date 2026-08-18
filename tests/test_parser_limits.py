"""轻量解析器资源限制测试（方向报告 P1-2：页数/成员数/解压大小/超时）。"""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from core.config_manager import config_manager
from services.parser import (
    EpubParser,
    ParserLimitError,
    PdfParser,
    load_parser_limits,
)


def _set_parser_cfg(monkeypatch, **kw):
    """把 kw 写入 config_manager 的 parser.* 键（下限由 load_parser_limits 保证≥1）。"""
    parser_cfg = config_manager._config.setdefault("parser", {})
    for key, val in kw.items():
        monkeypatch.setitem(parser_cfg, key, val)


def _make_epub(members: dict[str, bytes], n_extra_files: int = 0) -> bytes:
    """构造内存 zip：members 为 html 成员名→内容，n_extra_files 为额外非 html 成员。"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
        for i in range(n_extra_files):
            z.writestr(f"extra_{i}.bin", b"x")
    return buf.getvalue()


def test_load_parser_limits_has_defaults():
    limits = load_parser_limits()
    assert limits.page_limit >= 1
    assert limits.epub_max_members >= 1
    assert limits.epub_member_max_bytes >= 1
    assert limits.epub_max_decompressed_bytes >= 1
    assert limits.timeout_seconds >= 1
    assert set(limits.to_dict()) == {
        "page_limit", "epub_max_members", "epub_member_max_bytes",
        "epub_max_decompressed_bytes", "timeout_seconds",
    }


def test_epub_rejects_excessive_member_count(monkeypatch):
    _set_parser_cfg(monkeypatch, epub_max_members=3)
    data = _make_epub(
        {"c1.xhtml": b"<html>a</html>"},
        n_extra_files=10,  # 总成员 11 > 3 => 拒绝
    )
    with pytest.raises(ParserLimitError, match="成员数"):
        EpubParser().parse_document(BytesIO(data), "book.epub")


def test_epub_skips_oversized_member(monkeypatch):
    # c1(<html>first</html>)=18 字节 ≤ 上限 20 => 保留；c2 明显超限 => 跳过
    _set_parser_cfg(monkeypatch, epub_member_max_bytes=20)
    data = _make_epub({"c1.xhtml": b"<html>first</html>", "c2.xhtml": b"<html>second second second second</html>"})
    doc = EpubParser().parse_document(BytesIO(data), "book.epub")
    assert any("first" in p.text for p in doc.passages)
    assert not any("second" in p.text for p in doc.passages)


def test_epub_stops_reading_when_total_decompressed_exceeded(monkeypatch):
    # 每个成员 <html>X</html> 解压后恰为 14 字节；累计上限设为 28：
    # c1(14) + c2(14) 后累计 28 已读取，读到 c3 前累计将超 28 => 截断 c3。
    _set_parser_cfg(monkeypatch, epub_max_decompressed_bytes=28)
    data = _make_epub({
        "c1.xhtml": b"<html>a</html>",  # 14
        "c2.xhtml": b"<html>b</html>",  # 14
        "c3.xhtml": b"<html>c</html>",  # 14
    })
    doc = EpubParser().parse_document(BytesIO(data), "book.epub")
    texts = [p.text for p in doc.passages]
    assert any("a" in t for t in texts)
    assert any("b" in t for t in texts)
    assert not any("c" in t for t in texts)