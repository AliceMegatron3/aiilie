"""作者审核提示词与结果解析。

该模块只负责把待审资产转换为审核意见，不执行批准、发布、启用或回滚。
"""
from __future__ import annotations

import json
from typing import Any

from services.prompt_template_manager import prompt_manager


def render_author_review_prompt(
    asset: Any,
    *,
    evidence: Any = None,
    scope: Any = None,
    existing_rules: Any = None,
    reviewer: str = "author",
) -> str:
    """渲染作者审核模板，所有输入均序列化为可审计文本。"""
    return prompt_manager.render(
        "author_asset_review",
        {
            "asset": json.dumps(asset, ensure_ascii=False, default=str),
            "evidence": json.dumps(evidence or [], ensure_ascii=False, default=str),
            "scope": json.dumps(scope or {}, ensure_ascii=False, default=str),
            "existing_rules": json.dumps(existing_rules or [], ensure_ascii=False, default=str),
            "reviewer": reviewer,
        },
    )


def parse_author_review(raw: Any) -> dict[str, Any] | None:
    """解析审核模型输出；格式错误返回 None，禁止当作批准结果。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        result = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    if result.get("decision") not in {
        "APPROVE", "APPROVE_GRAY", "MODIFY", "REJECT", "ARCHIVE", "ROLLBACK"
    }:
        return None
    return result