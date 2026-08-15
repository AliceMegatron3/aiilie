import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

class RuleConflictDetector:
    def detect(self, new_rule: Dict, existing_rules: List[Dict]) -> bool:
        """
        检查新规则是否与现有规则冲突。
        例如：条件重叠但动作完全相反（如一个设定模式为 rapid，一个为 think）。
        """
        for rule in existing_rules:
            # 简化逻辑：如果条件相似但 action 相反，判定为冲突
            if new_rule.get('condition') == rule.get('condition'):
                if new_rule.get('action') != rule.get('action'):
                    logger.warning(f"🚨 规则冲突告警！新规则 [{new_rule.get('id')}] 与已有规则 [{rule.get('id')}] 动作互斥。")
                    return True
        return False

class RuleSimulator:
    def simulate(self, rule: Dict) -> float:
        """
        在沙盒/历史数据上跑测试，评估规则收益。
        返回预计提升的分数 (0.0 - 1.0)。
        """
        logger.info(f"正在沙盒环境中模拟运行规则: {rule.get('id')} ...")
        # 伪代码：假设模拟后预估收益极高
        simulated_score = 0.85 
        return simulated_score

class RuleLifecycleManager:
    def evaluate_rule(self, rule: Dict):
        """
        对服役中的规则进行考核。
        """
        apply_count = rule.get("apply_count", 0)
        effect_score = rule.get("effect_score", 1.0)
        
        if apply_count > 50 and effect_score < 0.3:
            rule["status"] = "DEPRECATED"
            logger.warning(f"📉 规则 {rule.get('id')} 实际收益极低，已被系统自动废弃。")
        else:
            logger.info(f"✅ 规则 {rule.get('id')} 表现稳定，继续服役。")
