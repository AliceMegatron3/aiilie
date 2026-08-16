import pytest
from fastapi.testclient import TestClient
from main import app
from services.session_pool import session_pool
from services.prompt_template_manager import prompt_manager

@pytest.fixture(scope="module")
def client():
    # Use TestClient with a with-statement to trigger the lifespan events
    with TestClient(app) as client:
        yield client

def test_system_status(client):
    """测试1：系统启动测试，大盘状态是否健康"""
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    data = response.json()
    assert "global_state" in data

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
    """测试补丁D：云端同步服务"""
    res = client.post("/api/v1/projects/test_proj_1/sync")
    assert res.status_code == 200

def test_switch_compatibility(client):
    """测试开关兼容性：确认关闭开关不影响核心引擎"""
    # 禁用会话池
    session_pool.enable = False
    res = client.post("/api/v1/sessions/create", json={"model_key": "test"})
    assert res.status_code == 503
    assert "Session pool is disabled" in res.json()["detail"]
    session_pool.enable = True  # 恢复状态
