import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import logging
import uuid
from api.deps import get_db, get_llm_client, verify_token
from services.author_review import parse_author_review, render_author_review_prompt
from services.skill_governance import (
    SkillGovernance,
    SkillGovernanceError,
)
logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_token)])

_skill_governance = SkillGovernance()


class AuthorReviewRequest(BaseModel):
    asset: Any
    evidence: list[Any] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    existing_rules: list[Any] = Field(default_factory=list)
    reviewer: str = "author"


class SkillCandidateRequest(BaseModel):
    name: str
    prompt: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_task: str = ""
    source_reflection: str = ""


class SkillReviewRequest(BaseModel):
    reviewer: str = "author"
    note: str = ""
@router.get("/reflection/pending-rules")
async def get_pending_rules(db=Depends(get_db)):
    """读取尚未启用的优化规则，供作者/管理员审核界面使用。"""
    try:
        cursor = await db.conn.execute(
            "SELECT * FROM optimization_rules WHERE is_active = 0 ORDER BY confidence DESC"
        )
        rows = await cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        return {"data": [dict(zip(columns, row)) for row in rows]}
    except Exception as exc:
        logger.error("读取待审核规则失败: %s", exc)
        raise HTTPException(status_code=503, detail="规则审核存储暂不可用") from exc


@router.post("/reflection/rules/{rule_id}/approve")
async def approve_rule(rule_id: str, db=Depends(get_db)):
    """批准已存在的规则；不接受不存在的 ID，也不伪造上线成功。"""
    if not rule_id:
        raise HTTPException(status_code=400, detail="rule_id 不能为空")
    try:
        cursor = await db.conn.execute(
            "SELECT is_active FROM optimization_rules WHERE rule_id = ?", (rule_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="规则不存在，未执行批准")
        if bool(row[0]):
            return {"status": "already_active", "message": f"Rule {rule_id} 已处于激活状态。", "rule_id": rule_id}
        cursor = await db.conn.execute(
            "UPDATE optimization_rules SET is_active = 1 WHERE rule_id = ? AND is_active = 0",
            (rule_id,),
        )
        if cursor.rowcount != 1:
            await db.conn.rollback()
            raise HTTPException(status_code=409, detail="规则状态已发生变化，未执行批准")
        await db.conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.conn.rollback()
        logger.error("批准规则失败: %s", exc)
        raise HTTPException(status_code=503, detail="规则审核存储暂不可用") from exc
    logger.info("人工批准优化规则: %s", rule_id)
    return {"status": "success", "message": f"Rule {rule_id} activated.", "rule_id": rule_id}


@router.post("/reflection/author-review")
async def author_review(
    payload: AuthorReviewRequest,
    llm_client=Depends(get_llm_client),
):
    """生成作者审核意见；不自动批准或发布资产。"""
    prompt = render_author_review_prompt(
        payload.asset,
        evidence=payload.evidence,
        scope=payload.scope,
        existing_rules=payload.existing_rules,
        reviewer=payload.reviewer,
    )
    if llm_client is None:
        return {
            "status": "needs_review",
            "decision": "MODIFY",
            "reason": "审核模型未启用，已生成审核提示词但未形成自动意见。",
            "prompt": prompt,
        }
    try:
        raw = await llm_client.generate_completion(prompt, temperature=0.1, max_tokens=1000)
        result = parse_author_review(raw)
        if result is None:
            raise ValueError("作者审核模型返回非合法审核 JSON")
        return {"status": "reviewed", "review": result, "prompt": prompt}
    except Exception as exc:
        logger.warning("作者审核模型调用失败: %s", exc)
        return {
            "status": "needs_review",
            "decision": "MODIFY",
            "reason": "审核模型失败，未执行批准或发布。",
            "prompt": prompt,
        }


@router.get("/reflection/skills/candidates")
async def list_skill_candidates(status: str | None = None):
    """读取技能候选缓冲池；正式技能不从该接口直接启用。"""
    return {"data": _skill_governance.list_candidates(status=status)}


@router.post("/reflection/skills/candidates")
async def submit_skill_candidate(payload: SkillCandidateRequest):
    """提交候选并执行规则初审，禁止绕过缓冲池直接落地。"""
    try:
        candidate_id = _skill_governance.submit_candidate(
            payload.name,
            payload.prompt,
            confidence=payload.confidence,
            source_task=payload.source_task,
            source_reflection=payload.source_reflection,
        )
        status = _skill_governance.auto_review(candidate_id)
        return {"status": status, "candidate_id": candidate_id}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/reflection/skills/candidates/{candidate_id}/approve")
async def approve_skill_candidate(candidate_id: str, payload: SkillReviewRequest):
    """作者/管理员人工批准候选；批准后仍需显式 promote 才能版本化。"""
    try:
        status = _skill_governance.manual_approve(
            candidate_id, reviewer=payload.reviewer, note=payload.note
        )
        return {"status": status, "candidate_id": candidate_id}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/reflection/skills/candidates/{candidate_id}/reject")
async def reject_skill_candidate(candidate_id: str, payload: SkillReviewRequest):
    """人工驳回候选并保留审核审计记录。"""
    try:
        status = _skill_governance.reject(
            candidate_id, reviewer=payload.reviewer, reason=payload.note or "作者驳回"
        )
        return {"status": status, "candidate_id": candidate_id}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

# ── P2:技能治理生命周期端点(原有孤岛方法通电) ──────────────────

class SkillGrayscaleRequest(BaseModel):
    percent: int = Field(ge=1, le=100)


class SkillRollbackRequest(BaseModel):
    target_version: int = Field(ge=1)


@router.get("/reflection/skills/candidates")
async def list_skill_candidates(status: str | None = None):
    """候选清单(可按状态过滤:PENDING/MANUAL_APPROVED/GRAYSCALE/FULL/REJECTED)。"""
    return {"candidates": _skill_governance.list_candidates(status=status)}


@router.post("/reflection/skills/candidates/{candidate_id}/promote")
async def promote_skill_candidate(candidate_id: str):
    """人工批准后版本化晋升(MANUAL_APPROVED→版本化)。"""
    try:
        version = _skill_governance.promote(candidate_id, operator="author")
        return {"candidate_id": candidate_id, "version": version}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/reflection/skills/candidates/{candidate_id}/grayscale")
async def start_skill_grayscale(candidate_id: str, payload: SkillGrayscaleRequest):
    """灰度发布:按比例在部分任务上生效(P2灰度消费的治理侧)。"""
    try:
        _skill_governance.start_grayscale(candidate_id, percent=payload.percent)
        return {"candidate_id": candidate_id, "percent": payload.percent, "status": "GRAYSCALE"}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/reflection/skills/candidates/{candidate_id}/full")
async def promote_skill_full(candidate_id: str):
    """灰度验证通过,全量转正。"""
    try:
        _skill_governance.promote_full(candidate_id)
        return {"candidate_id": candidate_id, "status": "FULL"}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/reflection/skills/candidates/{candidate_id}/stop-gray")
async def stop_skill_grayscale(candidate_id: str):
    """中止灰度(回退至未放量状态)。"""
    try:
        _skill_governance.stop_grayscale(candidate_id)
        return {"candidate_id": candidate_id, "status": "STOPPED"}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/reflection/skills/candidates/{candidate_id}/rollback")
async def rollback_skill_candidate(candidate_id: str, payload: SkillRollbackRequest):
    """版本回滚至指定历史版本。"""
    try:
        version = _skill_governance.rollback(
            candidate_id, target_version=payload.target_version, operator="author"
        )
        return {"candidate_id": candidate_id, "version": version}
    except SkillGovernanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
