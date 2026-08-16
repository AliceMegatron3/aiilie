"""
services/idle_indexer.py — 空闲索引器(批次4)
==============================================
"五小时自动备份"思路的修正落地(作者已确认):不是备份,是**空闲索引构建**。

目标:文档静默后,利用空闲算力把它蒸馏成智能体易索引的形式——
① 摘要卡(scope=project, draft 等待审核) ② 实体提取初筛(轮廓级,
不全量量化) ③ 关系账本矛盾巡检。产物落入 .ai_index/ 并可完全关闭。

触发条件(全部满足才跑):
- feature.idle_index_enable = true(默认开,可在设置关闭)
- 存在文档,其 updated_at 晚于上次索引时间(欠账)
- 系统空闲:批次1队列无活跃任务(不抢占创作算力)

设计:产物一律 draft,作者在治理面板审核——绝不自动批准。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def index_manifest_path() -> Path:
    from core.path_resolver import get_ai_index_dir

    return get_ai_index_dir() / "doc_index_manifest.json"


def _load_manifest() -> dict[str, str]:
    """doc_id → 上次索引 ISO 时间。文件缺失/损坏时返回空(全量欠账)。"""
    path = index_manifest_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_manifest(manifest: dict[str, str]) -> None:
    try:
        path = index_manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("[IdleIndexer] 清单保存失败(不阻断): %s", exc)


def pending_docs(docs: list, manifest: dict[str, str] | None = None) -> list:
    """筛出有欠账的文档:updated_at 晚于上次索引时间或从未索引。

    docs: 元素为 dict,须含 doc_id 与 updated_at(ISO 字符串)。
    """
    manifest = manifest if manifest is not None else _load_manifest()
    out = []
    for d in docs:
        doc_id = str(d.get("doc_id") or "")
        updated = str(d.get("updated_at") or "")
        if not doc_id:
            continue
        last = manifest.get(doc_id)
        if last is None or updated > last:
            out.append(d)
    return out


def _summary_path(doc_id: str) -> Path:
    from core.path_resolver import get_ai_index_dir

    return get_ai_index_dir() / f"{doc_id}_summary.json"


async def index_document(
    doc_id: str, raw_content: str, project_id: str = "",
    indexer=None, ensemble_svc=None,
) -> dict:
    """对单个文档执行空闲索引(异步:indexer/ensemble 均为 async 调用)。

    产出:
    - 摘要卡 InfoCard(scope=project, draft, payload.source=idle_index)
    - 关系矛盾巡检结果(调用 ensemble 的轨道检查,如有项目轨道)
    - .ai_index/{doc_id}_summary.json 落盘
    全部产物为 draft/建议,绝不自动批准。任何一步失败不阻断整体。
    """
    result = {"doc_id": doc_id, "indexed_at": _now(), "summary_card": None,
              "conflict_checks": [], "chars": len(raw_content or "")}
    try:
        from services.telemetry_store import text_digest

        # 摘要卡(仅当 indexer 可用时)
        if indexer is not None and (raw_content or "").strip():
            try:
                from models.cards import InfoCard

                preview = (raw_content or "").strip()[:400]
                card = InfoCard(
                    card_sub_type="idle_doc_summary",
                    category="plot",
                    content=preview[:200],
                    source_book_id="idle_indexer",
                    summary=f"[空闲索引摘要] {doc_id}({result['chars']}字)",
                    payload={
                        "doc_id": doc_id,
                        "content_preview": preview,
                        "digest": text_digest(raw_content),
                        "project_id": project_id,
                        "source": "idle_index",
                        "needs_author_review": True,
                    },
                    scope_level="project",
                    status="draft",  # 审而非发:永不自动approved
                )
                result["summary_card"] = await indexer.save_card(card)
            except Exception as exc:
                logger.warning("[IdleIndexer] 摘要卡失败: %s", exc)

        # 关系矛盾巡检(有轨道且内容涉及角色时)
        if ensemble_svc is not None:
            try:
                tracks = await ensemble_svc.get_tracks(project_id or "", at_chapter=None)
                from services.ensemble import EnsembleService

                for t in tracks:
                    if not (t.name or t.character_id):
                        continue
                    hit = EnsembleService.check_action_vs_track(t, raw_content or "")
                    if hit:
                        result["conflict_checks"].append({"character": t.character_id, "note": hit})
            except Exception as exc:
                logger.warning("[IdleIndexer] 矛盾巡检失败: %s", exc)
    except Exception as exc:
        logger.warning("[IdleIndexer] 索引文档异常(不阻断): %s", exc)

    try:
        path = _summary_path(doc_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("[IdleIndexer] 落盘失败(不阻断): %s", exc)
    return result


async def run_idle_index_cycle(
    pm, indexer=None, ensemble_svc=None, docs=None,
) -> dict:
    """一次完整空闲索引周期。

    - docs 未提供时从 pm 拉取全部项目文档
    - 只索引有欠账的文档;索引后更新清单
    返回 {indexed: n, skipped: m, failures: k}
    """
    if pm is None:
        return {"indexed": 0, "skipped": 0, "failures": 0}
    manifest = _load_manifest()
    if docs is None:
        docs = []
        try:
            projects = await pm.list_projects()
            for p in projects:
                pid = p.project_id if isinstance(p, dict) else getattr(p, "project_id", "")
                if not pid:
                    continue
                try:
                    pdocs = await pm.list_project_documents(pid)
                except Exception:
                    continue
                for d in pdocs:
                    docs.append({
                        "doc_id": d.doc_id if not isinstance(d, dict) else d["doc_id"],
                        "updated_at": d.updated_at if not isinstance(d, dict) else d.get("updated_at", ""),
                        "project_id": pid,
                        "raw_content": d.raw_content if not isinstance(d, dict) else d.get("raw_content", ""),
                    })
        except Exception as exc:
            logger.warning("[IdleIndexer] 拉取项目文档失败: %s", exc)
            return {"indexed": 0, "skipped": 0, "failures": 0}

    pending = pending_docs(docs, manifest)
    indexed = 0
    failures = 0
    for d in pending:
        try:
            await index_document(
                d["doc_id"], d.get("raw_content", ""),
                project_id=d.get("project_id", ""),
                indexer=indexer, ensemble_svc=ensemble_svc,
            )
            manifest[d["doc_id"]] = _now()
            indexed += 1
        except Exception as exc:
            logger.warning("[IdleIndexer] 文档 %s 索引失败: %s", d.get("doc_id"), exc)
            failures += 1
    _save_manifest(manifest)
    return {"indexed": indexed, "skipped": len(docs) - len(pending), "failures": failures}


async def is_system_idle(batch1_task_manager=None) -> bool:
    """系统空闲判定:批次1队列无活跃任务且待执行数为0。"""
    if batch1_task_manager is None:
        return True
    try:
        qsize = batch1_task_manager.queue_size
        active = getattr(batch1_task_manager, "_active_task_id", None)
        return qsize == 0 and active is None
    except Exception:
        return True
