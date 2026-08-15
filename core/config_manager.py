"""
core/config_manager.py — 全局配置与密钥统一入口
==================================================
收敛所有配置文件读取到本模块，业务代码严禁直接 open() 读取 YAML：
- config/config.yaml        → 主配置树（支持 AIILIE_ 环境变量覆盖）
- config/llm_provider.yaml  → 合并为 config 树的 llm_provider 分支（密钥统一出口）
"""
import os
import threading
from pathlib import Path
from typing import Any, Dict

import yaml

from utils.resource_path import get_resource_path


class ConfigManager:
    """线程安全的全局配置单例（进程内唯一实例）。"""

    _instance: "ConfigManager | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._config: Dict[str, Any] = {}
                    inst._load_config()
                    cls._instance = inst
        return cls._instance

    # ── 加载与合并 ────────────────────────────────────────────────
    def _load_config(self) -> None:
        self._config = {}
        # 1. 主配置
        main_path = get_resource_path("config/config.yaml")
        if main_path.exists():
            with open(main_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        # 2. 密钥配置文件（收敛入口：llm_provider 统一从 config_manager 读取）
        provider_path = get_resource_path("config/llm_provider.yaml")
        if provider_path.exists():
            with open(provider_path, "r", encoding="utf-8") as f:
                provider_cfg = yaml.safe_load(f) or {}
            self._config["llm_provider"] = provider_cfg
        # 3. 环境变量覆盖（AIILIE_DEEPSEEK_API_KEY 等，优先级最高）
        self._overlay_env_vars(self._config, prefix="AIILIE_")
        # 3.1 简写兼容：AIILIE_DEEPSEEK_API_KEY → llm_provider.deepseek.api_key。
        #     全路径写法为 AIILIE_LLM_PROVIDER_DEEPSEEK_API_KEY，
        #     为兼容 README/注释约定，此处显式映射简写形式（优先级最高）。
        short_key = os.environ.get("AIILIE_DEEPSEEK_API_KEY")
        if short_key:
            provider = self._config.setdefault("llm_provider", {})
            deepseek_cfg = provider.setdefault("deepseek", {})
            if isinstance(deepseek_cfg, dict):
                deepseek_cfg["api_key"] = short_key

    def reload(self) -> None:
        """重新加载配置（供 Settings API 修改配置后热生效）。"""
        with self._lock:
            self._load_config()

    def _overlay_env_vars(self, config_dict: dict, prefix: str = "") -> None:
        for key, value in config_dict.items():
            env_key = f"{prefix}{key.upper()}"
            if isinstance(value, dict):
                self._overlay_env_vars(value, prefix=f"{env_key}_")
            elif env_key in os.environ:
                env_val = os.environ[env_key]
                if env_val.lower() in ("true", "false"):
                    config_dict[key] = env_val.lower() == "true"
                elif env_val.isdigit():
                    config_dict[key] = int(env_val)
                else:
                    config_dict[key] = env_val

    # ── 读取接口 ─────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        val: Any = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, default)
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes")
        return bool(val)

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    # ── 密钥统一出口 ─────────────────────────────────────────────
    def get_llm_api_key(self, provider: str = "deepseek") -> str:
        """获取大模型 API 密钥（环境变量 > 配置文件）。"""
        return str(self.get(f"llm_provider.{provider}.api_key", "") or "").strip()

    def get_llm_base(self, provider: str = "deepseek", default: str = "") -> str:
        return str(self.get(f"llm_provider.{provider}.api_base", default) or default)

    def get_llm_model(self, provider: str = "deepseek", default: str = "") -> str:
        return str(self.get(f"llm_provider.{provider}.model_name", default) or default)


config_manager = ConfigManager()
