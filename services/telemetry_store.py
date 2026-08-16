"""services/telemetry_store.py — 统一遥测落账层(批次C)
========================================================
此前 runs / author_signals / retrieval_miss / scoring_history 四条
JSONL 各写各的:路径解析、脏行容忍、目录创建重复四遍,且无轮转。
本模块收敛公共写入与读取,保持**旧文件格式与路径完全兼容**——
既有数据不迁移即可继续读。

只做三件事:追加、读取(脏行容忍)、按大小轮转。聚合逻辑仍留在
各领域模块,避免遥测层长成第二个上帝模块。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 单文件轮转阈值(字节);超过则改名为 .1 备份,新文件继续写
DEFAULT_ROTATE_BYTES = 8 * 1024 * 1024


def resolve_path(name: str, override=None) -> Path:
    """遥测文件路径:显式覆盖优先(测试),否则落 app_data。"""
    if override is not None:
        return Path(override)
    from core.path_resolver import get_app_data_dir

    return Path(get_app_data_dir()) / name


def append_record(path: Path, record: dict, rotate_bytes: int = DEFAULT_ROTATE_BYTES) -> bool:
    """追加一条记录。失败仅告警返回 False,绝不阻断业务链路。"""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if rotate_bytes > 0 and path.exists() and path.stat().st_size >= rotate_bytes:
            backup = path.with_suffix(path.suffix + ".1")
            try:
                backup.unlink(missing_ok=True)
                path.rename(backup)
            except OSError as exc:
                logger.warning("[Telemetry] 轮转失败(继续追加): %s", exc)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning("[Telemetry] 落账失败(不阻断): %s", exc)
        return False


def read_records(path: Path, include_rotated: bool = False) -> list[dict]:
    """读取记录,逐行容忍脏数据;可选并入上一轮转文件。"""
    path = Path(path)
    sources = []
    if include_rotated:
        backup = path.with_suffix(path.suffix + ".1")
        if backup.exists():
            sources.append(backup)
    if path.exists():
        sources.append(path)
    out: list[dict] = []
    for source in sources:
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("[Telemetry] 读取失败(跳过 %s): %s", source, exc)
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
    return out


def text_digest(text: str) -> str:
    """文本指纹:仅存哈希与长度,不落全文(避免敏感正文外泄)。"""
    import hashlib

    normalized = (text or "").strip()
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
