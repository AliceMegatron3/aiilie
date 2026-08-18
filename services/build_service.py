"""独立构建服务（build_service）。

在隔离的源码/输出卷中执行构建命令，施加资源限制，锁定依赖基线，
产出带 provenance 的产物 manifest，并在导入主应用前提供产物校验。

设计要点
--------
- 源码卷隔离：``src_dir`` 被复制到隔离工作目录 ``work/src``；构建命令 cwd 为
  ``work``（内含 ``src/`` 与 ``out/`` 两个子卷），原始 ``src_dir`` 只读不被污染。
- 输出卷隔离：产物统一写入独立 ``work/out``，与源码卷完全分离。
- 资源限制：复用 :mod:`services.windows_job` 的 Job Object（CPU/内存/进程数），
  复用 :class:`services.resource_profile.DiskQuotaGuard` 做磁盘硬配额；
  非 Windows / 不可用环境降级并记录，不假装受限。
- 依赖基线：构建前对 uv.lock / requirements.txt / package-lock.json 计算 sha256
  汇总 digest 并锁定；构建期间若被改动则构建失败（含构建后复核）。
- 产物 manifest + provenance：扫描 ``work/out`` 生成 build_manifest.json
  （产物清单/依赖 digest/构建命令/时间/python 与 node 版本/provenance）。
- 失败非零返回：任何失败（命令非零退出、超时、资源超限、依赖被改动、内部异常）
  -> 状态 FAILED + 非零 exit code + ``error.txt`` 写入 out 目录；成功返回 0。
- 导入前验证：``verify_artifacts()`` 校验产物 digest 与 manifest 一致，并支持
  可选签名/哈希 allowlist；是否拒绝“导入主应用”由调用方依据报告决定。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from services.cosign_verifier import find_signature_materials, is_signature_material
from services.resource_profile import DiskQuotaExceededError, DiskQuotaGuard
from services.windows_job import JobLimits, WindowsJobObject, kill_job

BUILD_SERVICE_VERSION = "1.0.0"
MANIFEST_FILENAME = "build_manifest.json"
ERROR_FILENAME = "error.txt"
# 构建依赖基线所覆盖的锁文件/依赖清单（按需存在）
DEPENDENCY_FILES = ("uv.lock", "requirements.txt", "package-lock.json")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 构建资源档位（memory/cpu 为每进程上限，进程数为 Job 内并发上限，disk 为 work 卷硬配额）
_BUILD_PROFILES: dict[str, dict[str, int]] = {
    "low": {
        "memory_mb": 512,
        "cpu_time_seconds": 300,
        "max_processes": 16,
        "disk_mb": 1024,
    },
    "standard": {
        "memory_mb": 2048,
        "cpu_time_seconds": 900,
        "max_processes": 32,
        "disk_mb": 8192,
    },
    "high": {
        "memory_mb": 8192,
        "cpu_time_seconds": 3600,
        "max_processes": 128,
        "disk_mb": 32768,
    },
}

__all__ = [
    "BUILD_SERVICE_VERSION",
    "BuildRequest",
    "BuildJob",
    "BuildStatus",
    "build_main_entry",
    "verify_artifacts",
    "compute_dependency_digest",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_dependency_digest(root: str | Path) -> str:
    """对依赖锁文件集合计算 sha256 汇总 digest（文件不存在则忽略）。

    覆盖 uv.lock / requirements.txt / package-lock.json；一个都没有时返回空串。
    """
    root_path = Path(root)
    hasher = hashlib.sha256()
    found = False
    for name in DEPENDENCY_FILES:
        path = root_path / name
        if path.is_file():
            found = True
            hasher.update(name.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
    if not found:
        return ""
    return hasher.hexdigest()


def _tree_digest(root: Path) -> str:
    """对整个目录树（相对路径 + 文件 sha256）计算汇总 digest。"""
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest.encode("ascii"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def _scan_artifacts(out_dir: Path) -> list[dict[str, Any]]:
    """扫描产物目录，返回 [{name, size, sha256}]（排除 manifest 自身）。"""
    artifacts: list[dict[str, Any]] = []
    if not out_dir.exists():
        return artifacts
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_FILENAME:
            continue
        artifacts.append(
            {
                "name": path.relative_to(out_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return artifacts


def _node_version() -> str:
    try:
        proc = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10
        )
        return (proc.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _pipe_reader(stream: Any, sink: list[bytes]) -> None:
    """后台线程排空子进程 stdout/stderr，避免管道满导致子进程阻塞。"""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            sink.append(chunk)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _kill_tree(job: WindowsJobObject, proc: subprocess.Popen) -> list[int]:
    """先经 Job Object 终止整棵进程树，再兜底 kill 直接子进程。"""
    killed: list[int] = []
    if job.handle:
        killed = kill_job(job.handle)
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    return killed


class BuildStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class BuildRequest:
    """独立构建请求。

    src_dir / out_dir 必须为独立目录（源码卷 / 输出卷）；构建在
    out_dir/work 下进行，源码复制到 work/src，产物写 work/out。
    command 可为 argv 列表或 shell 命令字符串（字符串经平台 shell 执行）。
    """

    src_dir: str | Path
    out_dir: str | Path
    command: str | list[str]
    env: dict[str, str] | None = None
    resource_profile: str = "standard"
    timeout_seconds: int | None = None
    disk_quota_bytes: int | None = None


class BuildError(RuntimeError):
    """构建失败（含失败原因与期望的 exit code）。"""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = int(exit_code) if int(exit_code) != 0 else 1


class BuildJob:
    """独立构建作业：状态机（PENDING -> RUNNING -> COMPLETED | FAILED）+ run()。

    run() 返回 exit code（成功 0，失败非零）；状态与错误保存在实例字段，
    manifest 写入 work/out/build_manifest.json。
    """

    def __init__(
        self,
        request: BuildRequest,
        work_dir: str | Path | None = None,
        job_id: str | None = None,
    ) -> None:
        self.request = request
        self.job_id = job_id or f"build_{uuid.uuid4().hex[:12]}"
        self.status = BuildStatus.PENDING
        self.exit_code: int | None = None
        self.error = ""
        self.manifest: dict[str, Any] | None = None
        self.work_dir = Path(work_dir) if work_dir else Path(request.out_dir) / "work"
        self.src_work = self.work_dir / "src"
        self.out_work = self.work_dir / "out"
        self.dependency_digest_locked = ""
        self.source_digest = ""
        self.degraded_reason = ""
        self.enforced_limits: dict[str, Any] = {}
        self.stdout_text = ""
        self.stderr_text = ""
        self.profile = self._resolve_profile(request.resource_profile)

    @staticmethod
    def _resolve_profile(name: str) -> dict[str, int]:
        if name not in _BUILD_PROFILES:
            raise ValueError(
                f"未知构建资源档位: {name!r} (可选: {', '.join(sorted(_BUILD_PROFILES))})"
            )
        return dict(_BUILD_PROFILES[name])

    # ── 状态机 ───────────────────────────────────────────────────────────
    def run(self) -> int:
        if self.status != BuildStatus.PENDING:
            raise RuntimeError(f"BuildJob 已运行（status={self.status.value}）")
        self.status = BuildStatus.RUNNING
        try:
            self._prepare_workdir()
            self.dependency_digest_locked = compute_dependency_digest(self.src_work)
            self.source_digest = _tree_digest(self.src_work)
            self._run_command()
            self._finalize_success()
        except BuildError as exc:
            self._fail(exc.message, exit_code=exc.exit_code)
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都转为 FAILED
            self._fail(f"构建内部异常: {exc}", exit_code=1)
        return int(self.exit_code or 0)

    # ── 内部流程 ─────────────────────────────────────────────────────────
    def _prepare_workdir(self) -> None:
        src_dir = Path(self.request.src_dir).resolve()
        if not src_dir.is_dir():
            raise BuildError(f"源码目录不存在: {src_dir}")
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.out_work.mkdir(parents=True, exist_ok=True)
        # 源码卷 -> 隔离工作目录（work/src），原始 src_dir 保持只读
        shutil.copytree(src_dir, self.src_work)

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "BUILD_SRC_DIR": str(self.src_work),
                "BUILD_OUT_DIR": str(self.out_work),
                "SRC_DIR": str(self.src_work),
                "OUT_DIR": str(self.out_work),
            }
        )
        if self.request.env:
            env.update({str(k): str(v) for k, v in self.request.env.items()})
        return env

    def _command_repr(self) -> str:
        cmd = self.request.command
        if isinstance(cmd, str):
            return cmd
        return " ".join(str(part) for part in cmd)

    def _run_command(self) -> None:
        profile = self.profile
        timeout = (
            self.request.timeout_seconds
            if self.request.timeout_seconds is not None
            else int(profile["cpu_time_seconds"])
        )
        env = self._build_env()
        disk_quota_bytes = self.request.disk_quota_bytes
        if disk_quota_bytes is None:
            disk_quota_bytes = int(profile["disk_mb"]) * 1024 * 1024
        disk_guard = DiskQuotaGuard(self.work_dir, disk_quota_bytes)
        job = WindowsJobObject(
            JobLimits(
                max_processes=int(profile["max_processes"]),
                memory_bytes=int(profile["memory_mb"]) * 1024 * 1024,
                cpu_time_100ns=int(profile["cpu_time_seconds"]) * 10_000_000,
            )
        )
        try:
            job.create()
        except Exception as exc:
            job.degraded_reason = f"job_create_failed: {exc}"
        self.enforced_limits = dict(job.enforced_limits)
        self.degraded_reason = job.degraded_reason

        shell = isinstance(self.request.command, str)
        try:
            proc = subprocess.Popen(
                self.request.command,
                cwd=str(self.work_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=shell,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise BuildError(f"无法启动构建命令: {exc}")

        try:
            job.assign(proc.pid)
        except Exception as exc:
            if not job.degraded_reason:
                job.degraded_reason = f"assign_failed: {exc}"
            self.degraded_reason = job.degraded_reason

        try:
            self._wait_command(proc, job, disk_guard, timeout)
        finally:
            job.close()

        if proc.returncode != 0:
            raise BuildError(
                f"构建命令退出码非零: {proc.returncode}",
                exit_code=int(proc.returncode or 1),
            )

    def _wait_command(
        self,
        proc: subprocess.Popen,
        job: WindowsJobObject,
        disk_guard: DiskQuotaGuard,
        timeout: int,
    ) -> None:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        readers: list[threading.Thread] = []
        if proc.stdout is not None:
            reader = threading.Thread(
                target=_pipe_reader, args=(proc.stdout, stdout_chunks), daemon=True
            )
            reader.start()
            readers.append(reader)
        if proc.stderr is not None:
            reader = threading.Thread(
                target=_pipe_reader, args=(proc.stderr, stderr_chunks), daemon=True
            )
            reader.start()
            readers.append(reader)

        started = time.monotonic()
        last_disk_check = 0.0
        last_dep_check = 0.0
        while True:
            if proc.poll() is not None:
                break
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                _kill_tree(job, proc)
                raise BuildError(f"构建超时（{timeout}s），进程树已终止")
            if disk_guard.enforced and elapsed - last_disk_check >= 0.5:
                try:
                    disk_guard.check()
                except DiskQuotaExceededError as exc:
                    _kill_tree(job, proc)
                    raise BuildError(f"磁盘配额超限: {exc}")
                last_disk_check = elapsed
            if elapsed - last_dep_check >= 2.0:
                if compute_dependency_digest(self.src_work) != self.dependency_digest_locked:
                    _kill_tree(job, proc)
                    raise BuildError("依赖文件在构建期间被修改（dependency digest 不一致）")
                last_dep_check = elapsed
            time.sleep(0.05)

        for reader in readers:
            reader.join(timeout=5)
        self.stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        self.stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

        # 命令结束后复核：磁盘配额 + 依赖基线
        if disk_guard.enforced:
            try:
                disk_guard.check()
            except DiskQuotaExceededError as exc:
                raise BuildError(f"磁盘配额超限: {exc}")
        if compute_dependency_digest(self.src_work) != self.dependency_digest_locked:
            raise BuildError("依赖文件在构建期间被修改（dependency digest 不一致）")

    def _finalize_success(self) -> None:
        artifacts = _scan_artifacts(self.out_work)
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "job_id": self.job_id,
            "status": BuildStatus.COMPLETED.value,
            "build_command": self._command_repr(),
            "build_time": _now(),
            "python_version": platform.python_version(),
            "node_version": _node_version(),
            "resource_profile": self.request.resource_profile,
            "src_dir": str(Path(self.request.src_dir).resolve()),
            "out_dir": str(Path(self.request.out_dir).resolve()),
            "work_dir": str(self.work_dir),
            "artifacts_dir": str(self.out_work),
            "source_digest": self.source_digest,
            "dependency_digest": self.dependency_digest_locked,
            "enforcement": {
                "job_available": bool(self.enforced_limits),
                "limits": self.enforced_limits,
                "degraded_reason": self.degraded_reason,
            },
            "artifacts": artifacts,
            "provenance": {
                "builder": "build_service",
                "builder_version": BUILD_SERVICE_VERSION,
                "source_digest": self.source_digest,
                "dependency_digest": self.dependency_digest_locked,
                "host": platform.node(),
                "platform": sys.platform,
            },
        }
        self.manifest = manifest
        manifest_path = self.out_work / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.status = BuildStatus.COMPLETED
        self.exit_code = 0

    def _fail(self, message: str, exit_code: int = 1) -> None:
        self.status = BuildStatus.FAILED
        self.error = message
        self.exit_code = int(exit_code) if int(exit_code) != 0 else 1
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.out_work.mkdir(parents=True, exist_ok=True)
        detail = (
            f"[build_service] {message}\n\n"
            f"stdout:\n{self.stdout_text}\n\n"
            f"stderr:\n{self.stderr_text}\n"
        )
        (self.out_work / ERROR_FILENAME).write_text(detail, encoding="utf-8")


def _normalize_allowlist(
    raw: dict[str, set[str]] | set[str] | list[str] | None,
) -> dict[str, set[str]] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return {str(k): {str(v) for v in values} for k, values in raw.items()}
    if isinstance(raw, (set, list, tuple)):
        return {"*": {str(v) for v in raw}}
    return None


def _allowed(name: str, digest: str, allowlist: dict[str, set[str]]) -> bool:
    if digest in allowlist.get("*", set()):
        return True
    return digest in allowlist.get(name, set())


def verify_artifacts(
    manifest: dict[str, Any] | str | Path,
    signer_allowlist: dict[str, set[str]] | set[str] | list[str] | None = None,
    *,
    verifier: Any | None = None,
    trust_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """导入主应用前的产物校验。

    - ``manifest`` 可为已加载的 dict，或 build_manifest.json 的路径。
    - ``signer_allowlist`` 可选：``{artifact_name: {允许的 sha256 集合}}``，或一个
      ``{允许的 sha256 集合}``（匹配任意产物）；命中即视为“签名/哈希白名单”通过。
    - ``verifier`` 可选（services.cosign_verifier.CosignVerifier）：当提供且传入
      ``signer_allowlist`` 或 ``trust_policy`` 要求签名时，对每个产物执行真实
      cosign 签名校验（查找 .sig/.bundle，缺失或校验失败 -> 拒绝导入主应用）。
    - ``trust_policy`` 可选：信任策略 dict（结构与 plugins.trust-policy.example.json
      一致：allowed_signers / require_signing / require_cosign / expected_identity /
      expected_issuer / trusted_digests）。仅当 ``verifier`` 传入时生效。
    - 校验项：manifest 字段完整、产物 size/sha256 与 manifest 一致、无未声明产物、
      可选 allowlist 命中、可选真实签名校验。
    - 返回报告 ``{ok, errors, verified_artifacts, artifacts_total, signer_allowlist_checked,
      signature_checked}``；任一失败 -> ``ok=False`` 并给出原因。是否拒绝“导入主应用”
      由调用方依据报告决定。无签名要求（verifier 为 None）时保持原行为不变。
    """
    errors: list[str] = []
    if isinstance(manifest, (str, Path)):
        try:
            manifest = json.loads(Path(manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "errors": [f"MANIFEST_UNREADABLE: {exc}"],
                "verified_artifacts": 0,
                "artifacts_total": 0,
                "signer_allowlist_checked": False,
            }
    if not isinstance(manifest, dict):
        return {
            "ok": False,
            "errors": ["MANIFEST_INVALID"],
            "verified_artifacts": 0,
            "artifacts_total": 0,
            "signer_allowlist_checked": False,
        }

    artifacts_dir = Path(str(manifest.get("artifacts_dir") or ""))
    if not artifacts_dir.is_dir():
        errors.append(f"ARTIFACTS_DIR_MISSING: {artifacts_dir}")

    for key in ("schema_version", "status", "dependency_digest", "provenance", "artifacts"):
        if key not in manifest:
            errors.append(f"MANIFEST_MISSING_FIELD: {key}")
    if "provenance" in manifest and "builder_version" not in manifest["provenance"]:
        errors.append("MANIFEST_MISSING_FIELD: provenance.builder_version")

    declared = manifest.get("artifacts")
    declared_list = declared if isinstance(declared, list) else []
    allowlist = _normalize_allowlist(signer_allowlist)
    policy = dict(trust_policy or {})
    require_signing = bool(policy.get("require_signing") or policy.get("require_cosign"))
    # 真实签名校验仅当调用方显式传入 verifier 且存在签名要求时启用
    signature_mode = verifier is not None and (signer_allowlist is not None or require_signing)
    verified = 0

    if artifacts_dir.is_dir():
        actual_files = {
            p.relative_to(artifacts_dir).as_posix(): p
            for p in artifacts_dir.rglob("*")
            if p.is_file() and p.name != MANIFEST_FILENAME
        }
        declared_names = {
            str(item.get("name", "")) for item in declared_list if isinstance(item, dict)
        }
        # 签名模式：.sig/.bundle 视为产物的 companion 文件，不作为独立产物校验
        companion_names = {
            name for name in actual_files if is_signature_material(name, declared_names)
        } if signature_mode else set()
        for name, path in actual_files.items():
            if name in companion_names:
                continue
            if name not in declared_names:
                errors.append(f"UNEXPECTED_ARTIFACT: {name}")
                continue
            try:
                size = path.stat().st_size
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                errors.append(f"ARTIFACT_UNREADABLE: {name}: {exc}")
                continue
            item = next(
                (it for it in declared_list if isinstance(it, dict) and str(it.get("name", "")) == name),
                None,
            )
            if item is not None and int(item.get("size", -1)) != size:
                errors.append(f"SIZE_MISMATCH: {name}")
            if item is not None and item.get("sha256") != digest:
                errors.append(f"DIGEST_MISMATCH: {name}")
            if allowlist is not None and not _allowed(name, digest, allowlist):
                errors.append(f"SIGNER_NOT_ALLOWED: {name}")
            verified += 1
        if signature_mode:
            # 真实 cosign 签名校验（fail-closed）：每个产物必须有可校验的 .sig/.bundle
            for name in sorted(actual_files):
                if name in companion_names or name not in declared_names:
                    continue
                path = actual_files[name]
                sig_path, bundle_path = find_signature_materials(artifacts_dir, name)
                if sig_path is None:
                    errors.append(f"SIGNATURE_MATERIAL_MISSING: {name}")
                    continue
                declared = next(
                    (it for it in declared_list if isinstance(it, dict) and str(it.get("name", "")) == name),
                    None,
                )
                expected_digest = declared.get("sha256") if declared else None
                try:
                    sig_result = verifier.verify_blob_signature(
                        artifact_path=path,
                        sig_path=sig_path,
                        bundle_path=bundle_path,
                        cert_identity=policy.get("expected_identity"),
                        cert_issuer=policy.get("expected_issuer"),
                        expected_digest=expected_digest,
                    )
                except Exception as exc:  # noqa: BLE001 - verifier 异常视为校验失败
                    errors.append(f"SIGNATURE_INVALID: {name}: {type(exc).__name__}: {exc}")
                    continue
                if not sig_result.get("ok"):
                    errors.append(
                        f"SIGNATURE_INVALID: {name}: {sig_result.get('reason', 'unknown')}"
                    )
                    continue
                actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
                policy_result = verifier.verify_policy(actual_digest, sig_result, policy)
                if not policy_result.get("ok"):
                    errors.append(
                        f"POLICY_REJECTED: {name}: {'; '.join(policy_result.get('errors', []))}"
                    )
        for item in declared_list:
            if isinstance(item, dict) and str(item.get("name", "")) not in actual_files:
                errors.append(f"ARTIFACT_MISSING: {item.get('name', '?')}")

    return {
        "ok": not errors,
        "errors": errors,
        "verified_artifacts": verified,
        "artifacts_total": len(declared_list),
        "signer_allowlist_checked": allowlist is not None,
        "signature_checked": signature_mode,
    }


def build_main_entry(
    src_dir: str | Path,
    out_dir: str | Path,
    command: str | list[str],
    env: dict[str, str] | None = None,
    resource_profile: str = "standard",
    timeout_seconds: int | None = None,
    disk_quota_bytes: int | None = None,
    verify: bool = True,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """同步构建入口（供脚本 / CI 调用）。

    返回 dict：{job_id, status, exit_code, error, manifest, verify,
    work_dir, artifacts_dir, degraded_reason}。exit_code 为 0 表示成功。
    """
    request = BuildRequest(
        src_dir=src_dir,
        out_dir=out_dir,
        command=command,
        env=env,
        resource_profile=resource_profile,
        timeout_seconds=timeout_seconds,
        disk_quota_bytes=disk_quota_bytes,
    )
    try:
        job = BuildJob(request, work_dir=work_dir)
        exit_code = job.run()
    except Exception as exc:  # noqa: BLE001 - 入口兜底：返回 FAILED 报告而非抛异常
        return {
            "job_id": "",
            "status": BuildStatus.FAILED.value,
            "exit_code": 1,
            "error": f"build_main_entry: {exc}",
            "manifest": None,
            "work_dir": str(Path(out_dir) / "work"),
            "artifacts_dir": str(Path(out_dir) / "work" / "out"),
            "degraded_reason": "",
        }
    result: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status.value,
        "exit_code": int(exit_code),
        "error": job.error,
        "manifest": job.manifest,
        "work_dir": str(job.work_dir),
        "artifacts_dir": str(job.out_work),
        "degraded_reason": job.degraded_reason,
    }
    if verify and job.manifest is not None:
        result["verify"] = verify_artifacts(job.manifest)
    return result
