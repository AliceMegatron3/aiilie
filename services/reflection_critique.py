"""反思学习 — 反思评审门（V0.3：反思需要检查清单，确定性）。

方向报告「三.3 反思阶段」要求候选进入作者审核前先检查：
    是否与已有技能冲突 / 是否改变适用边界 / 是否引入无来源结论 /
    是否降低已有测试通过率 / 是否只是重复表达 / 是否与项目级规则冲突。

本模块把这些检查固化为确定性（非 LLM）`ReflectionCritique`：
- 输入候选的 证据数、与既有最似度、冲突数、测试通过率(新/旧)、边界是否变化、项目规则是否合规；
- 逐项产出 `CritiqueCheck{pas}`，`approvable()` 要求全部通过才放行作者审核。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MIN_EVIDENCE = 1
DUPLICATE_SIMILARITY = 0.8


@dataclass
class CritiqueCheck:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class CritiqueReport:
    checks: list[CritiqueCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        return [c.check for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [{"check": c.check, "passed": c.passed, "detail": c.detail} for c in self.checks],
            "failed": self.failed_checks,
        }


def critique_candidate(
    *,
    claim: str,
    evidence_count: int = 0,
    existing_similarity: float = 0.0,
    existing_conflicts: int = 0,
    test_pass_rate: float = 1.0,
    prev_test_pass_rate: float = 1.0,
    boundary_changed: bool = False,
    project_rules_ok: bool = True,
) -> CritiqueReport:
    """按报告「反思需要检查」逐项判定候选。全通过才可供作者审核。"""
    checks: list[CritiqueCheck] = []
    claim = (claim or "").strip()

    checks.append(CritiqueCheck(
        "evidence_sourced",
        evidence_count >= MIN_EVIDENCE and bool(claim),
        f"证据数 {evidence_count}；无来源结论禁止进入正式",
    ))
    checks.append(CritiqueCheck(
        "not_duplicate",
        existing_similarity < DUPLICATE_SIMILARITY,
        f"与既有主张最似度 {existing_similarity:.2f}",
    ))
    checks.append(CritiqueCheck(
        "no_conflict", existing_conflicts == 0, f"冲突数 {existing_conflicts}"
    ))
    checks.append(CritiqueCheck(
        "tests_not_regressed",
        test_pass_rate >= prev_test_pass_rate,
        f"通过率 {test_pass_rate:.2f} vs 旧 {prev_test_pass_rate:.2f}",
    ))
    checks.append(CritiqueCheck(
        "boundary_unchanged", not boundary_changed, "不应悄悄改变技能适用边界"
    ))
    checks.append(CritiqueCheck(
        "respects_project_rules", project_rules_ok, "不得与项目级规则冲突"
    ))
    return CritiqueReport(checks=checks)


def critique_from_refine_detail(
    detail: Any,
    *,
    prev_test_pass_rate: float = 1.0,
    boundary_changed: bool = False,
    project_rules_ok: bool = True,
) -> CritiqueReport:
    """从 `knowledge_refinery` 的 `ClaimResult` 直接构造评审（复用其指标/证据/过测）。"""
    return critique_candidate(
        claim=getattr(detail, "claim", ""),
        evidence_count=getattr(detail, "evidence_count", 0),
        existing_similarity=float((getattr(detail, "metrics", {}) or {}).get("claim_repetition_ratio", 0.0)),
        existing_conflicts=int((getattr(detail, "metrics", {}) or {}).get("conflict_count", 0)),
        test_pass_rate=1.0 if getattr(detail, "tests_passed", False) else 0.0,
        prev_test_pass_rate=prev_test_pass_rate,
        boundary_changed=boundary_changed,
        project_rules_ok=project_rules_ok,
    )


__all__ = ["CritiqueCheck", "CritiqueReport", "critique_candidate", "critique_from_refine_detail"]