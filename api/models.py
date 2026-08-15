from typing import Any
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import logging

from fastapi import Request

from api.deps import verify_token

def get_db_from_state(request: Request):
    return request.app.state.db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models", tags=["Model Management"], dependencies=[Depends(verify_token)])

class ModelCredentialCreate(BaseModel):
    name: str
    api_endpoint: str
    api_key: str = ""
    model_tags: list[str] = []

@router.get("/ollama", summary="自动嗅探本地 Ollama 模型")
async def get_ollama_models() -> dict[str, Any]:
    """嗅探本地 http://127.0.0.1:11434/api/tags，获取可用模型。"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            response.raise_for_status()
            data = response.json()
            models = data.get("models", [])
            # 格式化返回值
            result = []
            for m in models:
                result.append({
                    "id": m.get("name"),
                    "name": m.get("name"),
                    "type": "local",
                    "tags": ["quantize", "fast"] # 默认打上量化专属标签
                })
            return {"success": True, "data": result}
    except Exception as e:
        logger.warning("嗅探 Ollama 失败: %s", e)
        # 如果没有安装 Ollama 或未启动，返回空列表而不是报错
        return {"success": True, "data": []}

@router.post("/credentials", summary="新增 API 模型凭证")
async def add_model_credential(
    payload: ModelCredentialCreate,
    db=Depends(get_db_from_state)
) -> dict[str, Any]:
    """添加 API 密钥和地址，用于高级逻辑推理。"""
    try:
        await db.insert_model_credential(payload.model_dump())
        return {"success": True, "message": "凭证已保存"}
    except Exception as e:
        logger.error("保存凭证失败: %s", e)
        raise HTTPException(status_code=500, detail="保存凭证失败")

@router.get("/credentials", summary="获取所有已存模型凭证")
async def list_model_credentials(
    db=Depends(get_db_from_state)
) -> dict[str, Any]:
    """获取所有模型，前端可用此做下拉列表。"""
    try:
        creds = await db.get_all_model_credentials()
        return {"success": True, "data": creds}
    except Exception as e:
        raise HTTPException(status_code=500, detail="读取凭证失败")

@router.delete("/credentials/{cred_id}", summary="删除模型凭证")
async def delete_model_credential(
    cred_id: str,
    db=Depends(get_db_from_state)
) -> dict[str, Any]:
    """删除不需要的 API。"""
    try:
        await db.delete_model_credential(cred_id)
        return {"success": True, "message": "凭证已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="删除凭证失败")
