"""批次4回归:空闲索引器(触发条件/欠账/产物/开关)。"""
from __future__ import annotations

import json

import pytest

from services.idle_indexer import (
    _load_manifest,
    _save_manifest,
    index_document,
    is_system_idle,
    pending_docs,
    run_idle_index_cycle,
)


def _doc(doc_id, updated, raw="正文内容", project_id="p1"):
    return {"doc_id": doc_id, "updated_at": updated, "raw_content": raw, "project_id": project_id}


def test_pending_docs_filters_by_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr("services.idle_indexer.index_manifest_path", lambda: tmp_path / "m.json")
    docs = [
        _doc("a", "2026-08-16T10:00:00"),  # 已索引且未变
        _doc("b", "2026-08-16T12:00:00"),  # 未索引
        _doc("c", "2026-08-16T11:00:00"),  # 已索引但文档更新更晚 → 欠账
    ]
    manifest = {"a": "2026-08-16T10:00:00", "c": "2026-08-16T09:00:00"}
    pending = pending_docs(docs, manifest)
    assert {d["doc_id"] for d in pending} == {"b", "c"}


def test_manifest_roundtrip_and_dirty_tolerance(tmp_path, monkeypatch):
    monkeypatch.setattr("services.idle_indexer.index_manifest_path", lambda: tmp_path / "m.json")
    _save_manifest({"a": "2026-01-01"})
    assert _load_manifest() == {"a": "2026-01-01"}
    # 损坏文件 → 空清单(全量欠账)
    (tmp_path / "m.json").write_text("{坏json", encoding="utf-8")
    assert _load_manifest() == {}


@pytest.mark.asyncio
async def test_index_document_produces_draft_card_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.idle_indexer.index_manifest_path", lambda: tmp_path / "m.json"
    )
    monkeypatch.setattr(
        "services.idle_indexer._summary_path",
        lambda doc_id: tmp_path / f"{doc_id}_summary.json",
    )

    class _Idx:
        def __init__(self):
            self.saved = []

        async def save_card(self, card):
            self.saved.append(card)
            return f"card_{len(self.saved)}"

    indexer = _Idx()
    result = await index_document("doc1", "正文" * 30, project_id="p1", indexer=indexer)
    assert result["summary_card"] == "card_1"
    card = indexer.saved[0]
    assert card.status == "draft", "索引产物必须 draft,不得自动批准"
    assert card.payload["needs_author_review"] is True
    assert card.payload["source"] == "idle_index"
    # 落盘
    assert (tmp_path / "doc1_summary.json").exists()
    summary = json.loads((tmp_path / "doc1_summary.json").read_text(encoding="utf-8"))
    assert summary["doc_id"] == "doc1"


@pytest.mark.asyncio
async def test_run_cycle_updates_manifest_and_skips_clean(tmp_path, monkeypatch):
    manifest_path = tmp_path / "m.json"
    monkeypatch.setattr("services.idle_indexer.index_manifest_path", lambda: manifest_path)

    class _PM:
        async def list_projects(self):
            return [type("P", (), {"project_id": "p1"})()]

        async def list_project_documents(self, pid):
            return [
                type("D", (), {
                    "doc_id": "d1", "updated_at": "2026-08-16T10:00:00",
                    "raw_content": "内容", "project_id": pid,
                })(),
            ]

    pm = _PM()
    r1 = await run_idle_index_cycle(pm)
    assert r1["indexed"] == 1 and r1["failures"] == 0
    assert _load_manifest()["d1"]

    # 二次运行:已索引且未变 → 全部跳过
    r2 = await run_idle_index_cycle(pm)
    assert r2["indexed"] == 0 and r2["skipped"] == 1


@pytest.mark.asyncio
async def test_is_system_idle_detects_busy():
    class _Busy:
        queue_size = 2
        _active_task_id = "t1"

    class _Idle:
        queue_size = 0
        _active_task_id = None

    assert await is_system_idle(_Idle()) is True
    assert await is_system_idle(_Busy()) is False
    assert await is_system_idle(None) is True


def test_idle_index_feature_flag_default_on():
    from core.config_manager import config_manager

    assert config_manager.get_bool("feature.idle_index_enable", True) is True
