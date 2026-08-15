"""冒烟测试：验证路由挂载、bootstrap 装配、模型序列化均正常。"""
import sys
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
ok = True
# 1. 模型序列化
from models.novel_agent import NovelAgentSkill, NovelAgentExecutionAudit, SubAgentCallRecord
from models.reflection import OptimizationRule
skill = NovelAgentSkill(
    name="测试技能", content={"genre": "玄幻"},
    agent_overrides={"combat_expert": {"enabled": False}},
)
dump = skill.model_dump(mode="json")
assert dump["card_sub_type"] == "novel_agent_skill", "技能卡类型错误"
print("[OK] NovelAgentSkill 序列化正常 (card_sub_type=novel_agent_skill)")
# 2. OptimizationRule 支持 NOVEL_AGENT_SCHEDULE scope
rule = OptimizationRule(
    scope="NOVEL_AGENT_SCHEDULE",
    condition={"agent_role": "combat_expert"},
    action={"agent_overrides": {"combat_expert": {"enabled": False}}},
)
print("[OK] OptimizationRule 支持 NOVEL_AGENT_SCHEDULE scope")
# 3. 配置开关读取
from core.config_manager import config_manager
ma_enable = config_manager.get_bool("feature.novel_multi_agent_enable", False)
learn_enable = config_manager.get_bool("novel_agent.enable_self_reflect_learn", False)
print(f"[INFO] feature.novel_multi_agent_enable = {ma_enable} (默认关闭, 不影响原逻辑)")
print(f"[INFO] novel_agent.enable_self_reflect_learn = {learn_enable} (默认关闭)")
# 4. API 路由挂载验证（模拟 api_router.py 的门控逻辑）
import core.config_manager as cm
cm.config_manager._config.setdefault("feature", {})["novel_multi_agent_enable"] = True
from api.api_router import api_router

def _collect_paths(routes):
    paths = []
    for r in routes:
        p = getattr(r, "path", None)
        if p:
            paths.append(p)
        sub = getattr(r, "routes", None)
        if sub:
            paths.extend(_collect_paths(sub))
    return paths
routes = _collect_paths(api_router.routes)
novel_routes = [p for p in routes if "novel-agent" in p]
print(f"[OK] 开关开启后挂载 novel-agent 路由: {novel_routes}")
assert any("novel-agent/skills" in p for p in novel_routes), "技能列表路由未挂载"
assert any("/feedback" in p for p in novel_routes), "反馈路由未挂载"
assert any("/toggle" in p for p in novel_routes), "技能切换路由未挂载"
# 5. bootstrap 装配函数存在性
from core import bootstrap
assert hasattr(bootstrap, "setup_novel_multi_agent"), "setup_novel_multi_agent 未定义"
print("[OK] bootstrap.setup_novel_multi_agent 已装配")
# 6. 提取器插件导入
from extractors.novel_agent_extractors import AgentRuleExtractor, NovelAgentSkillExtractor
print("[OK] 反思提取器插件可导入（AgentRuleExtractor / NovelAgentSkillExtractor）")
print("\n=== 冒烟测试全部通过 ✅ ===")