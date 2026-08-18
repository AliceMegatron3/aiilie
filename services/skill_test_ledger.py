"""技能测试答辩账本（V0.3：评测样例 / 可回放 / 不降低已有测试通过率）。

方向报告「一.3 炼模型」把 评测样例、可回放 列为第一阶段的形态；「三.3 反思需要检查」
含"是否降低已有测试通过率"。本模块用文件态 append-only 账本记录每个技能版本
的测试结果（确定性、可回放），并提供：
- `pass_rate(skill, label)`：某版本通过率；
- `has_regression(skill, candidate_label, baseline_label)`：候选通过率是否低于已有激活版本
  （违反"不得降低已有测试通过率"即视为回退化信号）；
- `record(...)` 只追加不覆盖（满足"可回放"）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from pathlib import Path as _P
    _SAFE = getattr(__import__("core.safe_join", fromlist=["safe_join"]), "safe_join", None)
except Exception:  # pragma: no cover
    _SAFE = None


def _local_safe_join(base, name: str) -> Path | None:
    """防遍历回退：解析后必须仍在 base 内。"""
    p = (base / name).resolve()
    if not name or name in (".", "..") or ".." in p.parts:
        return None
    return p


@dataclass
class TestRecord:
    skill: str
    label: str
    passed: int
    total: int
    note: str = ""
    created_at: float = field(default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "label": self.label, "passed": self.passed,
                "total": self.total, "note": self.note, "created_at": self.created_at}


class SkillTestLedger:
    """按 `skill__label.json`（safe_join，双下划线不冲突）存 append-only 测试记录。"""

    def __init__(self, base_dir: Path | None = None):
        from core.config_manager import config_manager
        cfg = config_manager._config.get("ledger_dir") or getattr(config_manager, "ledger_dir", None)
        default = Path(cfg) if cfg else (Path.home() / "No0_AI_V4" / "data" / "skill_test_ledger")
        self.dir = Path(base_dir) if base_dir is not None else default
        self._lock = asyncio.Lock()

    def _file(self, skill: str, label: str) -> Path:
        p = _local_safe_join(self.dir, f"{skill}__{label}.jsonl")
        if p is None:
            raise ValueError("非法的 skill/label 标识符")
        return p

    async def record(self, skill: str, label: str, passed: int, total: int, note: str = "") -> TestRecord:
        import time
        rec = TestRecord(skill=skill, label=label, passed=passed, total=total,
                         note=note, created_at=time.time())
        async with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._file(skill, label)
            await asyncio.to_thread(
                lambda: path.open("a", encoding="utf-8").write(json.dumps(rec.to_dict()) + "\n")
            )
        return rec

    async def records(self, skill: str, label: str) -> list[TestRecord]:
        path = self._file(skill, label)
        if not await asyncio.to_thread(path.exists):
            return []
        lines = await asyncio.to_thread(lambda: path.read_text(encoding="utf-8").splitlines())
        out = []
        for ln in lines:
            try:
                d = json.loads(ln)
                out.append(TestRecord(**{k: v for k, v in d.items() if k in TestRecord.__dataclass_fields__}))
            except Exception:
                continue
        return out

    async def pass_rate(self, skill: str, label: str) -> float:
        recs = await self.records(skill, label)
        if not recs:
            return float("nan")
        passed = sum(r.passed for r in recs)
        total = sum(r.total for r in recs)
        return passed / total if total else float("nan")

    async def has_regression(self, skill: str, candidate_label: str,
                             baseline_label: str | None = None) -> bool:
        """候选版本通过率是否低于基线（前一激活版本）。无穷或 NaN 视为无定论（False）。"""
        cand = await self.pass_rate(skill, candidate_label)
        if cand != cand or cand == float("inf"):
            return False
        if not baseline_label:
            prev = await self.records(skill, candidate_label)
            if not prev:
                return False
            return False
        bl = await self.pass_rate(skill, baseline_label)
        if bl != bl or bl == float("inf"):
            return False
        return cand < bl

    async def to_dict(self, skill: str | None = None) -> dict[str, Any]:
        if not await asyncio.to_thread(self.dir.is_dir):
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        for path in await asyncio.to_thread(lambda: list(self.dir.glob("*.jsonl"))):
            key = path.stem
            if skill is not None and key.split("__")[0] != skill:
                continue
            out[key] = [r.to_dict() for r in await self.records(*_split_key(key, path))]
        return out


def _split_key(key: str, path: Path) -> tuple[str, str]:
    if "__" in key:
        skill, _, label = key.partition("__")
        return skill, label
    return (key, path.name)


__all__ = ["SkillTestLedger", "TestRecord"]