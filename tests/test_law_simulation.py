"""法则表达式与有限模拟测试。"""
from __future__ import annotations

import pytest

from models.ledger import LawRecord
from services.law_compiler import LawCompiler
from services.law_simulator import LawEventSimulator, LawSimulationError


def test_safe_law_expression_evaluation_rejects_calls():
    compiler = LawCompiler()
    law = LawRecord(
        law_id="x", name="x", expression="x + 2 >= 4",
        variables={"x": {"value": 2, "unit": "dimensionless"}},
    )
    assert compiler.evaluate(law)["valid"] is True
    bad = law.model_copy(update={"expression": "__import__('os').getcwd()"})
    assert compiler.evaluate(bad)["valid"] is False


def test_event_simulator_is_seeded_and_bounded():
    simulator = LawEventSimulator()
    payload = {"energy": 1}
    events = [{"name": "storm", "effects": {"energy": -1}}]
    first = simulator.run(payload, events, seed=7)
    second = simulator.run(payload, events, seed=7)
    assert first == second
    with pytest.raises(LawSimulationError):
        simulator.run(payload, events * 2, max_steps=1)
