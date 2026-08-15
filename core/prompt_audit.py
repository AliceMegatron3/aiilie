"""
core/prompt_audit.py — 硬编码 Prompt 启动自检（架构整改 1.3）
==============================================================
系统启动时扫描已知业务模块源码，检测是否仍残留大段硬编码
system prompt（批次2书籍提取 / 批次3文档学习 / 批次4反思引擎 /
补丁G情感引擎），发现残留即打印告警日志。

审计原理：以「模板指纹」匹配源码文本。业务模块已迁移为
prompt_manager.render_or_fallback(...) 后，指纹（原硬编码的特征句）
不再出现在其源码中；services/legacy_prompts.py 为兜底集中区，
列入白名单豁免。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 审计规则：(业务模块相对路径, 特征指纹列表)
# 指纹为迁移前硬编码 prompt 的独特句子（迁移后业务源码中不应再出现）。
_AUDIT_RULES: list[tuple[str, list[str]]] = [
    (
        "strategies/learning.py",
        ["你是一个专业的文学内容分析AI"],
    ),
    (
        "services/expert_agents.py",
        [
            "你的唯一任务是提取新的世界观设定",
            "你的唯一任务是分析文中的战斗场面",
            "你的唯一任务是提取人物之间的对话交锋",
            "你的唯一任务是用上帝视角",
        ],
    ),
    (
        "utils/prompt_adapter.py",
        ["你是一个高级量化智能体", "你是一个高效的数据处理智能体"],
    ),
    (
        "services/divergent_engine.py",
        ["你是一个拥有顶级逻辑思维的网文主编", "你是一个拥有顶级逻辑思维的网文审核主编"],
    ),
    (
        "services/reflection_engine.py",
        [],  # 本模块无静态 system prompt（占位，保持审计覆盖声明）
    ),
    (
        "services/emotion_engine/quantifier_service.py",
        [],  # 补丁G情感引擎已迁移模板（占位）
    ),
]

# 豁免白名单：兜底文本集中区（硬编码属预期，仅模板缺失时启用）
_WHITELIST_FILES = {"services/legacy_prompts.py"}


def run_prompt_audit(project_root: Path | None = None) -> list[str]:
    """
    扫描审计规则中的业务模块，返回仍残留硬编码 prompt 的告警描述列表。

    Args:
        project_root: 项目根目录；默认取本文件上级目录。
    Returns:
        告警字符串列表（空列表表示全部通过）。
    """
    root = project_root or Path(__file__).resolve().parent.parent
    findings: list[str] = []

    for rel_path, fingerprints in _AUDIT_RULES:
        source_file = root / rel_path
        if not source_file.exists():
            logger.debug("[PromptAudit] 审计模块不存在，跳过: %s", rel_path)
            continue
        if rel_path in _WHITELIST_FILES:
            continue
        try:
            source_text = source_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("[PromptAudit] 读取审计模块失败: %s - %s", rel_path, exc)
            continue

        for fp in fingerprints:
            if fp in source_text:
                findings.append(f"{rel_path} (指纹: {fp[:20]}...)")

    if findings:
        logger.warning(
            "[PromptAudit] ⚠️ 检测到 %d 处业务模块仍残留硬编码 system prompt，"
            "请迁移至 PromptTemplateManager: %s",
            len(findings), findings,
        )
    else:
        logger.info(
            "[PromptAudit] ✅ 硬编码 Prompt 审计通过：批次2/3/4/情感引擎业务模块"
            "均已迁移至 PromptTemplateManager"
        )
    return findings
