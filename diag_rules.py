"""诊断：AgentRuleExtractor 为何未产出 ACTIVE 规则。"""
import sys
from pathlib import Path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
import json
from extractors.novel_agent_extractors import AgentRuleExtractor, NovelAgentSkillExtractor
from services.novel_agent_learning_loop import NovelAgentLearningLoop
# 构造与测试一致的样本
def make_sample(task_id, genre, hit_score, ooc=0, circuit=False, feedback=None):
    return {
        "audit_id": f"audit_{task_id}", "task_id": f"task_{task_id}",
        "project_id": "proj_demo", "genre": genre, "scenario": "战斗",
        "agent_calls": [
            {"agent_role": "lore_expert", "is_active": True, "model_assigned": "qwen",
             "calls": 1, "total_tokens": 500, "duration_ms": 1000,
             "hit_score": hit_score, "ooc_warnings": ooc, "error_count": 0},
            {"agent_role": "combat_expert", "is_active": True, "model_assigned": "qwen",
             "calls": 1, "total_tokens": 300, "duration_ms": 800,
             "hit_score": hit_score * 0.9, "ooc_warnings": 0, "error_count": 1 if ooc > 0 else 0},
        ],
        "total_tokens": 800, "total_duration_ms": 1800, "hit_score": hit_score,
        "ooc_warnings": ooc, "circuit_break": circuit,
        "circuit_break_reason": "test" if circuit else "",
        "used_rule_ids": [], "used_skill_ids": [],
        "user_feedback_score": feedback, "user_ooc_marked": False, "user_comment": "",
        "created_at": "2026-08-14T00:00:00Z", "updated_at": "2026-08-14T00:00:00Z",
    }
samples = []
for i in range(7):
    if i < 5:
        g, h, o, c, f = "玄幻", 0.85, 0, False, 4.0
    elif i == 5:
        g, h, o, c, f = "玄幻", 0.3, 3, True, 1.0
    else:
        g, h, o, c, f = "都市", 0.4, 0, False, 2.0
    samples.append(make_sample(i, g, hit_score=h, ooc=o, circuit=c, feedback=f))
ext = AgentRuleExtractor(confidence_threshold=0.75, min_sample_count=5)
rules = ext.extract_rules(samples)
print(f"=== 共产出 {len(rules)} 条规则 ===")
for r in rules:
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("-" * 40)
sk_ext = NovelAgentSkillExtractor(min_sample_count=5)
skills = sk_ext.extract_skills(samples)
print(f"=== 技能 {len(skills)} 条 ===")