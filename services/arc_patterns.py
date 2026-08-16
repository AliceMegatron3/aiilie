"""
services/arc_patterns.py — 弧线模式注册表与卷级套用(P6)
==========================================================
作者手法指纹的载体:奇遇弧/副本弧/结伴任务弧等多章剧情骨架。
起伏配比经 ArcPattern.derive_budget_sequence 派生为整卷章级
预算序列——作者从"手写预算"升级为"选弧线微调"。

口径(讨论定稿):
- 六个手工出厂弧线为质量锚点(source=crafted/active);
- 量化挖掘的候选弧线 source=quantified/status=candidate,
  经治理通道(同浅知识律)方可转正;
- 参照系非模仿目标:作者选定本书弧线配方,指纹只帮定位。
"""
from __future__ import annotations

import logging

from models.narrative import ArcPattern, ArcStage, ChapterOutline

logger = logging.getLogger(__name__)


def _arc(name: str, description: str, stages: list[tuple[str, int]], variations: list[str]) -> ArcPattern:
    return ArcPattern(
        pattern_id=f"arc_builtin_{name}", name=name, description=description,
        stages=[ArcStage(name=n, tension_target=t) for n, t in stages],
        variation_slots=variations, source="crafted", status="active",
    )


BUILTIN_ARC_PATTERNS: dict[str, ArcPattern] = {
    p.pattern_id: p for p in [
        _arc("奇遇弧", "金庸式:角色于困顿中偶遇机缘,获得后经试炼付出代价,改变现状",
             [("困顿", 3), ("机缘", 5), ("获得", 6), ("试炼", 8), ("代价", 7), ("改变", 6)],
             ["机缘类型", "代价形式", "改变幅度"]),
        _arc("副本弧", "斗破式:进入封闭场层层历练,险胜收获,遗留问题引下一循环",
             [("入场", 4), ("层进", 6), ("隐患", 5), ("险胜", 9), ("收获", 6), ("遗留", 4)],
             ["封闭场性质", "层数", "遗留问题走向"]),
        _arc("结伴任务弧", "群像经典:被迫集结,磨合生隙,危机淬炼信任,同心完事",
             [("集结", 4), ("磨合", 5), ("危机", 7), ("信任", 8), ("同心", 9), ("完事", 5)],
             ["成员配置", "裂痕成因", "完事后的分合"]),
        _arc("复仇弧", "蒙冤潜伏布局反噬,了断之后的空落",
             [("蒙冤", 6), ("潜伏", 4), ("布局", 5), ("反噬", 8), ("了断", 10), ("空落", 5)],
             ["冤情性质", "布局手段", "空落处理"]),
        _arc("蜕变弧", "旧我生缝,挣扎破茧成新我,蜕变有其代价",
             [("旧我", 3), ("裂缝", 5), ("挣扎", 7), ("破茧", 9), ("新我", 6), ("代价", 5)],
             ["裂缝来源", "挣扎形态", "新我定义"]),
        _arc("归乡弧", "远走折转重临故地,面对旧账,和解或清算后落定",
             [("远走", 4), ("折转", 5), ("重临", 6), ("旧账", 8), ("了结", 9), ("落定", 5)],
             ["离乡原因", "旧账内容", "了结方式"]),
    ]
}


def get_arc_pattern(pattern_id: str) -> ArcPattern | None:
    return BUILTIN_ARC_PATTERNS.get(pattern_id)


def register_arc_candidate(pattern: ArcPattern) -> ArcPattern:
    """量化挖掘的候选弧线入池:强制 candidate(永不直接active)。"""
    pattern.source = "quantified"
    pattern.status = "candidate"
    BUILTIN_ARC_PATTERNS[pattern.pattern_id] = pattern
    logger.info("[ArcPatterns] 候选弧线入池: %s", pattern.name)
    return pattern


async def apply_arc_to_volume(
    narrative_service, project_id: str, volume_id: str,
    pattern: ArcPattern, n_chapters: int, start_number: int = 1,
) -> list[ChapterOutline]:
    """套用弧线到卷:派生章级预算序列并写弧线绑定。

    已存在的章(按章号)只更新预算与弧线绑定,不覆盖拍纲/状态;
    缺失的章创建空纲要占位(OUTLINE,待作者填拍)。
    """
    if n_chapters <= 0:
        return []
    budgets = pattern.derive_budget_sequence(n_chapters)
    existing = {
        c.chapter_number: c
        for c in await narrative_service.get_chapters(project_id, volume_id=volume_id)
    }
    saved: list[ChapterOutline] = []
    for i in range(n_chapters):
        number = start_number + i
        stage = pattern.stage_for_chapter(i, n_chapters)
        chapter = existing.get(number)
        if chapter is None:
            chapter = ChapterOutline(
                project_id=project_id, volume_id=volume_id,
                chapter_number=number, title=f"第{number}章",
            )
        chapter.budget = budgets[i]
        chapter.arc_pattern_id = pattern.pattern_id
        chapter.arc_stage = stage.name
        saved.append(await narrative_service.save_chapter(chapter))
    logger.info(
        "[ArcPatterns] 弧线「%s」已套用: 项目%s 卷%s 共%d章(起始第%d章)",
        pattern.name, project_id, volume_id, n_chapters, start_number,
    )
    return saved


def detect_arc_candidates_from_actuals(
    chapters: list[ChapterOutline], pattern_id: str | None = None,
) -> ArcPattern | None:
    """从章级实绩序列确定性识别简化三段弧(蓄势-高潮-回落)。

    量化挖掘通道的最小实现:峰值检测→三阶段张力目标,
    产出 candidate 弧线(经 register_arc_candidate 入池,治理后可用)。
    """
    tensions = [
        c.actuals.conflict_intensity if c.actuals else None for c in chapters
    ]
    if len([t for t in tensions if t is not None]) < 3:
        return None
    valid = [(i, t) for i, t in enumerate(tensions) if t is not None]
    peak_i, peak_t = max(valid, key=lambda x: x[1])
    pre = [t for _, t in valid if _ < peak_i]
    post = [t for _, t in valid if _ > peak_i]
    if not pre or not post:
        return None
    pre_avg = sum(pre) // len(pre)
    post_avg = sum(post) // len(post)
    if peak_t - max(pre_avg, post_avg) < 2:
        return None  # 无明显起伏,不产候选
    return ArcPattern(
        pattern_id=pattern_id or f"arc_mined_{peak_i}_{peak_t}",
        name=f"[量化候选] 蓄势{pre_avg}-高潮{peak_t}-回落{post_avg}",
        description=f"从{len(valid)}章实绩序列峰值检测派生的简化三段弧",
        stages=[
            ArcStage(name="蓄势", tension_target=pre_avg),
            ArcStage(name="高潮", tension_target=peak_t),
            ArcStage(name="回落", tension_target=post_avg),
        ],
        variation_slots=["蓄势长度", "峰值位置", "回落斜率"],
    )
