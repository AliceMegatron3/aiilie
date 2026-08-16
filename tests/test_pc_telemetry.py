"""批次C回归:统一遥测层(兼容/轮转/脏行) + 插件级指纹归因。"""
from __future__ import annotations

import json

import pytest

from models.behavior_plugin import BehaviorPluginRunRecord
from services import telemetry_store as ts
from services.author_signals import (
    attribute_plugin_outcomes,
    load_author_signal_records,
    load_author_signals,
    record_author_signal,
)
from services.behavior_plugins import load_run_records, persist_run_records


def test_append_read_and_dirty_line_tolerance(tmp_path):
    path = tmp_path / "t.jsonl"
    assert ts.append_record(path, {"a": 1}) is True
    ts.append_record(path, {"a": 2})
    path.write_text(path.read_text(encoding="utf-8") + "{坏行}\n[1,2]\n", encoding="utf-8")
    records = ts.read_records(path)
    assert [r["a"] for r in records] == [1, 2], "脏行与非字典行都应被跳过"


def test_rotation_keeps_previous_file(tmp_path):
    path = tmp_path / "r.jsonl"
    ts.append_record(path, {"n": 1}, rotate_bytes=10)
    ts.append_record(path, {"n": 2}, rotate_bytes=10)
    backup = path.with_suffix(path.suffix + ".1")
    assert backup.exists(), "超过阈值应轮转出 .1 备份"
    assert [r["n"] for r in ts.read_records(path)] == [2]
    assert [r["n"] for r in ts.read_records(path, include_rotated=True)] == [1, 2]


def test_text_digest_stability(tmp_path):
    assert ts.text_digest("") == ""
    assert ts.text_digest("  同一段  ") == ts.text_digest("同一段")
    assert ts.text_digest("甲") != ts.text_digest("乙")
    assert len(ts.text_digest("内容")) == 16


def test_legacy_run_records_without_new_fields(tmp_path):
    """旧格式(无 run_id/指纹)必须仍可读,不迁移即兼容。"""
    path = tmp_path / "runs.jsonl"
    path.write_text(
        json.dumps({"plugin_id": "a", "task_id": "t1", "triggered": True,
                    "accepted": True, "reason": "accepted"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    records = load_run_records(history_path=path)
    assert len(records) == 1
    assert records[0].plugin_id == "a"
    assert records[0].run_id, "缺字段应自动生成 run_id"
    assert records[0].output_digest == ""


def test_run_record_roundtrip_with_digests(tmp_path):
    path = tmp_path / "runs.jsonl"
    persist_run_records([
        BehaviorPluginRunRecord(
            plugin_id="a", task_id="t1", triggered=True, accepted=True,
            input_digest="aaa", output_digest="bbb",
        )
    ], history_path=path)
    loaded = load_run_records(history_path=path)
    assert loaded[0].input_digest == "aaa" and loaded[0].output_digest == "bbb"


def test_plugin_attribution_kept_vs_superseded(tmp_path, monkeypatch):
    signals_path = tmp_path / "signals.jsonl"
    monkeypatch.setattr(
        ts, "resolve_path",
        lambda name, override=None: (tmp_path / name) if override is None else __import__("pathlib").Path(override),
    )
    final_text = "作者定稿正文" * 10
    # t1:定稿与插件a产物一致;t2:定稿被改写
    record_author_signal("t1", final_text, final_text, path=signals_path)
    record_author_signal("t2", "生成稿乙" * 10, "作者重写乙" * 10, path=signals_path)
    signal_records = load_author_signal_records(path=signals_path)
    finals = {r["task_id"]: r["final_digest"] for r in signal_records}

    runs = [
        BehaviorPluginRunRecord(plugin_id="a", task_id="t1", triggered=True,
                                accepted=True, output_digest=finals["t1"]),
        BehaviorPluginRunRecord(plugin_id="b", task_id="t1", triggered=True,
                                accepted=True, output_digest="other-digest"),
        BehaviorPluginRunRecord(plugin_id="a", task_id="t2", triggered=True,
                                accepted=True, output_digest="stale"),
        # 未采纳/无指纹不参与归因
        BehaviorPluginRunRecord(plugin_id="c", task_id="t1", triggered=True, accepted=False),
    ]
    attribution = attribute_plugin_outcomes(runs, signal_records)
    assert attribution["a"]["attributed_runs"] == 2
    assert attribution["a"]["kept_final"] == 1
    assert attribution["a"]["kept_final_rate"] == 0.5
    assert attribution["b"]["kept_final"] == 0 and attribution["b"]["superseded"] == 1
    assert "c" not in attribution

    signals = load_author_signals(path=signals_path)
    assert signals["t1"] == 1.0
    # 改写稿与生成稿仍有共同字符,保留率应显著低于原样保留但不必为0
    assert 0.0 <= signals["t2"] < 0.5
