"""
strategies/learning.py — 学习策略层
=====================================
定义自学习引擎的策略抽象基类与默认实现。
负责将自然语言文本转化为结构化的 AIParseResult 模型所需的数据抽取 Prompt，
并提供健壮的 JSON 解析器以提取风格、角色、题材等。
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

class LearningStrategy(ABC):
    """自学习策略抽象基类。"""
    
    @abstractmethod
    def generate_prompt(self, text_segment: str) -> str:
        """根据输入的文本段落生成 AI 分析 Prompt"""
        pass

    @abstractmethod
    def parse_response(self, response: str) -> dict[str, Any]:
        """将 AI 返回的非结构化响应转换为结构化字典"""
        pass

    @abstractmethod
    def merge_results(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """合并多个片段的学习结果"""
        pass


class DefaultLearningStrategy(LearningStrategy):
    """
    通用默认学习策略。
    适配小说的通用元素（角色、风格、题材、剧情需求）。
    要求大模型输出纯 JSON 格式。
    """

    def generate_prompt(self, text_segment: str) -> str:
        # 架构整改 1.3：system prompt 迁移 PromptTemplateManager；
        # 模板缺失/渲染失败时回退旧基线硬编码（services/legacy_prompts.py）。
        from services.prompt_template_manager import prompt_manager
        from services.legacy_prompts import learning_doc_analyze

        return prompt_manager.render_or_fallback(
            "learning_doc_analyze",
            {"text_segment": text_segment},
            lambda: learning_doc_analyze(text_segment),
        )

    def parse_response(self, response: str) -> dict[str, Any]:
        """利用正则和容错机制解析出 JSON 字典"""
        try:
            # 去除可能的 markdown 标记
            clean_text = re.sub(r'```(json)?\n', '', response)
            clean_text = re.sub(r'```', '', clean_text).strip()
            # 找到大括号开始和结束的地方
            start = clean_text.find('{')
            end = clean_text.rfind('}')
            if start != -1 and end != -1:
                clean_text = clean_text[start:end+1]
                return json.loads(clean_text)
            else:
                raise ValueError("未找到有效的 JSON 大括号包裹段")
        except Exception as e:
            logger.error("AI 响应解析 JSON 失败: %s | 原始响应: %s", e, response)
            return {
                "genre_inferred": "未知",
                "style_analysis": "解析失败，需人工复核。",
                "key_characters": [],
                "core_demands": [],
                "index_keywords": []
            }

    def merge_results(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """合并多个长文本分段的分析结果"""
        merged = {
            "genre_inferred": set(),
            "style_analysis": [],
            "key_characters": {}, # 以角色名为 key 去重
            "core_demands": set(),
            "index_keywords": set()
        }

        for res in results:
            if genre := res.get("genre_inferred"):
                if genre != "未知":
                    merged["genre_inferred"].add(genre)
                    
            if style := res.get("style_analysis"):
                merged["style_analysis"].append(style)
                
            for char in res.get("key_characters", []):
                name = char.get("name")
                if name:
                    merged["key_characters"][name] = char.get("desc", "")
                    
            for demand in res.get("core_demands", []):
                merged["core_demands"].add(demand)
                
            for kw in res.get("index_keywords", []):
                merged["index_keywords"].add(kw)

        # 整理返回
        genre_list = list(merged["genre_inferred"])
        return {
            "genre_inferred": genre_list[0] if genre_list else "未知",
            "style_analysis": "；".join(merged["style_analysis"])[:500], # 截断超长
            "key_characters": [{"name": k, "desc": v} for k, v in merged["key_characters"].items()],
            "core_demands": list(merged["core_demands"]),
            "index_keywords": list(merged["index_keywords"])
        }
