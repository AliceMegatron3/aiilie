"""
services/auto_repair.py — 多文件受控自动修复（multi-file controlled auto-repair）
=================================================================================
在既有单文件版本控制能力之上（ProjectManager 快照/回滚、VersionControlService 分支
版本、author_review 作者批准），提供面向"多文件 change set"的受控自动修复闭环：

  - MultiFileChangeSet    ：一次修复涉及的单文件变更 {path, base_digest, proposed_content, approved}
  - compute_file_digest   ：sha256 原始内容指纹
  - detect_cross_file_conflicts：同一文件被多个 change set 修改 / base_digest 过期（stale）
                            → 冲突清单（不自动解决，交给作者）
  - apply_atomic          ：原子多文件提交（先全量备份 + 校验 base_digest，再一次性写入，
                            失败整体回滚，保证无部分提交）
  - snapshot_round        ：当前轮次全量快照（文件 + digest + 时间），供失败反馈闭环使用
  - run_auto_repair       ：有限轮次自动修复闭环（每轮 snapshot → repair_fn → 冲突检测
                            → 原子提交 → 失败反馈传给下一轮，直至收敛或 max_rounds）

兼容性约束：
  - 本模块是纯文件级操作，不依赖 feature.branch_version_enable 开关；开关关闭（默认）
    时不影响既有 ProjectManager / VersionControlService 的行为（新增功能在开关关闭时
    不改变任何既有行为）。
  - path 白名单复用 core/path_resolver.safe_join 防路径穿越。
  - 作者批准：apply_atomic(require_approval=True) 时仅返回待批准清单不落盘，与
    author_review / document_confirmation 的作者批准语义衔接。

受控调用位置（文档化，不做强制接线）：
  - 作者批准通过后 / 优化或自学习结果需要"多文件原子落盘"时：
        apply_atomic(changesets, backup_dir, require_approval=...)
  - 测试驱动的有限轮次自动修复：
        run_auto_repair(repair_fn, files, max_rounds=...)
  repair_fn 契约：repair_fn(round_no, files, snapshot, feedback)
      -> dict[str, str]（{path: proposed_content}）或 list[MultiFileChangeSet]。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.path_resolver import safe_join

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(path, root_dir=None) -> Path:
    """把逻辑路径解析为磁盘绝对路径；root_dir 非空时用 safe_join 防路径穿越。"""
    if root_dir is not None:
        return safe_join(root_dir, str(path))
    p = Path(path)
    return p if p.is_absolute() else p.resolve()


# ── 文件指纹 ───────────────────────────────────────────────────────

def compute_file_digest(path) -> str:
    """返回文件的 sha256 指纹（原始字节）；文件缺失返回空串。"""
    p = Path(path)
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── change set ─────────────────────────────────────────────────────

class MultiFileChangeSet:
    """表示一次自动修复涉及的单个文件变更。

    校验（构造时执行）：
      - path 非空；base_digest 非空（sha256 原始内容）；proposed_content 必须提供；
      - 提供了 root_dir 时，path 白名单限定在项目文档目录内
        （复用 core/path_resolver.safe_join，越界抛 ValueError 防路径穿越）。
    """

    def __init__(
        self,
        path,
        base_digest: str,
        proposed_content: str,
        approved: bool = False,
        root_dir=None,
    ):
        if not path or not str(path).strip():
            raise ValueError("path 不能为空")
        if not base_digest:
            raise ValueError("base_digest 不能为空")
        if proposed_content is None:
            raise ValueError("proposed_content 不能为空")
        self.path = str(path)
        self.base_digest = str(base_digest)
        self.proposed_content = str(proposed_content)
        self.approved = bool(approved)
        self.root_dir = Path(root_dir) if root_dir is not None else None
        self.target: Path | None = None
        if self.root_dir is not None:
            # path 白名单：限定项目文档目录，防路径穿越（safe_join 越界抛 ValueError）
            self.target = safe_join(self.root_dir, self.path)

    def __repr__(self) -> str:
        return f"MultiFileChangeSet(path={self.path!r}, approved={self.approved})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "base_digest": self.base_digest,
            "proposed_content": self.proposed_content,
            "approved": self.approved,
        }


def resolve_target(cs: MultiFileChangeSet, root_dir=None) -> Path:
    """解析 change set 目标文件绝对路径（root_dir 缺省时使用 change set 携带的 root_dir）。"""
    root = root_dir if root_dir is not None else cs.root_dir
    if root is not None:
        return safe_join(root, cs.path)
    p = Path(cs.path)
    return p if p.is_absolute() else p.resolve()


def _effective_root(changesets, root_dir):
    """确定统一的 root_dir：显式入参优先，否则取 change set 携带的一致 root_dir。"""
    if root_dir is not None:
        return Path(root_dir)
    roots = {cs.root_dir for cs in changesets if cs.root_dir is not None}
    if len(roots) == 1:
        return next(iter(roots))
    if not roots:
        # 全部 change set 均为绝对路径时无需 root_dir
        paths = [Path(cs.path) for cs in changesets]
        if paths and all(p.is_absolute() for p in paths):
            return None
        raise ValueError("需要提供 root_dir 才能解析相对路径")
    raise ValueError("多个 change set 的 root_dir 不一致")


# ── 跨文件冲突检测 ─────────────────────────────────────────────────

@dataclass
class Conflict:
    """跨文件冲突记录。kind ∈ {"duplicate_change", "stale_base"}。"""
    path: str
    kind: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "reason": self.reason}


def detect_cross_file_conflicts(changesets, root_dir=None) -> list[Conflict]:
    """检测跨文件冲突，返回冲突清单（不自动解决，交给作者裁决）。

    - duplicate_change：同一文件被多个 change set 同时修改；
    - stale_base：change set 的 base_digest 与磁盘当前内容不符（磁盘已被外部改动）。
    """
    root = _effective_root(changesets, root_dir)
    conflicts: list[Conflict] = []
    seen: set[str] = set()
    for cs in changesets:
        target = resolve_target(cs, root)
        target_key = str(target)
        if target_key in seen:
            conflicts.append(
                Conflict(
                    path=target_key,
                    kind="duplicate_change",
                    reason=f"同一文件 {cs.path} 被多个 change set 同时修改，需要作者裁决",
                )
            )
            continue
        seen.add(target_key)
        current = compute_file_digest(target)
        if current != cs.base_digest:
            conflicts.append(
                Conflict(
                    path=target_key,
                    kind="stale_base",
                    reason=(
                        f"base_digest 过期：期望 {cs.base_digest}，"
                        f"磁盘当前为 {current or '<文件缺失>'}"
                    ),
                )
            )
    return conflicts


# ── 原子多文件提交 ─────────────────────────────────────────────────

@dataclass
class ApplyResult:
    """原子多文件提交结果。"""
    success: bool
    backup_path: str | None = None
    committed: list[str] = field(default_factory=list)
    backups: list[dict[str, Any]] = field(default_factory=list)
    pending: bool = False
    pending_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "backup_path": self.backup_path,
            "committed": self.committed,
            "backups": self.backups,
            "pending": self.pending,
            "pending_files": self.pending_files,
            "errors": self.errors,
            "rolled_back": self.rolled_back,
        }


def _write_manifest(backup_dir: Path, manifests: list[dict[str, Any]]) -> None:
    (backup_dir / _MANIFEST_NAME).write_text(
        json.dumps(manifests, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def apply_atomic(changesets, backup_dir, root_dir=None, require_approval=False) -> ApplyResult:
    """原子多文件提交。

    流程：
      1. 先把所有目标文件备份到 backup_dir（含每文件原 digest 与 existed 标记）；
      2. 校验全部 base_digest 与磁盘一致——任一不匹配 → 整体拒绝（不写入任何文件）；
      3. require_approval=True 且存在未批准的 change set → 仅暂存（返回待批准清单），不落盘；
      4. 一次性写入全部 proposed_content；任一写入失败 → 从备份回滚已写入文件，
         保证"无部分提交"。
    """
    if not changesets:
        return ApplyResult(success=True, backup_path=str(backup_dir))
    root = _effective_root(changesets, root_dir)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    manifests: list[dict[str, Any]] = []
    stale_errors: list[str] = []
    for idx, cs in enumerate(changesets):
        target = resolve_target(cs, root)
        original_digest = compute_file_digest(target)
        if original_digest != cs.base_digest:
            stale_errors.append(
                f"{cs.path}: base_digest 过期（磁盘 {original_digest or '<文件缺失>'} ≠ {cs.base_digest}）"
            )
        backup_name = f"{idx:04d}_{Path(cs.path).name}.bak"
        existed = target.is_file()
        if existed:
            shutil.copy2(target, backup_dir / backup_name)
        else:
            (backup_dir / backup_name).write_bytes(b"")
        manifests.append(
            {
                "path": str(cs.path),
                "target": str(target),
                "backup": backup_name,
                "original_digest": original_digest,
                "base_digest": cs.base_digest,
                "existed": existed,
            }
        )

    if stale_errors:
        # 校验失败 → 整体拒绝，不部分提交
        return ApplyResult(
            success=False,
            backup_path=str(backup_dir),
            backups=manifests,
            errors=["存在 base_digest 过期，整体拒绝提交: " + "; ".join(stale_errors)],
        )

    _write_manifest(backup_dir, manifests)

    if require_approval:
        pending = [str(cs.path) for cs in changesets if not cs.approved]
        if pending:
            # 作者批准：仅 staging 不落盘，返回待批准清单
            return ApplyResult(
                success=False,
                backup_path=str(backup_dir),
                backups=manifests,
                pending=True,
                pending_files=pending,
                errors=["存在未获作者批准的 change set，仅暂存待批准"],
            )

    written: list[str] = []
    try:
        for cs in changesets:
            target = resolve_target(cs, root)
            _write_file(target, cs.proposed_content)
            written.append(str(target))
    except Exception as exc:
        restored = restore_from_backup(backup_dir)
        logger.warning("[AutoRepair] 原子提交失败，已回滚 %d 个文件: %s", restored, exc)
        return ApplyResult(
            success=False,
            backup_path=str(backup_dir),
            backups=manifests,
            errors=[f"写入失败: {exc}（已回滚 {restored} 个文件）"],
            rolled_back=True,
        )

    return ApplyResult(
        success=True,
        backup_path=str(backup_dir),
        committed=[str(cs.path) for cs in changesets],
        backups=manifests,
    )


def restore_from_backup(backup_dir, root_dir=None) -> int:
    """依据 backup_dir/manifest.json 把全部文件恢复为备份状态。返回恢复的文件数。"""
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / _MANIFEST_NAME
    if not manifest_path.exists():
        return 0
    try:
        manifests = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    restored = 0
    for item in manifests:
        target = Path(item["target"]) if item.get("target") else _resolve_path(item["path"], root_dir)
        backup_file = backup_dir / item["backup"]
        try:
            if item.get("existed", True):
                if not target.parent.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, target)
            else:
                target.unlink(missing_ok=True)
            restored += 1
        except OSError as exc:
            logger.warning("[AutoRepair] 恢复文件失败 %s: %s", target, exc)
    return restored


# ── 轮次快照 ───────────────────────────────────────────────────────

def snapshot_round(project_docs, round_no, root_dir=None) -> dict[str, Any]:
    """为当前轮次生成全量快照（文件 + digest + 时间）。

    project_docs 支持两种输入：
      - 文件路径列表（str/Path），root_dir 提供时视为相对路径；
      - ProjectDoc 模型列表（此时 root_dir 通常为项目根目录，取 {project_id}/docs/{doc_id}.md）。
    """
    entries = []
    for doc in project_docs:
        if hasattr(doc, "doc_id") and hasattr(doc, "project_id"):
            rel = f"{doc.project_id}/docs/{doc.doc_id}.md"
        else:
            rel = str(doc)
        target = _resolve_path(rel, root_dir)
        entries.append(
            {
                "path": str(target),
                "rel_path": rel,
                "digest": compute_file_digest(target),
                "size": target.stat().st_size if target.is_file() else 0,
            }
        )
    return {"round": int(round_no), "timestamp": _now_iso(), "files": entries}


def _snapshots_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    da = {f["path"]: f["digest"] for f in a["files"]}
    db = {f["path"]: f["digest"] for f in b["files"]}
    return da == db


def _normalize_proposed(proposed, snapshot, root_dir=None) -> list[MultiFileChangeSet]:
    """把 repair_fn 返回值统一为 MultiFileChangeSet 列表。

    - dict {path: content}：base_digest 取当前快照指纹；
    - list[MultiFileChangeSet]：直接使用。
    """
    if isinstance(proposed, list):
        for item in proposed:
            if not isinstance(item, MultiFileChangeSet):
                raise TypeError("repair_fn 返回列表时元素必须为 MultiFileChangeSet")
        return proposed
    if isinstance(proposed, dict):
        change_sets: list[MultiFileChangeSet] = []
        for rel, content in proposed.items():
            target = _resolve_path(rel, root_dir)
            base = compute_file_digest(target)
            for f in snapshot["files"]:
                if f["rel_path"] == str(rel) or Path(f["path"]) == target:
                    base = f["digest"]
                    break
            if root_dir is None:
                change_sets.append(MultiFileChangeSet(str(target), base, content))
            else:
                change_sets.append(MultiFileChangeSet(rel, base, content, root_dir=root_dir))
        return change_sets
    raise TypeError("repair_fn 必须返回 dict 或 list[MultiFileChangeSet]")


# ── 有限轮次自动修复闭环 ──────────────────────────────────────────

def run_auto_repair(
    repair_fn: Callable,
    files,
    max_rounds: int = 3,
    root_dir=None,
    backup_root=None,
    require_approval: bool = False,
) -> dict[str, Any]:
    """有限轮次自动修复闭环。

    repair_fn 契约：
        repair_fn(round_no, files, snapshot, feedback)
          -> dict[str, str] | list[MultiFileChangeSet]
      其中 feedback 为上一轮 apply 失败的反馈（首轮为 None）。

    每轮：snapshot_round → repair_fn → detect_cross_file_conflicts（有冲突即停，交作者）
    → apply_atomic（成功记录提交并检查收敛；失败把反馈传给下一轮）→ 重复至
    max_rounds 或所有文件 digest 稳定（无变化即收敛）。
    """
    root = Path(root_dir) if root_dir is not None else None
    backup_root = Path(backup_root) if backup_root else Path(
        tempfile.mkdtemp(prefix="auto_repair_")
    )
    backup_root.mkdir(parents=True, exist_ok=True)

    round_details: list[dict[str, Any]] = []
    committed: list[str] = []
    feedback: dict[str, Any] | None = None
    converged = False
    conflicts: list[dict[str, Any]] = []
    last_apply: ApplyResult | None = None
    backup_path = str(backup_root)

    for round_no in range(1, int(max_rounds) + 1):
        snapshot = snapshot_round(files, round_no, root_dir=root)
        try:
            proposed = repair_fn(round_no, [str(f) for f in files], snapshot, feedback)
        except Exception as exc:
            feedback = {"round": round_no, "errors": [f"repair_fn 异常: {exc}"]}
            round_details.append(
                {
                    "round": round_no,
                    "snapshot": snapshot,
                    "proposed": None,
                    "conflicts": [],
                    "apply": None,
                    "feedback": feedback,
                }
            )
            continue

        changesets = _normalize_proposed(proposed, snapshot, root_dir=root)
        if not changesets:
            # 无变更提议 → 已收敛
            round_details.append(
                {
                    "round": round_no,
                    "snapshot": snapshot,
                    "proposed": [],
                    "conflicts": [],
                    "apply": None,
                    "feedback": None,
                }
            )
            converged = True
            break

        round_conflicts = detect_cross_file_conflicts(changesets, root_dir=root)
        conflicts = [c.to_dict() for c in round_conflicts]
        if conflicts:
            round_details.append(
                {
                    "round": round_no,
                    "snapshot": snapshot,
                    "proposed": [cs.to_dict() for cs in changesets],
                    "conflicts": conflicts,
                    "apply": None,
                    "feedback": None,
                }
            )
            break

        result = apply_atomic(
            changesets,
            backup_root / f"round_{round_no:02d}",
            root_dir=root,
            require_approval=require_approval,
        )
        last_apply = result
        if result.backup_path:
            backup_path = result.backup_path

        if result.pending:
            round_details.append(
                {
                    "round": round_no,
                    "snapshot": snapshot,
                    "proposed": [cs.to_dict() for cs in changesets],
                    "conflicts": [],
                    "apply": result.to_dict(),
                    "feedback": None,
                }
            )
            break

        if result.success:
            committed.extend(result.committed)
            post = snapshot_round(files, round_no, root_dir=root)
            converged = _snapshots_equal(snapshot, post)
            round_details.append(
                {
                    "round": round_no,
                    "snapshot": snapshot,
                    "proposed": [cs.to_dict() for cs in changesets],
                    "conflicts": [],
                    "apply": result.to_dict(),
                    "feedback": None,
                }
            )
            if converged:
                break
        else:
            feedback = {
                "round": round_no,
                "errors": result.errors,
                "previous_proposed": [cs.to_dict() for cs in changesets],
                "previous_snapshot": snapshot,
            }
            round_details.append(
                {
                    "round": round_no,
                    "snapshot": snapshot,
                    "proposed": [cs.to_dict() for cs in changesets],
                    "conflicts": [],
                    "apply": result.to_dict(),
                    "feedback": feedback,
                }
            )

    if conflicts:
        stopped_reason = "conflict"
    elif last_apply and last_apply.pending:
        stopped_reason = "pending"
    elif converged:
        stopped_reason = "converged"
    else:
        stopped_reason = "max_rounds"

    return {
        "success": converged and not conflicts,
        "converged": converged,
        "rounds": len(round_details),
        "max_rounds": int(max_rounds),
        "stopped_reason": stopped_reason,
        "not_converged": stopped_reason == "max_rounds",
        "round_details": round_details,
        "committed": list(dict.fromkeys(committed)),
        "backup_path": backup_path,
        "conflicts": conflicts,
        "pending": bool(last_apply and last_apply.pending),
    }
