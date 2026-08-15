from fastapi import APIRouter, Depends
import logging
from core.plugin_manager import plugin_manager
from api.deps import verify_token

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])

@router.get("/plugins")
async def list_plugins():
    """补丁C扩展：列出所有已安装的插件"""
    return {"data": list(plugin_manager.loaded_plugins.values())}

@router.post("/plugins/install")
async def install_plugin():
    """补丁C扩展：模拟安装插件"""
    logger.info("📦 收到新插件安装请求...")
    return {"status": "success", "message": "Plugin installed successfully. Restart required."}
