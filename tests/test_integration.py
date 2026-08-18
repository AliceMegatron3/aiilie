import pytest
from fastapi.testclient import TestClient
from main import app
from services.session_pool import session_pool
from services.prompt_template_manager import prompt_manager

@pytest.fixture(scope="module")
def client():
    # 隔离全局 ledger 灰度：integration 启动完整应用，隔离测试库无迁移记录，
    # 先关闭 authoritative 门禁避免 fail-closed 拒绝启动；测试结束还原配置。
    from core.config_manager import config_manager
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    prev = {k: ledger_cfg.get(k) for k in ("authoritative", "read_mode")}
    ledger_cfg["authoritative"] = False
    ledger_cfg["read_mode"] = "legacy"
    try:
        # Use TestClient with a with-statement to trigger the lifespan events
        with TestClient(app) as client:
            yield client
    finally:
        for k, v in prev.items():
            if v is None:
                ledger_cfg.pop(k, None)
            else:
                ledger_cfg[k] = v

def test_system_status(client):
    """测试1：系统启动测试，大盘状态是否健康（7.8 统一 success/data 包络）"""
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "global_state" in body["data"]

def test_knowledge_metrics_endpoint(client):
    """V0.2 量化拆书：候选知识确定性量化指标（规则结果，非 LLM）"""
    res = client.post("/api/v1/library/knowledge/metrics", json={
        "claim": "因果推理需可证伪证据",
        "evidence": [
            {"document_id": "d1", "chapter": "c1", "quote": "a"},
            {"document_id": "d1", "chapter": "c1", "quote": "b"},
            {"document_id": "d2", "chapter": "c2", "quote": "c"},
        ]
    })
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    metrics = body["data"]["metrics"]
    assert metrics["evidence_count"] == 3
    assert metrics["independent_chapter_count"] == 2
    assert abs(metrics["source_coverage"] - 2 / 3) < 1e-3
    assert "rule_confidence" in metrics


def test_agent_plan_lifecycle_gates(client):
    """V0.4 受控执行：通用 Agent Plan 版本化审批门（API 视角）。"""
    create = client.post("/api/v1/agent/plans", json={
        "goal": "量化书籍",
        "identity": "agent-a",
        "steps": [{"tool": "library.search_cards", "args": {"k": "v"}, "description": "查卡"}],
    })
    assert create.status_code == 200
    plan_id = create.json()["data"]["plan_id"]
    v1 = create.json()["data"]["version"]
    try:
        # 未批准即执行 → 403
        unapproved = client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": v1, "actor": "author"})
        assert unapproved.status_code == 403

        # 提交 → 批准 → 执行
        sub = client.post(f"/api/v1/agent/plans/{plan_id}/submit", json={"version": v1, "actor": "author"})
        assert sub.status_code == 200
        appr = client.post(f"/api/v1/agent/plans/{plan_id}/approve", json={"version": v1, "actor": "author"})
        assert appr.status_code == 200
        exec_res = client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": v1, "actor": "author"})
        assert exec_res.status_code == 200
        assert exec_res.json()["data"]["steps"][0]["tool"] == "library.search_cards"

        # 每步审计已记录（只追加，可回放）。Batch 5：不再以 PENDING 伪装完成——
        # 执行必须产出真实 handler 的终态（runner 记 start=RUNNING + 终态 OK/ERROR）。
        audit = client.get(f"/api/v1/agent/plans/{plan_id}/audit")
        assert audit.status_code == 200
        audit_events = audit.json()["data"]["events"]
        assert len(audit_events) >= 1
        assert audit_events[0]["tool"] == "library.search_cards"
        # 审计轨存在真实运行记录（非仅 dispatched/PENDING 占位）
        assert any(e["status"] != "PENDING" for e in audit_events)
        assert audit_events[0]["status"] == "RUNNING"  # runner 真实 start 事件

        # agent 不能批准自己的计划
        self_appr = client.post(f"/api/v1/agent/plans/{plan_id}/approve", json={"version": v1, "actor": "agent-a"})
        assert self_appr.status_code == 409

        # 编辑出新候选版本，未批准前禁止执行；旧批准版本仍可执行且内容不变
        edit = client.post(f"/api/v1/agent/plans/{plan_id}/edit", json={
            "steps": [{"tool": "project.query", "args": {}, "description": "查项目"}],
            "goal": "扩大范围",
        })
        assert edit.status_code == 200
        v2 = edit.json()["data"]["version"]
        assert v2 == v1 + 1
        v2_exec = client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": v2, "actor": "author"})
        assert v2_exec.status_code == 403
        v1_again = client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": v1, "actor": "author"})
        assert v1_again.status_code == 200
        assert v1_again.json()["data"]["steps"][0]["tool"] == "library.search_cards"
    finally:
        import asyncio
        from services.agent_plan_store import AgentPlanStore
        from services.plan_audit import plan_audit_store
        asyncio.run(AgentPlanStore().delete(plan_id))
        asyncio.run(plan_audit_store.delete(plan_id))

def test_agent_plan_rejects_non_whitelisted_tool(client):
    """V0.4：已批准计划引用未注册工具 → 执行门 403（白名单 fail-closed）。"""
    create = client.post("/api/v1/agent/plans", json={
        "goal": "越权操作",
        "identity": "agent-a",
        "steps": [{"tool": "evil.system_shell", "args": {}, "description": "执行外部命令"}],
    })
    plan_id = create.json()["data"]["plan_id"]
    v1 = create.json()["data"]["version"]
    try:
        client.post(f"/api/v1/agent/plans/{plan_id}/submit", json={"version": v1, "actor": "author"})
        client.post(f"/api/v1/agent/plans/{plan_id}/approve", json={"version": v1, "actor": "author"})
        # 已批准，但工具不在白名单 → 仍被拒（403）
        exec_res = client.post(f"/api/v1/agent/plans/{plan_id}/execute", json={"version": v1, "actor": "author"})
        assert exec_res.status_code == 403
        assert "未注册或权限不足" in exec_res.json()["detail"]
    finally:
        import asyncio
        from services.agent_plan_store import AgentPlanStore
        from services.plan_audit import plan_audit_store
        asyncio.run(AgentPlanStore().delete(plan_id))
        asyncio.run(plan_audit_store.delete(plan_id))


def test_knowledge_claim_store_endpoint(client):
    """V0.2：候选知识提交（证据/去重/冲突门）API 视角。"""
    sub = client.post("/api/v1/library/knowledge/claims", json={
        "claim": "因果推理需可证伪证据",
        "evidence": [{"document_id": "d1", "chapter": "c1", "quote": "因果推理需可证伪证据"}],
        "conditions": ["对象=正式技能"],
        "steps": ["步骤1"],
    })
    assert sub.status_code == 200
    body = sub.json()
    assert body["success"] is True
    cid = body["data"]["candidate_id"]
    assert body["data"]["action"] in ("stored", "conflict")
    try:
        # 无证据 → no_evidence
        no_ev = client.post("/api/v1/library/knowledge/claims", json={"claim": "无来源观点"})
        assert no_ev.json()["data"]["action"] == "no_evidence"
        # 列表可回放
        listing = client.get("/api/v1/library/knowledge/claims")
        assert listing.status_code == 200
        assert listing.json()["data"]["count"] >= 1
    finally:
        import asyncio
        from services.knowledge_claim_store import KnowledgeClaimStore
        asyncio.run(KnowledgeClaimStore().delete(cid))


def test_knowledge_refine_endpoint(client):
    """V0.2：知识炼制主链路（段落→证据→量化门→技能候选）API 视角。"""
    res = client.post("/api/v1/library/knowledge/refine", json={
        "document_id": "d_refine",
        "blocks": ["科学方法强调因果推理需要可证伪的证据。"],
        "claims": ["因果推理需可证伪证据"],
        "skill_name": "skill.extracted",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 1
    assert data["stored"] == 1
    assert data["no_evidence"] == 0
    assert data["proposed_candidates"] == ["skill.extracted@1.1.0-candidate"]
    cid = data["details"][0]["candidate_id"]
    try:
        import asyncio
        from services.knowledge_claim_store import KnowledgeClaimStore
        asyncio.run(KnowledgeClaimStore().delete(cid))
    except Exception:
        pass


def test_patch_f_session_pool(client):
    """测试补丁F：会话池，跨分支会话隔离、冻结与销毁"""
    # 创建会话
    res = client.post("/api/v1/sessions/create", json={
        "model_key": "ollama/test",
        "bind_branch_id": "branch_alpha"
    })
    assert res.status_code == 200
    session_id = res.json()["session_id"]
    
    # 获取会话
    res2 = client.get(f"/api/v1/sessions/{session_id}")
    assert res2.status_code == 200
    assert res2.json()["data"]["bind_branch_id"] == "branch_alpha"
    
    # 冻结会话
    res3 = client.post(f"/api/v1/sessions/{session_id}/freeze")
    assert res3.status_code == 200
    
    # 销毁会话
    res4 = client.delete(f"/api/v1/sessions/{session_id}")
    assert res4.status_code == 200
    
    # 确认已销毁
    res5 = client.get(f"/api/v1/sessions/{session_id}")
    assert res5.status_code == 404

def test_patch_f_prompt_templates(client):
    """测试补丁F：提示词模板获取与渲染"""
    # 获取列表
    res = client.get("/api/v1/prompts/list")
    assert res.status_code == 200
    data = res.json()["data"]
    assert isinstance(data, list)
    
    # 预设一个模板
    client.post("/api/v1/prompts/save", json={
        "template_id": "book_extract_worldview",
        "name": "Extract",
        "category": "extraction",
        "template_text": "Welcome to {{ book_text }}, style: {{ style_tags }}",
        "version": "1.0",
        "is_builtin": False
    })
    
    # 渲染预览
    res2 = client.post("/api/v1/prompts/render", json={
        "template_id": "book_extract_worldview",
        "variables": {"book_text": "cyberpunk city", "style_tags": "neon"}
    })
    assert res2.status_code == 200, res2.text
    assert "cyberpunk city" in res2.json()["rendered_text"]

def test_patch_e_resource_stats(client):
    """测试补丁E：获取全局资源监控状态"""
    res = client.get("/api/v1/system/resource-stats")
    assert res.status_code == 200
    data = res.json()["data"]
    assert "disk_free_gb" in data
    assert "directory_usage_mb" in data

def test_patch_e_trigger_gc(client):
    """测试补丁E：手动触发全局垃圾回收（正规实现：core/gc_manager.py，
    统一 ok() 响应包装，task_id 位于 data 下）。"""
    res = client.post("/api/v1/system/gc-run")
    assert res.status_code == 200
    payload = res.json()
    assert payload.get("success") is True
    assert "task_id" in (payload.get("data") or {})

def test_patch_b_pending_rules(client):
    """测试补丁B：反思系统人工治理"""
    res = client.get("/api/v1/reflection/pending-rules")
    assert res.status_code == 200
    
def test_patch_c_plugin_install(client):
    """测试补丁C：插件生态接口"""
    res = client.post("/api/v1/plugins/install")
    assert res.status_code == 501
    assert res.headers["X-Feature-Status"] == "not_implemented"
    assert "未执行任何安装" in res.json()["detail"]

def test_patch_d_cloud_sync(client):
    """测试补丁D：云端同步服务

    真实云端 provider 未接入，按方向报告 B 类降级——
    返回结构化 DISABLED(HTTP 501)，不再伪装“已同步成功”。
    """
    res = client.post("/api/v1/projects/test_proj_1/sync")
    assert res.status_code == 501
    assert res.headers.get("X-Feature-Status") == "disabled"
    body = res.json()
    assert body["status"] == "DISABLED"
    assert body["feature"] == "project.sync"
    assert body["reason"] == "provider_not_configured"

    status = client.get("/api/v1/projects/test_proj_1/sync-status")
    assert status.status_code == 501
    assert status.headers.get("X-Feature-Status") == "disabled"
    assert status.json()["status"] == "DISABLED"

def test_plugins_run_disabled(client):
    """测试 B 类收口：插件执行无生产可靠入口 → 结构化 DISABLED（不伪装"已执行成功"）。

    隔离 worker / 内存预算未就绪前 plugins.execution_enabled=false，
    POST /plugins/run 必须返回 501 + X-Feature-Status: disabled；
    只读清单/计划接口不受影响。
    """
    res = client.post("/api/v1/plugins/run", json={"plugin_id": "x"})
    assert res.status_code == 501
    assert res.headers.get("X-Feature-Status") == "disabled"
    body = res.json()
    assert body["status"] == "DISABLED"
    assert body["feature"] == "plugins.execution"
    assert body["reason"] == "execution_disabled_by_default"

    # 只读能力清单仍可用（不受禁用影响）
    caps = client.get("/api/v1/plugins/capabilities")
    assert caps.status_code == 200

def test_switch_compatibility(client):
    """测试开关兼容性：确认关闭开关不影响核心引擎"""
    # 禁用会话池
    session_pool.enable = False
    res = client.post("/api/v1/sessions/create", json={"model_key": "test"})
    assert res.status_code == 503
    assert "Session pool is disabled" in res.json()["detail"]
    session_pool.enable = True  # 恢复状态


def test_knowledge_claim_sync_endpoint(client):
    """V0.2 落库打通：已入库量化主张同步进权威索引库/Ledger（幂等、ok() 包络）。"""
    submit = client.post("/api/v1/library/knowledge/claims", json={
        "claim": "因果推理需可证伪证据，避免仅凭相关关系下结论。",
        "evidence": [
            {"document_id": "sync_doc", "chapter": "c1", "quote": "相关性不等于因果性。"},
            {"document_id": "sync_doc", "chapter": "c1", "quote": "可证伪性是科学结论的边界。"},
        ],
    })
    assert submit.status_code == 200 and submit.json()["success"] is True
    assert submit.json()["data"]["action"] == "stored"

    res = client.post("/api/v1/library/knowledge/sync")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"]["projected"] >= 1
    assert body["data"]["skipped"] == 0

    # 幂等：再次同步不产生重复
    again = client.post("/api/v1/library/knowledge/sync")
    assert again.json()["data"]["projected"] == body["data"]["projected"]


def test_knowledge_corpus_endpoint(client):
    """V0.2 技能语料打通：已入库主张输出为技能语料摘要。"""
    submit = client.post("/api/v1/library/knowledge/claims", json={
        "claim": "角色动机应与其成长弧线一致，以维持叙事自洽。",
        "evidence": [{"document_id": "corpus_doc", "chapter": "c1", "quote": "动机决定行为逻辑。"}],
    })
    assert submit.json()["data"]["action"] == "stored"
    res = client.get("/api/v1/library/knowledge/corpus")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"]["count"] >= 1
    entry = body["data"]["entries"][0]
    assert "claim" in entry and "evidence_quotes" in entry and "rule_confidence" in entry
