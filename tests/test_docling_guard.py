"""Docling 可选生产接入防护层 golden tests（桩解析器，不安装重型 docling）。

覆盖：页数限制截断、超时、并发信号量、离线缺模型失败、provenance 字段、
表格/页码/版面结构化透传、ParserFactory 在 docling.enabled=false 时不用 Docling。
"""
from __future__ import annotations

import asyncio
import sys
import time
from io import BytesIO

import pytest

from core.config_manager import config_manager
from services.docling_guard import (
    DoclingGuard,
    DoclingLimits,
    DoclingOfflineError,
    DoclingTimeoutError,
    build_provenance,
    check_docling_readiness,
    compute_source_hash,
    ensure_model_artifacts,
    load_limits,
    truncate_document,
)
from services.parser import DoclingParser, ParsedDocument, ParsedPassage, ParserFactory, PdfParser


class _StubDoclingParser:
    """桩 DoclingParser：模拟返回结构化页/表格/页码/版面 JSON，不真正安装 docling。"""

    parser_id = "docling"
    parser_version = "stub"

    def __init__(self, page_count=3, delay=0.0, tracker=None):
        self.pages = [
            {"page": i, "layout_blocks": [{"type": "paragraph", "text": f"page {i} block"}]}
            for i in range(1, page_count + 1)
        ]
        self.tables = [{"page": 1, "cells": [["h1", "h2"], ["a", "b"]]}]
        self.delay = delay
        self.tracker = tracker

    def parse_document(self, file, filename) -> ParsedDocument:
        if self.tracker is not None:
            self.tracker["active"] += 1
            self.tracker["max"] = max(self.tracker["max"], self.tracker["active"])
        if self.delay:
            time.sleep(self.delay)
        if self.tracker is not None:
            self.tracker["active"] -= 1
        passages = [
            ParsedPassage(sequence=i - 1, text=f"page {i} content", page_start=i, page_end=i)
            for i in range(1, len(self.pages) + 1)
        ]
        return ParsedDocument(
            title="stub",
            media_type="application/pdf",
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            passages=passages,
            pages=self.pages,
            tables=self.tables,
        )


def _docling_cfg(monkeypatch, values: dict) -> dict:
    cfg = config_manager._config.setdefault("docling", {})
    monkeypatch.setitem(cfg, "enabled", values.pop("enabled", False))
    for key, value in values.items():
        monkeypatch.setitem(cfg, key, value)
    return cfg


# ── 限制读取 ─────────────────────────────────────────────
def test_load_limits_defaults():
    assert load_limits() == DoclingLimits()


def test_load_limits_reads_config_values(monkeypatch):
    _docling_cfg(monkeypatch, {"page_limit": 50, "cpu_threads": 3, "timeout_seconds": 30, "max_concurrency": 4})
    limits = load_limits()
    assert limits.page_limit == 50
    assert limits.cpu_threads == 3
    assert limits.timeout_seconds == 30
    assert limits.max_concurrency == 4


# ── 页数限制截断 ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_page_limit_truncates_and_marks_truncated():
    stub = _StubDoclingParser(page_count=300)
    guard = DoclingGuard(limits=DoclingLimits(page_limit=200))
    result = await guard.run_parse(stub, BytesIO(b"x" * 16), "book.pdf")
    assert result.truncated is True
    assert len(result.pages) == 200
    assert len(result.passages) == 200
    assert result.provenance["truncated"] is True
    assert result.provenance["page_count"] == 200


@pytest.mark.asyncio
async def test_page_limit_not_exceeded_keeps_all():
    stub = _StubDoclingParser(page_count=5)
    guard = DoclingGuard(limits=DoclingLimits(page_limit=200))
    result = await guard.run_parse(stub, BytesIO(b"x" * 16), "book.pdf")
    assert result.truncated is False
    assert len(result.pages) == 5
    assert result.provenance["truncated"] is False
    assert result.provenance["page_count"] == 5


def test_truncate_document_pure():
    doc = _StubDoclingParser(page_count=10).parse_document(BytesIO(b"x"), "b.pdf")
    truncated = truncate_document(doc, 3)
    assert len(truncated.pages) == 3
    assert len(truncated.tables) <= 3
    assert all((p.page_start or 1) <= 3 for p in truncated.passages)


# ── 超时 ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_timeout_fails_with_reason():
    stub = _StubDoclingParser(delay=0.5)
    guard = DoclingGuard(limits=DoclingLimits(timeout_seconds=0.05))
    with pytest.raises(DoclingTimeoutError) as exc:
        await guard.run_parse(stub, BytesIO(b"x"), "book.pdf")
    assert "超时" in str(exc.value)


# ── 并发信号量 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_max_concurrency_semaphore_serializes():
    tracker = {"active": 0, "max": 0}
    stub = _StubDoclingParser(delay=0.15, tracker=tracker)
    guard = DoclingGuard(limits=DoclingLimits(max_concurrency=1, timeout_seconds=5))
    results = await asyncio.gather(*[guard.run_parse(stub, BytesIO(b"x"), f"b{i}.pdf") for i in range(3)])
    assert len(results) == 3
    assert tracker["max"] == 1


@pytest.mark.asyncio
async def test_max_concurrency_allows_parallel():
    tracker = {"active": 0, "max": 0}
    stub = _StubDoclingParser(delay=0.15, tracker=tracker)
    guard = DoclingGuard(limits=DoclingLimits(max_concurrency=2, timeout_seconds=5))
    await asyncio.gather(*[guard.run_parse(stub, BytesIO(b"x"), f"b{i}.pdf") for i in range(4)])
    assert tracker["max"] == 2


# ── 离线缺模型 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_offline_mode_missing_models_fails(tmp_path):
    guard = DoclingGuard(
        limits=DoclingLimits(), cache_dir=tmp_path / "no_models", offline=True
    )
    stub = _StubDoclingParser()
    with pytest.raises(DoclingOfflineError) as exc:
        await guard.run_parse(stub, BytesIO(b"x"), "book.pdf")
    assert "模型缓存目录" in str(exc.value)
    assert "预下载" in str(exc.value)


def test_offline_ready_when_artifacts_present(tmp_path):
    cache_dir = tmp_path / "models"
    cache_dir.mkdir()
    (cache_dir / "model.bin").write_bytes(b"model-artifact")
    guard = DoclingGuard(cache_dir=cache_dir, offline=True)
    guard.check_offline()  # 不应抛异常
    ready, missing = ensure_model_artifacts(cache_dir)
    assert ready is True
    assert missing == []


def test_ensure_model_artifacts_reports_missing(tmp_path):
    ready, missing = ensure_model_artifacts(tmp_path / "missing")
    assert ready is False
    assert missing


# ── provenance 与结构化透传 ──────────────────────────────
@pytest.mark.asyncio
async def test_provenance_fields_and_structured_pass_through():
    stub = _StubDoclingParser(page_count=3)
    guard = DoclingGuard(limits=DoclingLimits())
    source = b"source-bytes-1234"
    result = await guard.run_parse(stub, BytesIO(source), "book.pdf")

    prov = result.provenance
    assert prov["parser_id"] == "docling"
    assert prov["parser_version"] == "stub"
    assert prov["model_cache"]
    assert prov["page_count"] == 3
    assert prov["truncated"] is False
    assert prov["elapsed_ms"] >= 0
    assert prov["source_hash"] == compute_source_hash(source)

    # 表格/页码/版面结构化透传
    assert result.tables == [{"page": 1, "cells": [["h1", "h2"], ["a", "b"]]}]
    assert result.pages[0]["page"] == 1
    assert result.pages[0]["layout_blocks"][0]["type"] == "paragraph"


def test_build_provenance_fields():
    prov = build_provenance(
        parser_id="docling", parser_version="1.0", model_cache="/cache",
        page_count=7, truncated=False, elapsed_ms=42, source_hash="abc",
    )
    assert prov == {
        "parser_id": "docling",
        "parser_version": "1.0",
        "model_cache": "/cache",
        "page_count": 7,
        "truncated": False,
        "elapsed_ms": 42,
        "source_hash": "abc",
    }


# ── ParserFactory 可选加载 ───────────────────────────────
def test_factory_disabled_docling_uses_fallback(monkeypatch):
    _docling_cfg(monkeypatch, {"enabled": False})
    parser = ParserFactory.get_parser("book.pdf")
    assert not isinstance(parser, DoclingParser)
    assert isinstance(parser, PdfParser)


def test_factory_enabled_but_docling_unavailable_falls_back(monkeypatch):
    # 模拟 docling 未安装（测试环境不装重型依赖）→ 即使启用也回退轻量解析器
    _docling_cfg(monkeypatch, {"enabled": True})
    monkeypatch.setitem(sys.modules, "docling", None)  # 使 import docling 抛 ImportError
    parser = ParserFactory.get_parser("book.pdf")
    assert not isinstance(parser, DoclingParser)
    assert isinstance(parser, PdfParser)


def test_factory_enabled_with_installed_docling_uses_docling(monkeypatch):
    # 模拟 docling 已安装 → 选用 DoclingParser（其同步解析因真实 docling 缺失走 guard 抛错，
    # 但工厂选择逻辑正确）。用假模块注入避免真实安装。
    class _FakeDocling:
        pass

    monkeypatch.setitem(sys.modules, "docling", _FakeDocling())
    _docling_cfg(monkeypatch, {"enabled": True})
    parser = ParserFactory.get_parser("book.pdf")
    assert isinstance(parser, DoclingParser)


# ── readiness ────────────────────────────────────────────
def test_check_docling_readiness_disabled(monkeypatch):
    _docling_cfg(monkeypatch, {"enabled": False})
    report = check_docling_readiness()
    assert report["enabled"] is False
    assert "installed" in report
    assert "offline_ready" in report
    assert report["limits"]["max_concurrency"] >= 1
    assert report["limits"]["page_limit"] >= 1
    assert isinstance(report["errors"], list)


def test_docling_readiness_endpoint_mounted():
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    paths = {r.path for r in flatten_api_router(api_router)}
    assert any(p.endswith("/system/docling/readiness") for p in paths)
