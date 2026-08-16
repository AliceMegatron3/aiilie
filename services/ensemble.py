"""
services/ensemble.py — 群像层引擎(P7)
========================================
生活轨道滚动、关系账本三态力学(基线/挂账/按深浅回摆)、派系
生命周期(事件作用域临时容器)、声纹注入块、牵挂矛盾巡检。

执行回馈接口:render_ensemble_context(project_id, 在场角色, 事件)
→ 声纹+当前关系+派系图块,供拍级分解与打磨插件在场组合维消费。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.database import DatabaseManager
from models.ensemble import (
    EventDelta,
    FactionCard,
    LifeTrack,
    RelationshipLedgerEntry,
    VoiceCard,
    pair_key,
)

logger = logging.getLogger(__name__)

_NEGATIVE_VERBS = ("杀", "弃", "卖", "害", "背叛", "舍")


def _relation_label(value: int) -> str:
    if value >= 60:
        return "挚交"
    if value >= 20:
        return "友善"
    if value > -20:
        return "泛泛"
    if value > -60:
        return "疏离敌意"
    return "死敌"


class EnsembleService:
    """群像层引擎(自有表,严格隔离)。"""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    async def initialize(self) -> None:
        # 批次2:轨道表迁移为三元主键(project_id, character_id, chapter_key),
        # 支持按章号归档角色历史状态。按 SQLite 迁移惯例:备份→建新→迁移→删旧。
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS ensemble_relationships (
                entry_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                pair_k TEXT NOT NULL,
                character_a TEXT NOT NULL,
                character_b TEXT NOT NULL,
                baseline INTEGER NOT NULL DEFAULT 0,
                events TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS ensemble_factions (
                faction_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                name TEXT NOT NULL,
                members TEXT NOT NULL DEFAULT '[]',
                stance TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
        """)
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS ensemble_voices (
                character_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, character_id)
            )
        """)
        await self._migrate_life_tracks()
        await self.db.conn.commit()
        logger.info("[Ensemble] 群像层四表初始化完成(轨道表已用三元主键)")

    async def _migrate_life_tracks(self) -> None:
        """把二元主键轨道表迁移为三元主键(chapter_key);幂等。"""
        cur = await self.db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ensemble_life_tracks'"
        )
        if await cur.fetchone() is None:
            # 全新库/首次:直接建新结构
            await self.db.conn.execute("""
                CREATE TABLE ensemble_life_tracks (
                    character_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    chapter_key INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (project_id, character_id, chapter_key)
                )
            """)
            return
        cols = await self.db.conn.execute("PRAGMA table_info(ensemble_life_tracks)")
        col_names = [c[1] for c in await cols.fetchall()]
        if "chapter_key" in col_names:
            return  # 已是新结构
        # 旧结构迁移:备份→建新→复制→删旧
        await self.db.conn.execute(
            "ALTER TABLE ensemble_life_tracks RENAME TO ensemble_life_tracks_old"
        )
        await self.db.conn.execute("""
            CREATE TABLE ensemble_life_tracks (
                character_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                data TEXT NOT NULL,
                chapter_key INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (project_id, character_id, chapter_key)
            )
        """)
        await self.db.conn.execute("""
            INSERT OR IGNORE INTO ensemble_life_tracks (character_id, project_id, data, chapter_key)
            SELECT character_id, project_id, data, MAX(COALESCE(updated_chapter, 1), 1)
            FROM ensemble_life_tracks_old
            GROUP BY project_id, character_id
        """)
        await self.db.conn.execute("DROP TABLE ensemble_life_tracks_old")

    # ── 生活轨道 ──────────────────────────────────────────────

    async def upsert_track(self, track: LifeTrack) -> LifeTrack:
        """按 (project_id, character_id, chapter_key) 三元主键 upsert。

        不覆盖其他章号的历史快照——同一角色不同章各存一条,
        get_track_at_chapter 据此做区间检索。
        """
        key = max(1, track.chapter_key or track.updated_chapter)
        await self.db.conn.execute(
            """INSERT INTO ensemble_life_tracks (character_id, project_id, data, chapter_key)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_id, character_id, chapter_key) DO UPDATE SET
                   data=excluded.data""",
            (track.character_id, track.project_id, track.model_dump_json(), key),
        )
        await self.db.conn.commit()
        return track

    async def get_tracks(self, project_id: str, at_chapter: int | None = None) -> list[LifeTrack]:
        """轨道清单;at_chapter 给定时返回各角色「该章号及之前最近」的快照。"""
        if at_chapter is None:
            cursor = await self.db.conn.execute(
                "SELECT data, chapter_key FROM ensemble_life_tracks WHERE project_id = ? ORDER BY chapter_key",
                (project_id,),
            )
            rows = await cursor.fetchall()
            return [self._load_track(r[0], r[1]) for r in rows]
        # 每角色取 chapter_key <= at_chapter 的最大一条
        cursor = await self.db.conn.execute(
            """SELECT t.data, t.chapter_key FROM ensemble_life_tracks t
               JOIN (SELECT character_id, MAX(chapter_key) AS mx
                     FROM ensemble_life_tracks
                     WHERE project_id = ? AND chapter_key <= ?
                     GROUP BY character_id) sub
               ON t.character_id = sub.character_id AND t.chapter_key = sub.mx
               WHERE t.project_id = ?""",
            (project_id, at_chapter, project_id),
        )
        rows = await cursor.fetchall()
        return [self._load_track(r[0], r[1]) for r in rows]

    async def get_track_at_chapter(
        self, project_id: str, character_id: str, chapter: int
    ) -> LifeTrack | None:
        """单角色在指定章号(≤chapter)最近的状态快照;未来章号兜底到最新。"""
        cursor = await self.db.conn.execute(
            """SELECT data, chapter_key FROM ensemble_life_tracks
               WHERE project_id = ? AND character_id = ? AND chapter_key <= ?
               ORDER BY chapter_key DESC LIMIT 1""",
            (project_id, character_id, chapter),
        )
        row = await cursor.fetchone()
        return self._load_track(row[0], row[1]) if row else None

    async def get_track_history(self, project_id: str, character_id: str) -> list[LifeTrack]:
        """单角色全历史快照(按章号升序)。"""
        cursor = await self.db.conn.execute(
            """SELECT data, chapter_key FROM ensemble_life_tracks
               WHERE project_id = ? AND character_id = ?
               ORDER BY chapter_key ASC""",
            (project_id, character_id),
        )
        rows = await cursor.fetchall()
        return [self._load_track(r[0], r[1]) for r in rows]

    @staticmethod
    def _load_track(data_json: str, chapter_key: int) -> LifeTrack:
        """反序列化轨道快照,并以 SQL 的 chapter_key 列覆盖载荷值。

        兼容存量旧数据:早期载荷在 chapter_key 字段引入前序列化,
        反序列化后 chapter_key 会回落默认值 1;SQL 列才是权威。
        """
        track = LifeTrack.model_validate_json(data_json)
        track.chapter_key = int(chapter_key or 1)
        track.updated_chapter = max(track.updated_chapter, int(chapter_key or 1))
        return track

    # ── 关系账本 ──────────────────────────────────────────────

    async def _get_entry(self, project_id: str, a: str, b: str) -> RelationshipLedgerEntry | None:
        cursor = await self.db.conn.execute(
            "SELECT entry_id FROM ensemble_relationships WHERE project_id = ? AND pair_k = ?",
            (project_id, pair_key(a, b)),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        cursor = await self.db.conn.execute(
            "SELECT * FROM ensemble_relationships WHERE entry_id = ?", (row[0],)
        )
        data = dict(zip([d[0] for d in cursor.description], await cursor.fetchone()))
        data.pop("pair_k", None)
        data["events"] = [EventDelta(**e) for e in json.loads(data.get("events") or "[]")]
        return RelationshipLedgerEntry(**data)

    async def set_baseline(self, project_id: str, a: str, b: str, baseline: int) -> RelationshipLedgerEntry:
        entry = await self._get_entry(project_id, a, b) or RelationshipLedgerEntry(
            project_id=project_id, character_a=a, character_b=b,
        )
        entry.baseline = max(-100, min(100, baseline))
        await self._save_entry(entry)
        return entry

    async def record_event_delta(
        self, project_id: str, a: str, b: str, event_id: str, delta: int, reason: str = "",
    ) -> RelationshipLedgerEntry:
        entry = await self._get_entry(project_id, a, b) or RelationshipLedgerEntry(
            project_id=project_id, character_a=a, character_b=b,
        )
        entry.events.append(EventDelta(event_id=event_id, delta=delta, reason=reason))
        await self._save_entry(entry)
        return entry

    async def close_event(self, project_id: str, event_id: str) -> list[str]:
        """事件清算:全部挂账按深浅回摆结晶,返回回摆提醒清单。"""
        cursor = await self.db.conn.execute(
            "SELECT entry_id FROM ensemble_relationships WHERE project_id = ?", (project_id,),
        )
        ids = [r[0] for r in await cursor.fetchall()]
        reminders: list[str] = []
        for entry_id in ids:
            cursor = await self.db.conn.execute(
                "SELECT * FROM ensemble_relationships WHERE entry_id = ?", (entry_id,)
            )
            data = dict(zip([d[0] for d in cursor.description], await cursor.fetchone()))
            entry = RelationshipLedgerEntry(
                entry_id=data["entry_id"], project_id=data["project_id"],
                character_a=data["character_a"], character_b=data["character_b"],
                baseline=data["baseline"],
                events=[EventDelta(**e) for e in json.loads(data.get("events") or "[]")],
                updated_at=data["updated_at"],
            )
            reminder = entry.close_event(event_id)
            if reminder:
                await self._save_entry(entry)
                reminders.append(reminder)
        # 派系随事件解散归档
        await self.db.conn.execute(
            "UPDATE ensemble_factions SET status = 'dissolved' WHERE project_id = ? AND event_id = ? AND status = 'active'",
            (project_id, event_id),
        )
        await self.db.conn.commit()
        return reminders

    async def current_relationships(self, project_id: str) -> list[dict]:
        cursor = await self.db.conn.execute(
            "SELECT * FROM ensemble_relationships WHERE project_id = ?", (project_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        out = []
        for r in rows:
            data = dict(zip(cols, r))
            events = [EventDelta(**e) for e in json.loads(data.get("events") or "[]")]
            entry = RelationshipLedgerEntry(
                entry_id=data["entry_id"], project_id=data["project_id"],
                character_a=data["character_a"], character_b=data["character_b"],
                baseline=data["baseline"], events=events, updated_at=data["updated_at"],
            )
            current = entry.current()
            out.append({
                "pair": entry.pair, "current": current, "label": _relation_label(current),
                "baseline": entry.baseline,
                "active_event_deltas": sum(e.delta for e in events if e.active),
            })
        return out

    async def _save_entry(self, entry: RelationshipLedgerEntry) -> None:
        await self.db.conn.execute(
            """INSERT INTO ensemble_relationships
               (entry_id, project_id, pair_k, character_a, character_b, baseline, events, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET baseline=excluded.baseline,
                   events=excluded.events, updated_at=excluded.updated_at""",
            (entry.entry_id, entry.project_id, entry.pair, entry.character_a,
             entry.character_b, entry.baseline,
             json.dumps([e.model_dump() for e in entry.events]), entry.updated_at),
        )
        await self.db.conn.commit()

    # ── 派系 ──────────────────────────────────────────────────

    async def create_faction(self, faction: FactionCard) -> FactionCard:
        await self.db.conn.execute(
            """INSERT INTO ensemble_factions
               (faction_id, project_id, event_id, name, members, stance, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(faction_id) DO UPDATE SET name=excluded.name,
                   members=excluded.members, stance=excluded.stance, status=excluded.status""",
            (faction.faction_id, faction.project_id, faction.event_id, faction.name,
             json.dumps(faction.members), json.dumps(faction.stance),
             faction.status, faction.created_at),
        )
        await self.db.conn.commit()
        return faction

    async def get_factions(self, project_id: str, active_only: bool = True) -> list[FactionCard]:
        if active_only:
            cursor = await self.db.conn.execute(
                "SELECT * FROM ensemble_factions WHERE project_id = ? AND status = 'active'",
                (project_id,),
            )
        else:
            cursor = await self.db.conn.execute(
                "SELECT * FROM ensemble_factions WHERE project_id = ?", (project_id,),
            )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        out = []
        for r in rows:
            data = dict(zip(cols, r))
            data["members"] = json.loads(data.get("members") or "[]")
            data["stance"] = json.loads(data.get("stance") or "{}")
            out.append(FactionCard(**data))
        return out

    def derive_factions(
        self, project_id: str, event_id: str, tracks: list[LifeTrack],
        event_name: str = "大事件",
    ) -> list[FactionCard]:
        """派系半自动推导:同需求归同派(身份×需求矩阵的简化实现),作者确认。"""
        groups: dict[str, list[LifeTrack]] = {}
        for t in tracks:
            key = t.needs[0] if t.needs else "未明"
            groups.setdefault(key, []).append(t)
        factions = []
        for need, members in groups.items():
            if len(members) < 1:
                continue
            factions.append(FactionCard(
                project_id=project_id, event_id=event_id,
                name=f"{event_name}·{'/'.join(m.name or m.character_id for m in members[:3])}系",
                members=[m.character_id for m in members],
                stance={m.character_id: f"因「{need}」而聚" for m in members},
            ))
        return factions

    # ── 声纹 ──────────────────────────────────────────────────

    async def upsert_voice(self, voice: VoiceCard) -> VoiceCard:
        await self.db.conn.execute(
            """INSERT INTO ensemble_voices (character_id, project_id, data, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(project_id, character_id) DO UPDATE SET
                   data=excluded.data, updated_at=excluded.updated_at""",
            (voice.character_id, voice.project_id, voice.model_dump_json(), voice.updated_at),
        )
        await self.db.conn.commit()
        return voice

    async def get_voice(self, project_id: str, character_id: str) -> VoiceCard | None:
        cursor = await self.db.conn.execute(
            "SELECT data FROM ensemble_voices WHERE project_id = ? AND character_id = ?",
            (project_id, character_id),
        )
        row = await cursor.fetchone()
        return VoiceCard.model_validate_json(row[0]) if row else None

    # ── 执行回馈:在场上下文块 + 牵挂矛盾巡检 ────────────────────

    async def render_ensemble_context(
        self, project_id: str, onstage_ids: list[str], event_id: str | None = None,
    ) -> str:
        """在场声纹+当前关系+派系图(生成注入块;空则返回空串)。

        关系行优先显示角色名(名字比ID对模型更可用),无名回退ID。
        """
        parts: list[str] = []
        name_map: dict[str, str] = {}
        try:
            for t in await self.get_tracks(project_id):
                if t.name:
                    name_map[t.character_id] = t.name
        except Exception:
            pass

        def display(cid: str) -> str:
            name = name_map.get(cid)
            return f"{name}({cid})" if name else cid

        for cid in onstage_ids:
            voice = await self.get_voice(project_id, cid)
            if voice:
                parts.append(
                    f"- {voice.name or display(cid)}:声纹={voice.speech_habits or '—'};"
                    f"决策={voice.decision_style or '—'}"
                )
        if parts:
            parts.insert(0, "【在场角色声纹(对白按各人声纹分口吻)】")
        rels = await self.current_relationships(project_id)
        onstage = set(onstage_ids)
        rel_lines = []
        for r in rels:
            a, b = r["pair"].split("|")
            if a in onstage and b in onstage:
                rel_lines.append(f"- {display(a)}×{display(b)}:{r['label']}({r['current']:+d})")
        if rel_lines:
            parts.append("【在场关系当前态】\n" + "\n".join(rel_lines))
        factions = await self.get_factions(project_id, active_only=True)
        if event_id:
            factions = [f for f in factions if f.event_id == event_id]
        if factions:
            fl = [f"- {f.name}:{','.join(display(m) for m in f.members)}" for f in factions]
            parts.append("【事件派系图(临时对位,事件终了回摆)】\n" + "\n".join(fl))
        return ("\n\n".join(parts) + "\n\n") if parts else ""

    @staticmethod
    def check_action_vs_track(track: LifeTrack, action_text: str) -> str | None:
        """牵挂矛盾巡检:动作以负面动词触及牵挂→出提案(知识反思巡检调用)。"""
        text = action_text or ""
        for attach in track.attachments:
            if attach and attach in text and any(v in text for v in _NEGATIVE_VERBS):
                return (
                    f"角色「{track.name or track.character_id}」的动作触及牵挂「{attach}」"
                    f"且含负面动词——建议作者核对其轨道动机(当前需求:{'/'.join(track.needs) or '未明'})"
                )
        return None


def infer_onstage_ids(tracks: list[LifeTrack], text: str) -> list[str]:
    """确定性在场角色识别:角色名/ID/别名出现在文本(命令+拍纲)即在场。

    能用算的不用LLM。别名来自轨道卡 aliases(玄德/孟德之类的字与尊称)。
    同一角色多处命中只计一次,顺序按轨道卡顺序保持确定。
    """
    text = text or ""
    onstage: list[str] = []
    seen: set[str] = set()
    for t in tracks:
        if t.character_id in seen:
            continue
        candidates = [n for n in ([t.name, t.character_id] + list(t.aliases or [])) if n]
        if any(n in text for n in candidates):
            onstage.append(t.character_id)
            seen.add(t.character_id)
    return onstage
