"""契约统一（7.8）：后端统一响应包络 {success, data, message, error_code}。"""
from __future__ import annotations

from core.response import fail, ok


def test_ok_envelope():
    out = ok({"a": 1}, message="done")
    assert out == {"success": True, "data": {"a": 1}, "message": "done", "error_code": None}


def test_ok_default_message():
    out = ok([1, 2])
    assert out["success"] is True
    assert out["data"] == [1, 2]
    assert out["message"] == "success"
    assert out["error_code"] is None


def test_fail_envelope():
    out = fail("boom", error_code="MY_ERR", data={"k": "v"})
    assert out == {"success": False, "data": {"k": "v"}, "message": "boom", "error_code": "MY_ERR"}