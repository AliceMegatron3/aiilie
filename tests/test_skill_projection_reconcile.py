"""
tests/test_skill_projection_reconcile.py — 旧投影与治理源全量对账测试
====================================================================
覆盖：
1. reconcile_old_projections 枚举三大旧投影来源（universal_skills / InfoCard /
   行为插件 override JSON），未映射旧技能幂等导入为治理候选；
2. 重复调用不重复导入（skg_legacy_map 幂等）；
3. 迁移后源行发生漂移 → changed_after_migration 记录；
4. list_projection_gaps 纯只读枚举缺口（不写库）。

全部使用临时库 / 临时 appdata / 临时索引目录，不触碰真实数据。
"""
from __future__ import annotations

import json

import pytest

from services.skill_projection_reconcile import (
    reconcile_old_projections,
    list_projection_gaps,
    SRC_UNIVERSAL,
    SRC_INFO_CARD,
    SRC_BEHAVIOR_OVERRIDE,
)


@pytest.fixture
def legacy_config(monkeypatch):
    """用例内 monkeypatch 全局 config：临时索引器 save_cards 需落 cards 表。

    全局 ledger.authoritative=true 且 allow_legacy_card_projection=false 时，
    卡片只写 Ledger 不写 cards 表，search_cards 将读不到测试卡片。
    """
    import core.config_manager as cm

    cfg = cm.config_manager._config
    old_ledger = dict(cfg.get("ledger", {}) or {})
    cfg.setdefault("ledger", {})["authoritative"] = False
    cfg.setdefault("ledger", {})["allow_legacy_card_projection"] = True
    yield
    cfg["ledger"] = old_ledger


async def _make_env(tmp_path):
    """构造临时环境：主库 DatabaseManager + 治理库 + 索引器 + appdata 目录。"""
    from core.database import DatabaseManager
    from services.indexer import CardIndexer
    from services.skill_governance import SkillGovernance

    db = DatabaseManager(db_path=tmp_path / "tasks.db")
    await db.initialize()
    # 建 universal_skills 表（与 core/bootstrap._init_database_tables 对齐）
    await db.conn.execute(
        """CREATE TABLE IF NOT EXISTS universal_skills (
            skill_id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
            content TEXT NOT NULL, applicability TEXT NOT NULL,
            source_cards TEXT, created_at REAL NOT NULL
        )"""
    )
    await db.conn.commit()

    governance = SkillGovernance(tmp_path / "skill_governance.db")
    indexer = CardIndexer(index_dir=tmp_path / "index")
    await indexer.initialize()

    app_data = tmp_path / "appdata"
    app_data.mkdir(parents=True, exist_ok=True)
    return db, governance, indexer, app_data


async def _seed_universal_skills(db, rows: list[dict]) -> None:
    for row in rows:
        await db.conn.execute(
            "INSERT INTO universal_skills"
            " (skill_id, type, name, content, applicability, source_cards, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["skill_id"], row["type"], row["name"], row["content"],
                row["applicability"], row.get("source_cards", "[]"), row["created_at"],
            ),
        )
    await db.conn.commit()


async def _write_behavior_overrides(app_data, overrides: dict) -> None:
    (app_data / "behavior_plugin_overrides.json").write_text(
        json.dumps(overrides, ensure_ascii=False), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_reconcile_imports_all_sources_and_is_idempotent(tmp_path, legacy_config):
    from models.cards import InfoCard

    db, governance, indexer, app_data = await _make_env(tmp_path)
    try:
        # ── 来源1：universal_skills 两行 ──
        await _seed_universal_skills(
            db,
            [
                {
                    "skill_id": "us_001", "type": "TEMPLATE", "name": "宴会五步法",
                    "content": json.dumps({"prompt": "按以下步骤打磨宴会场景：一、二、三、四、五。"}),
                    "applicability": "宴会", "source_cards": json.dumps(["card_a"]),
                    "created_at": 1000.0,
                },
                {
                    "skill_id": "us_002", "type": "STYLE", "name": "三字短句节奏",
                    "content": json.dumps({"prompt": "多用三字短句收束段落。"}),
                    "applicability": "节奏", "source_cards": "[]", "created_at": 1001.0,
                },
            ],
        )
        # ── 来源2：行为插件 override JSON（两条）──
        await _write_behavior_overrides(
            app_data,
            {
                "bp_learned_1": {"status": "GRAY", "gray_percent": 50},
                "bp_learned_2": {"status": "ACTIVE", "gray_percent": 100},
            },
        )
        # ── 来源3：InfoCard 两张（novel_agent_skill 技能卡 + TEMPLATE 规则卡）──
        cards = [
            InfoCard(
                card_id="card_skill_1", source_book_id="novel_agent_skill_library",
                content="技能：情感冲突推进节奏", tags=["novel_agent_skill"],
                card_sub_type="novel_agent_skill",
            ),
            InfoCard(
                card_id="card_tpl_2", source_book_id="book_x",
                content="模板：登场节奏三拍", tags=["template"],
                card_sub_type="登场", knowledge_type="TEMPLATE",
            ),
        ]
        await indexer.save_cards(cards)

        # ── 第一次对账：全部 6 条导入 ──
        report = await reconcile_old_projections(governance, indexer, app_data, db=db)
        assert report["total_legacy"] == 6  # 2 universal + 2 override + 2 infocard
        assert len(report["imported_candidates"]) == 6
        assert report["already_migrated"] == []
        assert report["changed_after_migration"] == []
        assert report["errors"] == []
        assert report["projections_remaining"] == []

        # skg_legacy_map 三条来源都有映射
        conn = governance._get_conn()
        mapped = conn.execute("SELECT COUNT(*) FROM skg_legacy_map").fetchone()[0]
        assert mapped == 6
        for source, legacy_id in (
            (SRC_UNIVERSAL, "us_001"),
            (SRC_INFO_CARD, "card_skill_1"),
            (SRC_BEHAVIOR_OVERRIDE, "behavior_plugin:bp_learned_1"),
        ):
            row = conn.execute(
                "SELECT candidate_id FROM skg_legacy_map WHERE source_system=? AND legacy_id=?",
                (source, legacy_id),
            ).fetchone()
            assert row is not None, f"{source}:{legacy_id} 未建立映射"
        assert len(governance.list_candidates()) == 6

        # ── 第二次对账：幂等，不再重复导入 ──
        report2 = await reconcile_old_projections(governance, indexer, app_data, db=db)
        assert report2["total_legacy"] == 6
        assert report2["imported_candidates"] == []
        assert len(report2["already_migrated"]) == 6
        assert len(governance.list_candidates()) == 6  # 未重复导入
    finally:
        await db.close()
        await indexer.close()
        governance.close()


@pytest.mark.asyncio
async def test_reconcile_detects_changed_after_migration(tmp_path, legacy_config):
    db, governance, indexer, app_data = await _make_env(tmp_path)
    try:
        await _seed_universal_skills(
            db,
            [
                {
                    "skill_id": "us_chg", "type": "TEMPLATE", "name": "旧技能",
                    "content": json.dumps({"prompt": "旧版本内容，用于漂移检测。"}),
                    "applicability": "x", "source_cards": "[]", "created_at": 1000.0,
                },
            ],
        )
        first = await reconcile_old_projections(governance, indexer, app_data, db=db)
        assert len(first["imported_candidates"]) == 1

        # 源行在迁移后被修改（内容漂移）→ 应被标记 changed_after_migration
        await db.conn.execute(
            "UPDATE universal_skills SET content = ? WHERE skill_id = 'us_chg'",
            (json.dumps({"prompt": "新版本内容，源行发生漂移。"}),),
        )
        await db.conn.commit()

        second = await reconcile_old_projections(governance, indexer, app_data, db=db)
        assert second["already_migrated"] == ["us_chg"]
        assert second["imported_candidates"] == []
        assert len(second["changed_after_migration"]) == 1
        entry = second["changed_after_migration"][0]
        assert entry["source"] == SRC_UNIVERSAL
        assert entry["legacy_id"] == "us_chg"
        assert entry["old_hash"] != entry["new_hash"]
    finally:
        await db.close()
        await indexer.close()
        governance.close()


@pytest.mark.asyncio
async def test_list_projection_gaps_is_read_only(tmp_path):
    db, governance, indexer, app_data = await _make_env(tmp_path)
    try:
        await _seed_universal_skills(
            db,
            [
                {
                    "skill_id": "us_gap", "type": "TEMPLATE", "name": "缺口技能",
                    "content": json.dumps({"prompt": "尚未进入治理源的旧技能。"}),
                    "applicability": "x", "source_cards": "[]", "created_at": 1000.0,
                },
                {
                    "skill_id": "us_mapped", "type": "TEMPLATE", "name": "已迁移技能",
                    "content": json.dumps({"prompt": "已迁移的旧技能内容。"}),
                    "applicability": "x", "source_cards": "[]", "created_at": 1001.0,
                },
            ],
        )
        # 一条已映射，验证 mapped 计数
        governance.import_legacy_skill(
            {
                "skill_id": "us_mapped", "name": "已迁移技能",
                "content": {"prompt": "已迁移的旧技能内容。"},
            },
            source=SRC_UNIVERSAL,
        )

        report = await list_projection_gaps(governance, indexer, app_data, db=db)
        assert report["total_legacy"] == 2
        assert report["mapped"] == 1
        assert report["gaps_count"] == 1
        assert report["gaps"][0]["source"] == SRC_UNIVERSAL
        assert report["gaps"][0]["legacy_id"] == "us_gap"

        # 只读：绝不写库
        conn = governance._get_conn()
        assert conn.execute("SELECT COUNT(*) FROM skg_candidates").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM skg_legacy_map").fetchone()[0] == 1
    finally:
        await db.close()
        await indexer.close()
        governance.close()
