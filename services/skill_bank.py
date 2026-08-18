"""反思学习 — 技能版本库（V0.3/V0.4：候选技能的集中登记与查看）。

方向报告「四、第一批工具」含"查看候选技能"。本模块提供 `SkillBank`：按技能名维护
`SkillVersionedSkill`，聚合"激活版"与"未激活候选版"，供 `reflection.view_candidates`
工具读取。纯内存、确定性。
"""
from __future__ import annotations

from typing import Any

from services.skill_versioning import SkillVersionedSkill


class SkillBank:
    """按技能名集中登记 `SkillVersionedSkill`，暴露候选与激活版视图。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillVersionedSkill] = {}

    def get(self, name: str, initial: str = "1.0.0") -> SkillVersionedSkill:
        """取或按初始版本创建技能（首次创建即有一个激活版）。"""
        skill = self._skills.get(name)
        if skill is None:
            skill = SkillVersionedSkill(name, initial)
            self._skills[name] = skill
        return skill

    def names(self) -> list[str]:
        return sorted(self._skills)

    def candidates(self) -> dict[str, list[str]]:
        """技能名 → 未激活候选版本 label 列表。"""
        return {
            name: skill.candidate_labels()
            for name, skill in sorted(self._skills.items())
        }

    def active_versions(self) -> dict[str, str]:
        return {name: skill.active_label() for name, skill in sorted(self._skills.items())}

    def summary(self) -> dict[str, Any]:
        return {
            "skills": [
                {
                    "name": name,
                    "active": skill.active_label(),
                    "candidates": skill.candidate_labels(),
                }
                for name, skill in sorted(self._skills.items())
            ]
        }


__all__ = ["SkillBank"]