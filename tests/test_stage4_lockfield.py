"""阶段4世界观锁定场回归测试(讨论稿20260816 第六章)。

覆盖:锁定场配置版本化(单场+编年史)、must 集物化(证据过滤+
确定性排序+版本缓存)、偏离登记生命周期(建议→确认/驳回)。
"""
from __future__ import annotations

import pytest

from core.database import DatabaseManager
from models.lockfield import (
    DivergenceRecord,
    DivergenceStatus,
    LockFieldConfig,
    LockFieldTypeRule,
    evidence_at_least,
)
from services.lockfield import LockFieldService


class _FakeIndexer:
    """内存假索引器:按 library_id 返回预置卡片行。"""

    def __init__(self, libraries: dict[str, list[dict]]):
        self.libraries = libraries
        self.queries: list[dict] = []

    async def search_cards(self, **kwargs):
        self.queries.append(kwargs)
        lib = kwargs.get("library_id")
        return [dict(row) for row in self.libraries.get(lib, [])]


@pytest.fixture
async def svc(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "stage4.db")
    await db.initialize()
    service = LockFieldService(db)
    await service.initialize()
    try:
        yield service
    finally:
        await db.close()


def _lib_cards() -> dict[str, list[dict]]:
    return {
        "historical_materials": [
            {"card_id": "c-02", "evidence_level": "A", "content": "190年董卓迁都长安"},
            {"card_id": "c-01", "evidence_level": "B", "content": "东汉五铢钱制"},
            {"card_id": "c-03", "evidence_level": "D", "content": "野史轶闻(证据不足)"},
        ],
        "sanfu_zhi": [
            {"card_id": "s-01", "evidence_level": "B", "content": "长安城坊布局"},
        ],
    }


# ── 证据等级比较 ──────────────────────────────────────────────

def test_evidence_at_least():
    assert evidence_at_least("A", "B") and evidence_at_least("B", "B")
    assert not evidence_at_least("C", "B")
    assert not evidence_at_least("X", "B")  # 非法等级视为不足


# ── 配置版本化 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_field_save_bumps_version_and_logs(svc):
    config = LockFieldConfig(
        project_id="p1", name="乱世双璧·正史基底",
        locked_library_ids=["historical_materials"],
    )
    v1 = await svc.save_field(config)
    assert v1.version == 1

    v1.locked_library_ids = ["historical_materials", "sanfu_zhi"]
    v2 = await svc.save_field(v1, detail={"locked_library_ids": v1.locked_library_ids})
    assert v2.version == 2

    loaded = await svc.get_field("p1")
    assert loaded is not None and loaded.version == 2
    assert loaded.locked_library_ids == ["historical_materials", "sanfu_zhi"]

    versions = await svc.list_versions(loaded.field_id)
    assert [v.version for v in versions] == [2, 1]  # 倒序编年史
    assert versions[-1].change_type == "create"


# ── must 集物化 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_materialize_must_set_filters_and_sorts_deterministically(svc):
    svc.indexer = _FakeIndexer(_lib_cards())
    config = await svc.save_field(LockFieldConfig(
        project_id="p1", locked_library_ids=["historical_materials", "sanfu_zhi"],
    ))

    must = await svc.materialize_must_set(config)
    # 证据 D 被过滤;确定性排序:库字典序 + 卡ID字典序
    assert must.card_ids == ["c-01", "c-02", "s-01"]
    assert must.total == 3
    assert len(must.digest) == 12
    # 检索参数:状态/强度过滤按类型规则下推
    assert all(q["status"] == "approved" and q["rule_strength"] == "hard"
               for q in svc.indexer.queries)

    # 同版本缓存:不重复检索
    before = len(svc.indexer.queries)
    cached = await svc.materialize_must_set(config)
    assert cached.digest == must.digest
    assert len(svc.indexer.queries) == before

    # 版本演化 → 自动重建缓存
    config.locked_library_ids = ["historical_materials"]
    bumped = await svc.save_field(config)
    rebuilt = await svc.materialize_must_set(bumped)
    assert rebuilt.card_ids == ["c-01", "c-02"]

    # 证据阈值收紧到 A → 只留 c-02
    bumped.type_rules = LockFieldTypeRule(min_evidence="A")
    bumped = await svc.save_field(bumped, change_type="update_rules",
                                  detail={"type_rules": bumped.type_rules.model_dump()})
    strict = await svc.materialize_must_set(bumped)
    assert strict.card_ids == ["c-02"]


@pytest.mark.asyncio
async def test_materialize_empty_library_returns_empty(svc):
    svc.indexer = _FakeIndexer({})
    config = await svc.save_field(LockFieldConfig(project_id="p2"))
    must = await svc.materialize_must_set(config)
    assert must.total == 0 and must.card_ids == []


# ── 偏离登记 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_divergence_lifecycle_pending_confirm_retract(svc):
    record = await svc.propose_divergence(DivergenceRecord(
        project_id="p1", description="本书设定:刘备于190年病逝",
        diverged_from_card_id="c-99", chapter_number=3,
        command_context="作者指令:改写历史走向",
    ))
    assert record.status == DivergenceStatus.PENDING

    confirmed = await svc.confirm_divergence(record.divergence_id)
    assert confirmed.status == DivergenceStatus.CONFIRMED

    # 编年可查:PENDING 过滤为空,全量含 1 条
    assert await svc.list_divergences("p1", DivergenceStatus.PENDING) == []
    assert len(await svc.list_divergences("p1")) == 1

    retracted = await svc.retract_divergence(record.divergence_id)
    assert retracted.status == DivergenceStatus.RETRACTED


@pytest.mark.asyncio
async def test_rollback_rebuilds_from_version_log(svc):
    config = await svc.save_field(LockFieldConfig(
        project_id="p3", locked_library_ids=["historical_materials"],
    ))
    config.locked_library_ids = ["historical_materials", "sanfu_zhi"]
    await svc.save_field(config, detail={"locked_library_ids": config.locked_library_ids})
    v2 = await svc.get_field("p3")
    assert v2.locked_library_ids == ["historical_materials", "sanfu_zhi"]

    rolled = await svc.rollback_to_version(v2.field_id, target_version=1)
    assert rolled is not None
    assert rolled.version == 3  # 回滚=新版本,编年史只增不改
    assert rolled.locked_library_ids == ["historical_materials"]


@pytest.mark.asyncio
async def test_api_router_mounted():
    from api.api_router import api_router

    paths = {r.path for r in api_router.routes}
    assert any("/lockfield/" in p for p in paths)
