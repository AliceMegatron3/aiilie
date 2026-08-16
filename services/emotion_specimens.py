"""
services/emotion_specimens.py — 情感标本闭环(P5)
====================================================
情绪的感情的"文字化标本":某种情绪表达的**手法实例**——带溯源的
原文摘录 + 为什么奏效的手法注解 + 合法变体空间。用于写作时的
少样本锚定(每拍1-2枚封顶),学手法不学词句。

双来源(讨论定稿):书库量化旁路收割(scope=library) +
作者确认回流段落精炼(scope=project,优先)。

三护栏:
- 注入预算:query/build 侧 limit 封顶(默认2);
- 防抄袭:提示块明示"学手法不学词句"+变异槽位随行;
- 证据分级:标本以draft入库,多源互证后才升reviewed/approved(卡片状态机同律)。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_EXPRESSION_MODES = ("dialogue", "action", "interior", "scene")
_INTENSITY_BANDS = ("low", "mid", "high", "chaotic")


class EmotionSpecimen(BaseModel):
    """情感标本(InfoCard subtype=emotion_specimen 的 payload 契约)。"""
    emotion_type: str = Field(description="情绪类型,挂情感本体词汇(如 悲恸/隐忍/狂喜)")
    intensity_band: str = Field(default="mid", description="强度带:low/mid/high/chaotic")
    expression_mode: str = Field(default="scene", description="表达方式:dialogue/action/interior/scene")
    specimen_text: str = Field(description="原文摘录(手法载体)")
    craft_note: str = Field(default="", description="为什么奏效(手法注解)")
    variation_slots: list[str] = Field(default_factory=list, description="合法变体空间")
    source_anchor: str = Field(default="", description="溯源(书名/章节/URL)")
    scope_level: str = Field(default="library", description="library=书库挖掘/project=作者作品")


_FALLBACK_PROMPT = (
    "你是文学手法分析师。从以下文本中识别最有手法价值的情绪表达片段,"
    "每片给出:情绪类型、强度带(low/mid/high/chaotic)、表达方式"
    "(dialogue/action/interior/scene)、原文摘录(≤80字)、手法注解(为什么奏效)、变体空间。\n"
    '仅输出JSON数组:[{"emotion_type":"...","intensity_band":"mid","expression_mode":"dialogue",'
    '"specimen_text":"...","craft_note":"...","variation_slots":["..."]}]\n'
    "最多3片,没有合适片段输出 []。\n\n文本:\n{content}"
)


def _render_extract_prompt(text: str) -> str:
    try:
        from services.prompt_template_manager import prompt_manager

        return prompt_manager.render_or_fallback(
            "emotion_specimen_extract", {"content": text},
            lambda t=text: _FALLBACK_PROMPT.format(content=t),
        )
    except Exception:
        return _FALLBACK_PROMPT.format(content=text)


def _parse_specimens(raw: str) -> list[EmotionSpecimen]:
    """宽松解析 LLM 产出(容忍markdown围栏/前后杂文),非法条目丢弃。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            spec = EmotionSpecimen(
                emotion_type=str(item.get("emotion_type", "")).strip(),
                intensity_band=(
                    str(item.get("intensity_band", "mid"))
                    if str(item.get("intensity_band", "mid")) in _INTENSITY_BANDS else "mid"
                ),
                expression_mode=(
                    str(item.get("expression_mode", "scene"))
                    if str(item.get("expression_mode", "scene")) in _EXPRESSION_MODES else "scene"
                ),
                specimen_text=str(item.get("specimen_text", "")).strip(),
                craft_note=str(item.get("craft_note", "")).strip(),
                variation_slots=[str(v) for v in (item.get("variation_slots") or [])][:5],
            )
            if spec.emotion_type and spec.specimen_text:
                out.append(spec)
        except Exception:
            continue
    return out[:3]


async def harvest_specimens(
    dispatcher: Any, text: str, source_anchor: str = "",
    scope_level: str = "library",
) -> list[EmotionSpecimen]:
    """从文本收割情感标本(量化旁路:BookQuantifier逐段过全文时可顺带调用)。"""
    if dispatcher is None or not (text or "").strip():
        return []
    try:
        raw = await dispatcher.dispatch(_render_extract_prompt(text[:4000]), override_mode="think")
    except Exception as exc:
        logger.warning("[EmotionSpecimens] 收割调度失败(返回空): %s", exc)
        return []
    specs = _parse_specimens(str(raw))
    for s in specs:
        s.source_anchor = source_anchor
        s.scope_level = scope_level
    return specs


async def save_specimen(
    indexer: Any, specimen: EmotionSpecimen, project_id: str = "",
    status: str = "draft",
) -> str | None:
    """标本落库:InfoCard(subtype=emotion_specimen),draft强制过作者审。"""
    if indexer is None:
        return None
    try:
        from models.cards import InfoCard

        card = InfoCard(
            card_sub_type="emotion_specimen",
            category="style",
            content=specimen.specimen_text[:200],
            source_book_id=specimen.source_anchor or "emotion_specimen",
            payload={**specimen.model_dump(), "project_id": project_id,
                     "needs_author_review": True},
            summary=f"[情感标本] {specimen.emotion_type}/{specimen.expression_mode}",
            scope_level=specimen.scope_level,
            status=status,  # 永不自动approved,互证升档走卡片状态机
        )
        return await indexer.save_card(card)
    except Exception as exc:
        logger.warning("[EmotionSpecimens] 标本落库失败: %s", exc)
        return None


async def link_specimen(indexer: Any, specimen_card_id: str, related_ids: list[str]) -> int:
    """索引化:标本↔人物/情节卡连线(CardRelation)。"""
    if indexer is None:
        return 0
    linked = 0
    for target in related_ids:
        try:
            await indexer.upsert_relation(
                specimen_card_id, target, relation_type="expresses", note="P5情感标本索引"
            )
            linked += 1
        except Exception as exc:
            logger.warning("[EmotionSpecimens] 连线失败(%s→%s): %s", specimen_card_id, target, exc)
    return linked


async def query_specimens(
    indexer: Any, emotion: str | None = None, mode: str | None = None,
    project_id: str = "", limit: int = 2,
) -> list[EmotionSpecimen]:
    """检索标本:项目级优先、库级补充;limit默认2(注入预算纪律)。"""
    if indexer is None:
        return []
    limit = max(1, min(int(limit), 2))  # 硬顶2枚(注入预算纪律)
    specs: list[EmotionSpecimen] = []
    try:
        rows = await indexer.search_cards(
            card_type="info", subtype="emotion_specimen", limit=30
        )
    except Exception as exc:
        logger.warning("[EmotionSpecimens] 检索失败: %s", exc)
        return []
    for row in rows:
        if len(specs) >= limit:
            break
        try:
            detail = await indexer.get_card_detail(row["card_id"]) or {}
        except Exception:
            continue
        payload = detail.get("payload") or {}
        if not payload.get("emotion_type"):
            continue
        if emotion and emotion not in str(payload.get("emotion_type", "")):
            continue
        if mode and str(payload.get("expression_mode")) != mode:
            continue
        pid = str(payload.get("project_id") or "")
        if project_id and pid and pid != project_id:
            continue
        try:
            specs.append(EmotionSpecimen.model_validate(payload))
        except Exception:
            continue
    # 项目级优先(project_scope有值者排前)
    specs.sort(key=lambda s: s.scope_level != "project")
    return specs


def build_specimen_block(specimens: list[EmotionSpecimen]) -> str:
    """少样本注入块:防抄袭框架(学手法不学词句)+变体提示。"""
    if not specimens:
        return ""
    lines = ["【情绪手法标本(学其手法,严禁照抄词句)】"]
    for s in specimens:
        lines.append(
            f"- {s.emotion_type}({s.expression_mode}):手法={s.craft_note or '见摘录结构'};"
            f"可变处={','.join(s.variation_slots) or '人物/场景/语气'}"
        )
    return "\n".join(lines) + "\n\n"


async def emotion_context_block(
    indexer: Any, target_emotion: str | None, recent_emotions: list[str] | None = None,
    project_id: str = "", limit: int = 2,
) -> str:
    """生成时的情绪上下文入口:目标情绪优先,近帧情绪兜底。"""
    for emotion in [target_emotion] + list(recent_emotions or []):
        if not emotion:
            continue
        specs = await query_specimens(indexer, emotion=emotion, project_id=project_id, limit=limit)
        if specs:
            return build_specimen_block(specs)
    return ""


# 确定性情绪推断(供 TriggerContext 情绪维;能用算的不用LLM)
_EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "悲": ("悲", "哭", "泪", "死", "别离", "恸"),
    "怒": ("怒", "恨", "愤", "杀意"),
    "惧": ("惧", "怕", "惶", "惊"),
    "喜": ("喜", "笑", "重逢", "成婚"),
    "隐忍": ("隐忍", "克制", "压抑", "咽下"),
}


def infer_emotion_state(text: str) -> str | None:
    """关键词法情绪推断:命中最多者胜(平局取先)。"""
    best, best_hits = None, 0
    for emotion, kws in _EMOTION_KEYWORDS.items():
        hits = sum(1 for k in kws if k in (text or ""))
        if hits > best_hits:
            best, best_hits = emotion, hits
    return best
