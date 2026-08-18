"""core/dependency_audit.py — 依赖锁一致性与许可证清单（Batch 7 / 工作约束7）。

约束 7：所有第三方依赖记录「版本、来源、许可证」。本模块提供两个确定性检查：
- `lock_consistency()`：解析 `pyproject.toml` 声明的运行期依赖 与 `uv.lock`
  锁定包名，断言「声明即锁定」——防止未锁定/松依赖漂移（Docker/CI 均以 uv.lock 安装）。
- `license_inventory()`：用 `importlib.metadata` 读取已安装发行版的
  {name, version, license, source=PyPI} 清单（许可证缺失也如实记录，不伪造 MIT）。
"""
from __future__ import annotations

import sys
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def _norm(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def _declared_deps(pyproject_path: Path) -> list[str]:
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    deps: list[Any] = data.get("project", {}).get("dependencies", [])
    names: list[str] = []
    for dep in deps:
        # 处理规范如 "fastapi>=0.115.0"、"pydantic[standard]>=2"（去 aside 与 version spec）
        token = str(dep).split(";")[0].strip()
        base = token
        for sep in (">=", "==", "<=", "~=", "!=", ">", "<"):
            if sep in base:
                base = base.split(sep)[0]
                break
        base = base.split("[")[0].strip()  # 去掉 extras，如 pydantic[standard]
        if base:
            names.append(_norm(base))
    return sorted(set(names))


def _locked_names(lock_path: Path) -> set[str]:
    with lock_path.open("rb") as fh:
        data = tomllib.load(fh)
    return {_norm(str(p.get("name", ""))) for p in data.get("package", []) if p.get("name")}


def lock_consistency(
    root: Path | None = None,
    *,
    pyproject: str = "pyproject.toml",
    uvlock: str = "uv.lock",
) -> dict[str, Any]:
    """返回：声明的运行期依赖是否全部在 uv.lock 中被锁定。"""
    root = Path(root) if root is not None else _DEFAULT_ROOT
    lock_path = root / uvlock
    if not lock_path.exists():
        return {"consistent": False, "unlocked": [], "lockfile_missing": str(lock_path)}
    declared = _declared_deps(root / pyproject)
    locked = _locked_names(lock_path)
    unlocked = [d for d in declared if d not in locked]
    return {"consistent": not unlocked, "declared": declared, "unlocked": unlocked}


def license_inventory() -> list[dict[str, str]]:
    """读取已安装第三方发行版的 名称/版本/许可证/来源 清单（如实记录）。"""
    inventory: list[dict[str, str]] = []
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name:
            continue
        version = (dist.metadata.get("Version") or "").strip()
        # 许可证可能在 License 或 classifier 中；缺失如实标注 UNKNOWN，不伪造
        license_txt = (dist.metadata.get("License") or "").strip()
        if not license_txt:
            classifiers = dist.metadata.get_all("Classifier") or []
            license_txt = next((c for c in classifiers if c.startswith("License ::")), "")
            license_txt = license_txt.replace("License :: OSI Approved :: ", "")
        inventory.append(
            {
                "name": name,
                "version": version,
                "license": license_txt or "UNKNOWN",
                "source": "pypi",
            }
        )
    return sorted(inventory, key=lambda r: r["name"].lower())


def dump_sbom() -> dict[str, Any]:
    """生成 SBOM 风格第三方依赖清单（约束7 成品）：记录 名称/版本/许可证/来源。
    结构稳、可序列化为 JSON，供发布审计与合规使用。"""
    lock = lock_consistency()
    return {
        "format": "spdx-ish/aiilie",
        "declared_deps_locked": lock["consistent"],
        "declared_dependencies": lock.get("declared", []),
        "components": license_inventory(),
    }


__all__ = ["dump_sbom", "license_inventory", "lock_consistency"]