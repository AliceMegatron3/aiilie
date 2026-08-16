from fastapi import APIRouter
from api.experience import router as experience_router
from api.library import router as library_router
from api.llm_debug import router as llm_debug_router
from api.lockfield import router as lockfield_router
from api.models import router as models_router
from api.narrative import router as narrative_router
from api.orchestrator import router as orchestrator_router
from api.plugins import router as plugins_router
from api.projects import router as projects_router
from api.quantize_reflection import router as quantize_reflection_router
from api.reflection import router as reflection_router
from api.reflection_governance import router as reflection_governance_router
from api.resource import router as resource_router
from api.router_prompt import router as router_prompt_router
from api.router_session import router as router_session_router
from api.settings import router as settings_router
from api.sync import router as sync_router
from api.system import router as system_router
from api.task_api import router as task_api_router
from api.websocket import router as websocket_router
from api.workspace import router as workspace_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(experience_router)
api_router.include_router(library_router)
api_router.include_router(llm_debug_router)
api_router.include_router(lockfield_router)
api_router.include_router(models_router)
api_router.include_router(narrative_router)
api_router.include_router(orchestrator_router)
api_router.include_router(plugins_router)
api_router.include_router(projects_router)
api_router.include_router(quantize_reflection_router)
api_router.include_router(reflection_router)
api_router.include_router(reflection_governance_router)
api_router.include_router(resource_router)
api_router.include_router(router_prompt_router)
api_router.include_router(router_session_router)
api_router.include_router(settings_router)
api_router.include_router(sync_router)
api_router.include_router(system_router)
api_router.include_router(task_api_router)
api_router.include_router(websocket_router)
api_router.include_router(workspace_router)

# 补丁 G: Emotion Engine
from core.config_manager import config_manager
if config_manager.get("feature.emotion_quantify_enable", False):
    from api.router_emotion import router as emotion_router
    api_router.include_router(emotion_router)

# 补丁 1: 非线性叙事时间轴系统（feature.timeline_enable 门控）
if config_manager.get("feature.timeline_enable", False):
    from api.router_timeline import router as timeline_router
    api_router.include_router(timeline_router)

# 补丁 2: 沉浸式角色语调 TTS 与校对系统（feature.tts_enable 门控）
if config_manager.get("feature.tts_enable", False):
    from api.router_tts import router as tts_router
    api_router.include_router(tts_router)

# 补丁 4: 场景可视化分镜生成系统（feature.storyboard_enable 门控）
if config_manager.get("feature.storyboard_enable", False):
    from api.router_storyboard import router as storyboard_router
    api_router.include_router(storyboard_router)

# 批次 7: 深度思考与元认知扩展系统（feature.deep_thinking_enable 门控）
if config_manager.get("feature.deep_thinking_enable", False):
    from api.router_deep_think import router as deep_think_router
    api_router.include_router(deep_think_router)

# 第十部分: 多智能体小说创作自学习闭环（feature.novel_multi_agent_enable 门控）
if config_manager.get("feature.novel_multi_agent_enable", False):
    from api.novel_agent import router as novel_agent_router
    api_router.include_router(novel_agent_router)
