"""
tests/test_path_resolver.py — 统一运行时索引路径解析测试
=========================================================
验证 get_library_index_dir() 与权威 data 目录、tasks.db 的一致性，
确保运行时收敛到单一权威索引路径（AppData data 目录）。
依赖 conftest 的 autouse auto_isolated_env 夹具：APPDATA/USERPROFILE
均被重定向到临时目录，不会触碰真实用户数据。
"""
from __future__ import annotations

import core.path_resolver as pr


def test_library_index_dir_same_as_db_parent():
    """权威索引目录与 tasks.db 位于同一权威 data 目录。"""
    assert pr.get_library_index_dir() == pr.get_db_path("tasks.db").parent


def test_library_index_dir_is_app_data_dir():
    """权威索引目录即 AppData data 目录本身（真实库与 tasks.db 同处）。"""
    assert pr.get_library_index_dir() == pr.get_app_data_dir()


def test_library_index_dir_single_authoritative_in_dev():
    """开发模式下为单一权威路径，且与 workspace 暂存索引目录可区分。"""
    lib_dir = pr.get_library_index_dir()
    ai_dir = pr.get_ai_index_dir()
    assert lib_dir != ai_dir
    # 确定性：多次调用返回同一路径
    assert pr.get_library_index_dir() == lib_dir
    # 权威索引库 db 直接位于该目录下
    assert (lib_dir / "library_index.db").parent == lib_dir


def test_library_index_dir_stable_under_frozen(monkeypatch):
    """即使处于 PyInstaller 打包（frozen）模式，权威索引目录也保持不变。"""
    monkeypatch.setattr(pr, "is_frozen", lambda: True)
    assert pr.get_library_index_dir() == pr.get_app_data_dir()


def test_safe_join_normal_segment_resolves_inside(tmp_path):
    """safe_join 正常标识符解析在 base_dir 内。"""
    base = tmp_path / "vault"
    path = pr.safe_join(base, "project", "doc_abc.json")
    assert path.resolve().is_relative_to(base.resolve())
    assert path.name == "doc_abc.json"


def test_safe_join_rejects_traversal(tmp_path):
    """safe_join 必须拒绝 ../ 越界与绝对路径（Batch 7 路径安全回归）。"""
    import pytest

    base = tmp_path / "vault"
    for evil in ("../../etc/passwd", "..\\..\\windows\\evil", "/etc/shadow", "C:\\tmp\\x"):
        with pytest.raises(ValueError):
            pr.safe_join(base, "project", evil)


def test_safe_join_rejects_multiple_traversal_segments(tmp_path):
    """多个用户可控段整体越界（如 a/../../etc）也必须被拒绝。"""
    import pytest

    base = tmp_path / "vault"
    with pytest.raises(ValueError):
        pr.safe_join(base, "a", "../../etc/passwd")
