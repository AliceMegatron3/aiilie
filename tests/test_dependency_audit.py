"""Batch 7 / 约束7：依赖锁一致性 + 第三方许可证清单（版本/来源/许可证 如实记录）。"""
from __future__ import annotations

from pathlib import Path

from core.dependency_audit import dump_sbom, license_inventory, lock_consistency


def test_lock_consistency_declared_equals_locked():
    root = Path(__file__).resolve().parent.parent  # 仓库根
    report = lock_consistency(root)
    assert report["consistent"] is True, f"声明但未锁定: {report.get('unlocked')}"
    assert report["declared"], "应解析到至少一项运行期依赖"
    # 抽查核心依赖确实被锁定
    assert "fastapi" in report["declared"]
    assert "uvicorn" in report["declared"]


def test_license_inventory_records_name_version_license_source():
    inv = license_inventory()
    assert isinstance(inv, list)
    # 至少一个已安装的第三方发行版（如 fastapi）
    names = {r["name"] for r in inv}
    assert any("fastapi" == n or n.startswith("fastapi") for n in names)
    sample = next(r for r in inv if r["name"] == "fastapi")
    assert sample["source"] == "pypi"
    assert sample.get("version")
    assert "license" in sample  # 缺失如实记 UNKNOWN，不伪造


def test_unlocked_dependency_detected(tmp_path):
    # 构造一个声明了但未锁定依赖的临时项目 → inconsistent
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0.0.0"\ndependencies=["ghostpkg-fake>1.0"]\n[project.optional-dependencies]\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "real"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    report = lock_consistency(tmp_path)
    assert report["consistent"] is False
    assert "ghostpkg-fake" in report["unlocked"]


def test_dump_sbom_contains_components_and_lock_state():
    """约束7 成品：SBOM 清单记录 名称/版本/许可证/来源，且声明依赖已锁定。"""
    import json

    sbom = dump_sbom()
    json.dumps(sbom, ensure_ascii=False)  # 可序列化
    assert sbom["format"] == "spdx-ish/aiilie"
    assert sbom["declared_deps_locked"] is True
    comps = sbom["components"]
    assert isinstance(comps, list) and comps
    keys = set(comps[0].keys())
    assert {"name", "version", "license", "source"} <= keys
    assert any(c["name"] == "fastapi" for c in comps)