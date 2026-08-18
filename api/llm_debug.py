"""
api/llm_debug.py — LLM 模型通道调试网关
===================================================
专用于隔离测试大语言模型连通性与基础推理。
通过独立前缀 /api/v1/llm/deepseek 挂载，不与主业务逻辑路由发生任何耦合。
"""
from __future__ import annotations
import logging
from pathlib import Path
import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core.path_resolver import get_app_data_dir
from utils.llm_adapter import DeepSeekClient
from utils.resource_path import get_resource_path
from core.response import ok
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/llm/deepseek", tags=["LLM Debug"], dependencies=[Depends(verify_token)])
class DebugPromptRequest(BaseModel):
    prompt: str
    temperature: float = 0.7
def load_deepseek_config() -> dict:
    """热加载配置文件获取当前配置。"""
    # 使用 get_resource_path 确保打包后也能找到配置文件
    config_path = get_resource_path("config/llm_provider.yaml")
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="未找到配置文件 config/llm_provider.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("deepseek", {})
    except Exception as e:
        logger.error("加载 DeepSeek 配置失败: %s", e)
        raise HTTPException(status_code=500, detail="解析配置文件失败")
@router.get("/ping", summary="测试 DeepSeek 配置连通性")
async def ping_deepseek():
    """仅读取配置并检查开关是否开启、密钥是否填写。"""
    config = load_deepseek_config()
    if not config:
        return ok({"status": "error", "message": "配置节点 deepseek 不存在"}, message="deepseek 配置缺失")

    return ok(
        {
            "status": "ok",
            "api_base": config.get("api_base"),
            "model_name": config.get("model_name"),
            "is_enabled": config.get("enable_switch", False),
            "has_api_key": bool(config.get("api_key")),
        },
        message="success",
    )
@router.post("/chat", summary="直接调试 DeepSeek 推理能力")
async def debug_chat(request: DebugPromptRequest):
    """
    实例化 DeepSeekClient 并发起单轮请求，验证参数有效性与连通性。
    该接口不受业务上下游限制。
    """
    config = load_deepseek_config()
    api_key = config.get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 api_key，无法发起请求")
        
    client = DeepSeekClient(
        api_key=api_key,
        api_base=config.get("api_base", "https://api.deepseek.com/v1"),
        model_name=config.get("model_name", "deepseek-r1"),
        request_timeout=config.get("request_timeout", 60),
        max_retry_times=config.get("max_retry_times", 2)
    )
    
    try:
        reply = await client.generate_completion(request.prompt, temperature=request.temperature)
        return ok({"response": reply}, message="success")
    except Exception as e:
        logger.error("DeepSeek 调试调用失败: %s", e)
        from core.errors import http_error
        raise http_error(502, "LLM_DEBUG_CALL_FAILED")