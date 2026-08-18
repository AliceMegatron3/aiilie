"""One-way SkillGovernance compatibility projections."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from models.cards import InfoCard
from models.novel_agent import NovelAgentSkill


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillProjectionService:
    """Projects FULL governance versions to legacy stores without reading them as authority."""

    def __init__(self, db: Any, indexer: Any, governance: Any) -> None:
        self.db = db
        self.indexer = indexer
        self.governance = governance

    async def sync_full(self) -> dict[str, int]:
        projected = 0
        for item in self.governance.resolve_active_skills({"task_id": "projection-sync"}):
            if item["rollout"] != "FULL":
                continue
            if await self.project(item):
                projected += 1
        return {"projected": projected}

    async def project(self, resolved: dict[str, Any]) -> bool:
        candidate_id = str(resolved["candidate_id"])
        version = int(resolved["version"])
        artifact = dict(resolved.get("artifact") or {})
        content_hash = hashlib.sha256(json.dumps(artifact, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        key = f"governance:{candidate_id}"
        conn = self.governance._get_conn()
        row = conn.execute("SELECT version, content_hash FROM skg_projection_state WHERE projection_key=?", (key,)).fetchone()
        if row is not None and int(row["version"]) == version and row["content_hash"] == content_hash:
            return False
        governance_meta = {"candidate_id": candidate_id, "version": version, "rollout": resolved["rollout"], "content_hash": content_hash}
        if "novel_agent_skill" in artifact:
            await self._project_novel_skill(artifact["novel_agent_skill"], governance_meta)
        elif artifact.get("content") is not None:
            await self._project_universal_skill(candidate_id, resolved, artifact, governance_meta)
        conn.execute(
            "INSERT INTO skg_projection_state(projection_key, candidate_id, version, content_hash, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(projection_key) DO UPDATE SET version=excluded.version, content_hash=excluded.content_hash, updated_at=excluded.updated_at",
            (key, candidate_id, version, content_hash, _now()),
        )
        conn.commit()
        return True

    async def _project_universal_skill(self, candidate_id: str, resolved: dict[str, Any], artifact: dict[str, Any], meta: dict[str, Any]) -> None:
        content = artifact.get("content") or {"prompt": resolved["prompt"]}
        await self.db.conn.execute(
            """INSERT INTO universal_skills(skill_id, type, name, content, source_cards, applicability, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(skill_id) DO UPDATE SET name=excluded.name, content=excluded.content,
                 source_cards=excluded.source_cards, applicability=excluded.applicability""",
            (
                candidate_id, str(artifact.get("type") or "TEMPLATE"), str(resolved["name"]),
                json.dumps({**(content if isinstance(content, dict) else {"prompt": str(content)}), "_governance": meta}, ensure_ascii=False),
                json.dumps(artifact.get("source_cards") or [], ensure_ascii=False), str(artifact.get("applicability") or ""), _now(),
            ),
        )
        await self.db.conn.commit()

    async def _project_novel_skill(self, raw: dict[str, Any], meta: dict[str, Any]) -> None:
        skill = NovelAgentSkill.model_validate(raw)
        payload = skill.model_dump(mode="json")
        payload["_governance"] = meta
        await self.db.conn.execute(
            """INSERT INTO novel_agent_skills(skill_id, name, skill_type, payload, status, apply_count, effect_score, creator, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'ACTIVE', 0, 0, 'governance_projection', ?, ?)
               ON CONFLICT(skill_id) DO UPDATE SET name=excluded.name, payload=excluded.payload,
                 status='ACTIVE', updated_at=excluded.updated_at""",
            (skill.skill_id, skill.name, skill.type, json.dumps(payload, ensure_ascii=False), _now(), _now()),
        )
        card = InfoCard(
            card_id=skill.skill_id,
            source_book_id="novel_agent_skill_library",
            content=skill.content.get("content_text", "") or f"技能: {skill.name}",
            tags=["novel_agent_skill", skill.name, "governance_projection"],
            card_sub_type="novel_agent_skill",
            payload=payload,
        )
        await self.indexer.save_card(card)
        await self.db.conn.commit()
