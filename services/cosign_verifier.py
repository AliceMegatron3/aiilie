"""真实 Cosign/Sigstore 发布链路：产物签名校验服务。

提供 :class:`CosignVerifier`：

- ``verify_blob_signature``：调用真实 ``cosign verify-blob`` 校验产物，把 artifact
  sha256（与 build_service manifest 的 digest 对齐）绑定到签名结果，解析 Sigstore
  bundle 中的 Rekor/Fulcio provenance，并按信任策略校验证书 identity / issuer。
- ``verify_policy``：部署前强制验证（签名必须存在、signer 在 allowlist、
  digest 在 trust store），按 ``plugins.trust-policy.example.json`` 的结构执行。

设计要点
--------
- 不伪造成功：cosign 二进制不可用 / 调用失败 / digest 不匹配 / identity|issuer 不符
  -> 一律 ``ok=False`` 并给出 ``reason``。
- ``command_runner`` 可注入（默认 subprocess），便于纯逻辑单测。
- 证书解析为纯 Python DER（零第三方依赖）：从 bundle 的 Fulcio certificate
  尽力提取 subjectAlternativeName 与 OIDC issuer；解析失败标记 ``provenance_absent``。
- 不真实上传 Rekor：无 OIDC 身份时不签名到公共透明日志；生产发布建议使用
  ``cosign sign-blob --identity-token <token>``（OIDC/Fulcio）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# 签名材料命名约定（cosign sign-blob 常用输出）
SIG_SUFFIX = ".sig"
B64SIG_SUFFIX = ".b64sig"
BUNDLE_SUFFIXES = (".sig.bundle", ".bundle", ".sig.bundle.json")

# X.509 subjectAlternativeName OID
_SAN_OID = "2.5.29.17"
# Fulcio 证书中的 OIDC issuer 扩展 OID（sigstore 规范）
_FULCIO_ISSUER_OID = "1.3.6.1.4.1.57264.1.1"

__all__ = [
    "B64SIG_SUFFIX",
    "BUNDLE_SUFFIXES",
    "CommandResult",
    "CosignVerifier",
    "SIG_SUFFIX",
    "find_signature_materials",
    "is_signature_material",
]


@dataclass(frozen=True)
class CommandResult:
    """注入式 command_runner 的统一返回类型。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""


def _default_runner(argv: list[str]) -> CommandResult:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_signature_material(name: str, declared_names: set[str]) -> bool:
    """判断 ``name`` 是否为某个已声明产物的签名 companion 文件（.sig/.b64sig/.bundle）。

    用于产物校验时把签名材料视为 companion，而非独立产物。
    """
    for declared in declared_names:
        for suffix in (SIG_SUFFIX, B64SIG_SUFFIX) + BUNDLE_SUFFIXES:
            if name == declared + suffix:
                return True
    return False


def find_signature_materials(
    artifacts_dir: str | Path, name: str
) -> tuple[Path | None, Path | None]:
    """在产物目录中定位产物的签名材料，返回 ``(sig_path, bundle_path)``（均可为 None）。

    命名约定：``artifact.bin`` -> ``artifact.bin.sig`` / ``artifact.bin.b64sig``，
    bundle -> ``artifact.bin.sig.bundle`` / ``artifact.bin.bundle``。
    """
    base = Path(artifacts_dir) / name
    sig: Path | None = None
    for suffix in (SIG_SUFFIX, B64SIG_SUFFIX):
        candidate = Path(str(base) + suffix)
        if candidate.is_file():
            sig = candidate
            break
    bundle: Path | None = None
    for suffix in BUNDLE_SUFFIXES:
        candidate = Path(str(base) + suffix)
        if candidate.is_file():
            bundle = candidate
            break
    return sig, bundle


# ── 纯 Python DER / X.509 尽力解析（零第三方依赖）──────────────────────
def _der_read_tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int] | None:
    """读取一个 DER TLV，返回 ``(tag, value, next_offset)``；输入不合法返回 None。"""
    try:
        tag = data[offset]
        offset += 1
        length = data[offset]
        offset += 1
        if length & 0x80:
            num = length & 0x7F
            if num == 0 or num > 4:
                return None
            length = int.from_bytes(data[offset : offset + num], "big")
            offset += num
        value = data[offset : offset + length]
    except (IndexError, ValueError):
        return None
    return tag, value, offset + length


def _der_oid_to_string(value: bytes) -> str:
    """把 DER OBJECT IDENTIFIER 的 content 转成点分字符串。"""
    if not value:
        return ""
    first = value[0]
    parts = [str(first // 40), str(first % 40)]
    num = 0
    for byte in value[1:]:
        num = (num << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(num))
            num = 0
    return ".".join(parts)


def _parse_der_general_names(data: bytes) -> list[str]:
    """解析 subjectAlternativeName 的 DER 内容（GeneralNames 序列）。

    支持 RFC822Name（email）/ DNSName / URI；其他类型尽力跳过。
    """
    seq = _der_read_tlv(data, 0)
    if seq is None or seq[0] != 0x30:
        return []
    names: list[str] = []
    pos = 0
    body = seq[1]
    while pos < len(body):
        tlv = _der_read_tlv(body, pos)
        if tlv is None:
            break
        tag, value, nxt = tlv
        pos = nxt
        if tag == 0x81:  # RFC822Name [1] IA5String
            names.append(value.decode("utf-8", errors="replace"))
        elif tag == 0x82:  # DNSName [2] IA5String
            names.append(f"dns:{value.decode('utf-8', errors='replace')}")
        elif tag == 0x86:  # URI [6] IA5String
            names.append(f"uri:{value.decode('utf-8', errors='replace')}")
        # otherName/dirName 等类型不做深度解析，忽略
    return names


def _parse_der_string(data: bytes) -> str | None:
    """尽力把 DER 字符串（UTF8String/IA5String/PrintableString）或裸 UTF-8 解码为文本。"""
    tlv = _der_read_tlv(data, 0)
    if tlv is not None and tlv[0] in (0x0C, 0x16, 0x13, 0x14, 0x1E):
        return tlv[1].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except Exception:
        return None


def _parse_der_certificate(raw_b64: str | None) -> dict[str, Any] | None:
    """尽力解析 X.509 证书（纯 Python DER），返回 ``{subject, issuer, sans}``。

    - ``sans``：subjectAlternativeName 中的 email/dns/uri 列表
    - ``issuer``：Fulcio OIDC issuer 扩展（1.3.6.1.4.1.57264.1.1）值，缺省为颁发者 DN
    解析失败返回 None（调用方标记 ``provenance_absent``）。
    """
    if not raw_b64:
        return None
    try:
        der = base64.b64decode(raw_b64)
    except Exception:
        return None
    # Certificate ::= SEQUENCE { tbsCertificate, signatureAlgorithm, signatureValue }
    outer = _der_read_tlv(der, 0)
    if outer is None or outer[0] != 0x30:
        return None
    tbs = _der_read_tlv(outer[1], 0)
    if tbs is None or tbs[0] != 0x30:
        return None
    tbs_body = tbs[1]
    # 遍历 tbsCertificate 子项，定位 extensions [3]（tag 0xA3，EXPLICIT）
    extensions_value: bytes | None = None
    pos = 0
    while pos < len(tbs_body):
        tlv = _der_read_tlv(tbs_body, pos)
        if tlv is None:
            break
        tag, value, nxt = tlv
        if tag == 0xA3:
            inner = _der_read_tlv(value, 0)
            extensions_value = inner[1] if inner else value
            break
        pos = nxt

    sans: list[str] = []
    fulcio_issuer = ""
    if extensions_value:
        pos = 0
        while pos < len(extensions_value):
            ext_tlv = _der_read_tlv(extensions_value, pos)
            if ext_tlv is None:
                break
            ext_tag, ext_body, nxt = ext_tlv
            pos = nxt
            if ext_tag != 0x30:
                continue
            # Extension ::= SEQUENCE { extnID OID, [critical BOOLEAN], extnValue OCTET STRING }
            oid = None
            ext_value = None
            p = 0
            while p < len(ext_body):
                tlv = _der_read_tlv(ext_body, p)
                if tlv is None:
                    break
                tag, value, q = tlv
                p = q
                if tag == 0x06:
                    oid = _der_oid_to_string(value)
                elif tag == 0x04:
                    ext_value = value
            if oid is None or ext_value is None:
                continue
            if oid == _SAN_OID:
                sans = _parse_der_general_names(ext_value)
            elif oid == _FULCIO_ISSUER_OID:
                parsed = _parse_der_string(ext_value)
                if parsed:
                    fulcio_issuer = parsed

    if not (sans or fulcio_issuer):
        return None
    return {"subject": "x509", "issuer": fulcio_issuer, "sans": sans}


class CosignVerifier:
    """Cosign/Sigstore 产物签名校验器。

    :param cosign_binary: cosign 可执行文件（默认 ``cosign``，走 PATH）。
    :param trust_policy: 可选默认信任策略（dict，结构与
        ``plugins.trust-policy.example.json`` 一致）。
    :param command_runner: 可注入的命令执行器，签名为
        ``Callable[[list[str]], CommandResult]``；默认使用 subprocess。
    """

    def __init__(
        self,
        cosign_binary: str = "cosign",
        trust_policy: dict[str, Any] | None = None,
        command_runner: Callable[[list[str]], CommandResult] | None = None,
    ) -> None:
        self.cosign_binary = cosign_binary
        self.trust_policy = dict(trust_policy or {})
        self._command_runner = command_runner or _default_runner

    def is_available(self) -> bool:
        """注入自定义 runner 时视为工具可用（由 runner 行为决定）；默认探测 PATH。"""
        if self._command_runner is not _default_runner:
            return True
        return shutil.which(self.cosign_binary) is not None

    # ── 签名校验 ───────────────────────────────────────────────────────
    def verify_blob_signature(
        self,
        artifact_path: str | Path,
        sig_path: str | Path | None = None,
        bundle_path: str | Path | None = None,
        key_path: str | Path | None = None,
        cert_identity: str | None = None,
        cert_issuer: str | None = None,
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        """校验 blob 签名并返回 binding / provenance 报告。

        调用真实 ``cosign verify-blob``：:

            cosign verify-blob --signature <sig> [--bundle <bundle>] [--key <key>]
                [--certificate-identity <identity>] [--certificate-oidc-issuer <issuer>]
                <artifact>

        :param expected_digest: manifest 中该产物的 sha256（digest 绑定）；提供且与
            实际文件 digest 不一致时 ``ok=False``。
        :return: dict，含 ``ok`` / ``reason`` / ``tool_available`` / ``artifact_digest`` /
            ``digest_bound`` / ``identity_matches`` / ``issuer_matches`` /
            ``provenance`` / ``raw``。cosign 不可用或失败时 ``ok=False``，绝不伪造成功。
        """
        artifact = Path(artifact_path).resolve()
        report: dict[str, Any] = {
            "ok": False,
            "reason": "",
            "tool_available": True,
            "artifact_path": str(artifact),
            "artifact_digest": _sha256(artifact),
            "expected_digest": expected_digest,
            "digest_bound": False,
            "expected_identity": cert_identity,
            "identity": None,
            "identity_matches": None,
            "expected_issuer": cert_issuer,
            "issuer": None,
            "issuer_matches": None,
            "provenance": {
                "rekor_entry": None,
                "fulcio_cert_subject": None,
                "fulcio_issuer": None,
                "fulcio_cert_sans": [],
                "provenance_absent": bundle_path is None,
            },
            "raw": {"command": [], "stdout": "", "stderr": ""},
        }
        if sig_path is None:
            report["reason"] = "SIGNATURE_MISSING"
            return report

        argv: list[str] = [
            self.cosign_binary,
            "verify-blob",
            "--signature",
            str(Path(sig_path).resolve()),
        ]
        if bundle_path:
            argv += ["--bundle", str(Path(bundle_path).resolve())]
        if key_path:
            argv += ["--key", str(Path(key_path).resolve())]
        if cert_identity:
            argv += ["--certificate-identity", cert_identity]
        if cert_issuer:
            argv += ["--certificate-oidc-issuer", cert_issuer]
        argv.append(str(artifact))
        report["raw"]["command"] = argv

        try:
            result = self._command_runner(argv)
        except FileNotFoundError as exc:
            report["tool_available"] = False
            report["reason"] = f"COSIGN_UNAVAILABLE: {exc}"
            return report
        except Exception as exc:  # noqa: BLE001 - 统一视为工具不可用
            report["tool_available"] = False
            report["reason"] = f"COSIGN_UNAVAILABLE: {type(exc).__name__}: {exc}"
            return report
        report["raw"]["stdout"] = result.stdout
        report["raw"]["stderr"] = result.stderr

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            report["reason"] = (
                f"COSIGN_VERIFY_FAILED (rc={result.returncode}): {detail or 'unknown'}"
            )
            return report

        # digest 绑定：expected_digest（manifest digest）必须与实际文件一致
        if expected_digest is not None and expected_digest != report["artifact_digest"]:
            report["reason"] = (
                f"DIGEST_MISMATCH: expected {expected_digest}, got {report['artifact_digest']}"
            )
            return report
        report["digest_bound"] = True

        # Rekor/Fulcio provenance：尽力解析 bundle
        if bundle_path:
            report["provenance"] = self._parse_bundle_provenance(bundle_path)
        provenance = report["provenance"]
        cert_sans = provenance.get("fulcio_cert_sans") or []
        has_cert = bool(cert_sans or provenance.get("fulcio_issuer"))

        # identity / issuer 校验：trust_policy 声明 expected_identity/expected_issuer
        # 时，校验 bundle 中 Fulcio 证书的 SAN 与 OIDC issuer（fail-closed）。
        if cert_identity and has_cert:
            report["identity_matches"] = cert_identity in cert_sans
            if not report["identity_matches"]:
                report["reason"] = f"IDENTITY_MISMATCH: {cert_identity} not in {cert_sans}"
                return report
        elif cert_identity:
            report["identity_matches"] = False
        if cert_issuer and has_cert:
            report["issuer_matches"] = bool(
                provenance.get("fulcio_issuer") and cert_issuer in provenance["fulcio_issuer"]
            )
            if not report["issuer_matches"]:
                report["reason"] = (
                    f"ISSUER_MISMATCH: {cert_issuer} not in {provenance.get('fulcio_issuer')}"
                )
                return report
        elif cert_issuer:
            report["issuer_matches"] = False
        if (cert_identity or cert_issuer) and not has_cert:
            report["reason"] = (
                "CERTIFICATE_ABSENT: expected identity/issuer but bundle has no certificate"
            )
            return report

        report["identity"] = next(
            (s for s in cert_sans if not s.startswith(("dns:", "uri:"))), cert_identity
        )
        report["issuer"] = provenance.get("fulcio_issuer") or cert_issuer
        report["ok"] = True
        report["reason"] = "VERIFIED"
        return report

    def verify_blob_identity(
        self, artifact_path: str | Path, cert_identity: str
    ) -> dict[str, Any]:
        """Batch 6：把「插件受信时的 cosign verify-blob + certificate-identity」命令
        收敛为本服务的唯一构造处——PluginManager 不再自行拼 Cosign 命令。

        语义与 plugin_manager._cosign_verify 对齐：以产物目录为 artifact，仅按
        `--certificate-identity` 校验。cosign 不可用/失败一律 ok=False（不伪造成功）。
        """
        artifact = Path(artifact_path).resolve()
        report: dict[str, Any] = {
            "ok": False,
            "tool_available": True,
            "reason": "",
            "status": "ERROR",
            "artifact_path": str(artifact),
        }
        argv: list[str] = [
            self.cosign_binary,
            "verify-blob",
            str(artifact),
            "--certificate-identity",
            cert_identity,
        ]
        try:
            result = self._command_runner(argv)
        except (OSError, subprocess.SubprocessError) as exc:
            report["tool_available"] = False
            report["reason"] = f"COSIGN_UNAVAILABLE: {type(exc).__name__}"
            report["status"] = "COSIGN_UNAVAILABLE"
            return report
        if result.returncode != 0:
            report["reason"] = f"COSIGN_VERIFY_FAILED (rc={result.returncode})"
            report["status"] = "INVALID"
            return report
        report["ok"] = True
        report["reason"] = "VERIFIED"
        report["status"] = "VERIFIED"
        return report

    # ── Provenance / 信任策略 ──────────────────────────────────────────
    def _parse_bundle_provenance(self, bundle_path: str | Path) -> dict[str, Any]:
        """尽力解析 Sigstore bundle，返回 provenance dict。

        - ``rekor_entry``：tlogEntries[0]（或 rekorBundle）的 log_index / integrated_time / kind
        - ``fulcio_cert_subject`` / ``fulcio_issuer`` / ``fulcio_cert_sans``：Fulcio 证书解析
        - ``provenance_absent``：bundle 无 rekor 条目且无证书时为 True
        """
        result: dict[str, Any] = {
            "rekor_entry": None,
            "fulcio_cert_subject": None,
            "fulcio_issuer": None,
            "fulcio_cert_sans": [],
            "provenance_absent": True,
        }
        try:
            data = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result["provenance_absent_reason"] = f"BUNDLE_UNREADABLE: {exc}"
            return result
        material = data.get("verificationMaterial") or {}
        tlog = material.get("tlogEntries") or []
        if tlog:
            entry = tlog[0]
            result["rekor_entry"] = {
                "log_index": str(entry.get("logIndex", "")),
                "integrated_time": str(entry.get("integratedTime", "")),
                "kind": (entry.get("kindVersion") or {}).get("kind", ""),
            }
        else:
            rekor = material.get("rekorBundle")
            if isinstance(rekor, dict):
                result["rekor_entry"] = {
                    "log_index": str(rekor.get("logIndex", "")),
                    "integrated_time": str(rekor.get("integratedTime", "")),
                    "kind": "rekorBundle",
                }
        cert = material.get("certificate") or {}
        cert_info = _parse_der_certificate(cert.get("rawBytes"))
        if cert_info:
            result["fulcio_cert_subject"] = cert_info.get("subject")
            result["fulcio_issuer"] = cert_info.get("issuer")
            result["fulcio_cert_sans"] = cert_info.get("sans", [])
        result["provenance_absent"] = not (
            result["rekor_entry"] or result["fulcio_cert_subject"]
        )
        return result

    def verify_policy(
        self,
        artifact_digest: str,
        sig_result: dict[str, Any] | None,
        trust_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """部署前强制验证，按 ``plugins.trust-policy.example.json`` 结构执行。

        - 签名必须存在：``require_signing`` / ``require_cosign`` 时 sig_result 必须 ``ok``。
        - signer 在 allowlist：``allowed_signers`` 非空时签名 identity 必须命中。
        - digest 在 trust store：``trusted_digests`` 非空时 artifact_digest 必须命中。

        :return: ``{ok, errors, require_signing, signer, signer_allowed, digest_in_store}``
        """
        policy = dict(trust_policy or self.trust_policy)
        sig = sig_result or {}
        errors: list[str] = []
        require_signing = bool(
            policy.get("require_signing") or policy.get("require_cosign") or policy.get("require_signer")
        )
        if require_signing and not sig.get("ok"):
            errors.append("SIGNATURE_REQUIRED: 产物缺少有效签名")
        signer = str(sig.get("identity") or sig.get("expected_identity") or "")
        allowed_signers = [str(x) for x in (policy.get("allowed_signers") or [])]
        if allowed_signers and signer not in allowed_signers:
            errors.append(f"SIGNER_NOT_ALLOWED: {signer or '<none>'}")
        trusted_digests = [str(x) for x in (policy.get("trusted_digests") or [])]
        digest = str(artifact_digest)
        if trusted_digests and digest not in trusted_digests:
            errors.append(f"DIGEST_NOT_IN_TRUST_STORE: {digest}")
        return {
            "ok": not errors,
            "errors": errors,
            "require_signing": require_signing,
            "signer": signer,
            "signer_allowed": (not allowed_signers) or signer in allowed_signers,
            "digest_in_store": (not trusted_digests) or digest in trusted_digests,
        }

    # ── 生产签名（OIDC keyless / Fulcio） ──────────────────────────────
    def sign_blob_keyless(
        self,
        artifact_path: str | Path,
        *,
        identity_token: str,
        oidc_issuer: str | None = None,
        output_dir: str | Path | None = None,
        with_bundle: bool = True,
    ) -> dict[str, Any]:
        """用 OIDC identity token 做 keyless 签名（生产发布用，替代 CI 的临时密钥演示）。

        在 Sigstore/Fulcio 下用工作负载身份（GitHub Actions 等）签名 blob：:

            cosign sign-blob [--oidc-issuer <issuer>] --identity-token <token>
                --output-signature <artifact>.sig --output-certificate <artifact>.crt
                [--bundle <artifact>.bundle] <artifact>

        - 签名材料落到 ``<artifact>.sig`` 与 ``<artifact>.sig.bundle``（与
          ``find_signature_materials`` / ``verify_blob_signature`` 的命名约定一致）。
        - 所有 Cosign 命令构造都收敛在此服务；cosign 不可用/命令失败一律 ``ok=False``，
          绝不伪造“已签名”。

        :return: ``{ok, reason, sig_path, bundle_path, raw}``。
        """
        artifact = Path(artifact_path).resolve()
        out = Path(output_dir).resolve() if output_dir else artifact.parent
        sig_path = out / (artifact.name + SIG_SUFFIX)
        bundle_path = out / (artifact.name + ".sig.bundle")
        report: dict[str, Any] = {
            "ok": False,
            "reason": "",
            "tool_available": True,
            "artifact_path": str(artifact),
            "sig_path": str(sig_path),
            "bundle_path": str(bundle_path) if with_bundle else None,
            "raw": {"command": [], "stdout": "", "stderr": ""},
        }
        if not identity_token:
            report["reason"] = "IDENTITY_TOKEN_MISSING"
            return report

        argv: list[str] = [
            self.cosign_binary,
            "sign-blob",
            "--identity-token",
            identity_token,
            "--output-signature",
            str(sig_path),
            "--output-certificate",
            str(out / (artifact.name + ".crt")),
        ]
        if oidc_issuer:
            argv += ["--oidc-issuer", oidc_issuer]
        if with_bundle:
            argv += ["--bundle", str(bundle_path)]
        argv.append(str(artifact))
        report["raw"]["command"] = argv

        try:
            result = self._command_runner(argv)
        except FileNotFoundError as exc:
            report["tool_available"] = False
            report["reason"] = f"COSIGN_UNAVAILABLE: {exc}"
            return report
        except Exception as exc:  # noqa: BLE001
            report["tool_available"] = False
            report["reason"] = f"COSIGN_UNAVAILABLE: {type(exc).__name__}: {exc}"
            return report
        report["raw"]["stdout"] = result.stdout
        report["raw"]["stderr"] = result.stderr

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            report["reason"] = (
                f"COSIGN_SIGN_FAILED (rc={result.returncode}): {detail or 'unknown'}"
            )
            return report
        # 产物落盘确认：签名文件存在才算成功
        if not sig_path.is_file():
            report["reason"] = "SIGNATURE_NOT_CREATED"
            return report
        report["ok"] = True
        report["reason"] = "SIGNED"
        return report
