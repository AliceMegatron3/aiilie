"""Host-controlled public HTTPS fetching API for agent workflows."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_web_access_service, verify_token
from core.response import ok
from models.web_access import PublicFetchRequest
from services.web_access import WebAccessError, WebAccessService

router = APIRouter(prefix="/web", tags=["Web Access"], dependencies=[Depends(verify_token)])


@router.post("/fetch")
async def fetch_public_web(
    payload: PublicFetchRequest,
    service: WebAccessService = Depends(get_web_access_service),
) -> dict[str, Any]:
    try:
        result = await service.fetch_public(payload)
    except WebAccessError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ok(result.model_dump(mode="json"), message="公网内容抓取完成")
