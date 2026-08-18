"""知识炼制流水线测试（V0.2 确定性主链路：段落→证据→量化门→技能候选）。"""
from __future__ import annotations

import pytest

from services.knowledge_claim_store import KnowledgeClaimStore
from services.knowledge_refinery import refine, refine_document
from services.knowledge_metrics import CandidateKnowledge, EvidenceRef
from services.parser import ParsedDocument, ParsedPassage
from services.skill_versioning import SkillVersionedSkill


@pytest.mark.asyncio
async def test_refine_document_end_to_end(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    skill = SkillVersionedSkill("skill.extracted", "1.0.0")
    doc = ParsedDocument(
        title="书A", parser_id="builtin.txt",
        passages=[
            ParsedPassage(sequence=0, heading="第一章", text="科学方法强调因果推理需要可证伪的证据。"),
            ParsedPassage(sequence=1, heading="第二章", text="写作要把握读者情绪节奏。"),
        ],
    )
    report = await refine_document(
        doc,
        ["因果推理需可证伪证据"],
        claim_store=store,
        skills={"skill.extracted": skill},
    )
    assert report.stored == 1
    detail = report.details[0]
    assert detail.skill_candidate == "skill.extracted@1.1.0-candidate"
    assert detail.evidence_count >= 1
    assert detail.tests_passed is True
    assert skill.can_activate(detail.skill_candidate) is True  # 过测待作者激活
    assert skill.active_label() == "skill.extracted@1.0.0"


@pytest.mark.asyncio
async def test_full_mainline_stores_and_proposes_candidate(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    skill = SkillVersionedSkill("skill.extracted", "1.2.0")
    blocks = ["科学方法强调因果推理需要可证伪的证据。"]
    report = await refine(
        blocks,
        ["因果推理需可证伪证据"],
        document_id="d1",
        claim_store=store,
        skills={"skill.extracted": skill},
    )
    assert report.total == 1
    assert report.stored == 1
    assert report.details[0].evidence_count == 1
    assert report.proposed_candidates == ["skill.extracted@1.3.0-candidate"]
    assert skill.active_label() == "skill.extracted@1.2.0"  # 激活版未被覆盖
    # 候选已提、冒烟过测，但未自动激活（待作者审核）
    assert report.details[0].skill_candidate == "skill.extracted@1.3.0-candidate"
    assert report.details[0].tests_passed is True
    assert skill.can_activate("skill.extracted@1.3.0-candidate") is True
    # 缺失证据时冒烟不过（候选保持未过测，激活待作者）
    report2 = await refine(
        ["无关段落关于天气。"],
        [{"claim": "股票均线金叉", "steps": ["无"]}],  # 无匹配证据
        document_id="d1", claim_store=store,
        skills={"skill.extracted": skill},
    )
    assert report2.no_evidence == 1


@pytest.mark.asyncio
async def test_no_source_evidence_blocked_and_duplicate_detected(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    blocks = ["雨水丰沛，作物生长。"]
    # 无关话题在文中无匹配 → no_evidence，不提交候选
    report1 = await refine(
        blocks, ["关于税收制度的深奥理论"], document_id="d1", claim_store=store
    )
    assert report1.no_evidence == 1
    assert report1.proposed_candidates == []
    # 有证据的主张入库后，重复提交 → duplicate
    blocks2 = ["写作需要把握情感节奏与张力。"]
    r2 = await refine(
        blocks2, ["写作需要把握情感节奏与张力"], document_id="d1", claim_store=store
    )
    assert r2.stored == 1
    r3 = await refine(
        blocks2, ["写作需要把握情感节奏与张力"], document_id="d1", claim_store=store
    )
    assert r3.duplicates == 1
    assert r3.proposed_candidates == []


@pytest.mark.asyncio
async def test_conflict_not_upgraded_to_candidate(tmp_path):
    store = KnowledgeClaimStore(base_dir=tmp_path)
    blocks1 = ["断更一定会损失读者黏性。"]
    # 首个近义主张带反例入库
    from services.knowledge_metrics import CandidateKnowledge, EvidenceRef

    await store.add_candidate(CandidateKnowledge(
        claim="断更一定会损失读者黏性",
        counterexamples=["编辑实测案例X"],
        evidence=[EvidenceRef(document_id="d1", chapter="c", quote="断更一定会损失读者黏性")],
    ))
    # 高度近义且共享反例的新主张 → conflict，不提出候选
    report = await refine(
        blocks1,
        [{"claim": "断更多少会损失读者黏性", "counterexamples": ["编辑实测案例X"]}],
        document_id="d1",
        claim_store=store,
    )
    assert report.conflicts == 1
    assert report.proposed_candidates == []


@pytest.mark.asyncio
async def test_provenance_and_metric_snapshot_persisted(tmp_path):
    """Batch 2：候选记录必须显式持久化 run_id/source_document_id/parser/model/
    input_hash，metrics 为强类型 MetricSnapshot（via=rule），可回放可溯源。"""
    store = KnowledgeClaimStore(base_dir=tmp_path)
    blocks = ["科学方法强调因果推理需要可证伪的证据。"]
    report = await refine(
        blocks,
        [{"claim": "因果推理需可证伪证据", "parser": "builtin.txt", "model": "rule"}],
        document_id="doc-x",
        claim_store=store,
        run_id="run-123",
    )
    assert report.stored == 1
    claims = await store.list_claims()
    assert len(claims) == 1
    rec = claims[0]
    assert rec["run_id"] == "run-123"
    assert rec["source_document_id"] == "doc-x"
    assert rec["parser"] == "builtin.txt"
    assert rec["model"] == "rule"
    assert rec["input_hash"]  # 非空指纹
    # metrics 为显式 MetricSnapshot（via=rule），非裸 dict 漂移
    assert rec["metrics"]["via"] == "rule"
    assert rec["metrics"]["evidence_count"] >= 1


@pytest.mark.asyncio
async def test_batch_idempotency_same_run_no_duplicate_side_effect(tmp_path):
    """Batch 2：同一输入重复跑 refine，命中既定 candidate_id（duplicate），
    不新增记录、不重复副作用。"""
    store = KnowledgeClaimStore(base_dir=tmp_path)
    blocks = ["雨水丰沛，作物生长。"]
    payload = [{"claim": "雨水促使作物生长", "run_id": "run-9"}]
    first = await refine(
        blocks, payload, document_id="d1", claim_store=store, run_id="run-9"
    )
    assert first.stored == 1
    cid1 = first.details[0].candidate_id

    second = await refine(
        blocks, payload, document_id="d1", claim_store=store, run_id="run-9"
    )
    assert second.duplicates == 1
    assert second.details[0].candidate_id == cid1  # 幂等命中相同候选
    assert len(await store.list_claims()) == 1  # 无重复落盘


@pytest.mark.asyncio
async def test_stored_passed_claim_registers_into_governance(tmp_path):
    """Batch 2 闭环：有证据且冒烟过测的主张经 refine 注册进 SkillGovernance
    缓冲池（submit_candidate，内容哈希幂等），并暴露出可查询的 candidate_id。"""
    from services.skill_governance import SkillGovernance

    store = KnowledgeClaimStore(base_dir=tmp_path / "claims")
    gov = SkillGovernance(db_path=tmp_path / "gov.db")
    blocks = ["科学方法强调因果推理需要可证伪的证据。"]
    report = await refine(
        blocks,
        [{"claim": "因果推理需可证伪证据", "run_id": "run-g"}],
        document_id="doc-g",
        claim_store=store,
        run_id="run-g",
        governance=gov,
    )
    assert report.stored == 1
    gc = report.details[0].governance_candidate_id
    assert gc.startswith("skillcand_")  # 已注册进 SkillGovernance 缓冲池
    # 治理候选确可查询
    assert gov._get_candidate(gc) is not None
    # 幂等：相同内容在不同 claim_store 再注册 → 命中同一候选 id，不新增
    store2 = KnowledgeClaimStore(base_dir=tmp_path / "claims2")
    report2 = await refine(
        blocks,
        [{"claim": "因果推理需可证伪证据", "run_id": "run-g"}],
        document_id="doc-g",
        claim_store=store2,
        run_id="run-g",
        governance=gov,
    )
    assert report2.details[0].governance_candidate_id == gc


@pytest.mark.asyncio
async def test_no_evidence_claim_not_registered_into_governance(tmp_path):
    """Batch 2：无证据主张不进入正式候选，也不得注册进 SkillGovernance。"""
    from services.skill_governance import SkillGovernance

    store = KnowledgeClaimStore(base_dir=tmp_path / "claims")
    gov = SkillGovernance(db_path=tmp_path / "gov.db")
    report = await refine(
        ["只讲天气的段落。"],
        ["关于量子计算深奥理论"],   # 文中无匹配证据
        document_id="d1",
        claim_store=store,
        governance=gov,
    )
    assert report.no_evidence == 1
    assert report.details[0].governance_candidate_id == ""  # 不注册


def test_upsert_reflection_session_idempotent_and_unique():
    """Batch 2：同一量化运行只产出一个 ReflectionSession（幂等），
    不同 run 产出不同会话；provenance 显式携带。"""
    from api.library import _upsert_reflection_session

    s1 = _upsert_reflection_session(
        run_id="run-1", source_snapshot="doc-x", parser="rule", model="",
        status="COMPLETED", artifact={"stored": 1},
    )
    s2 = _upsert_reflection_session(
        run_id="run-1", source_snapshot="doc-x", parser="rule", model="",
        status="COMPLETED", artifact={"stored": 1},
    )
    assert s1.session_id == s2.session_id  # 同一次运行复用同一会话

    s3 = _upsert_reflection_session(
        run_id="run-2", source_snapshot="doc-x", parser="rule", model="",
        status="COMPLETED", artifact={"stored": 1},
    )
    assert s3.session_id != s1.session_id  # 不同运行 → 不同会话
    assert s1.run_id == "run-1"
    assert s1.source_snapshot == "doc-x"
    assert s1.artifact["stored"] == 1


@pytest.mark.asyncio
async def test_refine_report_status_flags_no_evidence_and_failed(tmp_path):
    """Batch 2：无证据/空输入显式标记 NEEDS_REVIEW/FAILED，不伪装成功。"""
    store = KnowledgeClaimStore(base_dir=tmp_path)
    # 无证据 → NEEDS_REVIEW
    r1 = await refine(
        ["只讲天气的段落。"], ["关于量子计算深奥理论"], document_id="d1", claim_store=store
    )
    assert r1.no_evidence == 1
    assert r1.status == "NEEDS_REVIEW"
    # 空文档（无任何可炼制源段落）+ 主张 → FAILED，不伪装成功
    r2 = await refine(
        ["", "   "], ["某主张"], document_id="d2", claim_store=store
    )
    assert r2.status == "FAILED"
    # 正常入库 → COMPLETED
    r3 = await refine(
        ["写作要把握读者情绪节奏。"], ["写作要把握读者情绪节奏"],
        document_id="d3", claim_store=store,
    )
    assert r3.stored == 1
    assert r3.status == "COMPLETED"