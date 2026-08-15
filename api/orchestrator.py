from fastapi import APIRouter, Depends, HTTPException
from typing import Any
import logging
from models.orchestrator_schemas import OrchestratorRequest, ExecutionDAG
from services.orchestrator import dispatcher
from api.deps import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["Agent Orchestrator"], dependencies=[Depends(verify_token)])

@router.post("/plan", summary="[总督] 生成量化调度计划")
async def get_execution_plan(
    payload: OrchestratorRequest
) -> dict[str, Any]:
    """
    前端在正式发起量化前，可调用此接口。
    总督智能体会根据传入的模型和内容，返回其“思考”和“分兵排阵”的 DAG 图。
    前端可根据此图展示哪些专员被激活，哪些由于算力节省被挂起。
    """
    try:
        dag: ExecutionDAG = dispatcher.generate_dag(payload)
        return {
            "success": True,
            "message": "总督调度计划生成完毕",
            "data": dag.model_dump()
        }
    except Exception as e:
        logger.error("生成总督调度计划失败: %s", e)
        raise HTTPException(status_code=500, detail="调度计划生成失败")
