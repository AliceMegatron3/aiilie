from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from models.prompt_models import TemplateRenderRequest, PromptTemplate
from services.prompt_template_manager import prompt_manager
from api.deps import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])

@router.get("/prompts/list")
async def list_prompts():
    """补丁F扩展：获取所有系统与用户提示词模板清单"""
    return {"status": "success", "data": [tpl.model_dump() for tpl in prompt_manager.templates.values()]}

@router.post("/prompts/render")
async def render_prompt(req: TemplateRenderRequest):
    """补丁F扩展：预览渲染提示词模板（循环继承会返回 400）"""
    try:
        result = prompt_manager.render(req.template_id, req.variables)
        return {"status": "success", "rendered_text": result}
    except ValueError as e:
        # 循环继承检测等校验错误：明确 400 返回
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Render failed: {str(e)}")

@router.post("/prompts/save")
async def save_prompt(template: PromptTemplate):
    """补丁F扩展：保存用户自定义模板（内置模板会自动另存副本；自动版本快照）"""
    try:
        prompt_manager.save_template(template)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class RollbackRequest(BaseModel):
    version: int

@router.get("/prompts/{template_id}/versions")
async def list_template_versions(template_id: str):
    """第三部分：模板版本历史"""
    try:
        versions = prompt_manager.list_template_versions(template_id)
        return {"status": "success", "data": versions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/prompts/{template_id}/versions/{version}/rollback")
async def rollback_template(template_id: str, version: int):
    """第三部分：模板回滚到指定历史版本"""
    try:
        restored = prompt_manager.rollback_template(template_id, version)
        return {"status": "success", "data": restored.model_dump()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
