"""技能测试账本测试（V0.3：可回放 / 不降低已有测试通过率）。"""
from __future__ import annotations

import pytest

from services.skill_test_ledger import SkillTestLedger


@pytest.mark.asyncio
async def test_record_and_pass_rate(tmp_path):
    ledger = SkillTestLedger(base_dir=tmp_path)
    await ledger.record("skill.foo", "1.2.0", passed=4, total=4)
    await ledger.record("skill.foo", "1.3.0-candidate", passed=3, total=4)
    assert await ledger.pass_rate("skill.foo", "1.2.0") == 1.0
    assert await ledger.pass_rate("skill.foo", "1.3.0-candidate") == 0.75
    # 只追加、可回放
    recs = await ledger.records("skill.foo", "1.3.0-candidate")
    assert len(recs) == 1 and recs[0].passed == 3


@pytest.mark.asyncio
async def test_regression_detects_pass_rate_drop(tmp_path):
    ledger = SkillTestLedger(base_dir=tmp_path)
    await ledger.record("skill.foo", "1.2.0", passed=4, total=4)
    await ledger.record("skill.foo", "1.3.0-candidate", passed=3, total=4)
    assert await ledger.has_regression("skill.foo", "1.3.0-candidate", "1.2.0") is True
    await ledger.record("skill.foo", "1.4.0-candidate", passed=4, total=4)
    assert await ledger.has_regression("skill.foo", "1.4.0-candidate", "1.2.0") is False


@pytest.mark.asyncio
async def test_append_only_and_serialization(tmp_path):
    ledger = SkillTestLedger(base_dir=tmp_path)
    await ledger.record("skill.bar", "1.0.0-candidate", passed=0, total=4, note="declined")
    await ledger.record("skill.bar", "1.0.0-candidate", passed=4, total=4, note="re-test")
    data = await ledger.to_dict(skill="skill.bar")
    assert len(data["skill.bar__1.0.0-candidate"]) == 2
    # 通过率体现重测后
    assert await ledger.pass_rate("skill.bar", "1.0.0-candidate") == 0.5