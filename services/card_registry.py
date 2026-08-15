"""
services/card_registry.py — 卡片类型注册中心
=============================================
管理双层卡片体系（资料卡、数据卡）的动态类型注册。
支持从 JSON 配置热加载、运行时注册/卸载卡片类型及对应处理策略。
"""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from core.path_resolver import get_app_data_dir

logger = logging.getLogger(__name__)


class CardTypeDefinition(BaseModel):
    """卡片类型的元数据定义。"""
    subtype: str = Field(..., description="卡片子类型标识，如 world_view")
    card_category: str = Field(..., description="基类归属：'info' 或 'data'")
    processor_path: str | None = Field(default=None, description="处理器类的完整导入路径")
    description: str = Field(default="", description="该类型卡片的描述")


class CardTypeRegistry:
    """
    卡片类型注册中心。
    负责维护 subtype -> 定义/处理器的映射，并支持热加载。
    """

    def __init__(self) -> None:
        self._definitions: dict[str, CardTypeDefinition] = {}
        self._processors: dict[str, Callable] = {}
        
        self.config_dir = get_app_data_dir() / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "card_types.json"

    def register_default_types(self) -> None:
        """注册内置的基础类型，包括批次13新增的多维发散经验卡"""
        default_types = [
            ("world_view", "info", "世界观设定卡"),
            ("character", "info", "角色设定卡"),
            ("info_event", "info", "事件简报卡"),
            ("data_combat", "data", "战斗力数据卡"),
            ("data_emotion", "data", "好感度数据卡"),
            # 繁杂模式多维发散卡
            ("logic_world_building", "info", "世界观推演逻辑卡 (发散思维)"),
            ("logic_character_arc", "info", "角色弧光发散卡 (发散思维)"),
            ("logic_combat_tension", "info", "战力冲突拉扯卡 (发散思维)"),
            ("logic_plot_twist", "info", "剧情悬念反转卡 (发散思维)"),
            ("logic_general_divergence", "info", "通用发散思维结晶 (发散思维)")
        ]
        for subtype, category, desc in default_types:
            if not self.is_valid_type(subtype):
                self.register_type(subtype=subtype, card_category=category, description=desc)

    def _import_processor(self, path: str) -> Callable | None:
        """根据点分路径动态导入处理器类或函数。"""
        try:
            module_name, obj_name = path.rsplit(".", 1)
            module = importlib.import_module(module_name)
            obj = getattr(module, obj_name)
            if callable(obj):
                return obj
            else:
                logger.error("加载的处理器不是可调用对象: %s", path)
                return None
        except Exception as e:
            logger.error("动态导入处理器失败 [%s]: %s", path, e)
            return None

    async def reload_config(self) -> None:
        """从 JSON 配置文件热加载所有的卡片类型与处理器映射。"""
        if not self.config_file.exists():
            logger.info("卡片类型配置文件不存在，跳过加载: %s", self.config_file)
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            loaded_count = 0
            for subtype, raw_def in data.items():
                # 兼容性补充 subtype 字段
                if "subtype" not in raw_def:
                    raw_def["subtype"] = subtype
                
                card_def = CardTypeDefinition(**raw_def)
                self._register_internal(card_def)
                loaded_count += 1
                
            logger.info("热加载完成，共载入 %d 种卡片类型", loaded_count)
        except Exception as e:
            logger.exception("热加载配置文件失败: %s", e)
            raise

    async def save_config(self) -> None:
        """将当前内存中所有卡片类型定义持久化到 JSON 配置文件中。"""
        try:
            data = {
                subtype: definition.model_dump(exclude={"subtype"})
                for subtype, definition in self._definitions.items()
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info("已保存卡片类型配置至: %s", self.config_file)
        except Exception as e:
            logger.error("保存配置文件失败: %s", e)

    def _register_internal(self, definition: CardTypeDefinition, processor: Callable | None = None) -> None:
        """内部实际执行注册的逻辑。"""
        subtype = definition.subtype
        if definition.card_category not in ("info", "data"):
            raise ValueError(f"无效的 card_category: {definition.card_category}，必须为 'info' 或 'data'")

        self._definitions[subtype] = definition
        
        # 优先使用传入的 callable，否则尝试从配置路径动态加载
        if processor is not None:
            self._processors[subtype] = processor
        elif definition.processor_path:
            loaded = self._import_processor(definition.processor_path)
            if loaded:
                self._processors[subtype] = loaded
            else:
                logger.warning("卡片类型 '%s' 未能成功绑定处理器", subtype)
        
        logger.debug("已注册卡片类型: %s (Category: %s)", subtype, definition.card_category)

    def register_type(
        self, 
        subtype: str, 
        card_category: str, 
        processor_path: str | None = None, 
        processor: Callable | None = None,
        description: str = ""
    ) -> None:
        """
        运行时动态注册新的卡片类型。
        如果已存在则会覆盖更新，但需调用 save_config() 才会持久化。
        """
        definition = CardTypeDefinition(
            subtype=subtype,
            card_category=card_category,
            processor_path=processor_path,
            description=description
        )
        self._register_internal(definition, processor)

    def unregister_type(self, subtype: str) -> bool:
        """运行时卸载指定的卡片类型。"""
        if subtype in self._definitions:
            del self._definitions[subtype]
            if subtype in self._processors:
                del self._processors[subtype]
            logger.info("已卸载卡片类型: %s", subtype)
            return True
        return False

    def is_valid_type(self, subtype: str) -> bool:
        """校验卡片类型是否存在。"""
        return subtype in self._definitions

    def get_definition(self, subtype: str) -> CardTypeDefinition | None:
        """获取卡片类型定义元数据。"""
        return self._definitions.get(subtype)

    def get_processor(self, subtype: str) -> Callable | None:
        """获取负责处理此卡片类型的策略/处理器。"""
        return self._processors.get(subtype)

    def get_all_types(self) -> list[CardTypeDefinition]:
        """获取所有已注册的卡片类型。"""
        return list(self._definitions.values())
