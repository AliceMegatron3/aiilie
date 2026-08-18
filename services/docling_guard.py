"""Docling 可选生产接入防护层：限制、离线、并发、超时与结构化 provenance。

docling 是重型可选依赖（含模型工件），默认不启用。本模块用纯 Python 实现
生产级约束，可在未安装 docling 的环境下独立测试（桩解析器）：

- ``DoclingLimits``       页数/CPU 线程/超时/并发上限（来自 config.yaml 的 docling.* 键）
- ``DoclingGuard``        并发信号量 + 解析超时 + 页数截断 + 离线模型检查 + provenance
- ``check_docling_readiness()``  生产 readiness 报告（enabled/installed/offline_ready/limits/errors）
- ``ensure_model_artifacts()``   离线模式模型工件就绪检查（缺失时提示预下载命令）

provenance 字段约定（供上层登记 ledger 时使用）：
    {parser_id, parser_version, model_cache, page_count, truncated, elapsed_ms, source_hash}
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir

logger = logging.getLogger(__name__)

DEFAULT_PAGE_LIMIT = 200
DEFAULT_CPU_THREADS = 2
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_CONCURRENCY = 2

# 离线模式缺失模型工件时的预下载命令提示
_PREDOWNLOAD_HINT = (
    "请在开启 docling.offline=true 之前预下载模型工件到本地缓存目录，例如：\n"
    "  python -m docling_tools.models download --output {cache}\n"
    "  或将已下载的 docling 模型目录放入 {cache}（参考 docling 官方模型下载说明）。"
)


class DoclingGuardError(Exception):
    """Docling 防护层基类错误。"""


class DoclingTimeoutError(DoclingGuardError):
    """解析超时。"""


class DoclingOfflineError(DoclingGuardError):
    """离线模式模型工件缺失。"""


@dataclass(frozen=True)
class DoclingLimits:
    """docling 解析生产限制（页数/CPU 线程/超时/并发）。"""

    page_limit: int = DEFAULT_PAGE_LIMIT
    cpu_threads: int = DEFAULT_CPU_THREADS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    def to_dict(self) -> dict[str, int]:
        return {
            "page_limit": self.page_limit,
            "cpu_threads": self.cpu_threads,
            "timeout_seconds": self.timeout_seconds,
            "max_concurrency": self.max_concurrency,
        }


def load_limits() -> DoclingLimits:
    """从 config.yaml 的 docling.* 键读取限制（默认值兜底，下限 1）。"""
    return DoclingLimits(
        page_limit=max(1, config_manager.get_int("docling.page_limit", DEFAULT_PAGE_LIMIT)),
        cpu_threads=max(1, config_manager.get_int("docling.cpu_threads", DEFAULT_CPU_THREADS)),
        timeout_seconds=max(1, config_manager.get_int("docling.timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        max_concurrency=max(1, config_manager.get_int("docling.max_concurrency", DEFAULT_MAX_CONCURRENCY)),
    )


def model_cache_dir() -> Path:
    """模型缓存目录：config.docling.model_cache_dir 或默认 %APPDATA%/No0_AI_V4/data/docling_models。"""
    raw = config_manager.get("docling.model_cache_dir", "")
    if raw:
        return Path(raw)
    return get_app_data_dir() / "docling_models"


def offline_enabled() -> bool:
    """docling 离线模式开关（docling.offline）。"""
    return config_manager.get_bool("docling.offline", False)


def docling_installed() -> bool:
    """docling 是否已安装（只探测，不触发模型下载）。"""
    try:
        import docling  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def compute_source_hash(data: bytes) -> str:
    """来源内容 SHA-256（供 provenance/ledger 对账）。"""
    return hashlib.sha256(data).hexdigest()


def ensure_model_artifacts(cache_dir: Path | None = None) -> tuple[bool, list[str]]:
    """离线就绪检查：返回 (是否就绪, 缺失说明列表)。

    就绪判定：模型缓存目录存在且非空。非离线模式由调用方自行决定是否忽略。
    """
    cache = Path(cache_dir) if cache_dir is not None else model_cache_dir()
    missing: list[str] = []
    if not cache.exists():
        missing.append(f"docling 模型缓存目录不存在: {cache}")
    elif not any(cache.iterdir()):
        missing.append(f"docling 模型缓存目录为空: {cache}")
    return (not missing), missing


def build_provenance(
    *,
    parser_id: str,
    parser_version: str,
    model_cache: str,
    page_count: int,
    truncated: bool,
    elapsed_ms: int,
    source_hash: str,
) -> dict[str, Any]:
    """构造结构化 provenance 记录。"""
    return {
        "parser_id": parser_id,
        "parser_version": parser_version,
        "model_cache": model_cache,
        "page_count": page_count,
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "source_hash": source_hash,
    }


def _page_count(doc: Any) -> int:
    """统计解析结果页数：优先 pages 列表，否则取 passages 的最大页码。"""
    pages = getattr(doc, "pages", None)
    if pages:
        return len(pages)
    return max((p.page_start or 1 for p in getattr(doc, "passages", [])), default=0)


def truncate_document(doc: Any, page_limit: int) -> Any:
    """按页数上限截断解析结果（pages/tables/passages 同步裁剪）。"""
    if getattr(doc, "pages", None):
        doc.pages = doc.pages[:page_limit]
    if getattr(doc, "tables", None):
        doc.tables = [t for t in doc.tables if int(t.get("page", 1)) <= page_limit]
    if getattr(doc, "passages", None):
        doc.passages = [
            p for p in doc.passages if (p.page_start or 1) <= page_limit
        ]
    return doc


class DoclingGuard:
    """对 docling 解析施加生产约束：离线检查、并发信号量、超时、截断与 provenance。"""

    def __init__(
        self,
        limits: DoclingLimits | None = None,
        cache_dir: Path | str | None = None,
        offline: bool | None = None,
    ) -> None:
        self.limits = limits or load_limits()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else model_cache_dir()
        self.offline = offline if offline is not None else offline_enabled()
        # 信号量懒创建，避免在无事件循环上下文实例化（兼容 3.10 行为差异）
        self._semaphore: asyncio.Semaphore | None = None

    # ── 离线模式 ─────────────────────────────────────────────
    def check_offline(self) -> None:
        """离线模式模型工件缺失时直接失败（带预下载命令提示）。"""
        if not self.offline:
            return
        ready, missing = ensure_model_artifacts(self.cache_dir)
        if not ready:
            raise DoclingOfflineError(
                "\n".join(missing) + "\n" + _PREDOWNLOAD_HINT.format(cache=self.cache_dir)
            )

    # ── 限制与 provenance ────────────────────────────────────
    def apply_limits(
        self,
        doc: Any,
        parser: Any,
        source_hash: str = "",
        elapsed_ms: int = 0,
    ) -> Any:
        """页数截断 + 写入结构化 provenance；返回同一解析结果对象。"""
        truncated = False
        if self.limits.page_limit and _page_count(doc) > self.limits.page_limit:
            doc = truncate_document(doc, self.limits.page_limit)
            truncated = True
        doc.truncated = truncated
        doc.provenance = build_provenance(
            parser_id=parser.parser_id,
            parser_version=parser.parser_version,
            model_cache=str(self.cache_dir),
            page_count=_page_count(doc),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
            source_hash=source_hash,
        )
        return doc

    # ── 异步生产入口：并发信号量 + 超时 + 截断 + provenance ──
    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.limits.max_concurrency)
        return self._semaphore

    async def run_parse(self, parser: Any, file: BinaryIO, filename: str) -> Any:
        """异步执行解析：离线检查 → 并发信号量 → 超时 → 截断 → provenance。"""
        self.check_offline()
        data = file.read()
        source_hash = compute_source_hash(data)
        start = time.monotonic()
        async with self._sem():
            try:
                doc = await asyncio.wait_for(
                    asyncio.to_thread(parser.parse_document, io.BytesIO(data), filename),
                    timeout=self.limits.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                logger.warning("Docling 解析超时（>%ss）: %s", self.limits.timeout_seconds, filename)
                raise DoclingTimeoutError(
                    f"Docling 解析超时（超过 {self.limits.timeout_seconds}s）: {filename}"
                ) from exc
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return self.apply_limits(doc, parser, source_hash=source_hash, elapsed_ms=elapsed_ms)


def check_docling_readiness() -> dict[str, Any]:
    """生产 readiness：enabled / installed / offline_ready / limits / errors。"""
    enabled = config_manager.get_bool("docling.enabled", False)
    installed = docling_installed()
    limits = load_limits()
    errors: list[str] = []
    if enabled and not installed:
        errors.append(
            "docling.enabled=true 但 docling 未安装（pip install docling 或改用 Dockerfile.enhanced 增强镜像）"
        )
    offline_ready = True
    if enabled and installed and offline_enabled():
        ready, missing = ensure_model_artifacts()
        offline_ready = ready
        if not ready:
            errors.extend(missing)
    return {
        "enabled": enabled,
        "installed": installed,
        "offline_ready": offline_ready,
        "limits": limits.to_dict(),
        "errors": errors,
    }


_default_guard: DoclingGuard | None = None


def get_default_guard() -> DoclingGuard:
    """进程级共享防护壳（信号量跨请求共享，实现全局并发限制）。"""
    global _default_guard
    if _default_guard is None:
        _default_guard = DoclingGuard()
    return _default_guard


__all__ = [
    "DoclingGuard",
    "DoclingGuardError",
    "DoclingLimits",
    "DoclingOfflineError",
    "DoclingTimeoutError",
    "build_provenance",
    "check_docling_readiness",
    "compute_source_hash",
    "docling_installed",
    "ensure_model_artifacts",
    "get_default_guard",
    "load_limits",
    "model_cache_dir",
    "offline_enabled",
    "truncate_document",
]
