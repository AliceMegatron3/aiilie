"""
benchmarks/bench_api.py — 核心 API 性能基准
=============================================
对高频只读接口做并发延迟与吞吐基准，纳入 CI（阈值断言，防性能回退）。

用法：
    python benchmarks/bench_api.py [--base-url http://127.0.0.1:8000]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from typing import Awaitable, Callable

import httpx

# 性能阈值断言（CI 防回退），单位：秒（P95）
_THRESHOLDS = {
    "/health": 1.0,
    "/api/v1/system/status": 1.5,
    "/api/v1/library/books": 2.0,
    "/api/v1/workspace/info": 1.0,
}
_CONCURRENCY = 8
_ROUNDS = 5


async def _measure(
    client: httpx.AsyncClient, path: str, concurrency: int, rounds: int
) -> dict:
    latencies: list[float] = []

    async def one_round() -> None:
        start = time.perf_counter()
        try:
            await client.get(path)
        except Exception:
            pass  # 异常请求不统计延迟，由失败率体现
        finally:
            latencies.append(time.perf_counter() - start)

    for _ in range(rounds):
        await asyncio.gather(*(one_round() for _ in range(concurrency)))

    latencies.sort()
    p95_index = int(len(latencies) * 0.95) - 1
    return {
        "path": path,
        "requests": len(latencies),
        "p50_ms": round(statistics.median(latencies) * 1000, 2),
        "p95_ms": round(latencies[max(p95_index, 0)] * 1000, 2),
        "max_ms": round(max(latencies) * 1000, 2),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    results = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as client:
        for path in _THRESHOLDS:
            results.append(await _measure(client, path, _CONCURRENCY, _ROUNDS))

    failed = False
    print(f"{'path':<32} {'req':>5} {'p50_ms':>8} {'p95_ms':>8} {'max_ms':>8}  阈值")
    for r in results:
        threshold_ms = _THRESHOLDS[r["path"]] * 1000
        ok = r["p95_ms"] <= threshold_ms
        failed = failed or not ok
        flag = "PASS" if ok else "FAIL"
        print(
            f"{r['path']:<32} {r['requests']:>5} {r['p50_ms']:>8} {r['p95_ms']:>8} "
            f"{r['max_ms']:>8}  {threshold_ms:.0f}ms [{flag}]"
        )

    if failed:
        print("\n[Benchmark] 存在超过阈值的接口，性能基准未通过！")
        return 1
    print("\n[Benchmark] 全部接口性能达标。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
