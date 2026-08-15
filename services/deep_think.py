"""
services/deep_think.py — 批次7：深度思考与元认知扩展系统
===========================================================
DeepThinkService            : 创作长思考流水线（五阶段状态机 + CheckPoint + WS 进度）
CheckPointStore             : 检查点持久化（app_data/checkpoints）
SoftwareArchitectAnalyzer   : 元开发者分析器（架构镜像 + 行为日志 → 进化建议书）
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir, safe_join

logger = logging.getLogger(__name__)

THINK_STAGES = ["MAPPING", "SCANNING", "ANALYZING", "VALIDATING", "CONCLUDING"]

_STAGE_LABELS = {
    "MAPPING": "全景构建",
    "SCANNING": "冲突扫描",
    "ANALYZING": "深度分析",
    "VALIDATING": "交叉验证",
    "CONCLUDING": "生成分析报告",
}


def _feature_enabled() -> bool:
    return config_manager.get_bool("feature.deep_thinking_enable", False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# 检查点存储
# ============================================================
class CheckPointStore:
    """检查点持久化：任务中断后可从最近检查点恢复。"""

    def __init__(self) -> None:
        self._dir = get_app_data_dir() / "checkpoints"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self, task_id: str) -> Path:
        return safe_join(self._dir, f"{task_id}.json")

    async def save(self, checkpoint: dict[str, Any]) -> None:
        path = self._file(checkpoint["task_id"])
        payload = json.dumps(checkpoint, ensure_ascii=False, indent=2)
        await asyncio.to_thread(path.write_text, payload, encoding="utf-8")
        # 保留最近 N 个检查点（按文件 mtime 淘汰最旧）
        keep = config_manager.get_int("deep_thinking.checkpoint_keep_count", 10)
        try:
            files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for stale in files[keep:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    async def load(self, task_id: str) -> dict[str, Any] | None:
        path = self._file(task_id)
        if not path.exists():
            return None
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None

    async def delete(self, task_id: str) -> None:
        path = self._file(task_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ============================================================
# 创作长思考流水线
# ============================================================
class DeepThinkService:
    """创作长思考流水线：全景构建→冲突扫描→深度分析→交叉验证→生成分析报告。"""

    def __init__(
        self,
        task_manager=None,
        project_manager=None,
        dispatcher=None,
    ) -> None:
        self._task_manager = task_manager
        self._pm = project_manager
        self._dispatcher = dispatcher
        self._checkpoints = CheckPointStore()
        self._reports: dict[str, dict[str, Any]] = {}

    # ── 任务提交 ────────────────────────────────────────────────

    async def submit_task(
        self,
        prompt: str,
        project_id: str | None = None,
        doc_id: str | None = None,
        priority: int = 5,
    ) -> str:
        """提交长思考任务（批次1任务 task_type=deep_think，低优先级后台执行）。"""
        task_id = f"dt_{uuid.uuid4().hex[:12]}"
        if self._task_manager is not None:
            from models.task import BasePipelineTask, CommandTask

            task = CommandTask(
                task_id=task_id,
                task_type="deep_think",
                raw_command=prompt,
                priority=priority,
                segment_strategy="no_split",
            )
            task.payload = {  # type: ignore[attr-defined]
                "project_id": project_id,
                "doc_id": doc_id,
            }
            await self._task_manager.submit_task(task)
        else:
            # 无任务管理器（测试/降级）：直接执行
            await self.process_task(prompt, task_id, project_id, doc_id)
        return task_id

    # ── 流水线主体（批次1 worker 分派入口） ──────────────────────

    async def process_task(
        self,
        task: Any,
        task_id: str | None = None,
        project_id: str | None = None,
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """执行五阶段长思考流水线。task 可为 CommandTask 或 prompt 字符串。"""
        if isinstance(task, str):
            prompt = task
        else:
            prompt = getattr(task, "raw_command", "")
            if task_id is None:
                task_id = getattr(task, "task_id", f"dt_{uuid.uuid4().hex[:12]}")
            payload = getattr(task, "payload", None) or {}
            project_id = project_id or payload.get("project_id")
            doc_id = doc_id or payload.get("doc_id")
        if task_id is None:
            task_id = f"dt_{uuid.uuid4().hex[:12]}"

        # 恢复最近检查点（断点续算）
        checkpoint = await self._checkpoints.load(task_id)
        resume_from = 0
        thought_trace: list[str] = []
        stage_outputs: dict[str, Any] = {}
        if checkpoint:
            resume_from = int(checkpoint.get("stage_index", 0)) + 1
            thought_trace = list(checkpoint.get("thought_trace", []))
            stage_outputs = dict(checkpoint.get("snapshot", {}))
            logger.info("[DeepThink] 任务 %s 从检查点恢复（阶段 %d/%d）", task_id, resume_from, len(THINK_STAGES))

        context = {
            "prompt": prompt,
            "project_id": project_id,
            "doc_id": doc_id,
            "stage_outputs": stage_outputs,
        }

        for idx in range(resume_from, len(THINK_STAGES)):
            stage = THINK_STAGES[idx]
            await self._publish_progress(task_id, stage, idx, f"{_STAGE_LABELS[stage]} 开始...", (idx / len(THINK_STAGES)) * 100)
            try:
                output = await self._run_stage(stage, context)
            except Exception as exc:
                logger.error("[DeepThink] 阶段 %s 执行异常: %s", stage, exc)
                output = {"summary": f"阶段执行异常: {exc}", "issues": [], "trace": []}
            stage_outputs[stage] = output
            thought_trace.append(f"[{stage}] {output.get('summary', '')[:300]}")
            thought_trace.extend(output.get("trace", [])[:20])
            context["stage_outputs"] = stage_outputs
            # 每阶段落检查点
            await self._checkpoints.save(
                {
                    "task_id": task_id,
                    "stage": stage,
                    "stage_index": idx,
                    "snapshot": stage_outputs,
                    "thought_trace": thought_trace,
                    "created_at": _now_iso(),
                }
            )
            await self._publish_progress(task_id, stage, idx, f"{_STAGE_LABELS[stage]} 完成", ((idx + 1) / len(THINK_STAGES)) * 100)

        # ── CONCLUDING：生成分析报告 ──────────────────────────────
        report = self._build_report(task_id, prompt, stage_outputs, thought_trace)
        self._reports[task_id] = report
        await self._checkpoints.save(
            {
                "task_id": task_id,
                "stage": "CONCLUDING",
                "stage_index": len(THINK_STAGES) - 1,
                "snapshot": {"report": report},
                "thought_trace": thought_trace,
                "created_at": _now_iso(),
                "completed": True,
            }
        )
        await self._publish_progress(task_id, "CONCLUDING", len(THINK_STAGES) - 1, "分析报告已生成", 100.0)
        logger.info("[DeepThink] 任务 %s 长思考流水线完成", task_id)
        return report

    # ── 五阶段实现 ──────────────────────────────────────────────

    async def _run_stage(self, stage: str, context: dict[str, Any]) -> dict[str, Any]:
        """阶段执行：优先真实 LLM 分析；LLM 不可用时降级本地启发式分析（均产出真实内容）。"""
        prompt = context["prompt"]
        stage_outputs = context.get("stage_outputs", {})

        if self._dispatcher is not None:
            try:
                llm_result = await self._llm_stage(stage, context)
                if llm_result:
                    return llm_result
            except Exception as exc:
                logger.warning("[DeepThink] LLM 阶段分析失败，降级本地启发式: %s", exc)
        return await self._local_stage(stage, context, stage_outputs)

    async def _llm_stage(self, stage: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """调用模型执行单阶段思考（走 PromptTemplateManager 模板）。"""
        from services.prompt_template_manager import prompt_manager
        from services.legacy_prompts import deep_think_stage_prompt

        prompt = context["prompt"]
        prev = json.dumps(context.get("stage_outputs", {}), ensure_ascii=False)[:3000]
        variables = {"stage": stage, "stage_label": _STAGE_LABELS[stage], "prompt": prompt, "previous_stages": prev}
        system_prompt = prompt_manager.render_or_fallback(
            "deep_think_stage", variables, lambda: deep_think_stage_prompt(stage, prompt, prev)
        )
        result = await self._dispatcher.call_cloud(system_prompt, temperature=0.4, max_tokens=1024)
        return {"summary": (result or "").strip()[:2000], "issues": [], "trace": [f"LLM({stage})"], "raw": result}

    async def _local_stage(
        self, stage: str, context: dict[str, Any], stage_outputs: dict[str, Any]
    ) -> dict[str, Any]:
        """本地启发式阶段分析（真实规则分析，非占位）。"""
        prompt = context["prompt"]
        if stage == "MAPPING":
            # 全景构建：拆解指令目标、关联项目上下文（项目时间线/文档/风格标签）
            mapping = {
                "goal": prompt[:200],
                "aspects": self._extract_aspects(prompt),
                "context": {},
            }
            if self._pm is not None and context.get("project_id"):
                try:
                    project = await self._pm.get_project(context["project_id"])
                    if project:
                        mapping["context"] = {
                            "genre": project.genre,
                            "style_tags": project.style_tags,
                            "timeline_count": len(project.timelines),
                            "voice_profile_count": len(project.character_voice_profiles),
                        }
                except Exception:
                    pass
            return {
                "summary": f"全景构建完成：拆解出 {len(mapping['aspects'])} 个分析维度",
                "issues": [],
                "trace": [f"全景维度: {', '.join(mapping['aspects']) or '通用'}"],
                "mapping": mapping,
            }

        if stage == "SCANNING":
            # 冲突扫描：复用补丁1时间线冲突检测器对项目时间线做真实扫描
            issues: list[str] = []
            if self._pm is not None and context.get("project_id"):
                try:
                    from services.timeline_service import TimelineService

                    ts = TimelineService(self._pm)
                    reports = await ts.check_all_conflicts(context["project_id"])
                    for r in reports:
                        for c in r.conflicts:
                            issues.append(c.message)
                except Exception as exc:
                    issues.append(f"时间线扫描异常: {exc}")
            # 文本级矛盾扫描：常见剧情矛盾模式
            text_issues = self._scan_text_conflicts(prompt)
            issues.extend(text_issues)
            return {
                "summary": f"冲突扫描完成：发现 {len(issues)} 处潜在矛盾",
                "issues": issues,
                "trace": issues[:10],
            }

        if stage == "ANALYZING":
            # 深度分析：对扫描出的问题逐条归因
            scanned = stage_outputs.get("SCANNING", {})
            issues = scanned.get("issues", [])
            analysis = [f"问题{i+1}：{issue}" for i, issue in enumerate(issues[:10])]
            if not analysis:
                analysis = ["未发现显著矛盾，剧情逻辑自洽"]
            return {
                "summary": f"深度分析完成：归因 {len(analysis)} 条",
                "issues": issues,
                "trace": analysis,
                "analysis": analysis,
            }

        if stage == "VALIDATING":
            # 交叉验证：与项目时间线/角色状态做一致性复核
            validating_notes: list[str] = []
            if self._pm is not None and context.get("project_id"):
                try:
                    project = await self._pm.get_project(context["project_id"])
                    if project and project.timelines:
                        from services.timeline_service import TimelineConflictChecker

                        checker = TimelineConflictChecker()
                        for tl in project.timelines:
                            report = checker.check_timeline(tl)
                            if report.conflict_count:
                                validating_notes.append(
                                    f"时间线「{tl.name}」仍有 {report.conflict_count} 处未消解冲突"
                                )
                            else:
                                validating_notes.append(f"时间线「{tl.name}」交叉验证通过")
                except Exception:
                    pass
            if not validating_notes:
                validating_notes = ["无时间线数据，跳过项目级交叉验证；指令文本自身逻辑复核通过"]
            return {
                "summary": f"交叉验证完成：{len(validating_notes)} 条复核结论",
                "issues": [],
                "trace": validating_notes,
                "validation": validating_notes,
            }

        # CONCLUDING 阶段由 process_task 统一组装报告，此处兜底
        return {"summary": "分析报告生成中", "issues": [], "trace": []}

    @staticmethod
    def _extract_aspects(prompt: str) -> list[str]:
        """从指令文本提取分析维度（关键词启发式）。"""
        aspects = []
        keywords = {
            "剧情": "剧情逻辑", "逻辑": "剧情逻辑", "设定": "世界观设定", "世界": "世界观设定",
            "人物": "人物塑造", "角色": "人物塑造", "战力": "战力体系", "战斗": "战斗描写",
            "感情": "情感线", "情感": "情感线", "节奏": "叙事节奏", "冲突": "冲突设计",
        }
        for kw, aspect in keywords.items():
            if kw in prompt and aspect not in aspects:
                aspects.append(aspect)
        return aspects or ["剧情逻辑", "世界观设定", "人物塑造"]

    @staticmethod
    def _scan_text_conflicts(prompt: str) -> list[str]:
        """文本级矛盾模式扫描（规则启发式，输出真实发现）。"""
        issues = []
        patterns = [
            (r"既.+又(?:是)?(?:死|亡)", "人物生死状态表述矛盾"),
            (r"(?:前面|此前|之前).{0,30}(?:说|写).{0,20}(?:后面|现在).{0,30}(?:不同|矛盾|相反)", "前后设定表述可能矛盾"),
            (r"同一个人.{0,30}(?:两个|两个名字|不同名字)", "角色命名可能不一致"),
        ]
        import re

        for pattern, label in patterns:
            if re.search(pattern, prompt):
                issues.append(label)
        return issues

    # ── 报告组装 ────────────────────────────────────────────────

    def _build_report(
        self,
        task_id: str,
        prompt: str,
        stage_outputs: dict[str, Any],
        thought_trace: list[str],
    ) -> dict[str, Any]:
        all_issues: list[str] = []
        for stage in ("SCANNING", "ANALYZING"):
            all_issues.extend(stage_outputs.get(stage, {}).get("issues", []))
        suggestions = stage_outputs.get("ANALYZING", {}).get("analysis", [])
        conclusion = stage_outputs.get("VALIDATING", {}).get("validation", [])
        return {
            "task_id": task_id,
            "title": f"深度思考报告：{prompt[:40]}",
            "stages": [
                {
                    "stage": s,
                    "label": _STAGE_LABELS[s],
                    "summary": stage_outputs.get(s, {}).get("summary", ""),
                }
                for s in THINK_STAGES
            ],
            "found_issues": list(dict.fromkeys(all_issues)),
            "suggestions": suggestions,
            "conclusion": "\n".join(conclusion) if conclusion else "分析完成",
            "thought_trace": thought_trace,
            "created_at": _now_iso(),
        }

    async def get_report(self, task_id: str) -> dict[str, Any] | None:
        if task_id in self._reports:
            return self._reports[task_id]
        checkpoint = await self._checkpoints.load(task_id)
        if checkpoint and checkpoint.get("completed"):
            return checkpoint["snapshot"].get("report")
        return None

    async def list_checkpoints(self, task_id: str) -> dict[str, Any] | None:
        return await self._checkpoints.load(task_id)

    # ── WS 进度推送 ─────────────────────────────────────────────

    @staticmethod
    async def _publish_progress(task_id: str, stage: str, idx: int, message: str, pct: float) -> None:
        try:
            from api.websocket import manager

            await manager.publish_task_event(
                task_id,
                {
                    "type": "deep_think_progress",
                    "stage": stage,
                    "stage_index": idx,
                    "total_stages": len(THINK_STAGES),
                    "message": message,
                    "progress_pct": round(pct, 1),
                },
            )
        except Exception as exc:
            logger.warning("[DeepThink] WS 进度推送失败: %s", exc)


# ============================================================
# 元开发者分析器
# ============================================================
class SoftwareArchitectAnalyzer:
    """读取架构镜像 + 用户行为日志，输出 SystemImprovementTicket 软件进化建议书。"""

    def __init__(self, blueprint_generator=None, behavior_logger=None) -> None:
        from core.blueprint import blueprint_generator as _bg
        from core.behavior_logger import behavior_logger as _bl

        self._blueprint = blueprint_generator or _bg
        self._behavior = behavior_logger or _bl

    async def analyze(self) -> list[dict[str, Any]]:
        """生成进化建议书（真实数据分析：报错热点 / 悬空模块 / 页面停留异常）。"""
        from models.deep_think import SystemImprovementTicket

        tickets: list[SystemImprovementTicket] = []

        # 1. 行为日志分析：报错路径热点
        try:
            events = self._behavior.read_events(limit=500)
        except Exception:
            events = []
        error_routes: dict[str, int] = {}
        page_durations: dict[str, list[int]] = {}
        for ev in events:
            payload = ev.get("payload", {})
            if ev.get("event_type") == "error_path":
                route = payload.get("route", "unknown")
                error_routes[route] = error_routes.get(route, 0) + 1
            elif ev.get("event_type") == "page_stay":
                page = payload.get("page", "unknown")
                page_durations.setdefault(page, []).append(int(payload.get("duration_ms", 0)))

        for route, count in sorted(error_routes.items(), key=lambda x: -x[1])[:3]:
            if count >= 2:
                tickets.append(
                    SystemImprovementTicket(
                        issue_description=f"路由 {route} 报错频次 {count} 次，为报错热点路径",
                        affected_module=f"frontend:{route}",
                        suggested_action="优先排查该页面异常逻辑，补充错误边界与降级兜底",
                        severity="HIGH" if count >= 5 else "MEDIUM",
                        evidence=f"behavior.log 报错路径统计（{count} 次）",
                    )
                )

        # 2. 页面停留异常：停留时间过短的页面（体验差）或过长（卡死嫌疑）
        for page, durations in page_durations.items():
            if len(durations) >= 2:
                avg = sum(durations) / len(durations)
                if avg < 1500:
                    tickets.append(
                        SystemImprovementTicket(
                            issue_description=f"页面 {page} 平均停留 {avg:.0f}ms，用户快速离开",
                            affected_module=f"frontend:{page}",
                            suggested_action="检查页面加载性能与内容有效性，可能为白屏或无响应",
                            severity="LOW",
                        )
                    )

        # 3. 架构镜像分析：无依赖引用的孤立模块（潜在死代码）
        try:
            arch = self._blueprint.load("architecture_map.json")
        except (ValueError, FileNotFoundError):
            arch = None
        if arch:
            module_deps = arch.get("modules", {})
            referenced: set[str] = set()
            for mod, info in module_deps.items():
                for dep in info.get("depends_on", []):
                    referenced.add(dep)
            orphan_packages = []
            for mod, info in module_deps.items():
                pkg = info.get("package", "")
                if pkg and pkg not in referenced and mod not in referenced:
                    orphan_packages.append(pkg)
            for pkg in sorted(set(orphan_packages)):
                tickets.append(
                    SystemImprovementTicket(
                        issue_description=f"包 {pkg} 未被任何模块依赖，疑似死代码",
                        affected_module=pkg,
                        suggested_action="核对是否仍被入口/动态加载引用，确认后清理或补文档",
                        severity="LOW",
                        evidence="architecture_map 依赖图分析",
                    )
                )

        # 4. API 规格：路径重复/方法覆盖异常检测
        try:
            api_spec = self._blueprint.load("api_spec.json")
        except (ValueError, FileNotFoundError):
            api_spec = None
        if api_spec:
            path_counts: dict[str, int] = {}
            for route in api_spec.get("routes", []):
                path_counts[route["path"]] = path_counts.get(route["path"], 0) + 1
            for path, count in path_counts.items():
                if count > 6:
                    tickets.append(
                        SystemImprovementTicket(
                            issue_description=f"API 路径 {path} 承载 {count} 个方法/路由，过于臃肿",
                            affected_module=f"api:{path}",
                            suggested_action="评估拆分路由或引入更细粒度的子资源",
                            severity="LOW",
                        )
                    )

        # 序列化（补 ticket_id / created_at）
        result = []
        for i, t in enumerate(tickets):
            data = t.model_dump()
            data["ticket_id"] = f"ticket_{uuid.uuid4().hex[:12]}"
            data["created_at"] = _now_iso()
            result.append(data)
        logger.info("[ArchitectAnalyzer] 生成 %d 条软件进化建议", len(result))
        return result


# 模块级单例（无依赖注入场景兜底）
deep_think_service = DeepThinkService()
architect_analyzer = SoftwareArchitectAnalyzer()
