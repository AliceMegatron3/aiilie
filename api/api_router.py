from fastapi import APIRouter
from api.behavior_plugins import router as behavior_plugins_router
from api.agent_plans import router as agent_plans_router
from api.code import router as code_router
from api.ensemble import router as ensemble_router
from api.experience import router as experience_router
from api.library import router as library_router
from api.ledger import router as ledger_router
from api.knowledge import router as knowledge_router
from api.llm_debug import router as llm_debug_router
from api.lockfield import router as lockfield_router
from api.models import router as models_router
from api.narrative import router as narrative_router
from api.orchestrator import router as orchestrator_router
from api.plugins import router as plugins_router
from api.poetry import router as poetry_router
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
from api.web import router as web_router
from api.workspace import router as workspace_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(behavior_plugins_router)
api_router.include_router(agent_plans_router)
api_router.include_router(code_router)
api_router.include_router(ensemble_router)
api_router.include_router(experience_router)
api_router.include_router(library_router)
api_router.include_router(ledger_router)
api_router.include_router(knowledge_router)
api_router.include_router(llm_debug_router)
api_router.include_router(lockfield_router)
api_router.include_router(models_router)
api_router.include_router(narrative_router)
api_router.include_router(orchestrator_router)
api_router.include_router(plugins_router)
api_router.include_router(poetry_router)
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
api_router.include_router(web_router)
api_router.include_router(workspace_router)

# 阶段C：Feature Flag 统一为「运行时 gate」——路由始终挂载，由各 handler 内的
# _require_feature() 在请求时读取 config 裁决。这样开关可在运行时热切换、无需重启。
# 注意：这些开关若在关闭态被调用，返回明确的 disabled（400/403），而非 404。

# 补丁 G: Emotion Engine（handler 内 get_engine_services 运行时 403 门控）
from api.router_emotion import router as emotion_router
api_router.include_router(emotion_router)

# 补丁 1: 非线性叙事时间轴系统（router_timeline._require_feature 运行时 400 门控）
from api.router_timeline import router as timeline_router
api_router.include_router(timeline_router)

# 补丁 2: 沉浸式角色语调 TTS（router_tts._require_feature 运行时 400 门控）
from api.router_tts import router as tts_router
api_router.include_router(tts_router)

# 补丁 4: 场景可视化分镜生成系统（router_storyboard._require_feature 运行时 400 门控）
from api.router_storyboard import router as storyboard_router
api_router.include_router(storyboard_router)

# 批次 7: 深度思考与元认知扩展系统（router_deep_think._require_feature 运行时 400 门控）
from api.router_deep_think import router as deep_think_router
api_router.include_router(deep_think_router)

# 第十部分: 多智能体小说创作自学习闭环（api.novel_agent 路由级运行时 400 门控）
from api.novel_agent import router as novel_agent_router
api_router.include_router(novel_agent_router)
