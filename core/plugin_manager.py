import os
import json
import logging
from pathlib import Path
from utils.resource_path import get_resource_path

logger = logging.getLogger(__name__)

class PluginManager:
    """
    补丁C扩展：轻量级插件系统。
    允许动态加载第三方扩展，而无需修改系统源码。
    """
    def __init__(self):
        self.plugins_dir = get_resource_path("plugins")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.loaded_plugins = {}

    def load_all(self):
        logger.info(f"正在扫描插件目录: {self.plugins_dir}")
        for plugin_path in self.plugins_dir.iterdir():
            if plugin_path.is_dir():
                manifest_file = plugin_path / "plugin.json"
                if manifest_file.exists():
                    self._load_plugin(manifest_file)

    def _load_plugin(self, manifest_file: Path):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            
            plugin_id = manifest.get("id")
            logger.info(f"🧩 发现插件: {manifest.get('name')} (v{manifest.get('version')})")
            
            # 此处可以添加沙盒隔离机制（如 restrictedpython）
            # 当前演示：将其记录到已加载列表
            self.loaded_plugins[plugin_id] = manifest
            
            # 假设插件提供了新的提取策略
            strategies = manifest.get("extraction_strategies", [])
            for strategy in strategies:
                logger.info(f"   -> 注册扩展策略: {strategy}")
                
        except Exception as e:
            logger.error(f"加载插件失败 {manifest_file}: {e}")

plugin_manager = PluginManager()
