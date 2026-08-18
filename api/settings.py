"""
api/settings.py — 系统设置与 LLM 配置管理
===================================================
提供对 config/llm_provider.yaml 文件的读取与热更新。
"""
from __future__ import annotations
import logging
from pathlib import Path
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from utils.resource_path import get_resource_path
from core.response import ok
from api.deps import verify_token
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["Settings"], dependencies=[Depends(verify_token)])
CONFIG_PATH = get_resource_path("config/llm_provider.yaml")
# ====== Pydantic Models ======
class CloudProviderConfig(BaseModel):
    api_base: str = Field(default="https://api.deepseek.com/v1")
    api_key: str = Field(default="")
    model_name: str = Field(default="deepseek-r1")
    enable_switch: bool = Field(default=False)
    request_timeout: int = Field(default=60)
    max_retry_times: int = Field(default=2)
class LocalProviderConfig(BaseModel):
    api_base: str = Field(default="http://127.0.0.1:11434")
    model_name: str = Field(default="qwen2.5:7b")
    enable_switch: bool = Field(default=True)
class LLMSettings(BaseModel):
    deepseek: CloudProviderConfig = Field(default_factory=CloudProviderConfig)
    ollama: LocalProviderConfig = Field(default_factory=LocalProviderConfig)
# ====== Helper Functions ======
def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        # 如果文件不存在，创建默认配置
        default_config = {
            "deepseek": {
                "api_base": "https://api.deepseek.com/v1",
                "api_key": "",
                "model_name": "deepseek-r1",
                "request_timeout": 60,
                "max_retry_times": 2,
                "enable_switch": False
            },
            "ollama": {
                "api_base": "http://127.0.0.1:11434",
                "model_name": "deepseek-r1:7b",
                "enable_switch": True
            }
        }
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f, allow_unicode=True, sort_keys=False)
        return default_config
        
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            
            # 确保含有 ollama 结构
            if "ollama" not in data:
                data["ollama"] = {
                    "api_base": "http://127.0.0.1:11434",
                    "model_name": "deepseek-r1:7b",
                    "enable_switch": True
                }
            return data
    except Exception as e:
        logger.error(f"读取 LLM 配置失败: {e}")
        raise HTTPException(status_code=500, detail="解析配置文件失败")
# ====== Endpoints ======
@router.get("/llm", summary="获取当前大模型配置")
async def get_llm_settings() -> dict:
    data = load_settings()
    # 返回配置时对 api_key 做脱敏处理，避免前端暴露完整密钥
    settings = LLMSettings(**data)
    if settings.deepseek.api_key:
        settings.deepseek.api_key = _mask_api_key(settings.deepseek.api_key)
    return ok(settings.model_dump(), message="success")
@router.post("/llm", summary="保存大模型配置")
async def save_llm_settings(
    settings: LLMSettings,
    request: Request,
):
    try:
        # ── 脱敏占位保护：若前端原样回传了脱敏后的 api_key（含 "..."），
        #    则视为用户未修改密钥，继续沿用磁盘上的真实密钥，防止覆盖丢失。 ──
        old_data = load_settings()
        old_key = str((old_data.get("deepseek") or {}).get("api_key", "") or "")
        new_key = str(settings.deepseek.api_key or "")
        if new_key and _looks_masked(new_key):
            settings.deepseek.api_key = old_key
            logger.info("检测到脱敏占位 api_key，保留原密钥不变。")

        data = settings.model_dump()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)

        # 配置热生效：通知全局配置管理器重新加载，
        # 使云端门控 is_cloud_enabled / 新 base_url 立即生效（无需重启）。
        from core.config_manager import config_manager
        config_manager.reload()
        # 同步刷新全局 LLM 适配器（通过 request.app.state 避免循环导入）。
        # 若密钥被移除（enable_switch=false 或 api_key 清空），则云端功能整体禁用。
        try:
            from utils.llm_adapter import DeepSeekClient
            app_state = request.app.state
            if config_manager.get_bool("llm_provider.deepseek.enable_switch", False) and config_manager.get_llm_api_key("deepseek"):
                app_state.llm_client = DeepSeekClient(
                    api_key=config_manager.get_llm_api_key("deepseek"),
                    api_base=config_manager.get_llm_base("deepseek", default="https://api.deepseek.com/v1"),
                    model_name=config_manager.get_llm_model("deepseek", default="deepseek-r1"),
                )
                app_state.cloud_enabled = True
            else:
                app_state.llm_client = None
                app_state.cloud_enabled = False
            logger.info("LLM 客户端已随配置热更新重建")
        except Exception as e:
            logger.warning("LLM 客户端热更新失败（不影响配置保存）: %s", e)
        return ok({"status": "ok"}, message="配置保存成功")
    except Exception as e:
        logger.error(f"保存 LLM 配置失败: {e}")
        raise HTTPException(status_code=500, detail="保存配置文件失败")

def _mask_api_key(key: str) -> str:
    """对 API Key 进行脱敏处理，仅显示前4位和后4位。"""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"

def _looks_masked(key: str) -> bool:
    """
    判断前端回传的 api_key 是否为脱敏占位值。
    - 后端 _mask_api_key 输出格式：{前4}...{后4} 或 ****
    - 真实 DeepSeek/OpenAI Key 通常 30+ 字符且不含 "..."
    """
    if not key:
        return False
    if key == "****":
        return True
    if "..." in key and len(key) <= 16:
        return True
    return False

# ====== Feature Settings Endpoint ======
# 阶段A：暴露前端 UI 门控所需的全部功能开关，来源统一为 config.yaml 的 feature 分支。
_FEATURE_KEYS = (
    "emotion_quantify_enable",
    "branch_version_enable",
    "novel_multi_agent_enable",
    "storyboard_enable",
    "tts_enable",
    "deep_thinking_enable",
    "timeline_enable",
)


class FeatureSettings(BaseModel):
    emotion_quantify_enable: bool = Field(default=False)
    branch_version_enable: bool = Field(default=False)
    novel_multi_agent_enable: bool = Field(default=False)
    storyboard_enable: bool = Field(default=True)
    tts_enable: bool = Field(default=True)
    deep_thinking_enable: bool = Field(default=True)
    timeline_enable: bool = Field(default=True)


@router.get("/features", summary="获取系统功能开关配置")
async def get_feature_settings() -> dict:
    from core.config_manager import config_manager

    settings = FeatureSettings(
        **{key: config_manager.get_bool(f"feature.{key}", False) for key in _FEATURE_KEYS}
    )
    return ok(settings.model_dump(), message="success")

@router.post("/features", summary="保存系统功能开关配置")
async def save_feature_settings(settings: FeatureSettings):
    try:
        from core.config_manager import config_manager
        
        main_path = get_resource_path("config/config.yaml")
        data = {}
        if main_path.exists():
            with open(main_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                
        if "feature" not in data:
            data["feature"] = {}

        # 阶段C：持久化全部 feature 开关（_FEATURE_KEYS），reload 后运行时 gate 立即生效。
        for key in _FEATURE_KEYS:
            value = getattr(settings, key, None)
            if value is not None:
                data["feature"][key] = bool(value)

        with open(main_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)

        config_manager.reload()
        return ok({"status": "ok"}, message="功能开关配置保存成功（运行时 gate 已即时生效，无需重启）")
    except Exception as e:
        logger.error(f"保存功能开关失败: {e}")
        raise HTTPException(status_code=500, detail="保存配置文件失败")