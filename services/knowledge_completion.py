"""
services/knowledge_completion.py — 知识补全与缺口闭环(P3降级版)
====================================================================
"领域记忆的生长机制":检索未命中→缺口聚账→(LLM降级补全/未来搜索
Provider)→draft 卡强制过作者审核——审而非发。

降级口径(用户拍板"先降级后升级"):当前 complete_via_llm 用 LLM
内部知识补全(无实时性);SearchProvider 为接口位,搜索 API 凭证
到位后原位升级为真实时补全,流程不变。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_path(name: str) -> Path:
    from core.path_resolver import get_app_data_dir
    return get_app_data_dir() / name


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[KnowledgeCompletion] 落账失败(不阻断): %s", exc)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ── 检索未命中日志 ────────────────────────────────────────────

def log_retrieval_miss(
    project_id: str | None, query: str, card_count: int = 0, path=None
) -> None:
    """生成时上下文为空/过薄 → 记一条缺口信号(缺口闭环的原料)。"""
    if not project_id:
        return
    _append_jsonl(Path(path or _default_path("retrieval_miss.jsonl")), {
        "ts": _now(),
        "project_id": project_id,
        "query_excerpt": str(query or "")[:120],
        "card_count": int(card_count),
    })


def aggregate_gaps(records: list[dict], top_queries: int = 5) -> dict[str, Any]:
    """按项目聚合缺口:次数+样例查询。"""
    by_project: dict[str, dict] = {}
    for rec in records:
        pid = rec.get("project_id") or "unknown"
        entry = by_project.setdefault(pid, {"project_id": pid, "miss_count": 0, "queries": []})
        entry["miss_count"] += 1
        q = rec.get("query_excerpt")
        if q:
            entry["queries"].append(q)
    for entry in by_project.values():
        entry["queries"] = entry["queries"][:top_queries]
    return {
        "projects": sorted(by_project.values(), key=lambda e: -e["miss_count"]),
        "total_misses": sum(e["miss_count"] for e in by_project.values()),
    }


# ── 补全建议(思考模式/反思可提交,待作者确认) ──────────────────

def propose_completion(
    project_id: str, topic: str, description: str = "", source: str = "manual",
    path=None,
) -> dict:
    """登记一条补全建议(PENDING)——deep_think/知识反思的提案入口。"""
    record = {
        "proposal_id": f"kc_{datetime.now(timezone.utc).strftime('%H%M%S%f')}",
        "ts": _now(), "project_id": project_id, "topic": topic,
        "description": description, "source": source, "status": "PENDING",
    }
    _append_jsonl(Path(path or _default_path("knowledge_proposals.jsonl")), record)
    return record


def list_proposals(project_id: str | None = None, path=None) -> list[dict]:
    records = _read_jsonl(Path(path or _default_path("knowledge_proposals.jsonl")))
    if project_id:
        records = [r for r in records if r.get("project_id") == project_id]
    return records


# ── 搜索 Provider 接口位(凭证到位后原位升级) ───────────────────

class SearchProvider(Protocol):
    async def search(self, query: str) -> str: ...


class LLMKnowledgeProvider:
    """降级实现:用 LLM 内部知识补全(无实时性,产出仍过作者审核)。"""

    def __init__(self, dispatcher: Any):
        self.dispatcher = dispatcher

    async def search(self, query: str) -> str:
        prompt = (
            "你是史料与设定考据助手。就以下主题给出简明、可核验的知识条目"
            "(时间/地点/制度/器物等具体信息优先,不确定处明确标注\"待核\")。"
            "直接输出条目正文,不要输出JSON。\n\n主题:" + query
        )
        return str(await self.dispatcher.dispatch(prompt, override_mode="think") or "")


async def complete_via_llm(
    dispatcher: Any, topic: str, description: str = "",
    provider: SearchProvider | None = None,
) -> str:
    """执行补全:优先注入的搜索Provider,缺省走LLM知识降级。"""
    prov = provider or LLMKnowledgeProvider(dispatcher)
    content = (await prov.search(f"{topic}\n{description}".strip())).strip()
    if not content:
        raise ValueError("补全结果为空,拒绝产出空卡")
    return content


async def create_completion_card(
    indexer: Any, project_id: str, topic: str, content: str,
    source_tag: str = "llm_completion",
) -> str | None:
    """补全产物落为 draft 信息卡(强制draft状态,过作者审核)。"""
    if indexer is None:
        return None
    try:
        from models.cards import InfoCard

        card = InfoCard(
            card_sub_type="knowledge_completion",
            category="worldview",
            content=content,
            source_book_id="knowledge_completion",  # 非书源:补全通道标记
            original_fragment="",
            payload={
                "topic": topic,
                "content": content,
                "source_tag": source_tag,   # llm_completion / web_verified(升级后)
                "project_id": project_id,
                "needs_author_review": True,
            },
            summary=f"[补全建议] {topic}",
            scope_level="project",
            status="draft",   # 审而非发:永不直接approved
        )
        return await indexer.save_card(card)
    except Exception as exc:
        logger.warning("[KnowledgeCompletion] draft卡落库失败(不阻断): %s", exc)
        return None
