"""反思学习 — 版本门接入 SkillGovernance（V0.3 收口）。

方向报告「三.3 反思阶段」：技能不能被直接覆盖，须生成新版本
（`skill.foo@1.3.0-candidate`），**新版本通过测试后再激活，失败可以回滚**；
候选须经作者审核才影响正式技能。既有构件分工：

- `SkillVersionedSkill`：语义版本状态机（提出候选/记测试/过测才激活/可回滚，纯内存）；
- `SkillTestLedger`：文件态 append-only 测试答辩账本（可回放、检测通过率回退）；
- `SkillGovernance`：正式技能库的受控写入闸口（唯一落库入口、版本快照、审计、灰度）。

本模块把三者接到 SkillGovernance，补齐「候选 content 版本化落库 + 测试评估接入
+ 作者审核后 promote 走版本门」：

- `register_candidate`：把语义版本候选持久化进 SkillGovernance 缓冲池
  （artifact 携带 `semantic_version`/主张/证据数/条件/步骤，`dedup_key=语义版本`
  保证同内容不同版本是独立记录，可回放）；
- `evaluate_candidate`：跑冒烟测试 → 结果同步写入 SkillVersionedSkill.record_tests
  ＋ SkillTestLedger（append-only）＋ SkillGovernance.record_test_results（append-only）；
- `approve_and_promote`：作者 `manual_approve` 后走 `promote_through_gate`——
  须作者批准 **且** 已过测才版本化激活（fail-closed）；
- `run_version_gated_candidate`：上述三步的一站式端到端编排（供流水线/计划工具复用）。

验证：`tests/test_skill_version_gate.py`（R1 版本化落库 / R2 测试评估 / R3 版本门 promote
＋ 端到端全链路）。
"""
from __future__ import annotations

from typing import Any

from services.skill_evaluator import SmokeReport, run_smoke_tests
from services.skill_governance import CAND_AUTO_REJECTED, SkillGovernance
from services.skill_test_ledger import SkillTestLedger
from services.skill_versioning import SkillVersion, SkillVersionError, SkillVersionedSkill

DEFAULT_CONFIDENCE = 0.7


def candidate_prompt(
    *,
    claim: str,
    evidence_count: int = 0,
    conditions: list[str] | None = None,
    steps: list[str] | None = None,
) -> str:
    """由 主张/适用条件/可执行步骤/证据数 生成确定性技能候选内容。"""
    lines = [f"主张：{(claim or '').strip()}"]
    conditions = [c for c in (conditions or []) if (c or "").strip()]
    steps = [s for s in (steps or []) if (s or "").strip()]
    if conditions:
        lines.append("适用条件：" + "；".join(conditions))
    if steps:
        lines.append("可执行步骤：" + "；".join(steps))
    lines.append(f"证据数：{int(evidence_count)}")
    return "\n".join(lines)


def register_candidate(
    governance: SkillGovernance,
    skill: SkillVersionedSkill,
    candidate_label: str,
    *,
    claim: str,
    evidence_count: int = 0,
    conditions: list[str] | None = None,
    steps: list[str] | None = None,
    source_task: str = "reflection",
    source_reflection: str = "",
    confidence: float = DEFAULT_CONFIDENCE,
) -> str:
    """R1 候选 content 版本化落库：把语义版本候选持久化进 SkillGovernance。

    - 每个语义版本（`dedup_key=候选 label`）都是独立候选记录，同内容不同版本不冲突；
    - artifact 记录 semantic_version/claim/evidence_count/conditions/steps（可回放）；
    - 落库后立即跑规则初审（PENDING → AUTO_APPROVED / AUTO_REJECTED）。
    返回 governance candidate_id。
    """
    version = SkillVersion.parse(candidate_label)
    if not version.candidate:
        raise SkillVersionError("只能登记带 -candidate 的候选版本")
    prompt = candidate_prompt(
        claim=claim, evidence_count=evidence_count, conditions=conditions, steps=steps
    )
    candidate_id = governance.submit_candidate(
        version.name,
        prompt,
        confidence=float(confidence),
        source_task=source_task,
        source_reflection=source_reflection or candidate_label,
        artifact={
            "semantic_version": candidate_label,
            "skill_name": version.name,
            "claim": (claim or "").strip(),
            "evidence_count": int(evidence_count),
            "conditions": [c for c in (conditions or []) if (c or "").strip()],
            "steps": [s for s in (steps or []) if (s or "").strip()],
        },
        dedup_key=candidate_label,
    )
    governance.auto_review(candidate_id)
    return candidate_id


async def evaluate_candidate(
    governance: SkillGovernance,
    skill: SkillVersionedSkill,
    candidate_label: str,
    candidate_id: str | None = None,
    *,
    claim: str,
    evidence_count: int = 0,
    conditions: list[str] | None = None,
    steps: list[str] | None = None,
    ledger: SkillTestLedger | None = None,
    threshold: float = 1.0,
) -> SmokeReport:
    """R2 测试评估接入：跑冒烟测试，结果写入三处（状态机 + 账本 + Governance）。

    - `SkillVersionedSkill.record_tests(label, ok)`：版本门据此放行激活；
    - `SkillTestLedger.record`（append-only）：可回放、检测通过率回退；
    - `governance.record_test_results`（append-only）：作者批准后 promote_through_gate 依据。
    """
    report = run_smoke_tests(
        claim=claim, evidence_count=evidence_count, conditions=conditions, steps=steps
    )
    passed = report.ok(threshold)
    skill.record_tests(candidate_label, passed)
    if ledger is not None:
        await ledger.record(
            skill.name, candidate_label,
            passed=report.passed_count, total=len(report.cases),
            note=f"冒烟测试 threshold={threshold}",
        )
    if candidate_id is not None:
        governance.record_test_results(
            candidate_id,
            passed=passed,
            passed_count=report.passed_count,
            total_count=len(report.cases),
            note=f"冒烟测试 threshold={threshold}",
        )
    return report


def approve_and_promote(
    governance: SkillGovernance,
    candidate_id: str,
    *,
    reviewer: str = "author",
    note: str = "版本门：作者批准 + 已过测",
    skill: SkillVersionedSkill | None = None,
    candidate_label: str | None = None,
) -> dict[str, Any]:
    """R3 作者审核后 promote 走版本门：作者批准 → `promote_through_gate` 落地。

    版本门在 Governance 端强制「MANUAL_APPROVED 且 已过测」才版本化激活；
    未过测或未批准一律抛 `SkillGovernanceError`（fail-closed，激活版不受影响）。
    传入 `skill`/`candidate_label` 时，在 Governance 版本门通过后同步激活语义
    状态机（作者批准 → ACTIVE），保证持久层与运行时解析一致。
    """
    governance.manual_approve(candidate_id, reviewer=reviewer, note=note)
    version = governance.promote_through_gate(candidate_id, operator=reviewer)
    semantic_active: str | None = None
    if skill is not None and candidate_label is not None:
        semantic_active = skill.activate(candidate_label)
    return {
        "candidate_id": candidate_id,
        "version": version,
        "status": "PROMOTED",
        "semantic_active": semantic_active,
    }


async def run_version_gated_candidate(
    governance: SkillGovernance,
    skill: SkillVersionedSkill,
    *,
    claim: str,
    evidence_count: int = 0,
    conditions: list[str] | None = None,
    steps: list[str] | None = None,
    ledger: SkillTestLedger | None = None,
    reviewer: str = "author",
    source_task: str = "reflection",
) -> dict[str, Any]:
    """V0.3 端到端版本门循环：候选落库 → 测试评估 → 作者批准 → 版本门落地。

    返回含 governance candidate_id、评审结果与落地版本；候选未过测试时停在候选态，
    不激活（fail-closed）。
    """
    candidate_label = skill.propose_candidate("minor")
    candidate_id = register_candidate(
        governance, skill, candidate_label,
        claim=claim, evidence_count=evidence_count, conditions=conditions, steps=steps,
        source_task=source_task,
    )
    status = governance._get_candidate(candidate_id)["status"]
    if status == CAND_AUTO_REJECTED:
        return {
            "candidate_label": candidate_label,
            "candidate_id": candidate_id,
            "status": status,
            "promoted": False,
            "reason": "规则初审拦截",
        }
    report = await evaluate_candidate(
        governance, skill, candidate_label, candidate_id,
        claim=claim, evidence_count=evidence_count, conditions=conditions, steps=steps,
        ledger=ledger,
    )
    if not report.ok():
        return {
            "candidate_label": candidate_label,
            "candidate_id": candidate_id,
            "status": "TEST_FAILED",
            "promoted": False,
            "pass_rate": report.pass_rate,
            "reason": "候选未过冒烟测试，保持候选（不激活）",
        }
    outcome = approve_and_promote(
        governance, candidate_id, reviewer=reviewer,
        skill=skill, candidate_label=candidate_label,
    )
    outcome.update({
        "candidate_label": candidate_label,
        "promoted": True,
        "pass_rate": report.pass_rate,
        "semantic_active": skill.active_label(),
    })
    return outcome


__all__ = [
    "approve_and_promote",
    "candidate_prompt",
    "evaluate_candidate",
    "register_candidate",
    "run_version_gated_candidate",
]
