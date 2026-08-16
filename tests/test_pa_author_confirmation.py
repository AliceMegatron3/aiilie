"""批次A回归:生成基线登记与作者确认定稿(自动保存不得产生作者信号)。"""
from __future__ import annotations

import pytest

import services.author_signals as author_signals
import services.document_confirmation as dc


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """把基线与信号落盘隔离到临时目录。"""
    monkeypatch.setattr(dc, "_baseline_path", lambda doc_id: tmp_path / "baselines" / f"{doc_id}.json")
    monkeypatch.setattr(author_signals, "_default_path", lambda: tmp_path / "author_signals.jsonl")
    return tmp_path


def test_baseline_requires_task_and_text(isolated_paths):
    with pytest.raises(ValueError):
        dc.set_generation_baseline("doc1", "", "内容")
    with pytest.raises(ValueError):
        dc.set_generation_baseline("doc1", "task1", "   ")
    assert dc.get_generation_baseline("doc1") is None


def test_confirm_produces_signal_and_consumes_baseline(isolated_paths):
    dc.set_generation_baseline("doc1", "task1", "生成稿甲" * 20)
    assert dc.get_generation_baseline("doc1")["task_id"] == "task1"

    result = dc.confirm_author_final("doc1", "生成稿甲" * 20)
    assert result["task_id"] == "task1"
    assert result["signal"]["retention"] == 1.0

    # 基线被消费,重复确认必须失败(避免同一稿反复计信号)
    assert dc.get_generation_baseline("doc1") is None
    with pytest.raises(ValueError):
        dc.confirm_author_final("doc1", "生成稿甲" * 20)

    signals = author_signals.load_author_signals()
    assert signals["task1"] == 1.0


def test_rewrite_lowers_retention(isolated_paths):
    dc.set_generation_baseline("doc2", "task2", "甲" * 100)
    result = dc.confirm_author_final("doc2", "乙" * 100)
    assert result["signal"]["retention"] == 0.0


def test_empty_final_text_rejected(isolated_paths):
    dc.set_generation_baseline("doc3", "task3", "生成稿")
    with pytest.raises(ValueError):
        dc.confirm_author_final("doc3", "   ")
    # 失败不应消费基线
    assert dc.get_generation_baseline("doc3") is not None


def test_autosave_path_does_not_write_signals(isolated_paths):
    """普通保存链路不涉及确认模块,信号文件应保持为空。"""
    dc.set_generation_baseline("doc4", "task4", "生成稿")
    assert author_signals.load_author_signals() == {}


def test_confirmation_endpoints_mounted():
    from api.api_router import api_router

    paths = {r.path for r in api_router.routes}
    assert any(p.endswith("/generation-baseline") for p in paths)
    assert any(p.endswith("/author-confirm") for p in paths)
