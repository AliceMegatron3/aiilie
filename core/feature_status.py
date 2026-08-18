"""统一的功能 DEGRADED / DISABLED 响应契约。

方向基准：未接入真实 provider 的能力（TTS 合成 / 分镜生图等）不得返回“普通成功”，
统一返回结构化 disabled 响应，前端据此明确呈现“未接入/已禁用”，而非误判成功。

Body:
    {"status": "DISABLED", "feature": "<feature_id>", "reason": "<code>"}
HTTP: 501 Not Implemented；Header: X-Feature-Status: disabled

说明：直接用 JSONResponse 返回而非 raise HTTPException——后者会被 main.py 的统一
异常处理器将 dict detail 压成字符串，丢失结构化字段。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def feature_disabled(
    feature: str,
    reason: str = "provider_not_configured",
    status_code: int = 501,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "DISABLED",
            "feature": feature,
            "reason": reason,
        },
        headers={"X-Feature-Status": "disabled"},
    )