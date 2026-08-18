"""L0-L2 代码执行域安全回归测试。"""
from __future__ import annotations

import pytest

from models.code_execution import CodeTaskRequest
from models.task import CodeExecutionTask
from services.code_execution import CodeExecutionService, CodePolicyError


@pytest.mark.asyncio
async def test_code_plan_and_approved_compileall(tmp_path, monkeypatch):
    service = CodeExecutionService()
    service.workspace_root = tmp_path.resolve()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    request = CodeTaskRequest(command="compileall", target="sample.py")
    plan = service.plan(request)
    assert plan.requires_approval is True
    with pytest.raises(CodePolicyError, match="批准"):
        await service.execute(request, plan)
    approved = service.approve(plan, "approval-test")
    result = await service.execute(request, approved)
    assert result["exit_code"] == 0
    assert result["promoted"] is False
    assert result["audit"]["network_policy"] == "deny"


def test_code_stage_promote_and_rollback_are_approval_bound(tmp_path):
    service = CodeExecutionService()
    service.workspace_root = tmp_path.resolve()
    file = tmp_path / "sample.py"
    file.write_text("value = 1\n", encoding="utf-8")
    staged = service.stage_file("sample.py", "value = 2\n")
    assert file.read_text(encoding="utf-8") == "value = 1\n"
    promoted = service.promote(staged["change_id"], "approval-promote")
    assert promoted["status"] == "PROMOTED"
    assert file.read_text(encoding="utf-8") == "value = 2\n"
    rolled = service.rollback(staged["change_id"], "approval-rollback")
    assert rolled["status"] == "ROLLED_BACK"
    assert file.read_text(encoding="utf-8") == "value = 1\n"


def test_code_snapshot_and_diff_are_read_only(tmp_path):
    service = CodeExecutionService()
    service.workspace_root = tmp_path.resolve()
    file = tmp_path / "sample.py"
    file.write_text("value = 1\n", encoding="utf-8")
    snapshot = service.snapshot(".")
    assert snapshot["files"]["sample.py"]
    diff = service.diff("sample.py", "value = 2\n")
    assert diff["changed"] is True
    assert file.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.asyncio
async def test_cancel_unknown_code_task_is_rejected(tmp_path):
    service = CodeExecutionService()
    with pytest.raises(CodePolicyError, match="不存在"):
        await service.cancel("missing")


    service = CodeExecutionService()
    service.workspace_root = tmp_path.resolve()
    with pytest.raises(CodePolicyError):
        service.plan(CodeTaskRequest(command="pytest", target="C:/Windows/System32"))


@pytest.mark.asyncio
async def test_output_limit_stops_process_and_records_failure(tmp_path):
    service = CodeExecutionService()
    service.workspace_root = tmp_path.resolve()
    source = tmp_path / "test_noisy.py"
    source.write_text("def test_noisy():\n    print('x' * 10000)\n", encoding="utf-8")
    request = CodeTaskRequest(command="pytest", target="test_noisy.py", max_output_bytes=1024)
    plan = service.approve(service.plan(request), "approval-output-limit")

    with pytest.raises(CodePolicyError, match="OUTPUT_LIMIT_EXCEEDED"):
        await service.execute(request, plan, task_id="code-output-limit")

    record = service.get_task_record("code-output-limit")
    assert record["status"] == "FAILED"
    assert record["error"] == "OUTPUT_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_code_result_and_audit_persist_to_task_payload(tmp_path, tmp_db):
    service = CodeExecutionService(db=tmp_db)
    service.workspace_root = tmp_path.resolve()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    request = CodeTaskRequest(command="compileall", target="sample.py")
    plan = service.approve(service.plan(request), "approval-persist")
    task = CodeExecutionTask(
        task_id="code-persist",
        raw_command="code compileall sample.py",
        request_payload=request.model_dump(mode="json"),
        plan_payload=plan.model_dump(mode="json"),
    )
    await tmp_db.insert_task(task)

    result = await service.execute(request, plan, task_id=task.task_id)

    persisted = await tmp_db.get_task(task.task_id)
    import json
    payload = json.loads(persisted["task_payload"])
    assert payload["code_execution"]["result"]["exit_code"] == 0
    assert payload["code_execution"]["audit"]["approval_id"] == "approval-persist"
    assert result["audit"]["approval_id"] == "approval-persist"


@pytest.mark.asyncio
async def test_disk_quota_hard_limit_fails_task(tmp_path, monkeypatch):
    """磁盘硬配额：staging 镜像超过配额时任务以 DISK_QUOTA_EXCEEDED 失败并保留现场快照。"""
    from services.resource_profile import ResolvedResourceProfile

    service = CodeExecutionService()
    service.workspace_root = tmp_path.resolve()
    source = tmp_path / "big.py"
    source.write_text("#" + "y" * (1024 * 1024) + "\n", encoding="utf-8")
    tiny = ResolvedResourceProfile(
        name="low", cpu_cores=1, memory_mb=128, disk_bytes=512,
        max_processes=4, timeout_seconds=300, cpu_time_seconds=300,
        enforcement="best_effort",
    )
    monkeypatch.setattr(service.resource_profiles, "resolve", lambda *a, **k: tiny)
    request = CodeTaskRequest(command="compileall", target="big.py")
    plan = service.approve(service.plan(request), "approval-disk-quota")
    assert plan.disk_budget_bytes == 512

    with pytest.raises(CodePolicyError, match="DISK_QUOTA_EXCEEDED"):
        await service.execute(request, plan, task_id="code-disk-quota")

    record = service.get_task_record("code-disk-quota")
    assert record["status"] == "FAILED"
    assert record["error"] == "DISK_QUOTA_EXCEEDED"
    assert record["result"]["disk_cleanup"]["used_before"] > 0


@pytest.mark.asyncio
async def test_code_task_state_persisted_and_recovered(tmp_path, tmp_db):
    """任务状态持久化：生命周期节点写入 code_execution_tasks，RUNNING 遗留可恢复。"""
    service = CodeExecutionService(db=tmp_db)
    service.workspace_root = tmp_path.resolve()
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    request = CodeTaskRequest(command="compileall", target="sample.py")
    plan = service.approve(service.plan(request), "approval-persist-state")

    result = await service.execute(request, plan, task_id="code-persist-state")
    assert result["exit_code"] == 0

    cursor = await tmp_db.conn.execute(
        "SELECT status, plan, request_payload FROM code_execution_tasks WHERE task_id=?",
        ("code-persist-state",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "COMPLETED"
    assert row[1]  # plan JSON
    assert row[2]  # request_payload JSON

    # 模拟上次进程中断遗留 RUNNING → recover 恢复为 FAILED(INTERRUPTED_RECOVERED)
    await tmp_db.execute_write(
        "UPDATE code_execution_tasks SET status='RUNNING' WHERE task_id=?",
        ("code-persist-state",),
    )
    recovered = await service.recover_interrupted_code_tasks()
    assert recovered == 1
    cursor = await tmp_db.conn.execute(
        "SELECT status, error FROM code_execution_tasks WHERE task_id=?",
        ("code-persist-state",),
    )
    row = await cursor.fetchone()
    assert row[0] == "FAILED"
    assert row[1] == "INTERRUPTED_RECOVERED"
