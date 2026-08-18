"""白名单工具注册表测试（V0.4 受控执行：第一批工具 + 权限门）。"""
from __future__ import annotations

import pytest

from services.whitelisted_tools import (
    PermissionLevel,
    ToolNotAllowedError,
    ToolSpec,
    WhitelistedToolRegistry,
    assert_tools_allowed,
    build_report_first_batch,
    get_report_registry,
)


def test_report_first_batch_registered():
    reg = build_report_first_batch()
    allowed = set(reg.allowed_ids())
    for tool in (
        "project.query",
        "document.read",
        "library.search_cards",
        "library.quantize_start",
        "task.quantize_progress",
        "reflection.start",
        "reflection.view_candidates",
        "author_review.submit",
        "document.draft_create",
        "timeline.event_create",
        "knowledge.consistency_check",
    ):
        assert tool in allowed, tool


def test_validate_accepts_known_tools_by_permission():
    reg = build_report_first_batch()
    # 作者（GOVERN）可用全部白名单工具
    assert reg.validate(reg.allowed_ids(), PermissionLevel.GOVERN) == []
    # 只读调用方不能使用写权限工具
    rejected = reg.validate(["project.query", "library.quantize_start"], PermissionLevel.READ)
    assert rejected == ["library.quantize_start"]


def test_unknown_tool_rejected_fail_closed():
    reg = build_report_first_batch()
    rejected = reg.validate(["evil.tool", "project.query"], PermissionLevel.GOVERN)
    assert rejected == ["evil.tool"]
    with pytest.raises(ToolNotAllowedError):
        assert_tools_allowed(["evil.tool"], PermissionLevel.GOVERN)


def test_duplicate_register_rejected():
    reg = build_report_first_batch()
    with pytest.raises(ValueError):
        reg.register(ToolSpec("project.query", "重复", PermissionLevel.READ))


def test_assert_tools_allowed_passes_on_known():
    assert_tools_allowed(get_report_registry().allowed_ids(), PermissionLevel.GOVERN)