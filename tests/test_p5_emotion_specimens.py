"""P5 情感标本闭环回归测试。"""
from __future__ import annotations

import pytest

from services.emotion_specimens import (
    EmotionSpecimen,
    _parse_specimens,
    build_specimen_block,
    emotion_context_block,
    harvest_specimens,
    infer_emotion_state,
    link_specimen,
    query_specimens,
    save_specimen,
)


class _FakeDispatcher:
    def __init__(self, output="[]"):
        self.output = output
        self.prompts = []

    async def dispatch(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.output


_GOOD = """```json
[{"emotion_type":"隐忍","intensity_band":"mid","expression_mode":"dialogue",
  "specimen_text":"他顿了顿,把后半句咽了回去,只道:天冷,回吧。",
  "craft_note":"以收束句截断情绪,克制感由未说出的部分承担",
  "variation_slots":["语气","场合","称谓"]}]
```"""


class _FakeIndexer:
    def __init__(self, details: dict[str, dict]):
        self.details = details
        self.saved = []
        self.relations = []

    async def search_cards(self, **kw):
        return [{"card_id": cid} for cid in self.details]

    async def get_card_detail(self, card_id):
        return self.details.get(card_id)

    async def save_card(self, card):
        self.saved.append(card)
        return f"card_{len(self.saved)}"

    async def upsert_relation(self, source, target, **kw):
        self.relations.append((source, target, kw.get("relation_type")))
        return f"{source}:{target}"


def _detail(emotion, mode="dialogue", scope="project", pid="p1"):
    spec = EmotionSpecimen(
        emotion_type=emotion, expression_mode=mode, scope_level=scope,
        specimen_text="摘录", craft_note="手法", variation_slots=["语气"],
        source_anchor="书A/第3章",
    )
    return {"payload": {**spec.model_dump(), "project_id": pid}}


# ── 收割与解析 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_harvest_parses_markdown_fenced_json():
    d = _FakeDispatcher(output=_GOOD)
    specs = await harvest_specimens(d, "他顿了顿……回吧。", source_anchor="书A/第3章")
    assert len(specs) == 1
    s = specs[0]
    assert s.emotion_type == "隐忍" and s.expression_mode == "dialogue"
    assert s.source_anchor == "书A/第3章" and s.scope_level == "library"
    assert "情绪" in d.prompts[0]


def test_parse_robustness():
    assert _parse_specimens("垃圾输出") == []
    assert _parse_specimens("[]") == []
    # 非法字段容忍:强度带/表达方式非法回退默认
    specs = _parse_specimens('[{"emotion_type":"怒","intensity_band":"爆表","expression_mode":"rap","specimen_text":"x"}]')
    assert specs and specs[0].intensity_band == "mid" and specs[0].expression_mode == "scene"


@pytest.mark.asyncio
async def test_save_and_link_specimen():
    indexer = _FakeIndexer({})
    spec = EmotionSpecimen(
        emotion_type="悲", specimen_text="她把碗翻过来。",
        craft_note="空碗意象承重", source_anchor="作者稿/第2章", scope_level="project",
    )
    card_id = await save_specimen(indexer, spec, project_id="p1")
    assert card_id == "card_1"
    card = indexer.saved[0]
    assert card.card_sub_type == "emotion_specimen"
    assert card.status == "draft" and card.payload["needs_author_review"] is True
    linked = await link_specimen(indexer, card_id, ["char_9", "plot_2"])
    assert linked == 2
    assert ("card_1", "char_9", "expresses") in indexer.relations


# ── 检索与注入预算 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_budget_cap_and_project_priority():
    details = {
        "c_lib": _detail("悲", scope="library"),
        "c_proj": _detail("悲", scope="project"),
        "c_other": _detail("怒"),
        "c_foreign": _detail("悲", pid="p2"),
    }
    indexer = _FakeIndexer(details)
    specs = await query_specimens(indexer, emotion="悲", project_id="p1", limit=99)
    # 硬顶2枚 + 项目过滤(p2剔除) + 项目级优先
    assert len(specs) == 2
    assert specs[0].scope_level == "project"
    # 表达方式过滤
    only_dialogue = await query_specimens(indexer, emotion="悲", mode="dialogue", project_id="p1")
    assert all(s.expression_mode == "dialogue" for s in only_dialogue)


@pytest.mark.asyncio
async def test_emotion_context_block_fallback_chain():
    details = {"c_anger": _detail("怒")}
    indexer = _FakeIndexer(details)
    # 目标情绪无标本→近帧情绪兜底命中
    block = await emotion_context_block(indexer, "悲", recent_emotions=["怒"], project_id="p1")
    assert "学其手法" in block and "严禁照抄" in block
    # 全无命中→空串
    assert await emotion_context_block(indexer, "惧") == ""


def test_infer_emotion_state_deterministic():
    assert infer_emotion_state("写一场泪别,她哭着咽下所有委屈") == "悲"
    assert infer_emotion_state("他怒火中烧,杀意起伏") == "怒"
    assert infer_emotion_state("平平无奇的过渡段") is None


@pytest.mark.asyncio
async def test_supervisor_injects_specimen_block():
    from services.novel_supervisor import NovelSupervisor

    indexer = _FakeIndexer({"c_grief": _detail("悲")})
    outputs = [f"【稿{i}】" + "正" * 60 for i in range(4)] + ["抛光。" * 25] * 4
    dispatcher = _FakeDispatcher(output="")  # 仅记录prompt
    disp_outputs = iter(outputs)

    async def _dispatch(prompt, **kw):
        dispatcher.prompts.append(prompt)
        return next(disp_outputs)

    dispatcher.dispatch = _dispatch
    sup = NovelSupervisor(indexer=indexer, optimization_applier=None, dispatcher=dispatcher)
    res = await sup.execute_creation(
        task_id="p5-sup", cmd_text="写一段生离死别的泪别场景", project_id="p1",
    )
    assert res["success"] is True
    first_prompt = dispatcher.prompts[0]
    assert "情绪手法标本" in first_prompt and "悲" in first_prompt
