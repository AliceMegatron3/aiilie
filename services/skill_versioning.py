"""反思学习 — 技能语义版本化（V0.3：过测才激活、可回滚、禁止直接覆盖）。

方向报告「三.3 反思阶段」：
    技能不能被直接覆盖，而应生成新版本：
        skill.foo@1.2.0            （当前激活）
        skill.foo@1.3.0-candidate  （候选，未激活）
    新版本通过测试后再激活，失败可以回滚。

本模块把该不变量固化为纯状态机：
- `propose_candidate()`：基于激活版生成 `@minor+1.0-candidate`，绝不覆盖激活版。
- `record_tests(label, passed)`：记录候选版本测试结果。
- `activate(label)`：**仅当候选已通过测试**才原子激活（剥离 `-candidate`），并把旧激活版投入回滚栈。
- `rollback()`：恢复上一激活版；`rollback_chain` 保留可追溯历史（可回放）。

不依赖数据库/LLM；治理持久化由上层 SkillGovernance 承接，此处只负责版本门。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


_SEMVER = re.compile(r"^(?P<name>.+?)@(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<cand>-candidate)?$")


class SkillVersionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillVersion:
    name: str
    major: int
    minor: int
    patch: int
    candidate: bool = False

    def label(self) -> str:
        base = f"{self.name}@{self.major}.{self.minor}.{self.patch}"
        return f"{base}-candidate" if self.candidate else base

    @staticmethod
    def parse(text: str) -> "SkillVersion":
        m = _SEMVER.fullmatch((text or "").strip())
        if not m:
            raise SkillVersionError(f"非法技能版本标识: {text!r}")
        return SkillVersion(
            name=m.group("name"),
            major=int(m.group("major")),
            minor=int(m.group("minor")),
            patch=int(m.group("patch")),
            candidate=bool(m.group("cand")),
        )


class SkillVersionedSkill:
    """单个技能的版本化状态机（激活版 + 候选版 + 回滚栈）。"""

    def __init__(self, name: str, initial: str = "1.0.0") -> None:
        start = SkillVersion.parse(f"{name}@{initial}") if re.match(r"^\d", initial) else SkillVersion.parse(initial)
        if start.candidate:
            raise SkillVersionError("初始版本必须是已激活版本（不能带 -candidate）")
        self.name = name
        self._active = start
        self._candidates: dict[str, bool] = {}   # label -> passed_tests?
        self._rollback: list[SkillVersion] = []  # 已激活版本历史（供回滚/回放）

    # ── 查询 ────────────────────────────────────────────────
    def active_label(self) -> str:
        return self._active.label()

    def candidate_labels(self) -> list[str]:
        return sorted(self._candidates)

    def is_active(self, version: SkillVersion) -> bool:
        return version == self._active and not version.candidate

    # ── 版本提出（永不覆盖激活版） ────────────────────────────
    def propose_candidate(self, bump: str = "minor") -> str:
        """基于当前激活版生成新候选 `@x.(y+1).0-candidate`（bump=minor）或 `@(x+1).0.0-candidate`（major）。"""
        if bump == "minor":
            cand = SkillVersion(self.name, self._active.major, self._active.minor + 1, 0, candidate=True)
        elif bump == "major":
            cand = SkillVersion(self.name, self._active.major + 1, 0, 0, candidate=True)
        else:
            raise SkillVersionError(f"不支持的版本递增: {bump!r}")
        label = cand.label()
        if label in self._candidates:
            raise SkillVersionError(f"候选版本已存在，不可重复提出: {label}")
        self._candidates[label] = False  # 待测试
        return label

    # ── 测试门 ──────────────────────────────────────────────
    def record_tests(self, candidate_label: str, passed: bool) -> None:
        if candidate_label not in self._candidates:
            raise SkillVersionError(f"非候选版本，无法记录测试: {candidate_label}")
        self._candidates[candidate_label] = bool(passed)

    def can_activate(self, candidate_label: str) -> bool:
        return self._candidates.get(candidate_label, False)

    # ── 激活（过测才激活） ────────────────────────────────────
    def activate(self, candidate_label: str) -> str:
        """候选版本通过测试后才允许激活；激活后剥离 -candidate，旧激活版入回滚栈。"""
        if candidate_label not in self._candidates:
            raise SkillVersionError(f"候选版本不存在: {candidate_label}")
        if not self._candidates[candidate_label]:
            raise SkillVersionError(f"候选版本尚未通过测试，禁止激活: {candidate_label}")
        next_active = SkillVersion.parse(candidate_label)
        self._rollback.append(self._active)
        self._active = SkillVersion(
            name=next_active.name,
            major=next_active.major,
            minor=next_active.minor,
            patch=next_active.patch,
            candidate=False,
        )
        self._candidates.pop(candidate_label, None)
        return self.active_label()

    # ── 回滚（失败可回滚） ───────────────────────────────────
    def rollback(self, *, n: int = 1) -> str:
        if n < 1:
            raise SkillVersionError("回滚步数必须 >= 1")
        if len(self._rollback) < n:
            raise SkillVersionError(f"回滚栈不足：需要 {n}，现有 {len(self._rollback)}")
        for _ in range(n):
            self._active = self._rollback.pop()
        return self.active_label()

    def rollback_chain(self) -> list[str]:
        """已激活版本历史（旧→新，不含当前激活版），可回放。"""
        return [v.label() for v in self._rollback]

    # ── 回归基线（Batch 4：从 active semantic version 自动产生） ──
    def regression_baseline(self) -> dict[str, str]:
        """基于当前激活语义版本自动产生回归基线参照。

        基线 key 完全由 `name + active_label` 确定性导出（稳定、可回放），
        供候选激活前/后做效果对比：激活版变化 → 基线随之推进为新的激活版本，
        保证回归对照始终指向"当前权威激活版"，而非历史瞬间。
        """
        return {
            "skill": self.name,
            "baseline_label": self.active_label(),
            "baseline_key": f"baseline:{self.name}:{self.active_label()}",
        }


__all__ = [
    "SkillVersion",
    "SkillVersionError",
    "SkillVersionedSkill",
]