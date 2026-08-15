"""No.0 AI V4.0 FastAPI 应用入口。"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from core.bootstrap import create_lifespan
from core.config_manager import config_manager
from core.exceptions import AppError
from core.logger import setup_global_logger
from core.rate_limiter import TokenBucketRateLimiter
from core.response import fail

setup_global_logger()
from api.api_router import api_router  # noqa: E402

VERSION = "4.0.0"


def _runtime_value(name: str, config_key: str, default: Any, cast: type) -> Any:
    raw = os.environ.get(name, config_manager.get(config_key, default))
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def _register_exception_handlers(app: FastAPI) -> None:
    def _with_legacy_detail(content: dict[str, Any], detail: Any) -> dict[str, Any]:
        """在统一响应外保留 FastAPI v1 客户端依赖的 detail 字段。"""
        content["detail"] = detail
        return content

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        content = fail(exc.message, error_code=exc.error_code, data=exc.details)
        return JSONResponse(status_code=exc.status_code, content=_with_legacy_detail(content, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "HTTP 请求失败"
        content = fail(detail, error_code=f"HTTP_{exc.status_code}")
        return JSONResponse(status_code=exc.status_code, content=_with_legacy_detail(content, detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=fail("请求参数校验失败", error_code="VALIDATION_ERROR", data=exc.errors()))

    @app.exception_handler(Exception)
    async def unknown_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("ai_v4_main").exception("未处理的服务异常: %s", exc)
        return JSONResponse(status_code=500, content=fail("服务器内部故障，请查看后台日志", error_code="INTERNAL_ERROR"))


def create_app() -> FastAPI:
    app = FastAPI(title="No.0 AI V4.0", version=VERSION, lifespan=create_lifespan())
    _register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Token"],
    )
    app.add_middleware(TokenBucketRateLimiter,
                       rate=float(config_manager.get("rate_limiter.rate", 50) or 50),
                       capacity=int(config_manager.get("rate_limiter.capacity", 100) or 100))
    app.include_router(api_router)

    @app.get("/health", tags=["System"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION, "timestamp": datetime.now(timezone.utc).isoformat()}

    frontend_dir = Path(__file__).resolve().parent / "frontend" / "dist"
    if frontend_dir.is_dir() and (frontend_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=str(_runtime_value("AIILIE_SERVER_HOST", "server.host", "127.0.0.1", str)),
                port=int(_runtime_value("AIILIE_SERVER_PORT", "server.port", 8000, int)))