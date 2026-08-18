"""有限、可复现的法则事件模拟器。

这是基础事件引擎，不是连续物理引擎；状态更新必须来自显式事件，且有步数/状态大小上限。
"""
from __future__ import annotations

import copy
import random
from typing import Any


class LawSimulationError(ValueError):
    pass


class LawEventSimulator:
    def run(
        self,
        initial_state: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        seed: int = 0,
        max_steps: int = 100,
        max_state_keys: int = 256,
    ) -> dict[str, Any]:
        if len(events) > max_steps:
            raise LawSimulationError("事件数量超过 max_steps")
        state = copy.deepcopy(initial_state)
        rng = random.Random(seed)
        trace: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            if not isinstance(event, dict) or not isinstance(event.get("effects", {}), dict):
                raise LawSimulationError(f"事件 {index} 格式无效")
            effects = event["effects"]
            before = copy.deepcopy(state)
            for key, change in effects.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise LawSimulationError(f"事件 {index} 状态键非法")
                if isinstance(change, (int, float)) and isinstance(state.get(key, 0), (int, float)):
                    state[key] = state.get(key, 0) + change
                else:
                    state[key] = copy.deepcopy(change)
            if len(state) > max_state_keys:
                raise LawSimulationError("状态变量数量超过限制")
            trace.append({
                "step": index,
                "event": event.get("name", f"event_{index}"),
                "before": before,
                "after": copy.deepcopy(state),
                "random_sample": rng.random(),
            })
        return {"seed": seed, "steps": len(trace), "final_state": state, "trace": trace}
