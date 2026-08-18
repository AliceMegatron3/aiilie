"""
tests/test_auto_repair.py — 多文件受控自动修复（services/auto_repair.py）单元测试
==============================================================================
覆盖：change set 校验（路径穿越 / 缺 base_digest）、跨文件冲突检测（同文件双改 /
stale base）、原子多文件提交（全成功 / 写失败整体回滚 / stale 整体拒绝 / 作者批准
暂存 / 备份恢复）、有限轮次自动修复闭环（2 轮收敛 / max_rounds 不收敛 / 冲突即停 /
失败反馈传递）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import services.auto_repair as ar
from services.auto_repair import (
    MultiFileChangeSet,
    apply_atomic,
    compute_file_digest,
    detect_cross_file_conflicts,
    restore_from_backup,
    run_auto_repair,
    snapshot_round,
)


def _write(root: Path, name: str, content: str) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── change set 校验 ────────────────────────────────────────────────

def test_change_set_requires_base_digest(tmp_path):
    with pytest.raises(ValueError):
        MultiFileChangeSet("a.md", "", "新内容")
    with pytest.raises(ValueError):
        MultiFileChangeSet("", "deadbeef", "新内容")


def test_change_set_rejects_path_traversal(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    # 路径穿越（../ 越界）与白名单外路径 → 拒绝
    with pytest.raises(ValueError):
        MultiFileChangeSet("../../evil.txt", "deadbeef", "x", root_dir=root)
    with pytest.raises(ValueError):
        MultiFileChangeSet("../a/../b.md", "deadbeef", "x", root_dir=root)


def test_compute_file_digest(tmp_path):
    p = _write(tmp_path, "a.md", "hello")
    assert compute_file_digest(p) == hashlib.sha256(b"hello").hexdigest()
    assert compute_file_digest(tmp_path / "missing.md") == ""


# ── 跨文件冲突检测 ─────────────────────────────────────────────────

def test_detect_duplicate_change_conflict(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    _write(root, "a.md", "AAA")
    d = compute_file_digest(root / "a.md")
    cs1 = MultiFileChangeSet("a.md", d, "A1", root_dir=root)
    cs2 = MultiFileChangeSet("a.md", d, "A2", root_dir=root)
    conflicts = detect_cross_file_conflicts([cs1, cs2], root_dir=root)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "duplicate_change"
    assert "a.md" in conflicts[0].path


def test_detect_stale_base_conflict(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    f = _write(root, "a.md", "AAA")
    cs = MultiFileChangeSet("a.md", compute_file_digest(f), "BBB", root_dir=root)
    assert detect_cross_file_conflicts([cs], root_dir=root) == []

    # 磁盘在 change set 生成后被外部改动 → stale 冲突
    f.write_text("AAA_changed", encoding="utf-8")
    conflicts = detect_cross_file_conflicts([cs], root_dir=root)
    assert len(conflicts) == 1
    assert conflicts[0].kind == "stale_base"


# ── 原子多文件提交 ─────────────────────────────────────────────────

def test_apply_atomic_success(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    f1 = _write(root, "a.md", "AAA")
    f2 = _write(root, "b.md", "BBB")
    d1, d2 = compute_file_digest(f1), compute_file_digest(f2)
    backup = tmp_path / "backup"
    cs1 = MultiFileChangeSet("a.md", d1, "AAA2", root_dir=root)
    cs2 = MultiFileChangeSet("b.md", d2, "BBB2", root_dir=root)

    result = apply_atomic([cs1, cs2], backup, root_dir=root)
    assert result.success is True
    assert result.committed == ["a.md", "b.md"]
    assert f1.read_text(encoding="utf-8") == "AAA2"
    assert f2.read_text(encoding="utf-8") == "BBB2"
    # 备份含每文件原 digest
    assert (backup / "manifest.json").exists()
    by_name = {Path(b["path"]).name: b for b in result.backups}
    assert by_name["a.md"]["original_digest"] == d1
    assert by_name["b.md"]["original_digest"] == d2


def test_apply_atomic_rejects_stale_base_wholesale(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    f1 = _write(root, "a.md", "AAA")
    f2 = _write(root, "b.md", "BBB")
    cs1 = MultiFileChangeSet("a.md", compute_file_digest(f1), "AAA2", root_dir=root)
    cs2 = MultiFileChangeSet("b.md", compute_file_digest(f2), "BBB2", root_dir=root)
    # 提交前磁盘被外部改动 → 整体拒绝，无部分提交
    f2.write_text("BBB_changed", encoding="utf-8")

    result = apply_atomic([cs1, cs2], tmp_path / "backup", root_dir=root)
    assert result.success is False
    assert result.committed == []
    assert result.errors
    assert f1.read_text(encoding="utf-8") == "AAA"
    assert f2.read_text(encoding="utf-8") == "BBB_changed"


def test_apply_atomic_rollback_on_write_failure(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    f1 = _write(root, "a.md", "AAA")
    f2 = _write(root, "b.md", "BBB")
    cs1 = MultiFileChangeSet("a.md", compute_file_digest(f1), "AAA2", root_dir=root)
    cs2 = MultiFileChangeSet("b.md", compute_file_digest(f2), "BBB2", root_dir=root)

    real_write = ar._write_file

    def failing_write(path, content):
        if Path(path).name == "b.md":
            raise OSError("injected write failure")
        real_write(path, content)

    monkeypatch.setattr(ar, "_write_file", failing_write)
    backup = tmp_path / "backup"
    result = apply_atomic([cs1, cs2], backup, root_dir=root)
    assert result.success is False
    assert result.rolled_back is True
    assert result.committed == []
    # 无部分提交：a.md 已写入也被回滚恢复原内容
    assert f1.read_text(encoding="utf-8") == "AAA"
    assert f2.read_text(encoding="utf-8") == "BBB"


def test_restore_from_backup(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    f1 = _write(root, "a.md", "AAA")
    backup = tmp_path / "backup"
    cs = MultiFileChangeSet("a.md", compute_file_digest(f1), "AAA2", root_dir=root)
    result = apply_atomic([cs], backup, root_dir=root)
    assert result.success is True
    assert f1.read_text(encoding="utf-8") == "AAA2"
    # 备份可用于恢复
    assert restore_from_backup(backup) == 1
    assert f1.read_text(encoding="utf-8") == "AAA"


def test_apply_atomic_require_approval_stages(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    f1 = _write(root, "a.md", "AAA")
    # 未批准 → 仅暂存不落盘
    cs = MultiFileChangeSet("a.md", compute_file_digest(f1), "AAA2", approved=False, root_dir=root)
    result = apply_atomic([cs], tmp_path / "backup", root_dir=root, require_approval=True)
    assert result.success is False
    assert result.pending is True
    assert result.pending_files == ["a.md"]
    assert f1.read_text(encoding="utf-8") == "AAA"

    # 已批准 → 正常落盘
    cs2 = MultiFileChangeSet("a.md", compute_file_digest(f1), "AAA2", approved=True, root_dir=root)
    result2 = apply_atomic([cs2], tmp_path / "backup2", root_dir=root, require_approval=True)
    assert result2.success is True
    assert f1.read_text(encoding="utf-8") == "AAA2"


# ── 轮次快照 ───────────────────────────────────────────────────────

def test_snapshot_round(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    _write(root, "a.md", "AAA")
    _write(root, "b.md", "BBB")
    snap = snapshot_round(["a.md", "b.md"], 2, root_dir=root)
    assert snap["round"] == 2
    assert snap["timestamp"]
    by_rel = {f["rel_path"]: f for f in snap["files"]}
    assert by_rel["a.md"]["digest"] == compute_file_digest(root / "a.md")
    assert by_rel["b.md"]["size"] == 3


# ── 有限轮次自动修复闭环 ──────────────────────────────────────────

def test_run_auto_repair_converges_two_rounds(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    _write(root, "a.md", "原始内容")
    _write(root, "b.md", "原始内容2")
    calls = []

    def repair_fn(round_no, files, snapshot, feedback):
        calls.append(round_no)
        return {"a.md": "修复后内容", "b.md": "修复后内容2"}

    result = run_auto_repair(repair_fn, ["a.md", "b.md"], max_rounds=3, root_dir=root)
    assert calls == [1, 2]  # 首轮有差异、二轮稳定
    assert result["converged"] is True
    assert result["success"] is True
    assert result["rounds"] == 2
    assert result["stopped_reason"] == "converged"
    assert sorted(result["committed"]) == ["a.md", "b.md"]
    assert (root / "a.md").read_text(encoding="utf-8") == "修复后内容"
    assert (root / "b.md").read_text(encoding="utf-8") == "修复后内容2"
    assert result["backup_path"]


def test_run_auto_repair_stops_at_max_rounds(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    _write(root, "a.md", "x")

    def repair_fn(round_no, files, snapshot, feedback):
        # 每轮都产生新的变化，永不收敛
        return {"a.md": f"version-{round_no}"}

    result = run_auto_repair(repair_fn, ["a.md"], max_rounds=3, root_dir=root)
    assert result["rounds"] == 3
    assert result["max_rounds"] == 3
    assert result["not_converged"] is True
    assert result["converged"] is False
    assert result["success"] is False
    assert result["stopped_reason"] == "max_rounds"
    assert (root / "a.md").read_text(encoding="utf-8") == "version-3"


def test_run_auto_repair_stops_on_conflict(tmp_path):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    _write(root, "a.md", "AAA")
    d = compute_file_digest(root / "a.md")

    def repair_fn(round_no, files, snapshot, feedback):
        return [
            MultiFileChangeSet("a.md", d, "A1", root_dir=root),
            MultiFileChangeSet("a.md", d, "A2", root_dir=root),
        ]

    result = run_auto_repair(repair_fn, ["a.md"], max_rounds=3, root_dir=root)
    assert result["rounds"] == 1
    assert result["conflicts"] and result["conflicts"][0]["kind"] == "duplicate_change"
    assert result["success"] is False
    assert result["stopped_reason"] == "conflict"
    assert (root / "a.md").read_text(encoding="utf-8") == "AAA"


def test_run_auto_repair_passes_feedback_on_apply_failure(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir(parents=True)
    _write(root, "a.md", "原始")
    received_feedback = []
    real_write = ar._write_file
    write_calls = {"n": 0}

    def failing_write(path, content):
        write_calls["n"] += 1
        if write_calls["n"] == 1:
            raise OSError("injected first-write failure")
        real_write(path, content)

    monkeypatch.setattr(ar, "_write_file", failing_write)

    def repair_fn(round_no, files, snapshot, feedback):
        received_feedback.append((round_no, feedback))
        return {"a.md": "修复内容"}

    result = run_auto_repair(repair_fn, ["a.md"], max_rounds=3, root_dir=root)
    # 第 1 轮写入失败 → 失败反馈传给第 2 轮 repair_fn
    assert received_feedback[0][1] is None
    round2_feedback = received_feedback[1][1]
    assert round2_feedback is not None
    assert any("injected" in e for e in round2_feedback["errors"])
    assert result["converged"] is True
    assert (root / "a.md").read_text(encoding="utf-8") == "修复内容"
