"""
strategies/deep_think_splitter.py — 批次7：深度思考拆分策略
============================================================
按思考阶段（状态机）拆分任务，而非按字数：
MAPPING → SCANNING → ANALYZING → VALIDATING → CONCLUDING

实现批次1 SplitStrategy 接口，通过 CommandSplitter.register_strategy 注册，
segment_strategy="deep_think" 时自动选中。
拆分产物：5 个 Segment，phase 字段标记阶段，tail_context 注入 thought_trace 约定。
"""
import uuid
from typing import List

from models.task import CommandTask, Segment

# 思考五阶段（与 services/deep_think.py 保持一致）
THINK_STAGES = ["MAPPING", "SCANNING", "ANALYZING", "VALIDATING", "CONCLUDING"]


class DeepThinkSplitter:
    """批次7 拆分策略：按思考阶段拆分任务（保留历史骨架签名 + 新增批次1接口）。"""

    STAGES = THINK_STAGES

    # ── 历史骨架签名（models/deep_think.DeepThinkSegment 消费方兼容） ──
    def split_task(self, task_id: str, prompt: str):
        """按阶段生成 DeepThinkSegment 列表（历史接口）。"""
        from models.deep_think import DeepThinkSegment

        segments = []
        for phase in self.STAGES:
            segments.append(
                DeepThinkSegment(
                    id=str(uuid.uuid4()),
                    task_id=task_id,
                    phase=phase,
                    content=prompt,
                )
            )
        return segments

    # ── 批次1 SplitStrategy 接口 ─────────────────────────────────
    @property
    def name(self) -> str:
        return "deep_think"

    def split(self, task: CommandTask) -> List[Segment]:
        """按思考五阶段拆分 CommandTask：每阶段一个 Segment（phase 标记）。"""
        segments: List[Segment] = []
        for idx, phase in enumerate(self.STAGES):
            seg = Segment(
                parent_task_id=task.task_id,
                content_payload=task.raw_command,
                sequence_order=idx,
                phase=phase,
                tail_context={
                    # 批次7：尾巴约定 thought_trace 列表（跨阶段思考轨迹）
                    "thought_trace": [],
                    "current_phase": phase,
                    "stage_index": idx,
                    "total_stages": len(self.STAGES),
                },
            )
            segments.append(seg)
        return segments


class DeepThinkMerger:
    """历史骨架：DEEP_THINK 任务结果合并（读取 thought_trace 组装结构化报告）。"""

    def merge(self, traces) -> str:
        from models.deep_think import DeepThinkTail

        report = "# 🧠 深度思考与分析报告\n\n"

        all_issues = []
        all_traces = []
        for t in traces:
            if isinstance(t, DeepThinkTail):
                all_issues.extend(t.found_issues)
                all_traces.extend(t.thought_trace)
            elif isinstance(t, dict):
                all_issues.extend(t.get("found_issues", []))
                all_traces.extend(t.get("thought_trace", []))

        report += "## 发现的潜在问题\n"
        for idx, issue in enumerate(set(all_issues), 1):
            report += f"{idx}. {issue}\n"

        report += "\n## 思考轨迹还原\n"
        for trace in all_traces:
            report += f"> {trace}\n\n"

        report += "## 最终改进建议\n"
        report += "基于以上推理，建议在对应章节强化逻辑一致性，或修补背景设定缺漏。\n"

        return report
