import json
import logging

from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from core.plugin_manager import plugin_manager
from core.feature_status import feature_disabled
from api.deps import get_llm_client, verify_token

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])


class PluginResolveRequest(BaseModel):
    requested: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    caller_capabilities: list[str] = Field(default_factory=list)
    budget: dict[str, int] = Field(default_factory=dict)


class PluginPlanRequest(BaseModel):
    caller_id: str = "unknown"
    caller_permission: int = Field(default=0, ge=0, le=6)
    task_id: str = ""
    project_id: str = ""
    book_id: str = ""
    plugin_id: str = ""
    task_description: str = ""
    available_assets: list = Field(default_factory=list)


class PluginInstallRequest(BaseModel):
    source_path: str = Field(..., description="插件本地目录或 zip 包路径")


class PluginTrustRequest(BaseModel):
    operator: str = Field(default="author", min_length=1, max_length=128)
    signer: str | None = Field(default=None, max_length=256)


class PluginTrustPolicyRequest(BaseModel):
    allowed_signers: list[str] = Field(default_factory=list)
    require_signer: bool = False
    require_cosign: bool = False

@router.get("/plugins")
async def list_plugins():
    """列出所有已安装插件（含生命周期状态）。"""
    return {"data": plugin_manager.list_installed_plugins()}


@router.get("/plugins/capabilities")
async def list_plugin_capabilities():
    """列出能力、依赖和信任状态；只读，不执行插件。"""
    return {"data": plugin_manager.capability_catalog()}


@router.post("/plugins/resolve")
async def resolve_plugin_capabilities(payload: PluginResolveRequest):
    """解析插件依赖、冲突、能力和预算；只返回计划报告。"""
    return {"data": plugin_manager.resolve_capabilities(**payload.model_dump())}

@router.post("/plugins/install")
async def install_plugin(payload: PluginInstallRequest | None = None):
    """从本地目录或 zip 包安装插件；空请求保留旧的未实现兼容响应。"""
    if payload is None:
        return JSONResponse(
            status_code=501,
            headers={"X-Feature-Status": "not_implemented"},
            content={"detail": "未执行任何安装：请提供受控插件目录或 zip 包路径"},
        )
    if not payload.source_path:
        raise HTTPException(status_code=400, detail="source_path 不能为空")
    try:
        result = plugin_manager.install_plugin(payload.source_path)
    except Exception as e:
        logger.error("插件安装失败: %s", e)
        result = {"success": False, "message": f"插件安装异常: {e}"}
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "插件安装失败"))
    return {"success": True, "data": result}

@router.get("/plugins/trust-policy")
async def get_plugin_trust_policy():
    return {"success": True, "data": plugin_manager.get_trust_policy()}


@router.put("/plugins/trust-policy")
async def set_plugin_trust_policy(payload: PluginTrustPolicyRequest):
    try:
        policy = plugin_manager.set_trust_policy(**payload.model_dump())
        return {"success": True, "data": policy}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/plugins/{plugin_id}/trust-report")
async def plugin_trust_report(plugin_id: str):
    return {"success": True, "data": plugin_manager.trust_report(plugin_id)}


@router.post("/plugins/{plugin_id}/approve")
async def approve_plugin(plugin_id: str, payload: PluginTrustRequest):
    try:
        return {"success": True, "data": plugin_manager.approve_plugin(plugin_id, payload.operator, payload.signer)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plugins/{plugin_id}/revoke")
async def revoke_plugin(plugin_id: str, payload: PluginTrustRequest):
    return {"success": True, "data": plugin_manager.revoke_plugin(plugin_id, payload.operator)}


@router.get("/plugins/{plugin_id}/audit")
async def plugin_audit_trail(plugin_id: str, limit: int = 500):
    """读取某插件的信任/撤销审计轨（最新在前，append-only 只追加不删除）。"""
    if limit < 1 or limit > 5000:
        raise HTTPException(status_code=422, detail="limit 必须在 1..5000 之间")
    return {"success": True, "data": plugin_manager.audit_trail(plugin_id, limit=limit)}


@router.get("/plugins/audit")
async def plugin_audit_trail_all(limit: int = 500):
    """读取全部插件的信任/撤销审计轨（最新在前）。"""
    if limit < 1 or limit > 5000:
        raise HTTPException(status_code=422, detail="limit 必须在 1..5000 之间")
    return {"success": True, "data": plugin_manager.audit_trail(None, limit=limit)}


@router.post("/plugins/uninstall")
async def uninstall_plugin(payload: dict):
    """卸载指定插件。payload 需含 plugin_id。"""
    plugin_id = str(payload.get("plugin_id", "") or "").strip()
    if not plugin_id:
        raise HTTPException(status_code=400, detail="plugin_id 不能为空")
    result = plugin_manager.uninstall_plugin(plugin_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "插件卸载失败"))
    return {"success": True, "data": result}


@router.post("/plugins/run")
async def run_plugin(payload: dict | None = None):
    """执行插件（B 类收口）。

    隔离 worker / 内存预算未就绪前（plugins.execution_enabled=false），
    生产环境无可靠插件执行入口；不伪装"已执行成功"，返回结构化 DISABLED。
    只读的清单/解析/计划（/plugins、/plugins/capabilities、/plugins/resolve、/plugins/plan）不受影响。
    """
    return feature_disabled("plugins.execution", "execution_disabled_by_default")


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
