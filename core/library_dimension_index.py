"""
core/library_dimension_index.py — 书库量化体系精细化闭环（P4-1）
===============================================================
三驾马车「书库量化系统（静态数据底座）」的精细化能力补齐，纯增量模块：

1. DimensionIndexer          六大维度结构化索引（剧情/人设/世界观/情感/场景/文风）
2. CharacterWorldviewQuantizer 人设 & 世界观精细化量化模型（规则打分，无 LLM 依赖）
3. EmotionDimensionModel     情感维度建模与适配引擎（倾向/强度/调性）
4. SampleCleaningPipeline    样本标准化清洗与入库管道（去脏/去重/结构化校验）
5. LibraryCachePreloader     书库数据缓存与预加载（高频预加载 + 低频懒加载）

兼容性约定（P0-P3 成果不回滚、不重构）：
- 独立数据文件 library_dim_index.db，独立表名前缀 dim_*，不与 CardIndexer 的
  library_index.db / tasks.db 发生任何读写冲突；
- 不修改现有 indexer/quantifier/emotion_engine 的任何逻辑，仅新增能力层；
- 全部为同步核心 + asyncio.to_thread 异步适配，可被管家调度层按需调用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("ai_v4_library_dim")

# ---------------------------------------------------------------------------
# 通用常量与工具
# ---------------------------------------------------------------------------

DIMENSIONS: tuple[str, ...] = (
    "plot",       # 剧情
    "character",  # 人设
    "worldview",  # 世界观
    "emotion",    # 情感
    "scene",      # 场景
    "style",      # 文风
)

_DIMENSION_LABELS: dict[str, str] = {
    "plot": "剧情",
    "character": "人设",
    "worldview": "世界观",
    "emotion": "情感",
    "scene": "场景",
    "style": "文风",
}

# 人设量化：性格五维（外倾/宜人/尽责/神经质/开放）关键词词典
_CHARACTER_TRAITS: dict[str, tuple[str, ...]] = {
    "外向性": ("活泼", "开朗", "健谈", "社交", "热情", "爽朗", "自来熟", "豪放"),
    "宜人性": ("温和", "善良", "体贴", "包容", "忠诚", "仗义", "温柔", "同理心"),
    "尽责性": ("自律", "严谨", "可靠", "守约", "勤勉", "一丝不苟", "负责"),
    "情绪稳定": ("冷静", "沉稳", "淡定", "处变不惊", "隐忍", "克制"),
    "开放性": ("好奇", "冒险", "想象力", "创意", "天马行空", "求知", "猎奇"),
}

# 世界观规则分类词
_WORLDVIEW_RULES: dict[str, tuple[str, ...]] = {
    "力量体系": ("修炼", "等级", "灵力", "魔法", "内力", "境界", "斗气", "异能"),
    "社会结构": ("帝国", "宗门", "王朝", "家族", "学院", "公会", "联盟", "组织"),
    "时间空间": ("穿越", "重生", "平行世界", "时空", "位面", "轮回"),
    "禁忌规则": ("天劫", "雷劫", "诅咒", "封印", "禁忌", "代价", "誓言", "契约"),
}

_EMOTION_WORDS: dict[str, tuple[str, ...]] = {
    "喜": ("笑", "开心", "喜悦", "兴奋", "欣慰", "甜蜜", "欢喜", "愉悦"),
    "怒": ("怒", "愤怒", "暴怒", "怒火", "恼火", "气急", "愤恨"),
    "哀": ("悲", "哭", "伤心", "哀伤", "绝望", "失落", "痛苦", "心碎"),
    "惧": ("怕", "恐惧", "惊惧", "战栗", "胆寒", "惶恐", "害怕"),
    "惊": ("震惊", "惊讶", "意外", "目瞪口呆", "骇然", "猝不及防"),
    "厌": ("厌恶", "嫌弃", "反感", "鄙夷", "恶心", "不屑"),
}

_TONE_WORDS: dict[str, tuple[str, ...]] = {
    "热血": ("燃", "热血", "激昂", "豪迈", "澎湃", "凌云"),
    "温馨": ("温暖", "治愈", "柔和", "恬淡", "温馨"),
    "悬疑": ("谜", "诡异", "疑云", "扑朔", "暗流", "不寒而栗"),
    "悲壮": ("壮烈", "悲壮", "诀别", "牺牲", "赴死"),
    "诙谐": ("搞笑", "幽默", "吐槽", "戏谑", "滑稽", "捧腹"),
}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 1. 六大维度结构化索引
# ---------------------------------------------------------------------------

class DimensionIndexer:
    """六大维度结构化索引：独立 SQLite 存储（library_dim_index.db）。

    支持批量入索引、六维组合检索、按维度聚合统计。索引条目引用
    现有 CardIndexer 的卡片/文档 ID，不复制正文本体，仅存维度标签与元数据。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        from utils.resource_path import get_resource_path  # noqa: F401  # 兼容打包环境

        if db_path is None:
            from core.path_resolver import get_app_data_dir
            db_path = Path(get_app_data_dir()) / "library_dim_index.db"
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # -- 连接管理 -----------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_schema(self._conn)
        return self._conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dim_index_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref_id TEXT NOT NULL,             -- 卡片/文档 ID（引用现有体系）
                dimension TEXT NOT NULL,          -- plot/character/.../style
                tags TEXT NOT NULL DEFAULT '[]',  -- JSON 标签数组
                weight REAL NOT NULL DEFAULT 1.0,
                meta TEXT NOT NULL DEFAULT '{}',  -- JSON 附加元数据
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dim_ref ON dim_index_entries(ref_id);
            CREATE INDEX IF NOT EXISTS idx_dim_name ON dim_index_entries(dimension);
            CREATE TABLE IF NOT EXISTS dim_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()

    # -- 写入 ---------------------------------------------------------------

    def index_entry(
        self,
        ref_id: str,
        dimension: str,
        tags: Iterable[str],
        weight: float = 1.0,
        meta: dict[str, Any] | None = None,
    ) -> int:
        """为单个维度写入一条索引。dimension 非法时抛 ValueError。"""
        if dimension not in DIMENSIONS:
            raise ValueError(f"非法维度: {dimension}（合法值: {DIMENSIONS}）")
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "INSERT INTO dim_index_entries(ref_id, dimension, tags, weight, meta, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ref_id, dimension,
                    _json_dumps(list(tags)), float(weight),
                    _json_dumps(meta or {}), _now_iso(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def index_document(
        self,
        ref_id: str,
        dimension_tags: dict[str, Iterable[str]],
        meta: dict[str, Any] | None = None,
    ) -> int:
        """一次索引多维度。dimension_tags: {维度: [标签, ...]}。返回写入条数。"""
        count = 0
        for dim, tags in dimension_tags.items():
            if not tags:
                continue
            self.index_entry(ref_id, dim, tags, meta=meta)
            count += 1
        return count

    # -- 检索 ---------------------------------------------------------------

    def search_by_dimensions(
        self,
        dimensions: Iterable[str] | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按维度（可多个）+ 标签关键词检索，返回索引条目（含 ref_id 便于回源取卡片）。"""
        dims = [d for d in (dimensions or DIMENSIONS) if d in DIMENSIONS]
        if not dims:
            return []
        sql = "SELECT * FROM dim_index_entries WHERE dimension IN (%s)" % ",".join("?" * len(dims))
        args: list[Any] = list(dims)
        if tag:
            sql += " AND tags LIKE ?"
            args.append(f"%{tag}%")
        sql += " ORDER BY weight DESC, id DESC LIMIT ?"
        args.append(int(limit))
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(sql, args).fetchall()
        return [
            {
                "id": r["id"],
                "ref_id": r["ref_id"],
                "dimension": r["dimension"],
                "dimension_label": _DIMENSION_LABELS.get(r["dimension"], r["dimension"]),
                "tags": _json_loads(r["tags"], []),
                "weight": r["weight"],
                "meta": _json_loads(r["meta"], {}),
            }
            for r in rows
        ]

    def aggregate(self) -> dict[str, int]:
        """按维度聚合统计（支撑书库健康度与调度预判）。"""
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT dimension, COUNT(*) AS c FROM dim_index_entries GROUP BY dimension"
            ).fetchall()
        return {r["dimension"]: int(r["c"]) for r in rows}

    def remove_ref(self, ref_id: str) -> int:
        """移除某引用对象（卡片/文档）的全部索引条目。"""
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute("DELETE FROM dim_index_entries WHERE ref_id = ?", (ref_id,))
            conn.commit()
            return int(cur.rowcount)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- 异步适配 -----------------------------------------------------------

    async def async_index_document(self, *args, **kwargs) -> int:
        return await asyncio.to_thread(self.index_document, *args, **kwargs)

    async def async_search_by_dimensions(self, *args, **kwargs) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.search_by_dimensions, *args, **kwargs)


# ---------------------------------------------------------------------------
# 2. 人设 & 世界观精细化量化模型
# ---------------------------------------------------------------------------

class CharacterWorldviewQuantizer:
    """人设与世界观精细化量化模型（规则打分，确定性输出，无 LLM 依赖）。

    输出 0-1 归一化分数与命中关键词，供管家调度层做「人设一致性」「世界观
    规则匹配」判断，杜绝人设崩塌与风格错乱。
    """

    def quantize_character(self, text: str) -> dict[str, Any]:
        """人物性格量化：返回 {维度: 0-1 分数, 命中词}，并附 top 性格标签。"""
        result: dict[str, Any] = {"scores": {}, "hits": {}, "top_traits": []}
        for trait, words in _CHARACTER_TRAITS.items():
            hits = [w for w in words if w in text]
            score = min(1.0, len(hits) / 3.0)
            result["scores"][trait] = round(score, 2)
            result["hits"][trait] = hits
        ranked = sorted(result["scores"].items(), key=lambda kv: kv[1], reverse=True)
        result["top_traits"] = [k for k, v in ranked if v > 0][:3]
        return result

    def quantize_worldview(self, text: str) -> dict[str, Any]:
        """世界观规则量化：按四类规则（力量体系/社会结构/时空/禁忌）打分并输出命中词。"""
        result: dict[str, Any] = {"scores": {}, "hits": {}}
        for rule, words in _WORLDVIEW_RULES.items():
            hits = [w for w in words if w in text]
            score = min(1.0, len(hits) / 2.0)
            result["scores"][rule] = round(score, 2)
            result["hits"][rule] = hits
        result["rule_density"] = round(
            sum(1 for v in result["scores"].values() if v > 0) / len(_WORLDVIEW_RULES), 2
        )
        return result

    def consistency_check(
        self, character_profile: dict[str, float], dialogue_sample: str
    ) -> dict[str, Any]:
        """人设一致性快速校验：对比人物档案与对话样本的性格得分偏差。"""
        sample = self.quantize_character(dialogue_sample)["scores"]
        deviations = {
            k: round(abs(character_profile.get(k, 0.0) - sample.get(k, 0.0)), 2)
            for k in _CHARACTER_TRAITS
        }
        max_dev = max(deviations.values(), default=0.0)
        return {
            "deviations": deviations,
            "max_deviation": max_dev,
            "is_consistent": max_dev <= 0.5,
            "suggestion": None if max_dev <= 0.5 else "人物对话风格与档案偏差较大，建议检查人设数据或调整生成约束",
        }


# ---------------------------------------------------------------------------
# 3. 情感维度建模与适配引擎
# ---------------------------------------------------------------------------

class EmotionDimensionModel:
    """情感维度建模：情感倾向（六类）、情绪强度（0-1）、氛围调性（五类）。

    可对任意文本片段打分，并输出「生成适配提示」，供内容生成贴合场景情绪。
    """

    def analyze(self, text: str) -> dict[str, Any]:
        counts = {
            cat: sum(1 for w in words if w in text)
            for cat, words in _EMOTION_WORDS.items()
        }
        tones = {
            tone: sum(1 for w in words if w in text)
            for tone, words in _TONE_WORDS.items()
        }
        total = sum(counts.values()) or 1
        tendency = max(counts, key=counts.get) if any(counts.values()) else "中性"
        intensity = round(min(1.0, total / 8.0), 2)
        tone = max(tones, key=tones.get) if any(tones.values()) else "平实"
        return {
            "tendency": tendency,
            "tendency_scores": {k: round(v / total, 2) for k, v in counts.items()},
            "intensity": intensity,
            "tone": tone,
            "tone_scores": tones,
        }

    def adapt_style(self, card_emotion: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        """生成适配：对比卡片情绪与目标情绪，输出风格适配提示（倾向/强度/调性三路）。"""
        hints: list[str] = []
        if card_emotion.get("tendency") != target.get("tendency"):
            hints.append(f"情绪倾向需从「{card_emotion.get('tendency')}」过渡到「{target.get('tendency')}」")
        delta = (target.get("intensity", 0.0) or 0.0) - (card_emotion.get("intensity", 0.0) or 0.0)
        if abs(delta) > 0.2:
            hints.append(
                "情绪强度需%s（%+.2f）" % ("增强" if delta > 0 else "收敛", delta)
            )
        if card_emotion.get("tone") != target.get("tone"):
            hints.append(f"氛围调性需从「{card_emotion.get('tone')}」调整为「{target.get('tone')}」")
        return {"hints": hints, "matched": not hints}


# ---------------------------------------------------------------------------
# 4. 样本标准化清洗与入库管道
# ---------------------------------------------------------------------------

@dataclass
class CleanedSample:
    """清洗后的标准化样本。"""

    content: str
    word_count: int
    source_hash: str
    dimensions: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class SampleCleaningPipeline:
    """样本标准化清洗：HTML 清理 → 空行压缩 → 长度校验 → 内容哈希 → 脏数据拦截。

    杜绝脏数据、残缺数据、冗余数据入库；返回 None 表示样本被拦截（不合格）。
    """

    MIN_WORDS = 50        # 最短有效样本长度
    MAX_WORDS = 20000     # 单样本长度上限（超出建议切分）

    def __init__(self, min_words: int = MIN_WORDS, max_words: int = MAX_WORDS) -> None:
        self.min_words = min_words
        self.max_words = max_words

    def clean(self, raw: str, meta: dict[str, Any] | None = None) -> CleanedSample | None:
        import hashlib

        warnings: list[str] = []
        # 1) HTML/标记清理
        text = re.sub(r"<[^>]+>", "", raw or "")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)  # 控制字符
        # 2) 空行与空白压缩
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        # 3) 长度校验（残缺/超长拦截）
        words = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", text)
        word_count = len(words)
        if word_count < self.min_words:
            return None  # 残缺样本，直接拦截
        if word_count > self.max_words:
            warnings.append(f"样本超长（{word_count} 字），建议切分后入库")
        # 4) 高重复行检测（冗余样本拦截）
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if lines:
            dup_ratio = 1 - len(set(lines)) / len(lines)
            if dup_ratio > 0.9:
                warnings.append(f"重复行占比过高（{dup_ratio:.0%}），疑似冗余样本")
        # 5) 内容哈希（入库去重指纹）
        source_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
        sample = CleanedSample(
            content=text,
            word_count=word_count,
            source_hash=source_hash,
            warnings=warnings,
        )
        if meta:
            sample.dimensions = dict(meta)
        return sample


# ---------------------------------------------------------------------------
# 5. 书库数据缓存与预加载
# ---------------------------------------------------------------------------

class LibraryCachePreloader:
    """书库数据缓存与预加载：LRU + TTL。

    高频卡片 preload 预热；低频资源 lazy_load 按需加载并缓存；
    invalidate 支持卡片更新后失效。适配管家调度时序（执行前预加载）。
    """

    def __init__(self, max_size: int = 512, ttl_seconds: float = 300.0) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}  # key -> (expire_at, value)
        self._lock = threading.Lock()
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._stats: dict[str, int] = {"hits": 0, "misses": 0, "evictions": 0}

    def preload(self, card_id: str, value: Any) -> None:
        """高频卡片预热：写入缓存并刷新过期时间。"""
        with self._lock:
            self._cache.pop(card_id, None)
            self._cache[card_id] = (time.monotonic() + self.ttl_seconds, value)
            while len(self._cache) > self.max_size:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
                self._stats["evictions"] += 1

    def get(self, card_id: str) -> Any | None:
        """命中返回缓存值；未命中/过期返回 None。"""
        with self._lock:
            item = self._cache.get(card_id)
            if item is None:
                self._stats["misses"] += 1
                return None
            expire_at, value = item
            if time.monotonic() > expire_at:
                self._cache.pop(card_id, None)
                self._stats["misses"] += 1
                return None
            self._cache.pop(card_id, None)
            self._cache[card_id] = (time.monotonic() + self.ttl_seconds, value)
            self._stats["hits"] += 1
            return value

    def lazy_load(self, card_id: str, loader: Callable[[], Any]) -> Any:
        """低频资源懒加载：未命中时通过 loader 取数并缓存。"""
        cached = self.get(card_id)
        if cached is not None:
            return cached
        value = loader()
        self.preload(card_id, value)
        return value

    def invalidate(self, card_id: str) -> None:
        with self._lock:
            self._cache.pop(card_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {**self._stats, "size": len(self._cache)}
