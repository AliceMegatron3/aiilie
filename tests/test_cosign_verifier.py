"""Cosign/Sigstore 真实发布链路测试。

覆盖：
- 纯逻辑（注入 fake command_runner）：
  - verify_blob_signature 成功路径：digest 绑定 + Rekor/Fulcio provenance + identity/issuer
  - cosign 不可用 / 校验失败 -> ok=False（不伪造成功）
  - digest 不匹配（篡改产物）-> ok=False
  - bundle 无证书 -> identity 校验失败 / provenance_absent 记录
  - verify_policy：signer 不在 allowlist / digest 不在 trust store / require_signing 无签名 -> 拒绝
- build_service 集成：verify_artifacts 传 signer_allowlist + verifier 时校验签名，无签名拒绝
- 真实 cosign 工具用例：临时 RSA 密钥对 + sign-blob + verify-blob（不上传 Rekor）
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from services.build_service import verify_artifacts
from services.cosign_verifier import CosignVerifier, CommandResult

# 预生成的 Fulcio 样式测试证书（X.509 DER，base64）：
#   - subjectAlternativeName = RFC822Name alice@example.com
#   - 扩展 1.3.6.1.4.1.57264.1.1 = "https://accounts.google.com"（OIDC issuer）
# 使用固定常量，避免测试运行时依赖 cryptography（CI 未安装）。
_FULCIO_CERT_DER_B64 = (
    "MIIDDDCCAfSgAwIBAgIUd/EXMkkqnXNhnMe6z19rIYZsURswDQYJKoZIhvcNAQELBQAwFzEV"
    "MBMGA1UECgwMc2lnc3RvcmUuZGV2MB4XDTI2MDgxNjIxMjkyNloXDTI2MDgxODIxMjkyNlow"
    "HDEaMBgGA1UEAwwRYWxpY2VAZXhhbXBsZS5jb20wggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAw"
    "ggEKAoIBAQC9Qo574ZpH2EDV1WoSWcSOGRgCGku2rbBKfapm8Rh0HVpVaqgiIwXJmdgh20v3"
    "rMwIlQeLfz4NvRMEWe3DKx3uZQdfxV3UgcRi/pvBcqQLpD2UrRdvmmKTZUH3vNq+KHDL4lxN"
    "T8rX54LkrnVaOyIJRWyljLE4q3xNXuQXj7VfIC8345vRm7OrIxGmBsfpMEQX2eBBF4YURIEq"
    "wBRD4Q5vyXCy8Nx86EXf4vQbk3ZSDTYFrOxcGyv+uQD6a8x/Pa6RjAqDo8Egzrjj3z0Ird3y"
    "LpwHnDEsipdKSRgHTcEfsVKFzYuFlEu3Lp5zrZEYM5IAXgwH7vuZttMqdZsCRs+5AgMBAAGj"
    "SzBJMBwGA1UdEQQVMBOBEWFsaWNlQGV4YW1wbGUuY29tMCkGCisGAQQBg78wAQEEG2h0dHBz"
    "Oi8vYWNjb3VudHMuZ29vZ2xlLmNvbTANBgkqhkiG9w0BAQsFAAOCAQEAIo/YpRiVGu5zwkGQ"
    "8NBv0DLbSEHH+J66VwIeLJGUwo1SKRItAGqUcyWWZu6w7NPKYeEfguIqe1RBKOy+jUZ9QBI+"
    "52PNsyhKeZi8FhSXIzkT4dAOUSjCkWk1En8SPZKLhnjgWkPKz5w6GgbEc8GU69BNM0T0rYnFw"
    "OhvtZnBCqJDtV9sY7K52Up61toIcU8xTyEsQk8jkBT4gbxxBMr/VSrltH2GuKq1bArDj/Kql0"
    "KvzF+cmjPjl71jgQq8/GINrphouBo9XiXSlucjLNrnP0PNV/einVy6RzEpokeRXZFy9t+OPQS"
    "Vhau2h6kYlopF/WyeqWKJc/mmUx2HMQX3ig=="
)


class _FakeRunner:
    """注入的 command_runner：记录 argv，可配置返回结果或抛异常。"""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        fail: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(list(argv))
        if self.fail is not None:
            raise self.fail
        return CommandResult(self.returncode, self.stdout, self.stderr)


def _fulcio_bundle(
    tmp_path: Path,
    with_cert: bool = True,
    with_tlog: bool = True,
) -> Path:
    """构造一个包含 Fulcio 证书（预生成 DER）与 Rekor tlog 的 Sigstore bundle。"""
    material: dict[str, object] = {}
    if with_tlog:
        material["tlogEntries"] = [
            {
                "logIndex": "42",
                "integratedTime": "1690000000",
                "kindVersion": {"kind": "hashedrekord", "version": "0.0.1"},
            }
        ]
    if with_cert:
        material["certificate"] = {"rawBytes": _FULCIO_CERT_DER_B64}
    bundle = {
        "mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3",
        "verificationMaterial": material,
        "messageSignature": {
            "messageDigest": {"algorithm": "SHA2_256", "digest": "AA=="},
            "signature": "AA==",
        },
    }
    path = tmp_path / "artifact.bundle"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def _artifact(tmp_path: Path, payload: bytes = b"hello artifact") -> tuple[Path, Path]:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(payload)
    sig = tmp_path / "artifact.bin.sig"
    sig.write_text("bm90YXNpZ25hdHVyZQ==")
    return artifact, sig


# ── 纯逻辑：verify_blob_signature ─────────────────────────────────────
def test_verify_blob_signature_success_binds_digest_and_provenance(tmp_path):
    artifact, sig = _artifact(tmp_path)
    bundle = _fulcio_bundle(tmp_path)
    runner = _FakeRunner(returncode=0)
    verifier = CosignVerifier(
        cosign_binary="cosign",
        trust_policy={"allowed_signers": ["alice@example.com"]},
        command_runner=runner,
    )
    result = verifier.verify_blob_signature(
        artifact,
        sig,
        bundle_path=bundle,
        cert_identity="alice@example.com",
        cert_issuer="https://accounts.google.com",
        expected_digest=hashlib.sha256(b"hello artifact").hexdigest(),
    )
    assert result["ok"] is True
    assert result["tool_available"] is True
    assert result["artifact_digest"] == hashlib.sha256(b"hello artifact").hexdigest()
    assert result["digest_bound"] is True
    assert result["identity_matches"] is True
    assert result["issuer_matches"] is True
    assert result["identity"] == "alice@example.com"

    prov = result["provenance"]
    assert prov["rekor_entry"]["log_index"] == "42"
    assert "alice@example.com" in prov["fulcio_cert_sans"]
    assert prov["fulcio_issuer"] == "https://accounts.google.com"
    assert prov["provenance_absent"] is False

    # cosign argv 含 --signature/--bundle/--certificate-identity/--certificate-oidc-issuer
    argv = runner.calls[0]
    assert argv[0] == "cosign"
    assert "--signature" in argv
    assert "--bundle" in argv
    assert "--certificate-identity" in argv
    assert "--certificate-oidc-issuer" in argv

    # 信任策略校验通过
    policy = verifier.verify_policy(
        result["artifact_digest"], result, {"allowed_signers": ["alice@example.com"]}
    )
    assert policy["ok"] is True
    assert policy["signer_allowed"] is True


def test_verify_blob_signature_cosign_unavailable_is_not_forged(tmp_path):
    artifact, sig = _artifact(tmp_path)
    runner = _FakeRunner(fail=FileNotFoundError("cosign not found"))
    verifier = CosignVerifier(command_runner=runner)
    result = verifier.verify_blob_signature(artifact, sig)
    assert result["ok"] is False
    assert result["tool_available"] is False
    assert "COSIGN_UNAVAILABLE" in result["reason"]


def test_verify_blob_identity_single_command_and_fail_closed(tmp_path):
    """Batch 6：verify_blob_identity 是插件受信的 cosign 命令唯一构造处——
    成功 → VERIFIED；签名失败/工具不可用 → ok=False，绝不伪造。"""
    artifact = tmp_path / "plugin"
    artifact.mkdir()
    (artifact / "README.md").write_text("x", encoding="utf-8")

    ok_runner = _FakeRunner(returncode=0)
    v_ok = CosignVerifier(cosign_binary="cosign", command_runner=ok_runner)
    ok = v_ok.verify_blob_identity(artifact, "alice@example.com")
    assert ok["ok"] is True and ok["status"] == "VERIFIED"
    assert any("verify-blob" in a for a in ok_runner.calls[0])
    assert any("--certificate-identity" in a for a in ok_runner.calls[0])
    assert "alice@example.com" in ok_runner.calls[0]

    bad_runner = _FakeRunner(returncode=1, stderr="invalid signature")
    bad = CosignVerifier(cosign_binary="cosign", command_runner=bad_runner).verify_blob_identity(artifact, "alice@example.com")
    assert bad["ok"] is False and bad["status"] == "INVALID"

    # 工具不可用 → fail-closed（runner 抛 FileNotFoundError=cosign 不存在）
    no_tool = CosignVerifier(
        cosign_binary="cosign", command_runner=_FakeRunner(fail=FileNotFoundError("cosign not found"))
    ).verify_blob_identity(artifact, "x")
    assert no_tool["ok"] is False and no_tool["status"] == "COSIGN_UNAVAILABLE"


def test_verify_blob_signature_cosign_rejects_signature(tmp_path):
    artifact, sig = _artifact(tmp_path)
    runner = _FakeRunner(returncode=1, stderr="invalid signature")
    verifier = CosignVerifier(command_runner=runner)
    result = verifier.verify_blob_signature(artifact, sig)
    assert result["ok"] is False
    assert result["tool_available"] is True
    assert "COSIGN_VERIFY_FAILED" in result["reason"]


def test_verify_blob_signature_digest_binding_rejects_tampered(tmp_path):
    artifact, sig = _artifact(tmp_path)
    bundle = _fulcio_bundle(tmp_path)
    runner = _FakeRunner(returncode=0)
    verifier = CosignVerifier(command_runner=runner)
    # 篡改产物：期望 digest 与真实文件 digest 不一致 -> 拒绝（digest 绑定）
    expected = hashlib.sha256(b"tampered content").hexdigest()
    result = verifier.verify_blob_signature(artifact, sig, bundle_path=bundle, expected_digest=expected)
    assert result["ok"] is False
    assert "DIGEST_MISMATCH" in result["reason"]
    assert result["digest_bound"] is False


def test_verify_blob_signature_identity_mismatch_fails(tmp_path):
    artifact, sig = _artifact(tmp_path)
    bundle = _fulcio_bundle(tmp_path)  # SAN=alice@example.com
    runner = _FakeRunner(returncode=0)
    verifier = CosignVerifier(command_runner=runner)
    result = verifier.verify_blob_signature(
        artifact, sig, bundle_path=bundle, cert_identity="mallory@example.com"
    )
    assert result["ok"] is False
    assert result["identity_matches"] is False
    assert "IDENTITY_MISMATCH" in result["reason"]


def test_verify_blob_signature_bundle_without_cert_fails_closed(tmp_path):
    artifact, sig = _artifact(tmp_path)
    # 有 tlog 但无证书
    bundle = _fulcio_bundle(tmp_path, with_cert=False, with_tlog=True)
    runner = _FakeRunner(returncode=0)
    verifier = CosignVerifier(command_runner=runner)
    result = verifier.verify_blob_signature(
        artifact, sig, bundle_path=bundle, cert_identity="alice@example.com"
    )
    prov = result["provenance"]
    assert prov["rekor_entry"]["log_index"] == "42"
    # 期望 identity 但无证书 -> fail-closed：identity 校验失败 / provenance 缺失记录
    assert result["identity_matches"] is False
    assert result["ok"] is False
    assert "CERTIFICATE_ABSENT" in result["reason"] or prov.get("provenance_absent") is True


def test_verify_blob_signature_signature_missing(tmp_path):
    artifact, _sig = _artifact(tmp_path)
    verifier = CosignVerifier(command_runner=_FakeRunner(returncode=0))
    result = verifier.verify_blob_signature(artifact, sig_path=None)
    assert result["ok"] is False
    assert "SIGNATURE_MISSING" in result["reason"]


# ── 纯逻辑：verify_policy ──────────────────────────────────────────────
def test_verify_policy_rejects_non_allowed_signer_and_digest(tmp_path):
    verifier = CosignVerifier(trust_policy={})
    sig_ok = {"ok": True, "identity": "alice@example.com", "artifact_digest": "abc123"}

    # signer 不在 allowlist
    r1 = verifier.verify_policy("abc123", sig_ok, {"allowed_signers": ["bob@example.com"]})
    assert r1["ok"] is False
    assert any("SIGNER_NOT_ALLOWED" in e for e in r1["errors"])

    # digest 不在 trust store
    r2 = verifier.verify_policy("abc123", sig_ok, {"trusted_digests": ["def456"]})
    assert r2["ok"] is False
    assert any("DIGEST_NOT_IN_TRUST_STORE" in e for e in r2["errors"])

    # require_signing 但签名无效
    r3 = verifier.verify_policy("abc123", {"ok": False, "identity": ""}, {"require_signing": True})
    assert r3["ok"] is False
    assert any("SIGNATURE_REQUIRED" in e for e in r3["errors"])

    # 全部满足 -> 通过
    r4 = verifier.verify_policy(
        "abc123",
        sig_ok,
        {"allowed_signers": ["alice@example.com"], "trusted_digests": ["abc123"]},
    )
    assert r4["ok"] is True
    assert r4["signer_allowed"] is True
    assert r4["digest_in_store"] is True


# ── build_service 集成 ────────────────────────────────────────────────
def test_verify_artifacts_integrates_signature_check(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    artifact = out / "artifact.bin"
    artifact.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()
    (out / "artifact.bin.sig").write_text("bm90YXNpZ25hdHVyZQ==")
    bundle = _fulcio_bundle(tmp_path)
    (out / "artifact.bin.bundle").write_text(bundle.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "status": "COMPLETED",
        "dependency_digest": "dep",
        "artifacts_dir": str(out),
        "provenance": {"builder_version": "1.0.0"},
        "artifacts": [{"name": "artifact.bin", "size": len(b"payload"), "sha256": digest}],
    }
    runner = _FakeRunner(returncode=0)
    verifier = CosignVerifier(
        trust_policy={"require_signing": True, "expected_identity": "alice@example.com"},
        command_runner=runner,
    )
    policy = {
        "require_signing": True,
        "allowed_signers": ["alice@example.com"],
        "expected_identity": "alice@example.com",
    }
    report = verify_artifacts(
        manifest,
        signer_allowlist={"artifact.bin": {digest}},
        verifier=verifier,
        trust_policy=policy,
    )
    assert report["ok"] is True, report["errors"]
    assert report["signature_checked"] is True

    # 移除签名材料 -> 拒绝导入主应用
    (out / "artifact.bin.sig").unlink()
    (out / "artifact.bin.bundle").unlink()
    bad = verify_artifacts(
        manifest,
        signer_allowlist={"artifact.bin": {digest}},
        verifier=verifier,
        trust_policy=policy,
    )
    assert bad["ok"] is False
    assert any("SIGNATURE_MATERIAL_MISSING" in e for e in bad["errors"])

    # 无签名要求（verifier=None）时保持原行为（签名材料不在时不要求）
    plain = verify_artifacts(manifest, signer_allowlist={"artifact.bin": {digest}})
    assert plain["ok"] is True
    assert plain["signature_checked"] is False


# ── 真实 cosign 工具用例（skipif 保护）────────────────────────────────
_COSIGN_AVAILABLE = shutil.which("cosign") is not None


@pytest.mark.skipif(not _COSIGN_AVAILABLE, reason="cosign 未安装，跳过真实工具用例")
def test_real_cosign_keypair_sign_and_verify(tmp_path):
    """真实 cosign：临时 RSA 密钥对 + sign-blob + verify-blob（不上传 Rekor）。"""
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"real cosign signed payload")
    keypair = tmp_path / "demo-key"
    env = dict(os.environ)
    env["COSIGN_PASSWORD"] = ""

    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=120, env=env, check=False
        )

    gen = run(["cosign", "generate-key-pair", "--output-key-pair", str(keypair)])
    assert gen.returncode == 0, gen.stderr

    sig_path = tmp_path / "artifact.bin.sig"
    sign = run(
        [
            "cosign", "sign-blob", "--tlog-upload=false",
            "--key", f"{keypair}.key", "--output-signature", str(sig_path), str(artifact),
        ]
    )
    assert sign.returncode == 0, sign.stderr

    verify = run(
        [
            "cosign", "verify-blob", "--key", f"{keypair}.pub",
            "--signature", str(sig_path), str(artifact),
        ]
    )
    assert verify.returncode == 0, verify.stderr

    # 走完整 CosignVerifier 链路（key 模式，无证书）
    verifier = CosignVerifier(cosign_binary="cosign", trust_policy={})
    result = verifier.verify_blob_signature(artifact, sig_path, key_path=f"{keypair}.pub")
    assert result["ok"] is True, result
    assert result["tool_available"] is True
    assert result["artifact_digest"] == hashlib.sha256(b"real cosign signed payload").hexdigest()


@pytest.mark.skipif(not _COSIGN_AVAILABLE, reason="cosign 未安装，跳过真实工具用例")
def test_real_cosign_rejects_tampered_blob(tmp_path):
    """真实 cosign：篡改产物后 verify-blob 应失败（digest 绑定 + 真实签名校验）。"""
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"original")
    keypair = tmp_path / "demo-key"
    env = dict(os.environ)
    env["COSIGN_PASSWORD"] = ""

    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=120, env=env, check=False
        )

    gen = run(["cosign", "generate-key-pair", "--output-key-pair", str(keypair)])
    assert gen.returncode == 0, gen.stderr

    sig_path = tmp_path / "artifact.bin.sig"
    sign = run(
        [
            "cosign", "sign-blob", "--tlog-upload=false",
            "--key", f"{keypair}.key", "--output-signature", str(sig_path), str(artifact),
        ]
    )
    assert sign.returncode == 0, sign.stderr

    artifact.write_bytes(b"tampered")
    verify = run(
        [
            "cosign", "verify-blob", "--key", f"{keypair}.pub",
            "--signature", str(sig_path), str(artifact),
        ]
    )
    assert verify.returncode != 0  # 篡改后真实签名校验失败

    verifier = CosignVerifier(cosign_binary="cosign", trust_policy={})
    result = verifier.verify_blob_signature(
        artifact, sig_path, key_path=f"{keypair}.pub",
        expected_digest=hashlib.sha256(b"original").hexdigest(),
    )
    assert result["ok"] is False  # digest 绑定也应失败
