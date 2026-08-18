import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.path_resolver import get_app_data_dir
from core.plugin_lifecycle import plugin_lifecycle
# 注意：services.prompt_template_manager 的引用已移入 render_call_plan_prompt 函数体内
# 以消除 core→services 的模块级反向依赖（讨论稿20260816第二章差距盘点#6）。

logger = logging.getLogger(__name__)

_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MAX_ZIP_FILES = 512
_MAX_ZIP_UNPACKED_BYTES = 100 * 1024 * 1024
_MAX_PLUGIN_FILE_BYTES = 25 * 1024 * 1024
_ALLOWED_KINDS = {"resource", "extractor", "metric", "validator", "skill", "law", "projection", "writer"}


class PluginManifest(BaseModel):
    """Versioned plugin contract; manifest validation is not a sandbox."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    namespace: str = Field(default="local", min_length=1, max_length=128)
    kind: str = Field(default="resource")
    version: str = Field(default="0.0.0", min_length=1, max_length=64)
    api_version: str = Field(default="1.0", min_length=1, max_length=32)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    entrypoint: str | None = None
    external_command: list[str] | None = None
    requires: list[dict[str, Any] | str] = Field(default_factory=list)
    conflicts: list[dict[str, Any] | str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    resource_budget: dict[str, Any] = Field(default_factory=dict)
    artifact_digest: str | None = None
    source_repo: str | None = None
    source_revision: str | None = None
    license: str = "UNKNOWN"
    model_licenses: list[str] = Field(default_factory=list)
    signer: str | None = None
    provenance: str | None = None

    def validate_identity(self) -> None:
        if not _PLUGIN_ID_RE.fullmatch(self.id):
            raise ValueError("插件 id 只能包含小写字母、数字、点、下划线和短横线")
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError(f"不支持的插件 kind: {self.kind}")
        if self.entrypoint and self.external_command:
            raise ValueError("entrypoint 与 external_command 不能同时声明")
        if not self.entrypoint and not self.external_command:
            raise ValueError("插件必须声明 entrypoint 或 external_command")


class PluginManager:
    """
    补丁C扩展：轻量级插件系统。
    允许动态加载第三方扩展，而无需修改系统源码。
    """
    def __init__(self):
        # 插件属于用户数据，不得写入 PyInstaller 的只读/临时资源目录。
        self.plugins_dir = get_app_data_dir() / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir = self.plugins_dir / ".staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.trust_file = self.plugins_dir / "trust_store.json"
        self.policy_file = self.plugins_dir / "trust_policy.json"
        # 撤销/批准审计轨（Batch 6：append-only，可回放可解释）
        self.audit_file = self.plugins_dir / "trust_audit.jsonl"
        self.loaded_plugins: dict[str, dict[str, Any]] = {}

    def _append_audit(self, plugin_id: str, action: str, operator: str, detail: str = "") -> None:
        """只追加一条信任/撤销审计事件（JSONL，不覆盖/不删除，可回放）。"""
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "plugin_id": plugin_id,
            "action": action,          # APPROVE / REVOKE
            "operator": operator,
            "detail": detail,
        }
        try:
            with self.audit_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("[PluginManager] 审计写入失败 %s %s: %s", action, plugin_id, exc)

    def audit_trail(self, plugin_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """读取审计轨；可按 plugin_id 过滤（默认全部，最新在前）。"""
        if not self.audit_file.exists():
            return []
        events = []
        with self.audit_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if plugin_id is None or ev.get("plugin_id") == plugin_id:
                    events.append(ev)
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        return events[:limit]

    def _read_trust_policy(self) -> dict[str, Any]:
        defaults = {"allowed_signers": [], "require_signer": False, "require_cosign": False}
        try:
            data = json.loads(self.policy_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
        except (OSError, ValueError):
            pass
        return defaults

    def _cosign_verify(self, plugin_id: str, digest: str, signer: str | None) -> dict[str, Any]:
        policy = self._read_trust_policy()
        if not policy.get("require_cosign"):
            return {"required": False, "verified": None, "status": "NOT_REQUIRED"}
        manifest = self.loaded_plugins.get(plugin_id, {})
        bundle = manifest.get("provenance")
        if not bundle or not signer:
            return {"required": True, "verified": False, "status": "SIGNATURE_MATERIAL_MISSING"}
        # Batch 6：禁止 PluginManager 自己拼 Cosign 命令——统一委托唯一 ArtifactVerifier
        # （services.cosign_verifier.CosignVerifier）负责 cosign 命令构造与执行。
        from services.cosign_verifier import CosignVerifier

        live = CosignVerifier()
        report = live.verify_blob_identity(self.plugins_dir / plugin_id, signer)
        return {
            "required": True,
            "verified": bool(report["ok"]),
            "status": report["status"],
        }

    def _read_trust_store(self) -> dict[str, Any]:
        try:
            data = json.loads(self.trust_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_trust_store(self, data: dict[str, Any]) -> None:
        temporary = self.trust_file.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.trust_file)

    def get_trust_policy(self) -> dict[str, Any]:
        return self._read_trust_policy()

    def set_trust_policy(
        self,
        allowed_signers: list[str],
        require_signer: bool = False,
        require_cosign: bool = False,
    ) -> dict[str, Any]:
        cleaned = sorted({str(item).strip() for item in allowed_signers if str(item).strip()})
        if require_signer and not cleaned:
            raise ValueError("require_signer=true 时至少需要一个 allowed_signers")
        policy = {
            "allowed_signers": cleaned,
            "require_signer": bool(require_signer),
            "require_cosign": bool(require_cosign),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = self.policy_file.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.policy_file)
        return policy

    def trust_report(self, plugin_id: str) -> dict[str, Any]:
        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return {"plugin_id": plugin_id, "status": "NOT_FOUND", "trusted": False}
        record = self._read_trust_store().get(plugin_id)
        digest_match = bool(record and record.get("artifact_digest") == manifest.get("artifact_digest"))
        policy = self._read_trust_policy()
        declared_signer = str(manifest.get("signer") or "")
        approved_signer = str(record.get("signer") or "") if record else ""
        signer_required = bool(policy.get("require_signer") or declared_signer)
        allowed_signers = {str(item) for item in policy.get("allowed_signers", []) or []}
        signer_verified = bool(approved_signer and approved_signer == declared_signer) if declared_signer else bool(approved_signer)
        if allowed_signers:
            signer_verified = signer_verified and approved_signer in allowed_signers
        cosign = self._cosign_verify(plugin_id, manifest.get("artifact_digest", ""), approved_signer or declared_signer)
        trusted = digest_match and (not signer_required or signer_verified) and (not cosign["required"] or cosign["verified"] is True)
        return {
            "plugin_id": plugin_id,
            "artifact_digest": manifest.get("artifact_digest", ""),
            "approved_digest": record.get("artifact_digest") if record else None,
            "signer": manifest.get("signer"),
            "approved_signer": record.get("signer") if record else None,
            "digest_match": digest_match,
            "signer_match": signer_verified if signer_required else None,
            "trust_policy": policy,
            "cosign": cosign,
            "trusted": trusted,
            "status": "TRUSTED" if trusted else "UNTRUSTED",
        }

    def approve_plugin(self, plugin_id: str, operator: str = "author", signer: str | None = None) -> dict[str, Any]:
        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            raise ValueError("插件不存在")
        digest = str(manifest.get("artifact_digest", ""))
        if not digest:
            raise ValueError("插件缺少 artifact_digest")
        store = self._read_trust_store()
        policy = self._read_trust_policy()
        declared_signer = str(manifest.get("signer") or "")
        selected_signer = signer or declared_signer or ""
        if policy.get("require_signer") and not selected_signer:
            raise ValueError("当前 trust policy 要求 signer")
        allowed_signers = {str(item) for item in policy.get("allowed_signers", []) or []}
        if allowed_signers and selected_signer not in allowed_signers:
            raise ValueError("signer 不在 trust policy allowlist")
        if declared_signer and signer and signer != declared_signer:
            raise ValueError("批准 signer 与 manifest 声明不一致")
        record = {
            "plugin_id": plugin_id,
            "artifact_digest": digest,
            "operator": operator,
            "signer": selected_signer or None,
            "approval_policy": "digest_and_optional_signer",
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        store[plugin_id] = record
        self._write_trust_store(store)
        manifest["trust_state"] = "TRUSTED"
        manifest["trust_record"] = record
        self._append_audit(plugin_id, "APPROVE", operator,
                           f"digest={digest} signer={selected_signer or 'none'} policy={record['approval_policy']}")
        return record

    def revoke_plugin(self, plugin_id: str, operator: str = "author") -> dict[str, Any]:
        store = self._read_trust_store()
        record = store.pop(plugin_id, None)
        self._write_trust_store(store)
        if plugin_id in self.loaded_plugins:
            self.loaded_plugins[plugin_id]["trust_state"] = "REVOKED"
        digest = str(record.get("artifact_digest") if record else self.loaded_plugins.get(plugin_id, {}).get("artifact_digest", ""))
        self._append_audit(plugin_id, "REVOKE", operator,
                           f"digest={digest or 'unknown'} previous_trust={record is not None}")
        return {"plugin_id": plugin_id, "operator": operator, "revoked": record is not None, "revoked_at": datetime.now(timezone.utc).isoformat()}

    def load_all(self):
        """恢复磁盘上的 manifest；不会导入或执行第三方代码。"""
        logger.info("正在扫描插件目录: %s", self.plugins_dir)
        for plugin_path in self.plugins_dir.iterdir():
            if plugin_path.is_dir() and plugin_path.name != self.staging_dir.name:
                manifest_file = plugin_path / "plugin.json"
                if manifest_file.exists():
                    self._load_plugin(manifest_file)

    def is_trusted(self, plugin_id: str) -> bool:
        """digest 必须与独立 trust store 的批准记录完全一致。"""
        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return False
        record = self._read_trust_store().get(plugin_id)
        trusted = self.trust_report(plugin_id).get("trusted", False)
        manifest["trust_state"] = "TRUSTED" if trusted else "UNTRUSTED"
        if trusted:
            manifest["trust_record"] = record
        return trusted

    @staticmethod
    def _tree_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(relative)
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _validate_archive_member(name: str) -> None:
        normalized = name.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in parts:
            raise ValueError(f"不安全的插件归档路径: {name}")
        if any(part == "" for part in parts[:-1]):
            raise ValueError(f"插件归档包含非法空路径段: {name}")

    def _extract_zip_safely(self, archive: Path) -> Path:
        staging = Path(tempfile.mkdtemp(prefix="zip_", dir=self.staging_dir))
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                infos = zf.infolist()
                if len(infos) > _MAX_ZIP_FILES:
                    raise ValueError("插件归档文件数量超过上限")
                total_size = 0
                for info in infos:
                    self._validate_archive_member(info.filename)
                    if info.is_dir():
                        continue
                    if info.file_size > _MAX_PLUGIN_FILE_BYTES:
                        raise ValueError(f"插件文件过大: {info.filename}")
                    total_size += info.file_size
                    if total_size > _MAX_ZIP_UNPACKED_BYTES:
                        raise ValueError("插件归档解压后大小超过上限")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise ValueError("插件归档不允许包含符号链接")

                for info in infos:
                    if info.is_dir():
                        continue
                    target = (staging / info.filename.replace("\\", "/")).resolve()
                    target.relative_to(staging.resolve())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
            if (staging / "plugin.json").is_file():
                return staging
            children = [p for p in staging.iterdir() if p.is_dir()]
            if len(children) == 1 and (children[0] / "plugin.json").is_file():
                return children[0]
            raise ValueError("zip 包结构不正确：根目录或唯一子目录必须包含 plugin.json")
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @staticmethod
    def _read_manifest(manifest_file: Path) -> dict[str, Any]:
        try:
            raw = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest = PluginManifest.model_validate(raw)
            manifest.validate_identity()
            return manifest.model_dump(mode="json")
        except Exception as exc:
            raise ValueError(f"plugin.json 校验失败: {exc}") from exc

    def install_plugin(self, source_path: str) -> dict[str, Any]:
        """安全安装插件包；安装不等于信任或执行。"""
        source = Path(source_path).expanduser().resolve()
        extracted_root: Path | None = None
        try:
            if source.is_file() and source.suffix.lower() == ".zip":
                extracted_root = self._extract_zip_safely(source)
                candidate = extracted_root
            elif source.is_dir():
                candidate = source
            else:
                return {"success": False, "message": f"无效的插件来源路径: {source_path}"}

            manifest_file = candidate / "plugin.json"
            if not manifest_file.is_file():
                return {"success": False, "message": "插件目录中未找到 plugin.json"}
            manifest = self._read_manifest(manifest_file)
            artifact_digest = self._tree_digest(candidate)
            declared_digest = manifest.get("artifact_digest")
            if declared_digest and declared_digest != artifact_digest:
                return {"success": False, "message": "插件 artifact_digest 校验失败"}
            manifest["artifact_digest"] = artifact_digest
            plugin_id = manifest["id"]
            manifest["trust_state"] = "TRUSTED" if self._read_trust_store().get(plugin_id, {}).get("artifact_digest") == manifest.get("artifact_digest") else "UNTRUSTED"

            target = self.plugins_dir / plugin_id
            install_tmp = self.plugins_dir / f".{plugin_id}.installing"
            if install_tmp.exists():
                shutil.rmtree(install_tmp)
            shutil.copytree(candidate, install_tmp)
            if target.exists():
                shutil.rmtree(target)
            install_tmp.replace(target)

            if plugin_id in self.loaded_plugins:
                self.loaded_plugins.pop(plugin_id, None)
            plugin_lifecycle.register(plugin_id, manifest, replace=True)
            plugin_lifecycle.set_loading(plugin_id)
            self.loaded_plugins[plugin_id] = manifest
            # 未建立签名信任链时只能展示清单，禁止加载/执行。
            plugin_lifecycle.set_error(plugin_id, "插件尚未通过签名信任校验，已安装但未启用")
            return {
                "success": True,
                "plugin_id": plugin_id,
                "name": manifest.get("name"),
                "version": manifest.get("version"),
                "trust_state": manifest["trust_state"],
                "message": "插件已安装到受控目录，但尚未信任，未加载或执行",
            }
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            logger.warning("插件安装被拒绝: %s", exc)
            return {"success": False, "message": str(exc)}
        finally:
            if extracted_root is not None:
                shutil.rmtree(extracted_root if extracted_root.parent == self.staging_dir else extracted_root.parent, ignore_errors=True)

    def uninstall_plugin(self, plugin_id: str) -> dict[str, Any]:
        """卸载指定插件：从磁盘移除 + 从注册表移除。"""
        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return {"success": False, "message": f"插件 {plugin_id} 不存在"}

        plugin_dir = self.plugins_dir / plugin_id
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        self.loaded_plugins.pop(plugin_id, None)
        plugin_lifecycle.uninstall(plugin_id)
        logger.info("🗑️ 插件已卸载: %s", plugin_id)
        return {"success": True, "message": f"插件 {plugin_id} 已卸载"}

    def list_installed_plugins(self) -> list[dict[str, Any]]:
        """返回所有已安装插件的 manifest + 生命周期状态。"""
        result = []
        for pid, manifest in self.loaded_plugins.items():
            record = plugin_lifecycle.get(pid)
            entry = dict(manifest)
            if record:
                entry["state"] = record.state.value
                entry["error_message"] = record.error_message
            result.append(entry)
        return result

    def _load_plugin(self, manifest_file: Path):
        try:
            manifest = self._read_manifest(manifest_file)
            plugin_id = manifest["id"]
            manifest["artifact_digest"] = manifest.get("artifact_digest") or self._tree_digest(manifest_file.parent)
            manifest["trust_state"] = "TRUSTED" if self._read_trust_store().get(plugin_id, {}).get("artifact_digest") == manifest.get("artifact_digest") else "UNTRUSTED"
            self.loaded_plugins[plugin_id] = manifest
            plugin_lifecycle.register(plugin_id, manifest, replace=True)
            plugin_lifecycle.set_loading(plugin_id)
            plugin_lifecycle.set_error(plugin_id, "磁盘插件未通过签名信任校验，未加载或执行")
            logger.info("发现未信任插件清单: %s v%s", plugin_id, manifest.get("version"))
        except Exception as e:
            logger.error("加载插件失败 %s: %s", manifest_file, e)

    def get_manifests(self) -> list[dict[str, Any]]:
        """返回规划器可见的插件清单副本。"""
        return [dict(manifest) for manifest in self.loaded_plugins.values()]

    def resolve_capabilities(
        self,
        requested: list[str],
        required_capabilities: list[str] | None = None,
        caller_capabilities: list[str] | None = None,
        budget: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """生成执行前解析报告；永远不执行插件。"""
        from services.plugin_resolver import PluginResolver

        return PluginResolver(self.capability_catalog()).resolve(
            requested,
            required_capabilities=required_capabilities,
            caller_capabilities=caller_capabilities,
            budget=budget,
        )

    def capability_catalog(self) -> list[dict[str, Any]]:
        """只读能力目录；不把未信任插件伪装成可执行能力。"""
        catalog = []
        for manifest in self.loaded_plugins.values():
            catalog.append({
                "plugin_id": manifest.get("id", ""),
                "namespace": manifest.get("namespace", "local"),
                "kind": manifest.get("kind", ""),
                "version": manifest.get("version", ""),
                "capabilities": list(manifest.get("capabilities", [])),
                "requires": list(manifest.get("requires", [])),
                "conflicts": list(manifest.get("conflicts", [])),
                "trust_state": manifest.get("trust_state", "LEGACY_UNKNOWN"),
                "executable": self.is_trusted(str(manifest.get("id", ""))),
                "side_effects": list(manifest.get("side_effects", [])),
                "resource_budget": dict(manifest.get("resource_budget", {})),
            })
        return catalog

    def render_call_plan_prompt(self, context: dict[str, Any]) -> str:
        """仅生成调用规划提示词；此方法不执行插件，也不改变插件状态。"""
        import json

        variables = {
            "caller_id": context.get("caller_id", "unknown"),
            "caller_permission": context.get("caller_permission", 0),
            "task_id": context.get("task_id", ""),
            "project_id": context.get("project_id", ""),
            "book_id": context.get("book_id", ""),
            "task_description": context.get("task_description", ""),
            "plugin_manifests": json.dumps(self.get_manifests(), ensure_ascii=False),
            "available_assets": json.dumps(context.get("available_assets", []), ensure_ascii=False),
        }
        # 延迟 import：core 层不在模块加载时依赖 services 层
        from services.prompt_template_manager import prompt_manager

        return prompt_manager.render("plugin_call_planner", variables)

    def deterministic_call_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        """无模型时的安全规划器：缺少插件、权限或 schema 信息时拒绝调用。"""
        try:
            caller_permission = int(context.get("caller_permission", 0) or 0)
        except (TypeError, ValueError):
            return self._deny_plan(
                str(context.get("plugin_id", "") or ""),
                "调用者权限等级非法",
                "invalid_permission",
            )
        if caller_permission < 0 or caller_permission > 6:
            return self._deny_plan(
                str(context.get("plugin_id", "") or ""),
                "调用者权限等级越界",
                "invalid_permission",
            )
        plugin_id = context.get("plugin_id", "")
        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return {
                "decision": "DENY",
                "reason": "插件不存在或尚未加载",
                "calls": [],
                "rejected_calls": [{"plugin_id": plugin_id, "reason": "not_loaded"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
        # 旧内存规划清单没有 artifact_digest 时仍可生成计划，但永远不能交给 Runner 执行。
        if (manifest.get("artifact_digest") or "trust_state" in manifest) and not self.is_trusted(plugin_id):
            return self._deny_plan(plugin_id, "插件尚未通过信任校验", "untrusted_plugin")
        from services.plugin_manifest_policy import resolve_required_permission

        required = resolve_required_permission(manifest)
        try:
            if isinstance(required, str):
                required = int(required.removeprefix("L") or 0)
            required = int(required or 0)
        except (TypeError, ValueError):
            return self._deny_plan(plugin_id, "插件权限等级非法", "invalid_permission")
        if required < 0 or required > 6:
            return self._deny_plan(plugin_id, "插件权限等级越界", "invalid_permission")
        if required > caller_permission:
            return {
                "decision": "DENY",
                "reason": "调用者权限不足",
                "calls": [],
                "rejected_calls": [{"plugin_id": plugin_id, "reason": "permission_denied"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
        # 空对象是合法的 JSON Schema（表示无参数/空输出），只有缺少字段才拒绝。
        if (
            "input_schema" not in manifest
            or "output_schema" not in manifest
            or not isinstance(manifest["input_schema"], dict)
            or not isinstance(manifest["output_schema"], dict)
        ):
            return {
                "decision": "DENY",
                "reason": "插件 manifest 缺少 input_schema 或 output_schema",
                "calls": [],
                "rejected_calls": [{"plugin_id": plugin_id, "reason": "schema_missing"}],
                "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
            }
        return {
            "decision": "ALLOW",
            "reason": "仅生成待执行计划，尚未执行插件",
            "calls": [{
                "plugin_id": plugin_id,
                "plugin_version": manifest.get("version", ""),
                "permission_level": f"L{caller_permission}",
                "timeout_seconds": 60,
                "max_retries": 1,
                "side_effects": manifest.get("side_effects", []),
                "checkpoint": True,
                "result_status": "PLANNED",
            }],
            "rejected_calls": [],
            "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 60, "max_memory_mb": 0},
        }

    def validate_call_plan(self, context: dict[str, Any], plan: Any) -> dict[str, Any]:
        """校验模型提出的计划，禁止模型绕过插件、权限或 schema 闸门。

        该方法只返回可审计的计划，不执行插件。任何结构、权限或插件身份
        不一致都降级为 DENY，避免 API 将模型的任意 JSON 当成成功规划。
        """
        plugin_id = str(context.get("plugin_id", "") or "")
        try:
            caller_permission = int(context.get("caller_permission", 0) or 0)
        except (TypeError, ValueError):
            return self._deny_plan(plugin_id, "调用者权限等级非法", "invalid_permission")
        if caller_permission < 0 or caller_permission > 6:
            return self._deny_plan(plugin_id, "调用者权限等级越界", "invalid_permission")
        if not isinstance(plan, dict) or plan.get("decision") != "ALLOW":
            if isinstance(plan, dict) and plan.get("decision") == "DENY":
                return self._deny_plan(
                    plugin_id,
                    str(plan.get("reason") or "插件规划拒绝调用"),
                    "planner_denied",
                )
            return self._deny_plan(plugin_id, "插件规划结果未明确允许调用", "invalid_decision")

        manifest = self.loaded_plugins.get(plugin_id)
        if not manifest:
            return self._deny_plan(plugin_id, "插件不存在或尚未加载", "not_loaded")

        # 旧内存规划清单没有 artifact_digest 时仍可生成计划，但永远不能交给 Runner 执行。
        if (manifest.get("artifact_digest") or "trust_state" in manifest) and not self.is_trusted(plugin_id):
            return self._deny_plan(plugin_id, "插件尚未通过信任校验", "untrusted_plugin")
        from services.plugin_manifest_policy import resolve_required_permission

        required = resolve_required_permission(manifest)
        try:
            if isinstance(required, str):
                required = int(required.removeprefix("L") or 0)
            required = int(required or 0)
        except (TypeError, ValueError):
            return self._deny_plan(plugin_id, "插件权限等级非法", "invalid_permission")
        if required < 0 or required > 6:
            return self._deny_plan(plugin_id, "插件权限等级越界", "invalid_permission")
        if required > caller_permission:
            return self._deny_plan(plugin_id, "调用者权限不足", "permission_denied")
        if (
            "input_schema" not in manifest
            or "output_schema" not in manifest
            or not isinstance(manifest["input_schema"], dict)
            or not isinstance(manifest["output_schema"], dict)
        ):
            return self._deny_plan(plugin_id, "插件 manifest 缺少 input_schema 或 output_schema", "schema_missing")

        calls = plan.get("calls")
        if not isinstance(calls, list) or not calls:
            return self._deny_plan(plugin_id, "插件规划缺少待执行调用", "calls_missing")
        for call in calls:
            if not isinstance(call, dict) or call.get("plugin_id") != plugin_id:
                return self._deny_plan(plugin_id, "规划调用的插件身份不一致", "plugin_mismatch")
            try:
                declared_permission = call.get("permission_level", f"L{caller_permission}")
                if isinstance(declared_permission, str):
                    declared_permission = int(declared_permission.removeprefix("L") or 0)
                if int(declared_permission) > caller_permission or int(declared_permission) < required:
                    return self._deny_plan(plugin_id, "规划调用权限越界", "permission_escalation")
            except (TypeError, ValueError):
                return self._deny_plan(plugin_id, "规划调用权限等级非法", "invalid_call_permission")

        plan["decision"] = "ALLOW"
        plan["status"] = "PLANNED"
        return plan

    @staticmethod
    def _deny_plan(plugin_id: str, reason: str, rejection_reason: str) -> dict[str, Any]:
        return {
            "decision": "DENY",
            "reason": reason,
            "calls": [],
            "rejected_calls": [{"plugin_id": plugin_id, "reason": rejection_reason}],
            "resource_budget": {"estimated_tokens": 0, "estimated_seconds": 0, "max_memory_mb": 0},
        }

plugin_manager = PluginManager()
