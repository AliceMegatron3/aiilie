"""契约统一（Batch 0）：统一核心契约类型 + 语义命名收敛。

覆盖：ApiEnvelope 包络/往返、TaskSubmission/TaskCheckpoint/TaskHandler/
TaskAuditEvent 校验与默认值，以及 services 分段引擎语义名 SegmentExecutionEngine。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.contract import (
    ApiEnvelope,
    TaskAuditEvent,
    TaskCheckpoint,
    TaskHandler,
    TaskSubmission,
)
from core.response import fail, ok
from models.task import TaskStatus


# ── ApiEnvelope ────────────────────────────────────────────────────
def test_envelope_defaults():
    env = ApiEnvelope.ok({"a": 1}, message="done")
    assert env.success is True
    assert env.data == {"a": 1}
    assert env.message == "done"
    assert env.error_code is None


def test_envelope_from_core_response_ok():
    env = ApiEnvelope.model_validate(ok([1, 2]))
    assert env.success is True
    assert env.data == [1, 2]
    assert env.error_code is None


def test_envelope_from_core_response_fail():
    env = ApiEnvelope.model_validate(fail("boom", error_code="MY_ERR", data={"k": "v"}))
    assert env.success is False
    assert env.message == "boom"
    assert env.error_code == "MY_ERR"
    assert env.data == {"k": "v"}


def test_envelope_fail_preserves_error_code():
    env = ApiEnvelope.fail("denied", error_code="FORBIDDEN")
    assert env.model_dump()["success"] is False
    assert env.model_dump()["error_code"] == "FORBIDDEN"


# ── TaskSubmission ─────────────────────────────────────────────────
def test_submission_defaults():
    s = TaskSubmission(task_id="task_1", task_type="quantize")
    assert s.priority == 4
    assert s.payload == {}
    assert s.idempotency_key is None


def test_submission_priority_bounds():
    with pytest.raises(ValidationError):
        TaskSubmission(task_id="t", task_type="x", priority=9)


# ── TaskCheckpoint ─────────────────────────────────────────────────
def test_checkpoint_defaults():
    cp = TaskCheckpoint(task_id="task_1")
    assert cp.status == TaskStatus.PENDING
    assert cp.segment_id is None
    assert cp.progress == {}


# ── TaskHandler ────────────────────────────────────────────────────
def test_handler_unavailable_blocks_complete():
    h = TaskHandler(task_type="quantize", name="quant", required=True, available=False)
    assert h.available is False
    assert h.required is True


def test_handler_rejects_empty_task_type():
    with pytest.raises(ValidationError):
        TaskHandler(task_type="", name="q")


# ── TaskAuditEvent ─────────────────────────────────────────────────
def test_audit_event_outcome_enum():
    TaskAuditEvent(event_id="e1", task_id="t1", actor="user:a", action="task.submit", outcome="success")
    with pytest.raises(ValidationError):
        TaskAuditEvent(
            event_id="e2", task_id="t1", actor="user:a",
            action="task.submit", outcome="not_a_valid_outcome",
        )


# ── 语义命名收敛 ──────────────────────────────────────────────────
def test_services_segment_engine_semantic_name():
    """services.task_manager 必须暴露 SegmentExecutionEngine 语义名。"""
    from services import task_manager as stm
    assert hasattr(stm, "SegmentExecutionEngine")
    assert stm.SegmentExecutionEngine is stm.TaskManager


def test_core_persistent_queue_semantic_name():
    """core.task_manager 必须是 PersistentTaskQueue（TaskManager 仅为兼容别名）。"""
    from core import task_manager as ctm
    assert ctm.PersistentTaskQueue is not None
    assert ctm.PersistentTaskQueue is ctm.TaskManager


# ── Batch 2：QuantizeTask/ReflectionTask 显式 provenance（不依赖 extra=allow）──

def test_quantize_task_explicit_provenance_fields():
    from models.task import QuantizeTask

    t = QuantizeTask(
        task_id="q1", book_id="b1", run_id="run-1",
        source_document_id="doc-x", parser="builtin.txt", model="rule",
    )
    d = t.model_dump()
    assert d["run_id"] == "run-1"
    assert d["source_document_id"] == "doc-x"
    assert d["parser"] == "builtin.txt"
    assert d["model"] == "rule"
    # 显式字段可被还原（不经 extra 透传）
    from core.task_manager import _parse_task_dict
    rt = _parse_task_dict(d)
    assert rt.run_id == "run-1"


def test_reflection_task_explicit_provenance_fields():
    from models.task import ReflectionTask

    t = ReflectionTask(
        task_id="r1", session_id="s1", run_id="run-2",
        source_snapshot="snap-1", parser="builtin", model="rule", input_hash="h-1",
    )
    d = t.model_dump()
    assert d["run_id"] == "run-2"
    assert d["source_snapshot"] == "snap-1"
    assert d["input_hash"] == "h-1"
    from core.task_manager import _parse_task_dict
    rt = _parse_task_dict(d)
    assert rt.parser == "builtin"
    assert rt.model == "rule"