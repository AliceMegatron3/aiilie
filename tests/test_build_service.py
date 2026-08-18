"""独立构建服务（build_service）测试：隔离 / 资源限制 / 依赖 digest / manifest / 导入前验证。

覆盖：
- 成功路径：产物 manifest 生成、digest 正确、exit_code=0
- 失败路径：命令非零退出 -> FAILED、非零、error.txt 存在
- 超时 / 磁盘配额 / 依赖 digest 变更 -> 失败
- 隔离：构建写 out 不写 src，原始源码目录不被污染
- verify_artifacts：篡改产物 / 未声明产物 / 签名 allowlist 校验
- 真实 PyInstaller EXE smoke（BUILD_SERVICE_SMOKE=1 时运行，供 Windows CI）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from services.build_service import (
    BuildJob,
    BuildRequest,
    BuildStatus,
    build_main_entry,
    compute_dependency_digest,
    verify_artifacts,
)

MAKE_ARTIFACT = (
    "from pathlib import Path\n"
    "Path('out/artifact.txt').write_text('hello artifact\\n')\n"
)


def _make_src(tmp_path: Path, with_deps: bool = True) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "make_artifact.py").write_text(MAKE_ARTIFACT, encoding="utf-8")
    if with_deps:
        (src / "requirements.txt").write_text("fastapi>=0.115.0\n", encoding="utf-8")
        (src / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (src / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    return src


def _snapshot_dir(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_success_manifest_artifacts_and_digest(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "src/make_artifact.py"], resource_profile="low")
    assert res["exit_code"] == 0, res.get("error")
    assert res["status"] == "COMPLETED"

    manifest = res["manifest"]
    assert manifest["schema_version"] == 1
    assert manifest["status"] == "COMPLETED"
    # provenance：builder 版本 + 源码 digest
    assert manifest["provenance"]["builder"] == "build_service"
    assert manifest["provenance"]["builder_version"]
    assert manifest["provenance"]["source_digest"]
    # 依赖 digest 与独立计算一致
    assert manifest["dependency_digest"] == compute_dependency_digest(src)

    artifact = next(a for a in manifest["artifacts"] if a["name"] == "artifact.txt")
    artifacts_dir = Path(res["artifacts_dir"])
    # 产物 digest/size 与实际落盘文件一致（跨平台：Windows 文本模式会做换行翻译）
    actual = (artifacts_dir / "artifact.txt").read_bytes()
    assert artifact["size"] == len(actual)
    assert artifact["sha256"] == hashlib.sha256(actual).hexdigest()
    assert (artifacts_dir / "build_manifest.json").is_file()


def test_success_shell_redirect_command(tmp_path):
    """字符串命令（经平台 shell）支持 `> out/...` 重定向写产物。"""
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    exe = str(Path(sys.executable))
    sep = "\\" if os.name == "nt" else "/"
    if os.name == "nt":
        command = f'"{exe}" -c "print(1)" > out{sep}artifact.txt'
    else:
        command = f'"{exe}" -c \'print(1)\' > out{sep}artifact.txt'
    res = build_main_entry(src, out, command, resource_profile="low")
    assert res["exit_code"] == 0, res.get("error")
    assert (Path(res["artifacts_dir"]) / "artifact.txt").is_file()
    assert (Path(res["artifacts_dir"]) / "build_manifest.json").is_file()


def test_isolation_build_writes_out_not_src(tmp_path):
    """隔离：构建产物写 out，原始源码目录不被污染（无新增、无修改）。"""
    src = _make_src(tmp_path)
    before = _snapshot_dir(src)
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "src/make_artifact.py"], resource_profile="low")
    assert res["exit_code"] == 0
    after = _snapshot_dir(src)
    assert set(after) == set(before)
    assert all(after[k] == v for k, v in before.items())
    assert not (src / "artifact.txt").exists()
    assert (Path(res["artifacts_dir"]) / "artifact.txt").exists()


def test_failure_nonzero_exit_and_error_txt(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "-c", "import sys; sys.exit(3)"], resource_profile="low")
    assert res["exit_code"] == 3
    assert res["status"] == "FAILED"
    assert res["manifest"] is None
    assert "非零" in res["error"]
    error_txt = Path(res["artifacts_dir"]) / "error.txt"
    assert error_txt.is_file()
    body = error_txt.read_text(encoding="utf-8")
    assert "非零" in body or "exit" in body.lower()


def test_timeout_fails_with_nonzero(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    res = build_main_entry(
        src,
        out,
        [sys.executable, "-c", "import time; time.sleep(30)"],
        resource_profile="low",
        timeout_seconds=2,
    )
    assert res["exit_code"] != 0
    assert res["status"] == "FAILED"
    assert "超时" in res["error"]
    assert (Path(res["artifacts_dir"]) / "error.txt").is_file()


def test_disk_quota_exceeded_fails(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    # 写入超过 low 档磁盘配额（1MB）的大文件
    big = (
        "from pathlib import Path\n"
        "Path('out/big.bin').write_bytes(b'x' * (2 * 1024 * 1024))\n"
    )
    (src / "make_big.py").write_text(big, encoding="utf-8")
    res = build_main_entry(
        src, out, [sys.executable, "src/make_big.py"], resource_profile="low", disk_quota_bytes=64 * 1024
    )
    assert res["exit_code"] != 0
    assert res["status"] == "FAILED"
    assert "磁盘配额" in res["error"]
    assert (Path(res["artifacts_dir"]) / "error.txt").is_file()


def test_dependency_digest_change_fails(tmp_path):
    """构建期间依赖文件被改动 -> 依赖基线被锁定 -> 构建失败。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (src / "tamper.py").write_text(
        "from pathlib import Path\n"
        "Path('src/requirements.txt').write_text('tampered\\n')\n"
        "Path('out/x.txt').write_text('x')\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "src/tamper.py"], resource_profile="low")
    assert res["exit_code"] != 0
    assert res["status"] == "FAILED"
    assert "digest" in res["error"].lower()
    assert (Path(res["artifacts_dir"]) / "error.txt").is_file()
    # 原始源码目录未被污染
    assert (src / "requirements.txt").read_text(encoding="utf-8") == "fastapi\n"


def test_build_job_state_machine_and_unknown_profile(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    job = BuildJob(
        BuildRequest(src, out, [sys.executable, "src/make_artifact.py"], resource_profile="low")
    )
    assert job.status == BuildStatus.PENDING
    assert job.run() == 0
    assert job.status == BuildStatus.COMPLETED
    assert job.exit_code == 0

    with pytest.raises(ValueError, match="未知构建资源档位"):
        BuildJob(BuildRequest(src, out, [sys.executable, "-c", "pass"], resource_profile="nope"))
    bad = build_main_entry(src, out, [sys.executable, "-c", "pass"], resource_profile="nope")
    assert bad["status"] == "FAILED"
    assert bad["exit_code"] != 0


def test_verify_artifacts_ok_and_tamper_fails(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "src/make_artifact.py"], resource_profile="low")
    assert res["exit_code"] == 0
    assert res["verify"]["ok"] is True
    assert res["verify"]["verified_artifacts"] == 1

    artifact_path = Path(res["artifacts_dir"]) / "artifact.txt"
    artifact_path.write_text("tampered content\n", encoding="utf-8")
    report = verify_artifacts(res["manifest"])
    assert report["ok"] is False
    assert any("DIGEST_MISMATCH" in e for e in report["errors"])

    # manifest 路径形式的校验同样工作
    report_from_path = verify_artifacts(Path(res["artifacts_dir"]) / "build_manifest.json")
    assert report_from_path["ok"] is False


def test_verify_detects_unexpected_artifact(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "src/make_artifact.py"], resource_profile="low")
    assert res["exit_code"] == 0
    (Path(res["artifacts_dir"]) / "evil.bin").write_bytes(b"unexpected")
    report = verify_artifacts(res["manifest"])
    assert report["ok"] is False
    assert any("UNEXPECTED_ARTIFACT" in e for e in report["errors"])


def test_verify_signer_allowlist(tmp_path):
    src = _make_src(tmp_path)
    out = tmp_path / "out"
    res = build_main_entry(src, out, [sys.executable, "src/make_artifact.py"], resource_profile="low")
    assert res["exit_code"] == 0
    digest = res["manifest"]["artifacts"][0]["sha256"]

    ok_report = verify_artifacts(res["manifest"], signer_allowlist={"artifact.txt": {digest}})
    assert ok_report["ok"] is True
    assert ok_report["signer_allowlist_checked"] is True

    bad_report = verify_artifacts(res["manifest"], signer_allowlist={"artifact.txt": {"0" * 64}})
    assert bad_report["ok"] is False
    assert any("SIGNER_NOT_ALLOWED" in e for e in bad_report["errors"])

    global_report = verify_artifacts(res["manifest"], signer_allowlist={digest})
    assert global_report["ok"] is True


def _pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    os.environ.get("BUILD_SERVICE_SMOKE") != "1",
    reason="BUILD_SERVICE_SMOKE=1 才运行真实 PyInstaller EXE smoke（供 Windows CI）",
)
def test_real_pyinstaller_exe_smoke(tmp_path):
    """真实 EXE smoke：经 build_main_entry 用 PyInstaller 打一个小脚本，校验 EXE 与 manifest。"""
    if not _pyinstaller_available():
        pytest.skip("PyInstaller 不可用")
    src = tmp_path / "src"
    src.mkdir()
    (src / "entry.py").write_text(
        "def main():\n    print('smoke ok')\n\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    (src / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    out = tmp_path / "out"
    command = [
        sys.executable,
        "-c",
        "import os, subprocess, sys;"
        "sys.exit(subprocess.call([sys.executable, '-m', 'PyInstaller', '--noconfirm',"
        "'--onefile', '--name', 'smoke', '--distpath', os.environ['BUILD_OUT_DIR'],"
        "'src/entry.py']))",
    ]
    result = build_main_entry(src, out, command, resource_profile="standard", timeout_seconds=300)
    assert result["exit_code"] == 0, result.get("error")
    assert result["status"] == "COMPLETED"
    exe_name = "smoke.exe" if os.name == "nt" else "smoke"
    artifact_names = {a["name"] for a in result["manifest"]["artifacts"]}
    assert exe_name in artifact_names, artifact_names
    assert (Path(result["artifacts_dir"]) / exe_name).is_file()
    assert (Path(result["artifacts_dir"]) / "build_manifest.json").is_file()
    assert result["verify"]["ok"] is True, result["verify"]["errors"]
    # manifest 落盘内容可被独立解析
    raw = json.loads((Path(result["artifacts_dir"]) / "build_manifest.json").read_text(encoding="utf-8"))
    assert raw["status"] == "COMPLETED"
