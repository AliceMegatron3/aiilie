import json
import logging

from fastapi import APIRouter, Depends, Request, HTTPException, status
from pydantic import BaseModel, Field
from core.plugin_manager import plugin_manager
from api.deps import get_llm_client, verify_token

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])


class PluginPlanRequest(BaseModel):
    caller_id: str = "unknown"
    caller_permission: int = Field(default=0, ge=0, le=6)
    task_id: str = ""
    project_id: str = ""
    book_id: str = ""
    plugin_id: str = ""
    task_description: str = ""
    available_assets: list = Field(default_factory=list)

@router.get("/plugins")
async def list_plugins():
    """补丁C扩展：列出所有已安装的插件"""
    return {"data": list(plugin_manager.loaded_plugins.values())}

@router.post("/plugins/install")
async def install_plugin():
    """插件安装尚未实现；不再伪造安装成功。"""
    logger.warning("收到插件安装请求，但安全安装器尚未启用")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="插件安装器未启用，未执行任何安装或权限变更。",
        headers={"X-Feature-Status": "not_implemented"},
    )


@router.post("/plugins/plan")
async def plan_plugin_call(
    payload: PluginPlanRequest,
    request: Request,
    llm_client=Depends(get_llm_client),
):
    """生成权限感知的插件调用计划，不执行插件。"""
    context = payload.model_dump()
    prompt = plugin_manager.render_call_plan_prompt(context)
    if llm_client is None:
        plan = plugin_manager.deterministic_call_plan(context)
    else:
        try:
            raw = await llm_client.generate_completion(prompt, temperature=0.1, max_tokens=1000)
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("插件规划模型返回非 JSON")
            plan = json.loads(cleaned[start : end + 1])
            if not isinstance(plan, dict):
                raise ValueError("插件规划结果不是对象")
        except Exception as exc:
            logger.warning("插件规划模型失败，改用安全拒绝: %s", exc)
            plan = {
                "decision": "DENY",
                "reason": "插件规划模型失败，未执行调用",
                "calls": [],
                "rejected_calls": [{"plugin_id": payload.plugin_id, "reason": "planner_failed"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
    # 模型只负责提出候选计划，最终决策必须经过确定性权限/schema 校验。
    plan = plugin_manager.validate_call_plan(context, plan)
    return {"status": "planned", "prompt": prompt, "plan": plan}
