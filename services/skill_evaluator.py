"""反思学习 — 技能候选测试评估（V0.3：候选 → 测试 → 过测才激活 → 可回滚）。

方向报告「三.3」：新版本通过测试后再激活，失败可以回滚；「压力测试」是
候选进正式技能前的必经门。本模块提供**确定性**（非 LLM）测试评估：
- 内置冒烟测试：主张非空、证据数达标、适用条件齐备（若声明）等硬性门槛；
- `run_smoke_tests(payload)` 返回逐项 pass/fail 与通过率；
- 通过率 ≥ 阈值即 `SkillVersionedSkill.record_tests(label, True)` → 过门后可 `activate`；
  失败则保持候选（激活版不受影响，可后续回滚）。不依赖 LLM，结果可复现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.skill_versioning import SkillVersionedSkill, SkillVersionError

MIN_EVIDENCE_DEFAULT = 1
PASS_THRESHOLD_DEFAULT = 1.0


@dataclass
class SmokeCase:
    name: str
    passed: bool
    reason: str = ""


@dataclass
class SmokeReport:
    cases: list[SmokeCase] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def pass_rate(self) -> float:
        return (self.passed_count / len(self.cases)) if self.cases else 0.0

    def ok(self, threshold: float = PASS_THRESHOLD_DEFAULT) -> bool:
        return len(self.cases) > 0 and self.pass_rate >= threshold


def run_smoke_tests(
    *,
    claim: str,
    evidence_count: int = 0,
    conditions: list[str] | None = None,
    steps: list[str] | None = None,
    min_evidence: int = MIN_EVIDENCE_DEFAULT,
) -> SmokeReport:
    """候选技能冒烟测试：主张非空 + 证据数达标 + （若声明）适用条件/步骤齐备。"""
    cases: list[SmokeCase] = []
    claim = (claim or "").strip()
    cases.append(SmokeCase("claim_nonempty", bool(claim), "主张为空" if not claim else ""))
    cases.append(SmokeCase("evidence_sufficient", evidence_count >= min_evidence,
                           f"证据数 {evidence_count} < {min_evidence}" if evidence_count < min_evidence else ""))
    conditions = conditions or []
    if conditions:
        cases.append(SmokeCase("conditions_present",
                               all((c or "").strip() for c in conditions),
                               "存在空白适用条件" if any(not (c or "").strip() for c in conditions) else ""))
    steps = steps or []
    if steps:
        cases.append(SmokeCase("steps_present",
                               all((s or "").strip() for s in steps),
                               "存在空白可执行步骤" if any(not (s or "").strip() for s in steps) else ""))
    return SmokeReport(cases=cases)


def gate_candidate(
    skill: SkillVersionedSkill,
    candidate_label: str,
    *,
    claim: str,
    evidence_count: int = 0,
    conditions: list[str] | None = None,
    steps: list[str] | None = None,
    threshold: float = PASS_THRESHOLD_DEFAULT,
) -> bool:
    """跑冒烟测试；过测才 `record_tests(True)` 并尝试激活；失败保持候选。返回是否激活。"""
    report = run_smoke_tests(
        claim=claim, evidence_count=evidence_count, conditions=conditions, steps=steps
    )
    if not report.ok(threshold):
        skill.record_tests(candidate_label, False)
        return False
    # 过测 → 记录通过 → 过门可激活（不覆盖激活版）
    skill.record_tests(candidate_label, True)
    try:
        skill.activate(candidate_label)
        return True
    except SkillVersionError:
        return False


__all__ = ["SmokeCase", "SmokeReport", "gate_candidate", "run_smoke_tests"]