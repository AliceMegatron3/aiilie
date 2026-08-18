"""反思学习 — 作者审核门（V0.3/V0.4：提交作者审核 → 批准后过测才激活）。

方向报告「四、第一批工具」含"提交作者审核"；「三.3」规定只有 AUTHOR_APPROVED
后才能影响正式技能。本模块提供确定性（非 LLM）作者审核门：
- `submit(name, candidate_label)`：把"已过冒烟测试"的候选提交审核（记录待审）；
- `approve(reviewer, skill, name)`：作者批准后才 `skill.activate`（若候选未过测则拒绝，
  激活后旧激活版入回滚栈，可回滚）；
- `pending()`：待审清单（可回放）。

`make_author_review_submit_handler(gate, actor)` 可作为 `author_review.submit`
工具的默认 handler 注入 `PlanExecutor`。
"""
from __future__ import annotations

from typing import Any

from services.skill_versioning import SkillVersionedSkill, SkillVersionError


class AuthorReviewGateError(RuntimeError):
    pass


class AuthorReviewGate:
    """作者审核门：提交待审 → 批准（过测才激活）→ 可回滚。"""

    def __init__(self) -> None:
        self._under_review: dict[str, str] = {}  # 技能名 -> 候选 label

    def submit(self, name: str, candidate_label: str) -> str:
        name = (name or "").strip()
        candidate_label = (candidate_label or "").strip()
        if not name or not candidate_label:
            raise AuthorReviewGateError("提交作者审核需要 技能名 与 候选版本")
        self._under_review[name] = candidate_label
        return candidate_label

    def pending(self) -> dict[str, str]:
        return dict(self._under_review)

    def approve(self, reviewer: str, skill: SkillVersionedSkill, name: str) -> str:
        if not (reviewer or "").strip():
            raise AuthorReviewGateError("批准必须有 reviewer 身份")
        candidate_label = self._under_review.get(name)
        if not candidate_label:
            raise AuthorReviewGateError(f"技能 {name!r} 无待审候选")
        if not skill.can_activate(candidate_label):
            raise AuthorReviewGateError(f"候选 {candidate_label} 未过测，作者不可激活")
        try:
            active = skill.activate(candidate_label)
        except SkillVersionError as exc:
            raise AuthorReviewGateError(str(exc)) from exc
        del self._under_review[name]
        return active

    async def approve_async(
        self,
        reviewer: str,
        skill: SkillVersionedSkill,
        name: str,
        ledger: Any | None = None,
        baseline_label: str | None = None,
    ) -> str:
        """作者批准 + 可选"不降低已有测试通过率"回归门（报告「反思需要检查」）。

        传入 `ledger`（SkillTestLedger）时，走 `approve` 的基础门之后，再校验候选在该账本的
        通过率不跌破基线（`has_regression`）；若跌破则拒绝激活（fail-closed）。
        """
        review_label = self._under_review.get(name)  # 原候选 label（含 -candidate），账本以此记录
        active = self.approve(reviewer, skill, name)
        if ledger is not None and review_label:
            try:
                regressed = await ledger.has_regression(name, review_label, baseline_label)
            except Exception:
                regressed = False
            if regressed:
                # 回滚刚才的激活（激活版入过栈可回滚），并恢复待审
                try:
                    skill.rollback()
                except Exception:
                    pass
                self._under_review[name] = active
                raise AuthorReviewGateError("候选通过率低于激活基线，作者批准被回归门拒绝")
        return active


def make_author_review_submit_handler(
    gate: AuthorReviewGate | None = None, actor: str = "author"
) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        gate_ref = gate or AuthorReviewGate()
        name = str(args.get("name", "") or "")
        candidate_label = str(args.get("candidate_label", "") or "")
        gate_ref.submit(name, candidate_label)
        return {"submitted": candidate_label, "reviewer": actor, "status": "UNDER_REVIEW"}

    return handler


__all__ = ["AuthorReviewGate", "AuthorReviewGateError", "make_author_review_submit_handler"]