"""
core/blueprint.py — 批次7：系统架构镜像生成器
===============================================
GET /api/v1/system/blueprint 返回三份镜像：
1. architecture_map.json — 后端模块依赖图（静态扫描）
2. api_spec.json         — REST API 路由规格（运行时扫描 FastAPI app）
3. ui_flow.json          — 前端路由/视图流（静态扫描 router + views）

镜像生成策略：内存缓存 + 落盘 %APPDATA%/No0_AI_V4/blueprint/，
供 SoftwareArchitectAnalyzer 与前端蓝图面板读取。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.path_resolver import get_app_data_dir

logger = logging.getLogger(__name__)

_BLUEPRINT_FILES = ("architecture_map.json", "api_spec.json", "ui_flow.json")


class BlueprintGenerator:
    """架构镜像生成器（无状态工具类）。"""

    def blueprint_dir(self) -> Path:
        d = get_app_data_dir() / "blueprint"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── 1. 架构地图：后端模块依赖扫描 ────────────────────────────

    def generate_architecture_map(self) -> dict[str, Any]:
        """扫描项目 Python 模块结构与 import 关系，生成架构地图。"""
        import ast
        import os

        root = Path(__file__).resolve().parent.parent
        modules: dict[str, dict[str, Any]] = {}
        packages = ("api", "core", "guards", "models", "services", "strategies", "extractors", "utils")

        def _scan(directory: Path, package_name: str) -> None:
            for path in sorted(directory.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
                except (SyntaxError, OSError):
                    continue
                rel = path.relative_to(root)
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)
                    elif isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                # 只保留项目内依赖
                internal = sorted(
                    {
                        imp.split(".")[0]
                        for imp in imports
                        if imp.split(".")[0] in packages
                    }
                )
                modules[str(rel).replace(os.sep, "/")] = {
                    "package": package_name,
                    "depends_on": internal,
                }

        for pkg in packages:
            pkg_dir = root / pkg
            if pkg_dir.exists():
                _scan(pkg_dir, pkg)

        return {
            "generated_at": _now(),
            "module_count": len(modules),
            "modules": modules,
        }

    # ── 2. API 规格：运行时 FastAPI 路由扫描 ─────────────────────

    def generate_api_spec(self, app=None) -> dict[str, Any]:
        """扫描 FastAPI app 路由表生成 api_spec。app=None 时返回空规格。"""
        routes: list[dict[str, Any]] = []
        if app is not None:
            for route in app.routes:
                methods = sorted(getattr(route, "methods", set()) or set())
                if not methods:
                    continue
                routes.append(
                    {
                        "path": getattr(route, "path", ""),
                        "methods": methods,
                        "name": getattr(route, "name", ""),
                    }
                )
        routes.sort(key=lambda r: r["path"])
        return {"generated_at": _now(), "route_count": len(routes), "routes": routes}

    # ── 3. UI 流程：前端路由/视图静态扫描 ────────────────────────

    def generate_ui_flow(self) -> dict[str, Any]:
        """扫描前端 router 与 views，生成 UI 流程镜像。"""
        frontend_root = Path(__file__).resolve().parent.parent / "frontend"
        routes: list[dict[str, Any]] = []
        router_file = frontend_root / "src" / "router" / "index.ts"
        if router_file.exists():
            import re

            text = router_file.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(
                r"path:\s*['\"]([^'\"]+)['\"][\s\S]{0,200}?component:\s*\(\)\s*=>\s*import\(['\"]([^'\"]+)['\"]\)",
                text,
            ):
                routes.append({"path": m.group(1), "view": m.group(2)})
        views_dir = frontend_root / "src" / "components" / "views"
        views = []
        if views_dir.exists():
            views = sorted(p.name for p in views_dir.glob("*.vue"))
        return {
            "generated_at": _now(),
            "route_count": len(routes),
            "routes": routes,
            "views": views,
        }

    # ── 汇总与落盘 ──────────────────────────────────────────────

    def generate_all(self, app=None) -> dict[str, dict[str, Any]]:
        blueprints = {
            "architecture_map": self.generate_architecture_map(),
            "api_spec": self.generate_api_spec(app),
            "ui_flow": self.generate_ui_flow(),
        }
        blueprint_dir = self.blueprint_dir()
        for name, payload in blueprints.items():
            try:
                (blueprint_dir / f"{name}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning("[Blueprint] 镜像落盘失败 %s: %s", name, exc)
        logger.info(
            "[Blueprint] 架构镜像生成完成: %d 模块 / %d 路由 / %d 视图",
            blueprints["architecture_map"]["module_count"],
            blueprints["api_spec"]["route_count"],
            blueprints["ui_flow"]["route_count"],
        )
        return blueprints

    def load(self, name: str) -> dict[str, Any]:
        """按名称读取镜像（architecture_map / api_spec / ui_flow）。"""
        if name not in _BLUEPRINT_FILES:
            raise ValueError(f"未知镜像: {name}（可选 {', '.join(_BLUEPRINT_FILES)}）")
        path = self.blueprint_dir() / name
        if not path.exists():
            raise FileNotFoundError(f"镜像尚未生成: {name}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"镜像文件损坏: {name}") from exc


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# 模块级单例
blueprint_generator = BlueprintGenerator()
