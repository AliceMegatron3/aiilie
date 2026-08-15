import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, List
from jinja2.sandbox import SandboxedEnvironment

from models.prompt_models import PromptTemplate
from utils.resource_path import get_resource_path, get_user_data_path
from core.config_manager import config_manager
from core.path_resolver import safe_join

logger = logging.getLogger(__name__)

# 模板版本历史最大保留数
_MAX_TEMPLATE_VERSIONS = 20

class PromptTemplateManager:
    """补丁F扩展：提示词模板管理系统，支持沙箱渲染。"""
    
    def __init__(self):
        # 内置模板使用相对路径提取（打包时解压到临时目录）
        self.builtin_dir = get_resource_path("data/prompts/builtin")
        # 用户模板强制定向到 APPDATA 确保不丢失
        self.user_dir = get_user_data_path("data/prompts/user")
        
        self.builtin_dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        
        self.templates: Dict[str, PromptTemplate] = {}
        # 安全的 Jinja2 沙箱环境，防止恶意模板执行任意 Python
        self.jinja_env = SandboxedEnvironment()
        self.load_all()

    def load_all(self):
        self.templates.clear()
        # 加载内置
        for f in self.builtin_dir.glob("*.json"):
            self._load_file(f, True)
        # 加载用户
        for f in self.user_dir.glob("*.json"):
            self._load_file(f, False)
        logger.info(f"📝 成功加载 {len(self.templates)} 个提示词模板。")

    def _load_file(self, filepath: Path, is_builtin: bool):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                data['is_builtin'] = is_builtin
                tpl = PromptTemplate(**data)
                # 使用 template_id 作为唯一标识，如果存在变体可用 template_id#model_variant 区分
                key = f"{tpl.template_id}#{tpl.model_variant}" if tpl.model_variant else tpl.template_id
                self.templates[key] = tpl
        except Exception as e:
            logger.error(f"加载模板文件失败 {filepath}: {e}")

    def render(self, template_id: str, variables: dict, model_key: str = None) -> str:
        """根据 template_id 和传入的模型，渲染模板文本。支持模型变体 Fallback。

        第三部分：渲染前校验继承链，循环继承直接抛 ValueError。
        """
        # 优先寻找指定模型变体
        tpl = None
        if model_key:
            variant_key = f"{template_id}#{model_key}"
            tpl = self.templates.get(variant_key)
            
        if not tpl:
            # 降级寻找通用模板
            tpl = self.templates.get(template_id)
            
        if not tpl:
            raise ValueError(f"未找到模板: {template_id}")

        # 循环继承依赖检测：沿 inherit_template_id 链走，回到已访问节点即循环
        self._check_inherit_cycle(template_id)

        # 处理继承逻辑 (简单拼接，真实情况可扩展 jinja block)
        template_text = tpl.template_text
        if tpl.inherit_template_id:
            base_tpl = self.templates.get(tpl.inherit_template_id)
            if base_tpl:
                template_text = base_tpl.template_text + "\n\n" + template_text
                
        # 沙箱渲染
        try:
            j2_template = self.jinja_env.from_string(template_text)
            rendered = j2_template.render(**variables)
            return rendered
        except Exception as e:
            logger.error(f"模板渲染失败 [{template_id}]: {e}")
            raise e

    def _check_inherit_cycle(self, template_id: str) -> None:
        """循环继承依赖检测：A→B→A 抛 ValueError。

        仅检测单一层继承链（模板可能含 #model_variant 键，按基础 id 匹配）。
        """
        visited: set[str] = set()
        current: str = template_id
        while current and current not in visited:
            visited.add(current)
            tpl = self.templates.get(current)
            if tpl is None or not tpl.inherit_template_id:
                return
            current = tpl.inherit_template_id
        if current in visited:
            chain = " -> ".join(sorted(visited) + [current])
            raise ValueError(f"模板循环继承检测失败: {chain}（A→B→A 循环依赖禁止）")

    def render_or_fallback(
        self, template_id: str, variables: dict, fallback_factory
    ) -> str:
        """渲染模板；模板缺失/渲染失败时回退调用 fallback_factory() 的兜底文本。

        fallback_factory: 无参可调用对象，返回旧基线硬编码文本（见 services/legacy_prompts.py）。
        兜底保证打包环境模板意外缺失时业务行为与旧基线逐字一致。
        """
        try:
            return self.render(template_id, variables)
        except Exception as exc:
            logger.warning(
                "[PromptTemplate] 模板 %s 渲染失败，回退内置兜底: %s",
                template_id, exc,
            )
            try:
                return fallback_factory()
            except Exception as fallback_exc:  # pragma: no cover
                logger.error("[PromptTemplate] 兜底工厂执行失败: %s", fallback_exc)
                raise

    def save_template(self, template: PromptTemplate):
        if template.is_builtin:
            # 强制转存为用户空间
            template.is_builtin = False
            logger.info(f"内置模板 {template.template_id} 被修改，自动另存为用户副本。")
            
        key = f"{template.template_id}#{template.model_variant}" if template.model_variant else template.template_id
        
        # 第三部分：编辑自动版本快照（保存前把当前版本存入历史）
        self._snapshot_version(key, template)
        
        self.templates[key] = template
        
        # safe_join 防 template_id 路径穿越写文件
        out_file = safe_join(self.user_dir, f"{key.replace('#', '_')}.json")
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(template.model_dump(), f, ensure_ascii=False, indent=2)

    # ── 第三部分：模板编辑版本快照 / 历史 / 回滚 ─────────────────

    @property
    def versions_dir(self) -> Path:
        """模板版本历史目录（用户数据目录下）。"""
        d = get_user_data_path("data/prompts/template_versions")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _snapshot_version(self, key: str, new_template: PromptTemplate) -> None:
        """保存前把当前内存版本快照到版本历史（最多 20 版）。"""
        old = self.templates.get(key)
        if old is None:
            return
        # 内容未变化不产生版本
        if old.template_text == new_template.template_text and old.name == new_template.name:
            return

        tpl_dir = safe_join(self.versions_dir, key.replace("#", "_").replace("/", "_"))
        tpl_dir.mkdir(parents=True, exist_ok=True)
        index_path = tpl_dir / "index.json"

        index: list[dict] = []
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                index = []

        version_no = len(index) + 1
        ts = datetime.now(timezone.utc)
        snapshot_file = tpl_dir / f"v{version_no}_{ts.strftime('%Y%m%d%H%M%S')}.json"
        snapshot_file.write_text(
            json.dumps(old.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        index.append(
            {
                "version": version_no,
                "timestamp": ts.isoformat(),
                "file": snapshot_file.name,
                "name": old.name,
            }
        )
        # 淘汰最旧
        if len(index) > _MAX_TEMPLATE_VERSIONS:
            removed = index[: len(index) - _MAX_TEMPLATE_VERSIONS]
            index = index[len(index) - _MAX_TEMPLATE_VERSIONS:]
            for item in removed:
                stale = tpl_dir / item["file"]
                if stale.exists():
                    stale.unlink()
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_template_versions(self, template_id: str) -> list[dict]:
        """返回模板版本历史（不含模板正文）。"""
        key = template_id
        tpl_dir = safe_join(self.versions_dir, key.replace("#", "_").replace("/", "_"))
        index_path = tpl_dir / "index.json"
        if not index_path.exists():
            return []
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def get_template_version_content(self, template_id: str, version: int) -> dict:
        """读取指定版本的模板完整数据。"""
        versions = self.list_template_versions(template_id)
        target = next((v for v in versions if v.get("version") == version), None)
        if target is None:
            raise ValueError(f"模板版本不存在: {version}")
        tpl_dir = safe_join(
            self.versions_dir, template_id.replace("#", "_").replace("/", "_")
        )
        snapshot = tpl_dir / target["file"]
        if not snapshot.exists():
            raise ValueError(f"模板版本快照文件缺失: {target['file']}")
        return json.loads(snapshot.read_text(encoding="utf-8"))

    def rollback_template(self, template_id: str, version: int) -> PromptTemplate:
        """把模板回滚到指定历史版本（回滚本身产生新快照）。"""
        data = self.get_template_version_content(template_id, version)
        restored = PromptTemplate(**data)
        # 回滚到内置版本时保留用户副本语义
        restored.is_builtin = False
        restored.update_time = datetime.now(timezone.utc).isoformat()
        self.save_template(restored)
        logger.info("模板 %s 已回滚至版本 %d", template_id, version)
        return restored

prompt_manager = PromptTemplateManager()
