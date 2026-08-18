"""
services/skill_projection_reconcile.py — 旧投影与治理源全量对账（只读枚举 + 幂等导入）
=====================================================================================
把历史散落在各处的旧技能投影（universal_skills 表 / InfoCard / 行为插件 override JSON）
与 SkillGovernance 唯一候选池做全量对账，回答"旧技能是否都已进入治理源"：

- 已映射（skg_legacy_map 命中）        → already_migrated，并比对 source_row_hash 是否漂移
- 未映射（首次见到）                  → 经 governance.import_legacy_skill 幂等导入为候选
- list_projection_gaps                → 纯只读枚举（不写库），供开关关闭时观察缺口

硬约束：
- 本模块不修改 services/skill_governance.py，只调用其既有 API
  （import_legacy_skill 公开方法 + _get_conn 半公开只读查询，后者与
  services/skill_projection.py 的既有用法一致）。
- 写路径（reconcile_old_projections 的导入）仅在 feature.novel_multi_agent_enable
  开启时由装配方调用；开关关闭时只允许只读的 list_projection_gaps。

对账来源（source_system 标识，写入 skg_legacy_map.source_system）：
- "universal_skills"             主库 universal_skills 表（legacy_id = skill_id）
- "info_card"                    索引库中"技能类"InfoCard（legacy_id = card_id）
- "behavior_plugin_overrides"    行为插件治理覆盖 JSON（legacy_id = behavior_plugin:<pid>）
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_v4_skill_projection_reconcile")

# 旧投影来源标识（写入 skg_legacy_map.source_system）
SRC_UNIVERSAL = "universal_skills"
SRC_INFO_CARD = "info_card"
SRC_BEHAVIOR_OVERRIDE = "behavior_plugin_overrides"

# 行为插件 override JSON 文件名（与 services/behavior_plugins._default_overrides_path 对齐）
_BEHAVIOR_OVERRIDES_FILE = "behavior_plugin_overrides.json"

# 索引库中被视为"旧技能投影"的 InfoCard 标记：
# - card_sub_type 命中（novel_agent_skill 技能卡等）
# - knowledge_type 为规则/模板类
# - category 为 author 类卡（作者审核/笔记类投影）
_SKILL_LIKE_SUBTYPES = ("novel_agent_skill",)
_SKILL_LIKE_KNOWLEDGE_TYPES = ("RULE", "TEMPLATE")
_SKILL_LIKE_CATEGORIES = ("author",)


def _json_dumps(obj: Any) -> str:
    # 与 skill_governance._json_dumps 保持同一序列化口径，
    # 否则对账时 source_row_hash 永远比对不相等（误报漂移）。
    return json.dumps(obj, ensure_ascii=False, default=str)


def _row_hash(skill: dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(skill).encode("utf-8")).hexdigest()


def _get_legacy_mapping(governance: Any, source: str, legacy_id: str) -> dict[str, Any] | None:
    """只读查询 skg_legacy_map（返回 None 表示尚未映射）。"""
    conn = governance._get_conn()
    row = conn.execute(
        "SELECT candidate_id, source_row_hash, migrated_at FROM skg_legacy_map"
        " WHERE source_system=? AND legacy_id=?",
        (source, legacy_id),
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- 来源枚举

async def _enumerate_universal_skills(db: Any) -> list[dict[str, Any]]:
    """读取主库 universal_skills 表（若存在；db 未提供或表不存在则跳过该来源）。"""
    if db is None:
        return []
    try:
        cursor = await db.conn.execute(
            "SELECT skill_id, type, name, content, applicability, source_cards, created_at"
            " FROM universal_skills"
        )
    except Exception as exc:
        logger.warning("[SkillProjectionReconcile] universal_skills 表不可读（跳过该来源）: %s", exc)
        return []
    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(columns, row))
        results.append(
            {
                "skill_id": str(item["skill_id"]),
                "type": str(item.get("type") or "TEMPLATE"),
                "name": str(item.get("name") or item["skill_id"]),
                # content/source_cards 可能是 JSON 字符串（技能投影格式）或纯文本/已解析对象
                "content": _safe_json_loads(item.get("content")),
                "applicability": str(item.get("applicability") or ""),
                "source_cards": _safe_json_loads(item.get("source_cards"), default=[]),
            }
        )
    return results


def _is_skill_like_card(card: dict[str, Any]) -> bool:
    sub = str(card.get("card_sub_type") or card.get("subtype") or "").lower()
    kt = str(card.get("knowledge_type") or "").upper()
    cat = str(card.get("category") or "").lower()
    if any(k in sub for k in _SKILL_LIKE_SUBTYPES):
        return True
    if kt in _SKILL_LIKE_KNOWLEDGE_TYPES:
        return True
    if cat in _SKILL_LIKE_CATEGORIES or "author" in sub:
        return True
    return False


def _info_card_to_skill(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_id": str(card["card_id"]),
        "name": str(card.get("card_sub_type") or card.get("subtype") or card["card_id"]),
        "content": {
            "content_text": str(card.get("content") or ""),
            "payload": card.get("payload") or {},
        },
        "applicability": str(card.get("domain") or ""),
        "source_cards": [str(card.get("source_book_id") or card.get("source_book") or "")],
        "knowledge_type": str(card.get("knowledge_type") or "FACT"),
    }


async def _enumerate_info_cards(indexer: Any) -> list[dict[str, Any]]:
    """经 indexer 枚举"技能类"InfoCard（按 knowledge_type/子类型/author 类定向查询去重）。"""
    if indexer is None:
        return []
    seen: dict[str, dict[str, Any]] = {}
    queries = []
    for kt in _SKILL_LIKE_KNOWLEDGE_TYPES:
        queries.append({"card_type": "info", "knowledge_type": kt})
    for sub in _SKILL_LIKE_SUBTYPES:
        queries.append({"card_type": "info", "subtype": sub})
    for cat in _SKILL_LIKE_CATEGORIES:
        queries.append({"card_type": "info", "category": cat})
    for kwargs in queries:
        try:
            for card in await indexer.search_cards(limit=500, **kwargs):
                if _is_skill_like_card(card) and card.get("card_id"):
                    seen[str(card["card_id"])] = card
        except Exception as exc:
            logger.warning("[SkillProjectionReconcile] InfoCard 查询失败（跳过该查询）: %s", exc)
    return list(seen.values())


def _enumerate_behavior_overrides(app_data_dir: str | Path) -> list[dict[str, Any]]:
    """读取行为插件治理覆盖 JSON（每条 override 视为一条旧技能投影）。"""
    path = Path(app_data_dir) / _BEHAVIOR_OVERRIDES_FILE
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[SkillProjectionReconcile] 行为插件 override JSON 读取失败（跳过该来源）: %s", exc)
        return []
    results: list[dict[str, Any]] = []
    for pid, ov in (raw or {}).items():
        if not isinstance(ov, dict):
            continue
        status = str(ov.get("status") or "CANDIDATE")
        gray_percent = ov.get("gray_percent", 0)
        results.append(
            {
                "skill_id": f"behavior_plugin:{pid}",
                "name": f"behavior_plugin:{pid}",
                "content": {
                    "prompt": f"行为插件治理覆盖 {pid}",
                    "status": status,
                    "gray_percent": gray_percent,
                },
                "applicability": "behavior_plugin",
            }
        )
    return results


async def _enumerate_all(
    governance: Any, indexer: Any, app_data_dir: str | Path, db: Any
) -> list[tuple[str, str, dict[str, Any]]]:
    """汇总三大旧投影来源，返回 (source, legacy_id, skill_dict) 三元组列表。"""
    items: list[tuple[str, str, dict[str, Any]]] = []
    for skill in await _enumerate_universal_skills(db):
        items.append((SRC_UNIVERSAL, str(skill["skill_id"]), skill))
    for card in await _enumerate_info_cards(indexer):
        skill = _info_card_to_skill(card)
        items.append((SRC_INFO_CARD, str(card["card_id"]), skill))
    for skill in _enumerate_behavior_overrides(app_data_dir):
        items.append((SRC_BEHAVIOR_OVERRIDE, str(skill["skill_id"]), skill))
    return items


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default if default is not None else ""
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return str(value)


# --------------------------------------------------------------------------- 对账入口

async def reconcile_old_projections(
    governance: Any, indexer: Any, app_data_dir: str | Path, db: Any = None
) -> dict[str, Any]:
    """旧投影与治理源全量对账（写路径：未映射旧技能幂等导入为候选）。

    参数：
        governance       SkillGovernance 实例（唯一候选池/映射表所在）
        indexer          CardIndexer 实例（InfoCard 来源查询）
        app_data_dir     用户数据目录（读行为插件 override JSON）
        db               主库 DatabaseManager（读 universal_skills 表；为 None 时跳过该来源）

    返回统计字典：
        total_legacy             旧投影总数
        already_migrated         已映射的 legacy_id 清单
        imported_candidates      本次新导入的 legacy_id 清单
        changed_after_migration  迁移后源行发生漂移的记录（{source,legacy_id,candidate_id,old_hash,new_hash}）
        errors                   导入失败的错误信息
        projections_remaining    仍未映射的 legacy_id 清单（导入失败/遗留）
    """
    total = 0
    already_migrated: list[str] = []
    imported_candidates: list[str] = []
    changed_after_migration: list[dict[str, Any]] = []
    errors: list[str] = []
    projections_remaining: list[str] = []

    for source, legacy_id, skill in await _enumerate_all(governance, indexer, app_data_dir, db):
        total += 1
        mapping = _get_legacy_mapping(governance, source, legacy_id)
        current_hash = _row_hash(skill)
        if mapping is not None:
            already_migrated.append(legacy_id)
            stored_hash = mapping.get("source_row_hash") or ""
            if stored_hash and stored_hash != current_hash:
                changed_after_migration.append(
                    {
                        "source": source,
                        "legacy_id": legacy_id,
                        "candidate_id": str(mapping["candidate_id"]),
                        "old_hash": stored_hash,
                        "new_hash": current_hash,
                    }
                )
            continue
        try:
            governance.import_legacy_skill(skill, source=source)
            imported_candidates.append(legacy_id)
        except Exception as exc:
            logger.warning("[SkillProjectionReconcile] 导入失败 %s:%s: %s", source, legacy_id, exc)
            errors.append(f"{source}:{legacy_id}: {exc}")
            projections_remaining.append(f"{source}:{legacy_id}")

    return {
        "total_legacy": total,
        "already_migrated": already_migrated,
        "imported_candidates": imported_candidates,
        "changed_after_migration": changed_after_migration,
        "errors": errors,
        "projections_remaining": projections_remaining,
    }


async def list_projection_gaps(
    governance: Any, indexer: Any, app_data_dir: str | Path, db: Any = None
) -> dict[str, Any]:
    """只读枚举：返回旧投影中"尚未进入治理源"的缺口清单（不写库，供观察）。

    返回统计字典：
        total_legacy   旧投影总数
        mapped         已映射数
        gaps_count     缺口数
        gaps           [{source, legacy_id, name}]
    """
    total = 0
    mapped = 0
    gaps: list[dict[str, Any]] = []
    for source, legacy_id, skill in await _enumerate_all(governance, indexer, app_data_dir, db):
        total += 1
        if _get_legacy_mapping(governance, source, legacy_id) is not None:
            mapped += 1
            continue
        gaps.append(
            {
                "source": source,
                "legacy_id": legacy_id,
                "name": str(skill.get("name") or ""),
            }
        )
    return {
        "total_legacy": total,
        "mapped": mapped,
        "gaps_count": len(gaps),
        "gaps": gaps,
    }


class SkillProjectionReconciler:
    """绑定依赖的旧投影对账服务（供 bootstrap 装配为 app.state.skill_projection_reconcile）。

    - list_gaps()      只读枚举缺口，开关关闭时也可安全调用（不写库）
    - reconcile()      写路径（幂等导入），仅 feature.novel_multi_agent_enable 开启时调用
    """

    def __init__(
        self, governance: Any, indexer: Any, app_data_dir: str | Path, db: Any = None
    ) -> None:
        self.governance = governance
        self.indexer = indexer
        self.app_data_dir = Path(app_data_dir)
        self.db = db

    async def reconcile(self) -> dict[str, Any]:
        return await reconcile_old_projections(
            self.governance, self.indexer, self.app_data_dir, db=self.db
        )

    async def list_gaps(self) -> dict[str, Any]:
        return await list_projection_gaps(
            self.governance, self.indexer, self.app_data_dir, db=self.db
        )


__all__ = [
    "reconcile_old_projections",
    "list_projection_gaps",
    "SkillProjectionReconciler",
    "SRC_UNIVERSAL",
    "SRC_INFO_CARD",
    "SRC_BEHAVIOR_OVERRIDE",
]
