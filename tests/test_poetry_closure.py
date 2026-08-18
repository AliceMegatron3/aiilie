"""Poetry 诗词研究闭环测试（临时库/临时数据，不触碰真实数据）。

覆盖：持久化+多版本校勘+provenance、音韵分类、韵部候选、格律验证、典故回链、
评测集跑分、生成/改写。
"""
from __future__ import annotations

from services.poetry_evaluator import run_evaluation
from services.poetry_generator import generate_poem, rewrite_poem
from services.poetry_phonology import (
    classify_char,
    find_allusions,
    find_rhyme_candidates,
    validate_meter,
)
from services.poetry_store import PoetryStore


# ── 持久化 + Ledger provenance ─────────────────────────────
async def test_store_save_get_list_and_version_collation(tmp_db):
    store = PoetryStore(tmp_db)
    saved = await store.save_work(title="静夜思", author="李白", dynasty="唐", style="五绝", text="床前明月光，\n疑是地上霜。")
    work_id = saved["work"]["work_id"]
    assert saved["edition"]["version"] == 1

    # 第二版本（校勘）
    saved2 = await store.save_edition(work_id=work_id, text="床前明月光，\n疑是地上霜。\n举头望明月，\n低头思故乡。")
    assert saved2["version"] == 2

    got = await store.get_work(work_id)
    assert got is not None
    assert len(got["editions"]) == 2
    assert [e["version"] for e in got["editions"]] == [1, 2]
    assert got["editions"][-1]["text"].startswith("床前明月光")

    works = await store.list_works()
    assert any(w["work_id"] == work_id for w in works)


async def test_store_provenance_write(tmp_db):
    store = PoetryStore(tmp_db)
    saved = await store.save_work(title="登鹳雀楼", text="白日依山尽，\n黄河入海流。")
    edition_id = saved["edition"]["edition_id"]
    result = await store.add_ledger_provenance(
        edition_id,
        source_document_id="doc_sanguozhi_1",
        passage_id="passage_000",
        extraction_run_id="run_poetry_1",
    )
    assert result["ok"] is True
    got = await store.get_work(saved["work"]["work_id"])
    edition = got["editions"][-1]
    sources = edition["provenance"].get("sources", [])
    assert sources and sources[0]["source_document_id"] == "doc_sanguozhi_1"
    assert sources[0]["passage_id"] == "passage_000"
    assert sources[0]["extraction_run_id"] == "run_poetry_1"


# ── 音韵：中古音(平水韵近似)/普通话/平仄分离 ───────────────
def test_phonology_classify_char():
    cases = {
        "平": ("平", "八庚", False),
        "仄": ("仄", "十三职", False),
        "天": ("平", "一先", False),
        "地": ("仄", "四寘", False),
        "一": ("仄", "四质", False),   # 入声归仄
        "白": ("仄", "十一陌", False),  # 入声归仄
        "日": ("仄", "四质", False),   # 入声归仄
    }
    for char, (expect_pz, expect_grp, expect_approx) in cases.items():
        info = classify_char(char)
        assert info["ping_ze"] == expect_pz, f"{char}: {info}"
        assert info["rhyme_group"] == expect_grp, f"{char}: {info}"
        assert info["approx"] is expect_approx
        assert info["source"] == "平水韵"


def test_phonology_mandarin_approximation_flag():
    # 「床」在普通话表中但未收录平水韵 → 普通话声调近似 + approx=True
    info = classify_char("床")
    assert info["ping_ze"] == "平"
    assert info["source"] == "普通话近似" and info["approx"] is True
    assert info["rhyme_group"] is None


def test_find_rhyme_candidates_same_group():
    candidates = find_rhyme_candidates("风")
    assert candidates, "一东韵应有候选字"
    assert "风" not in candidates
    for ch in candidates:
        assert classify_char(ch)["rhyme_group"] == "一东"
    assert len(candidates) <= 20

    candidates_guang = find_rhyme_candidates("光", max=5)
    assert 0 < len(candidates_guang) <= 5
    assert all(classify_char(ch)["rhyme_group"] == "七阳" for ch in candidates_guang)


# ── 格律验证 ───────────────────────────────────────────────
def test_validate_meter_standard_five_quatrain():
    text = "白日依山尽，\n黄河入海流。\n欲穷千里目，\n更上一层楼。"
    result = validate_meter(text, "五绝")
    rules = {r["name"]: r for r in result["rules"]}
    assert result["ok"] is True
    assert rules["line_count"]["status"] == "pass"
    assert rules["line_lengths"]["status"] == "pass"
    assert rules["rhyme_consistency"]["status"] == "pass"
    assert result["summary"]["fail"] == 0


def test_validate_meter_wrong_length_and_rhyme():
    bad_len = validate_meter("春江花月夜，\n万里入秋。\n明月青山外，\n清泉石上。", "五绝")
    rules = {r["name"]: r for r in bad_len["rules"]}
    assert rules["line_lengths"]["status"] == "fail"

    bad_rhyme = validate_meter("春江花月夜，\n万里入青天。\n明月青山外，\n清泉石上红。", "五绝")
    rules = {r["name"]: r for r in bad_rhyme["rules"]}
    assert rules["rhyme_consistency"]["status"] == "fail"


def test_validate_meter_ci_profile():
    text = "花落春江夜未眠，\n故人千里共婵娟。\n相思一片青山外，\n明月何时照客船。\n风满袖，\n雨如烟。\n孤舟一叶白云边。\n天高地远无从问，\n谁寄相思到日边。"
    result = validate_meter(text, "鹧鸪天")
    rules = {r["name"]: r for r in result["rules"]}
    assert result["line_count"] == 9
    assert rules["line_lengths"]["status"] == "pass"
    assert rules["rhyme_consistency"]["status"] == "pass"


# ── 典故候选与出处回链 ─────────────────────────────────────
def test_find_allusions_hits_and_backlink():
    text = "老骥伏枥志千里，关关雎鸠在河洲。"
    hits = find_allusions(text)
    phrases = [h["phrase"] for h in hits]
    assert "老骥伏枥" in phrases
    assert "关关雎鸠" in phrases
    for hit in hits:
        assert hit["source"], "典故应带出处"
        assert hit["position"] >= 0
    item = next(h for h in hits if h["phrase"] == "老骥伏枥")
    assert "曹操" in item["source"] or "龟虽寿" in item["source"]


# ── 评测集 ─────────────────────────────────────────────────
def test_run_evaluation_pass_rate_positive():
    result = run_evaluation()
    assert result["total"] > 0
    assert result["pass_rate"] > 0
    assert "per_item" in result and len(result["per_item"]) == result["total"]


# ── 生成 / 改写 ────────────────────────────────────────────
def test_generate_poem_shape():
    result = generate_poem("五绝", "春")
    assert len(result["lines"]) == 4
    assert all(len(line) == 5 for line in result["lines"])
    assert result["text"]
    assert result["engine"] == "heuristic"

    seven = generate_poem("七绝", "秋")
    assert len(seven["lines"]) == 4
    assert all(len(line) == 7 for line in seven["lines"])


def test_rewrite_poem_gives_advice_on_broken_rhyme():
    text = "春江花月夜，\n万里入青天。\n明月青山外，\n清泉石上红。"
    result = rewrite_poem(text, target="prosody_fix")
    assert result["issue_count"] >= 1
    rhyme_issues = [i for i in result["issues"] if "韵" in i["issue"]]
    assert rhyme_issues, "错韵应给出韵脚改写建议"
    assert any(i["suggestions"] for i in result["issues"])
