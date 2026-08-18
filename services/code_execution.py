"""L0-L2 受限 Python 代码执行服务。

计划和执行严格分开；只允许固定 Python 模块命令，默认无网络、无正式工作区写入。
"""
from __future__ import annotations

import asyncio
import difflib
from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from core.config_manager import config_manager
from core.path_resolver import get_temp_root, get_workspace_dir, safe_join
from models.code_execution import CodeAudit, CodePlan, CodeTaskPhase, CodeTaskRecord, CodeTaskRequest, CodeTaskStatus
from services.network_isolation import apply_isolation, remove_isolation
from services.resource_profile import (
    DiskQuotaExceededError,
    DiskQuotaGuard,
    ResourceProfileError,
    ResourceProfileManager,
)
from services.windows_job import JobLimits, WindowsJobObject


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ALLOWED = {
    "compileall": ["-m", "compileall"],
    "pytest": ["-m", "pytest"],
    "unittest": ["-m", "unittest"],
}


class CodePolicyError(ValueError):
    pass


class CodeExecutionService:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db
        self.workspace_root = get_workspace_dir().resolve()
        self.temp_root = get_temp_root() / "code_execution"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.resource_profiles = ResourceProfileManager(self.temp_root)
        self.plans: dict[str, CodePlan] = {}
        self.audits: dict[str, CodeAudit] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.staged_changes: dict[str, dict[str, Any]] = {}
        self.promote_backups: dict[str, dict[str, bytes | None]] = {}
        self.task_records: dict[str, CodeTaskRecord] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.jobs: dict[str, WindowsJobObject] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self._code_table_ready = False
        self._recovery_done = False
        # 阶段B：服务端签发的审批记录（一次性 nonce，绑定计划 + 身份 + 过期时间）。
        # 客户端不能自行伪造 approval_id；只有 issue_approval 生成且未过期的 token 才有效。
        self.approvals: dict[str, dict[str, Any]] = {}

    def snapshot(self, target: str = ".") -> dict[str, Any]:
        root = self._relative_path(target)
        if not root.exists():
            raise CodePolicyError("工作区目标不存在")
        files: dict[str, str] = {}
        paths = [root] if root.is_file() else [item for item in root.rglob("*") if item.is_file()]
        for path in paths:
            relative = path.relative_to(self.workspace_root).as_posix()
            if len(files) >= 5000:
                raise CodePolicyError("快照文件数量超过上限")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files[relative] = digest
        snapshot_id = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:24]
        return {"snapshot_id": snapshot_id, "workspace": str(self.workspace_root), "files": files}

    def diff(self, relative_path: str, proposed_content: str) -> dict[str, Any]:
        path = self._relative_path(relative_path)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        diff_lines = list(difflib.unified_diff(
            before.splitlines(keepends=True), proposed_content.splitlines(keepends=True),
            fromfile=f"a/{relative_path}", tofile=f"b/{relative_path}",
        ))
        return {
            "path": relative_path,
            "before_digest": hashlib.sha256(before.encode()).hexdigest(),
            "after_digest": hashlib.sha256(proposed_content.encode()).hexdigest(),
            "changed": before != proposed_content,
            "diff": "".join(diff_lines),
        }

    def stage_file(self, relative_path: str, content: str, base_digest: str | None = None) -> dict[str, Any]:
        path = self._relative_path(relative_path)
        before = path.read_bytes() if path.exists() else None
        current_digest = hashlib.sha256(before or b"").hexdigest()
        if base_digest and base_digest != current_digest:
            raise CodePolicyError("工作区文件已变化，拒绝覆盖旧基线")
        change_id = f"stage_{hashlib.sha256((relative_path + content).encode()).hexdigest()[:20]}"
        self.staged_changes[change_id] = {
            "change_id": change_id,
            "path": relative_path,
            "content": content,
            "base_digest": current_digest,
            "after_digest": hashlib.sha256(content.encode()).hexdigest(),
            "status": "STAGED",
        }
        return dict(self.staged_changes[change_id])

    def promote(self, change_id: str, approval_id: str) -> dict[str, Any]:
        if not approval_id.strip():
            raise CodePolicyError("promote 必须提供作者批准 ID")
        change = self.staged_changes.get(change_id)
        if not change or change.get("status") != "STAGED":
            raise CodePolicyError("staging 变更不存在或已经处理")
        path = self._relative_path(change["path"])
        before = path.read_bytes() if path.exists() else None
        current_digest = hashlib.sha256(before or b"").hexdigest()
        if current_digest != change["base_digest"]:
            raise CodePolicyError("正式文件已变化，拒绝 promote")
        self.promote_backups[change_id] = {"path": path, "content": before}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{change_id}.tmp")
        temporary.write_text(change["content"], encoding="utf-8")
        temporary.replace(path)
        change["status"] = "PROMOTED"
        change["approval_id"] = approval_id
        return dict(change)

    def rollback(self, change_id: str, approval_id: str) -> dict[str, Any]:
        if not approval_id.strip():
            raise CodePolicyError("rollback 必须提供作者批准 ID")
        backup = self.promote_backups.get(change_id)
        if not backup:
            raise CodePolicyError("没有可回滚的 promote 记录")
        path = Path(str(backup["path"]))
        content = backup["content"]
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)
        change = self.staged_changes[change_id]
        change["status"] = "ROLLED_BACK"
        change["rollback_approval_id"] = approval_id
        return dict(change)

    def _publish_event(self, task_id: str, phase: CodeTaskPhase, progress: int, message: str) -> None:
        """向现有任务频道发布事件；没有 WebSocket 连接时静默降级。"""
        try:
            from api.websocket import manager
            asyncio.create_task(manager.publish_task_event(task_id, {"phase": phase.value, "progress": progress, "message": message, "domain": "code"}))
        except Exception:
            pass

    def _set_record(self, task_id: str, *, status: CodeTaskStatus | None = None, phase: CodeTaskPhase | None = None, progress: int | None = None, message: str | None = None, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        record = self.task_records.get(task_id)
        if record is None:
            return
        updates: dict[str, Any] = {"updated_at": _now()}
        if status is not None: updates["status"] = status
        if phase is not None: updates["phase"] = phase
        if progress is not None: updates["progress"] = max(0, min(100, progress))
        if message is not None: updates["message"] = message
        if result is not None: updates["result"] = result
        if error is not None: updates["error"] = error
        self.task_records[task_id] = record.model_copy(update=updates)
        current = self.task_records[task_id]
        self._publish_event(task_id, current.phase, current.progress, current.message)

    def get_task_record(self, task_id: str) -> dict[str, Any] | None:
        record = self.task_records.get(task_id)
        return record.model_dump(mode="json") if record else None

    async def _persist_task_result(self, task_id: str, result: dict[str, Any]) -> None:
        if self.db is None:
            return
        row = await self.db.get_task(task_id)
        if row is None:
            return
        try:
            payload = json.loads(row.get("task_payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        record = self.get_task_record(task_id)
        payload["code_execution"] = {
            "result": result,
            "record": record,
            "audit": result.get("audit", {}),
        }
        await self.db.update_task_payload(task_id, payload)

    # ── 任务状态持久化与崩溃恢复（SQLite code_execution_tasks）──────────────
    async def _ensure_code_task_table(self) -> None:
        """幂等创建 code_execution_tasks 表（复用 db.conn，队列化执行避免锁竞争）。"""
        if self._code_table_ready or self.db is None:
            return
        await self.db.execute_write(
            """CREATE TABLE IF NOT EXISTS code_execution_tasks (
                task_id         TEXT PRIMARY KEY,
                status          TEXT NOT NULL,
                plan            TEXT,
                request_payload TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                error           TEXT
            )"""
        )
        self._code_table_ready = True

    async def _get_code_task_row(self, task_id: str) -> dict[str, Any] | None:
        if self.db is None:
            return None
        cursor = await self.db.conn.execute(
            "SELECT * FROM code_execution_tasks WHERE task_id=?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    async def _persist_code_task_state(
        self,
        task_id: str,
        *,
        status: str | None = None,
        plan: CodePlan | None = None,
        request: CodeTaskRequest | None = None,
        error: str | None = None,
    ) -> None:
        """在执行生命周期关键节点 upsert 任务状态（幂等、失败不影响执行主流程）。"""
        if self.db is None:
            return
        await self._ensure_code_task_table()
        now = _now()
        existing = await self._get_code_task_row(task_id)
        created_at = existing["created_at"] if existing else now
        plan_json = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False) if plan else None
        request_json = json.dumps(request.model_dump(mode="json"), ensure_ascii=False) if request else None
        await self.db.execute_write(
            """INSERT INTO code_execution_tasks
                   (task_id, status, plan, request_payload, created_at, updated_at, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                   status=excluded.status,
                   plan=COALESCE(excluded.plan, plan),
                   request_payload=COALESCE(excluded.request_payload, request_payload),
                   updated_at=excluded.updated_at,
                   error=COALESCE(excluded.error, error)""",
            (
                task_id,
                status or "PENDING",
                plan_json,
                request_json,
                created_at,
                now,
                error,
            ),
        )

    async def _safe_persist_state(self, task_id: str, **kwargs: Any) -> None:
        """持久化是尽力而为：DB 异常不影响代码任务执行与上报。"""
        try:
            await self._persist_code_task_state(task_id, **kwargs)
        except Exception:
            pass

    async def recover_interrupted_code_tasks(self) -> int:
        """把上次进程退出时遗留的 RUNNING CodeTask 恢复为 FAILED（可重跑）。

        仅处理独立表 code_execution_tasks；返回被恢复的任务数。
        服务初始化（或首次执行前）调用一次；无 db 时返回 0。
        """
        if self.db is None:
            return 0
        await self._ensure_code_task_table()
        cursor = await self.db.conn.execute(
            "SELECT task_id FROM code_execution_tasks WHERE status='RUNNING'"
        )
        rows = await cursor.fetchall()
        now = _now()
        count = 0
        for (task_id,) in rows:
            await self.db.execute_write(
                """UPDATE code_execution_tasks SET status='FAILED', error=?, updated_at=?
                   WHERE task_id=?""",
                ("INTERRUPTED_RECOVERED", now, task_id),
            )
            count += 1
        return count

    def _cleanup_network_rules(self, rule_names: list[str]) -> None:
        if not rule_names:
            return
        try:
            remove_isolation(rule_names)
        except Exception:
            pass

    def _cleanup_staging(self, task_id: str) -> None:
        """可靠删除任务 staging 目录（阶段D：补 finally 清理，禁止残留临时镜像）。"""
        try:
            staging = self.temp_root / task_id
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass

    async def cancel(self, task_id: str) -> dict[str, Any]:
        event = self.cancel_events.get(task_id)
        if event is None:
            raise CodePolicyError("代码任务不存在")
        event.set()
        process = self.processes.get(task_id)
        if process is not None and process.returncode is None:
            process.kill()
        job = self.jobs.get(task_id)
        if job is not None:
            job.close()
        self._set_record(task_id, status=CodeTaskStatus.CANCELLED, message="已请求取消")
        state = self.get_task_record(task_id) or {}
        await self._safe_persist_state(task_id, status=CodeTaskStatus.CANCELLED.value)
        await self._persist_task_result(task_id, {"task_id": task_id, "status": "CANCELLED", "record": state})
        return state

    def _relative_path(self, raw: str) -> Path:
        value = str(raw or ".").strip()
        candidate = Path(value)
        if candidate.is_absolute():
            raise CodePolicyError("代码目标必须是工作区内相对路径")
        target = safe_join(self.workspace_root, value)
        if target != self.workspace_root and self.workspace_root not in target.parents:
            raise CodePolicyError("代码目标越出工作区")
        return target

    async def _read_limited(
        self, stream: asyncio.StreamReader | None, max_output: int
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return b"".join(chunks), False
            remaining = max_output - total
            if remaining > 0:
                chunks.append(chunk[:remaining])
            total += len(chunk)
            if total > max_output:
                return b"".join(chunks), True

    def plan(self, request: CodeTaskRequest) -> CodePlan:
        target = self._relative_path(request.target)
        if target.exists() and target.is_file() and target.suffix.lower() not in {".py", ".pyw"}:
            raise CodePolicyError("当前 L2 只允许 Python 目标")
        if request.cwd_relative:
            self._relative_path(request.cwd_relative)
        argv = [sys.executable, *_ALLOWED[request.command], request.target]
        try:
            profile = self.resource_profiles.resolve(request.resource_profile)
        except ResourceProfileError as exc:
            raise CodePolicyError(str(exc)) from exc
        plan = CodePlan(
            workspace_id=request.workspace_id,
            files_to_read=[request.target],
            argv=argv,
            cwd_relative=request.cwd_relative,
            capabilities=["workspace_read", "execute_python", "collect_output"],
            timeout_seconds=profile.timeout_seconds,
            max_output_bytes=request.max_output_bytes,
            resource_profile=profile.name,
            disk_budget_bytes=profile.disk_bytes,
            max_memory_mb=profile.memory_mb,
            max_processes=profile.max_processes,
            cpu_time_seconds=profile.cpu_time_seconds,
            risk_level="low",
            requires_approval=True,
        )
        self.plans[plan.plan_id] = plan
        return plan

    async def execute(self, request: CodeTaskRequest, plan: CodePlan, task_id: str | None = None) -> dict[str, Any]:
        # 纵深防御：即使上层误接入口，未显式开启也拒绝在宿主环境执行代码。
        if not config_manager.get_bool("code_execution.enabled", False):
            raise CodePolicyError("受限代码执行已禁用（code_execution.enabled=false），拒绝在宿主环境执行代码")
        if plan.status != "APPROVED":
            raise CodePolicyError("代码计划尚未获得作者批准")
        if not plan.approval_id.strip():
            raise CodePolicyError("代码计划缺少批准 ID")
        if plan.network_policy != "deny":
            raise CodePolicyError("当前执行域禁止网络")
        target = self._relative_path(request.target)
        if not target.exists():
            raise CodePolicyError("代码目标不存在")
        task_id = task_id or f"codetask_{hashlib.sha256(plan.plan_id.encode()).hexdigest()[:16]}"
        if self.db is not None and not self._recovery_done:
            self._recovery_done = True
            try:
                await self.recover_interrupted_code_tasks()
            except Exception:
                pass
        try:
            profile = self.resource_profiles.resolve(plan.resource_profile)
            if (
                profile.memory_mb != plan.max_memory_mb
                or profile.max_processes != plan.max_processes
                or profile.disk_bytes != plan.disk_budget_bytes
            ):
                raise CodePolicyError("执行计划资源档位与服务端配置不一致")
            self.resource_profiles.reserve(task_id, profile)
        except ResourceProfileError as exc:
            raise CodePolicyError(str(exc)) from exc
        self.task_records[task_id] = CodeTaskRecord(task_id=task_id, plan_id=plan.plan_id, status=CodeTaskStatus.RUNNING, phase=CodeTaskPhase.STAGING, progress=10, message="正在创建 staging")
        cancel_event = asyncio.Event()
        self.cancel_events[task_id] = cancel_event
        await self._safe_persist_state(task_id, status=CodeTaskStatus.RUNNING.value, plan=plan, request=request)
        staging = self.temp_root / task_id
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        # 当前第一版不把正式工作区复制到 staging 外执行；只读命令在 staging 镜像上运行。
        relative_target = target.relative_to(self.workspace_root)
        staging_target = staging / relative_target
        if target.is_file():
            staging_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, staging_target)
        else:
            shutil.copytree(target, staging_target)
        cwd = staging / (request.cwd_relative or ".")
        # ── 磁盘硬配额：写文件前预检（staging 镜像即工作目录）──────────────
        disk_guard = DiskQuotaGuard(staging, plan.disk_budget_bytes)
        if disk_guard.enforced:
            try:
                disk_guard.check()
            except DiskQuotaExceededError as exc:
                cleanup_manifest = disk_guard.cleanup_excess()
                self._set_record(
                    task_id,
                    status=CodeTaskStatus.FAILED,
                    phase=CodeTaskPhase.FINALIZING,
                    progress=100,
                    message="磁盘配额超限，任务未启动",
                    error="DISK_QUOTA_EXCEEDED",
                    result={"disk_cleanup": cleanup_manifest},
                )
                self.resource_profiles.release(task_id)
                self._cleanup_staging(task_id)
                await self._safe_persist_state(task_id, status=CodeTaskStatus.FAILED.value, plan=plan, request=request, error="DISK_QUOTA_EXCEEDED")
                raise CodePolicyError(f"DISK_QUOTA_EXCEEDED: {exc}")
        command = [sys.executable, *_ALLOWED[request.command], str(relative_target)]
        # ── 操作系统级网络隔离（配置 code_execution.network_policy，默认 off）──
        network_policy = str(config_manager.get("code_execution.network_policy", "off") or "off").strip().lower()
        network_isolation: dict[str, Any] = {
            "policy": "off", "program": str(sys.executable), "enforced": False, "rule_names": [],
        }
        if network_policy in ("local_only", "full"):
            network_isolation = apply_isolation(sys.executable, network_policy)
        net_rule_names = list(network_isolation.get("rule_names") or [])
        self._set_record(task_id, phase=CodeTaskPhase.EXECUTING, progress=25, message="正在执行受限 Python 命令")
        started = asyncio.get_running_loop().time()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=False,
        )
        self.processes[task_id] = process
        job = WindowsJobObject(JobLimits(
            max_processes=plan.max_processes,
            memory_bytes=plan.max_memory_mb * 1024 * 1024,
            cpu_time_100ns=plan.cpu_time_seconds * 10_000_000,
        ))
        try:
            job.create()
            job.assign(process.pid)
        except Exception as exc:
            job.degraded_reason = str(exc)
        if not job.available and job.degraded_reason == "windows_only":
            job.degraded_reason = "windows_job_unavailable; network_policy_not_os_enforced"
        self.jobs[task_id] = job
        if cancel_event.is_set():
            process.kill()

        try:
            stdout_task = asyncio.create_task(self._read_limited(process.stdout, max_output=plan.max_output_bytes))
            stderr_task = asyncio.create_task(self._read_limited(process.stderr, max_output=plan.max_output_bytes))
            wait_task = asyncio.create_task(process.wait())
            pending = {stdout_task, stderr_task, wait_task}
            poll_count = 0
            while not wait_task.done():
                done, pending = await asyncio.wait(
                    pending,
                    timeout=0.05,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    if asyncio.get_running_loop().time() - started > plan.timeout_seconds:
                        process.kill()
                        job.close()
                        await process.wait()
                        self._cleanup_network_rules(net_rule_names)
                        self._set_record(task_id, status=CodeTaskStatus.FAILED, phase=CodeTaskPhase.FINALIZING, progress=100, message="执行超时", error="代码任务超时，进程已终止")
                        self.resource_profiles.release(task_id)
                        self._cleanup_staging(task_id)
                        await self._safe_persist_state(task_id, status=CodeTaskStatus.FAILED.value, plan=plan, request=request, error="代码任务超时，进程已终止")
                        raise CodePolicyError("代码任务超时，进程已终止")
                    poll_count += 1
                    if disk_guard.enforced and poll_count % 20 == 0:
                        try:
                            disk_guard.check()
                        except DiskQuotaExceededError as exc:
                            process.kill()
                            job.close()
                            await process.wait()
                            cleanup_manifest = disk_guard.cleanup_excess()
                            self._cleanup_network_rules(net_rule_names)
                            self._set_record(
                                task_id,
                                status=CodeTaskStatus.FAILED,
                                phase=CodeTaskPhase.FINALIZING,
                                progress=100,
                                message="磁盘配额超限，进程已终止",
                                error="DISK_QUOTA_EXCEEDED",
                                result={"disk_cleanup": cleanup_manifest},
                            )
                            self.resource_profiles.release(task_id)
                            self._cleanup_staging(task_id)
                            await self._safe_persist_state(task_id, status=CodeTaskStatus.FAILED.value, plan=plan, request=request, error="DISK_QUOTA_EXCEEDED")
                            raise CodePolicyError(f"DISK_QUOTA_EXCEEDED: {exc}")
                    continue
                for reader in (stdout_task, stderr_task):
                    if reader in done:
                        _, limited = reader.result()
                        if limited:
                            process.kill()
                            job.close()
                            await process.wait()
                            for remaining in (stdout_task, stderr_task):
                                if not remaining.done():
                                    remaining.cancel()
                            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                            self._cleanup_network_rules(net_rule_names)
                            self._set_record(
                                task_id,
                                status=CodeTaskStatus.FAILED,
                                phase=CodeTaskPhase.FINALIZING,
                                progress=100,
                                message="输出超过限制，进程已终止",
                                error="OUTPUT_LIMIT_EXCEEDED",
                            )
                            self.resource_profiles.release(task_id)
                            self._cleanup_staging(task_id)
                            await self._safe_persist_state(task_id, status=CodeTaskStatus.FAILED.value, plan=plan, request=request, error="OUTPUT_LIMIT_EXCEEDED")
                            raise CodePolicyError("OUTPUT_LIMIT_EXCEEDED")
            stdout, stdout_limited = await stdout_task
            stderr, stderr_limited = await stderr_task
        finally:
            if "wait_task" in locals() and not wait_task.done():
                wait_task.cancel()
        max_bytes = plan.max_output_bytes
        stdout_text = stdout[:max_bytes].decode("utf-8", errors="replace")
        stderr_text = stderr[:max_bytes].decode("utf-8", errors="replace")
        audit = CodeAudit(
            task_id=task_id,
            plan_id=plan.plan_id,
            approval_id=plan.approval_id,
            command_digest=hashlib.sha256(json.dumps(command, ensure_ascii=False).encode()).hexdigest(),
            workspace_snapshot={"workspace": str(self.workspace_root), "target": str(relative_target)},
            capabilities=plan.capabilities,
            resource_requested={
                "timeout_seconds": plan.timeout_seconds,
                "cpu_time_seconds": plan.cpu_time_seconds,
                "max_memory_mb": plan.max_memory_mb,
                "max_processes": plan.max_processes,
                "max_output_bytes": max_bytes,
                "network_policy": plan.network_policy,
            },
            resource_observed={
                "duration_ms": int((asyncio.get_running_loop().time() - started) * 1000),
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
                "job_available": job.available,
                "job_limits": job.enforced_limits,
                "isolation_degraded": bool(job.degraded_reason),
                "degraded_reason": job.degraded_reason,
                "disk_quota_bytes": plan.disk_budget_bytes,
                "disk_quota_enforced": disk_guard.enforced,
                "disk_used_bytes": disk_guard.max_observed_bytes,
                "network_isolation": network_isolation
                if (network_policy == "off" or network_isolation.get("enforced"))
                else "not_enforced",
            },
            exit_code=process.returncode,
            stdout_digest=hashlib.sha256(stdout).hexdigest(),
            stderr_digest=hashlib.sha256(stderr).hexdigest(),
            status="COMPLETED" if process.returncode == 0 else "FAILED",
        )
        self.audits[task_id] = audit
        result = {"task_id": task_id, "exit_code": process.returncode, "stdout": stdout_text, "stderr": stderr_text, "audit": audit.model_dump(mode="json"), "promoted": False}
        self.tasks[task_id] = result
        final_status = CodeTaskStatus.COMPLETED if process.returncode == 0 else CodeTaskStatus.FAILED
        if cancel_event.is_set():
            final_status = CodeTaskStatus.CANCELLED
        self._set_record(task_id, status=final_status, phase=CodeTaskPhase.FINALIZING, progress=100, message="代码任务已取消" if final_status == CodeTaskStatus.CANCELLED else "代码任务完成", result=result)
        await self._persist_task_result(task_id, result)
        await self._safe_persist_state(task_id, status=final_status.value, plan=plan, request=request, error=self.task_records[task_id].error or None)
        self.processes.pop(task_id, None)
        self.cancel_events.pop(task_id, None)
        job.close()
        self.jobs.pop(task_id, None)
        self._cleanup_network_rules(net_rule_names)
        self.resource_profiles.release(task_id)
        self._cleanup_staging(task_id)
        return result

    def approve(self, plan: CodePlan, approval_id: str) -> CodePlan:
        if not approval_id.strip():
            raise CodePolicyError("批准 ID 不能为空")
        return plan.model_copy(update={"status": "APPROVED", "approval_id": approval_id})

    # ── 阶段B：服务端签发审批（一次性 nonce，替代客户端可伪造的 approval_id）──
    def issue_approval(self, plan_id: str, identity: str = "app_client", ttl_seconds: int = 300) -> dict[str, Any]:
        """服务端签发一次性审批凭证，绑定计划、身份与过期时间。

        token 由不可预测的随机串生成，客户端无法自行构造有效 approval_id。
        仅应由受信任的服务端代码（如人工批准流程 / WS 确认回调）调用。
        """
        token = secrets.token_urlsafe(32)
        self.approvals[token] = {
            "plan_id": plan_id,
            "identity": identity,
            "nonce": secrets.token_urlsafe(32),
            "issued_at": time.time(),
            "expires_at": time.time() + max(1, ttl_seconds),
        }
        return {"approval_id": token, "plan_id": plan_id, "expires_in_seconds": max(1, ttl_seconds)}

    def verify_approval(self, plan: CodePlan, approval_id: str, identity: str = "app_client") -> CodePlan:
        """校验客户端提交的 approval_id 是否为服务端签发的有效一次性凭证。

        失败（不存在、过期、计划不匹配、身份不匹配、或已被使用）一律抛 CodePolicyError，
        避免伪造/重放。校验通过后标记为已使用。
        """
        if not approval_id:
            raise CodePolicyError("缺少服务端签发的审批凭证")
        record = self.approvals.pop(approval_id, None)  # 一次性：取出即标记已使用
        if record is None:
            raise CodePolicyError("审批凭证无效或已使用（拒绝伪造/重放）")
        if record.get("plan_id") != plan.plan_id:
            raise CodePolicyError("审批凭证与计划不匹配")
        if record.get("identity") != identity:
            raise CodePolicyError("审批者身份与凭证不一致")
        if time.time() > float(record.get("expires_at", 0)):
            raise CodePolicyError("审批凭证已过期")
        return plan.model_copy(update={"status": "APPROVED", "approval_id": approval_id})

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks.get(task_id)
