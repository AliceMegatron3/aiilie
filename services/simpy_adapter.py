"""可选 SimPy 适配器；缺少依赖时返回明确状态。"""
from __future__ import annotations

from typing import Any


class SimpyUnavailableError(RuntimeError):
    pass


class SimpyLawAdapter:
    def available(self) -> bool:
        try:
            import simpy  # type: ignore  # noqa: F401
            return True
        except ImportError:
            return False

    def run_resource_processes(
        self,
        processes: list[dict[str, Any]],
        *,
        capacity: int = 1,
        until: float = 100.0,
    ) -> dict[str, Any]:
        """用 SimPy Resource 模拟有限容量资源竞争。"""
        try:
            import simpy  # type: ignore
        except ImportError as exc:
            raise SimpyUnavailableError("SimPy 未安装；无法运行资源竞争模拟") from exc
        if capacity < 1 or capacity > 256:
            raise ValueError("capacity 必须在 1 到 256 之间")
        if until <= 0 or until > 100000:
            raise ValueError("until 必须在 0 到 100000 之间")
        env = simpy.Environment()
        resource = simpy.Resource(env, capacity=capacity)
        events: list[dict[str, Any]] = []

        def process(spec: dict[str, Any]):
            name = str(spec.get("name", "process"))[:128]
            arrival = max(float(spec.get("arrival", 0)), 0)
            duration = float(spec.get("duration", 1))
            if duration <= 0 or duration > until:
                raise ValueError("duration 必须大于 0 且不超过 until")
            yield env.timeout(arrival)
            requested_at = env.now
            with resource.request() as request:
                yield request
                started_at = env.now
                yield env.timeout(duration)
                events.append({
                    "name": name,
                    "requested_at": requested_at,
                    "started_at": started_at,
                    "completed_at": env.now,
                    "wait": started_at - requested_at,
                })

        for spec in processes[:1000]:
            env.process(process(spec))
        env.run(until=until)
        return {"until": until, "capacity": capacity, "events": events}

    def run_processes(self, processes: list[dict[str, Any]], until: float = 100.0) -> dict[str, Any]:
        try:
            import simpy  # type: ignore
        except ImportError as exc:
            raise SimpyUnavailableError("SimPy 未安装；请使用基础 LawEventSimulator 或安装 simpy") from exc
        if until <= 0 or until > 100000:
            raise ValueError("until 必须在 0 到 100000 之间")
        env = simpy.Environment()
        events: list[dict[str, Any]] = []

        def process(spec: dict[str, Any]):
            name = str(spec.get("name", "process"))[:128]
            delay = float(spec.get("delay", 1.0))
            if delay <= 0:
                raise ValueError("process delay 必须大于 0")
            yield env.timeout(delay)
            events.append({"time": env.now, "name": name})

        for spec in processes[:1000]:
            env.process(process(spec))
        env.run(until=until)
        return {"until": until, "events": events}
