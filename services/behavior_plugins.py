"""
services/behavior_plugins.py — 行为插件注册表与编辑通行引擎(P1)
====================================================================
打磨类行为插件以"编辑通行"形态运行(用户拍板):生成稿依次通过
命中的打磨插件,每通行一次做确定性验收,不过验收自动回退该次
通行前文本——保底不伤稿。全程 RunRecord 落账(P2 统计原料)。

约束:
- 出厂四件为手工精写(CRAFTED/ACTIVE),质量锚点;
- 浅知识(QUANTIFIED)注册时强制 CANDIDATE,永不直接 active;
- 灰度采样确定性(同插件+同任务可复现);
- 模板渲染失败回退内置人设,调度器缺失跳过打磨不阻断创作。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from models.behavior_plugin import (
    BehaviorPluginRunRecord,
    BehaviorPluginSpec,
    PluginSource,
    PluginStatus,
    TriggerContext,
)

logger = logging.getLogger(__name__)

# 出厂四件的内置人设兜底(模板缺失/渲染失败时使用)
_BUILTIN_FALLBACKS: dict[str, str] = {
    "behavior_editor_tempo": "你是节拍编辑:只调句长张弛与段落密度,对位节奏目标,不改情节。直接输出完整正文。\n\n文稿:\n{content}",
    "behavior_editor_style": "你是文风编辑:修正出戏的书面腔与时代错位用词,在文风区间内允许自然变化。直接输出完整正文。\n\n文稿:\n{content}",
    "behavior_editor_sensory": "你是场景编辑:在关键场景增补克制的感官细节(每处不超两句),优先落在环境锚点。直接输出完整正文。\n\n文稿:\n{content}",
    "behavior_editor_dialogue": "你是 对白编辑:去书面腔、按人物声纹区分口吻、给关键对白留潜台词,立场与信息量不变。直接输出完整正文。\n\n文稿:\n{content}",
}


def _builtin_specs() -> list[BehaviorPluginSpec]:
    return [
        BehaviorPluginSpec(
            plugin_id="behavior_editor_tempo", name="节拍整饬",
            description="句长节奏/段落密度/张弛控制,对位本章节奏目标",
            prompt_template_id="behavior_editor_tempo",
            status=PluginStatus.ACTIVE, source=PluginSource.CRAFTED, run_order=10,
        ),
        BehaviorPluginSpec(
            plugin_id="behavior_editor_style", name="文风一致",
            description="用语习惯/叙述口吻/时代语感对齐项目风格区间",
            prompt_template_id="behavior_editor_style",
            status=PluginStatus.ACTIVE, source=PluginSource.CRAFTED, run_order=20,
        ),
        BehaviorPluginSpec(
            plugin_id="behavior_editor_sensory", name="感官细节增密",
            description="关键场景增补克制感官细节,对齐环境锚点(物价/器物/天候/地物)",
            prompt_template_id="behavior_editor_sensory",
            status=PluginStatus.ACTIVE, source=PluginSource.CRAFTED, run_order=30,
        ),
        BehaviorPluginSpec(
            plugin_id="behavior_editor_dialogue", name="对话淬火",
            description="去书面腔/按声纹分口吻/潜台词打磨(P7声纹卡接入后增强)",
            prompt_template_id="behavior_editor_dialogue",
            status=PluginStatus.ACTIVE, source=PluginSource.CRAFTED, run_order=40,
        ),
    ]


class BehaviorPluginRegistry:
    """行为插件注册表(内存态;治理流转经 P2 API 落 SkillGovernance/状态存储)。"""

    def __init__(self) -> None:
        self._plugins: dict[str, BehaviorPluginSpec] = {}
        for spec in _builtin_specs():
            self._plugins[spec.plugin_id] = spec

    def register(self, spec: BehaviorPluginSpec, force: bool = False) -> BehaviorPluginSpec:
        """注册插件。浅知识(QUANTIFIED)强制 CANDIDATE——永不直接上岗。"""
        if spec.source == PluginSource.QUANTIFIED:
            spec.status = PluginStatus.CANDIDATE
            spec.gray_percent = 0
        if spec.plugin_id in self._plugins and not force:
            existing = self._plugins[spec.plugin_id]
            if existing.version >= spec.version:
                return existing
        self._plugins[spec.plugin_id] = spec
        logger.info("[BehaviorPlugins] 注册 %s (%s/%s)", spec.plugin_id, spec.kind, spec.status.value)
        return spec

    def set_status(self, plugin_id: str, status: PluginStatus, gray_percent: int = 0) -> BehaviorPluginSpec | None:
        spec = self._plugins.get(plugin_id)
        if spec is None:
            return None
        spec.status = status
        if status == PluginStatus.GRAY:
            spec.gray_percent = max(0, min(100, gray_percent))
        return spec

    def get(self, plugin_id: str) -> BehaviorPluginSpec | None:
        return self._plugins.get(plugin_id)

    def list_plugins(self, status: PluginStatus | None = None) -> list[BehaviorPluginSpec]:
        specs = sorted(self._plugins.values(), key=lambda s: (s.run_order, s.plugin_id))
        if status is None:
            return specs
        return [s for s in specs if s.status == status]

    def active_for(self, ctx: TriggerContext) -> list[BehaviorPluginSpec]:
        """按上下文选出本次生效的插件:ACTIVE 全量 + GRAY 采样命中 + 触发匹配。"""
        out = []
        for spec in self.list_plugins():
            if spec.status == PluginStatus.ACTIVE:
                if spec.trigger.matches(ctx):
                    out.append(spec)
            elif spec.status == PluginStatus.GRAY and spec.gray_hit(ctx.task_id):
                if spec.trigger.matches(ctx):
                    out.append(spec)
        return out


# 全局注册表(bootstrap 无需装配——出厂件常驻;治理产物经 API 注册)
behavior_plugin_registry = BehaviorPluginRegistry()


def _render_pass_prompt(spec: BehaviorPluginSpec, draft: str) -> str:
    """渲染打磨 prompt:模板→内置人设兜底。"""
    try:
        from services.prompt_template_manager import prompt_manager

        return prompt_manager.render_or_fallback(
            spec.prompt_template_id,
            {"content": draft},
            lambda s=spec, d=draft: _BUILTIN_FALLBACKS.get(
                s.prompt_template_id, "请只按编辑目标润色以下文稿,直接输出完整正文。\n\n文稿:\n" + d
            ).format(content=d),
        )
    except Exception as exc:
        logger.warning("[BehaviorPlugins] 模板 %s 渲染异常,回退内置人设: %s", spec.prompt_template_id, exc)
        return _BUILTIN_FALLBACKS.get(
            spec.prompt_template_id, "请只按编辑目标润色以下文稿,直接输出完整正文。\n\n文稿:\n{content}"
        ).format(content=draft)


async def apply_polish_passes(
    draft: str,
    dispatcher: Any,
    ctx: TriggerContext,
    registry: BehaviorPluginRegistry | None = None,
) -> tuple[str, list[BehaviorPluginRunRecord]]:
    """对文稿依次执行命中的编辑通行。

    返回 (最终文本, 运行记录列表)。任一通行失败/验收不过:
    回退该次通行前文本并落账原因,继续后续通行——保底不伤稿。
    调度器缺失或文稿为空:原样返回,不阻断创作。
    """
    records: list[BehaviorPluginRunRecord] = []
    text = (draft or "").strip()
    if not text or dispatcher is None:
        return draft or "", records
    reg = registry or behavior_plugin_registry
    for spec in reg.active_for(ctx):
        rec = BehaviorPluginRunRecord(
            plugin_id=spec.plugin_id, task_id=ctx.task_id,
            input_chars=len(text),
        )
        t0 = time.time()
        try:
            prompt = _render_pass_prompt(spec, text)
            output = await dispatcher.dispatch(prompt, override_mode="think")
            ok, reason = spec.acceptance.check(text, str(output or ""))
            rec.duration_ms = int((time.time() - t0) * 1000)
            rec.triggered = True
            rec.accepted = ok
            rec.reason = reason
            if ok:
                text = str(output).strip()
                rec.output_chars = len(text)
            else:
                rec.output_chars = len(str(output or ""))
                logger.info("[BehaviorPlugins] %s 验收未过(%s),回退通行前文本", spec.plugin_id, reason)
        except Exception as exc:
            rec.duration_ms = int((time.time() - t0) * 1000)
            rec.triggered = True
            rec.accepted = False
            rec.reason = f"error:{exc}"
            logger.warning("[BehaviorPlugins] %s 执行异常,回退: %s", spec.plugin_id, exc)
        records.append(rec)
    return text, records
