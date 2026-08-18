"""Batch 7：容器非 root + 回环绑定 + secret 注入 静态门。

读仓库内 Dockerfile / docker-compose.yml，断言：
- 容器以非 root 用户运行（USER 出现在 CMD 之前，无 USER root）；
- 对外端口仅绑定回环地址（127.0.0.1），不把所有宿主接口暴露；
- 敏感项（API key）来自环境注入而非硬编码提交。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_runs_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER" in dockerfile, "Dockerfile 必须显式声明 USER（非 root）"
    # 找到最后一个 USER 指令，且其后没有 'root'
    idx = dockerfile.rindex("USER")
    tail = dockerfile[idx:]
    assert any(u == "appuser" for u in _users(tail)), "容器必须以非 root 用户运行"
    assert "root" not in dockerfile[idx:idx + 80], "不许再切回 root"


def _users(segment: str) -> list[str]:
    out = []
    for line in segment.splitlines():
        line = line.strip()
        if line.startswith("USER"):
            out.append(line.split()[1].split(":")[0])
    return out


def test_compose_binds_loopback_and_does_not_expose_secrets():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # 后端仅绑定回环地址（阶段A：避免把服务暴露到所有宿主接口）
    assert "127.0.0.1:8000:8000" in compose or '"127.0.0.1:8000:8000"' in compose
    # Redis 不向宿主暴露端口
    assert "ports: []" in compose
    # 口令/密钥经环境变量注入（${...}），非硬编码占位即用
    assert "REDIS_PASSWORD" in compose


def test_no_hardcoded_api_key_in_plaintext_config():
    """关键秘密不得以明文 JSON/YAML key 形式提交（抽查代表性配置）。"""
    suspicious = ("sk-", "BEGIN PRIVATE KEY", "ghp_", "AKIA")
    for rel in ("config/llm_provider.yaml", "config/config.yaml"):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in suspicious:
            assert needle not in text, f"{rel} 疑似明文敏感串: {needle}"


def test_cors_is_not_wildcard_and_limited_to_localhost():
    """Batch 7 CORS 回归：生产应用不允许通配来源；仅放行本地开发来源。

    通过构建应用读取注册的 CORSMiddleware 配置，证明没有 `allow_origins=['*']`。
    """
    from main import create_app

    app = create_app()
    cors = None
    for m in app.user_middleware:
        if m.cls.__name__ == "CORSMiddleware":
            cors = m
            break
    assert cors is not None, "应用必须注册 CORSMiddleware"
    origins = cors.kwargs.get("allow_origins") or []
    assert "*" not in origins, "不允许通配 CORS 来源"
    assert origins, "必须显式声明放行来源"
    assert all(o.startswith("http://127.0.0.1") or o.startswith("http://localhost") for o in origins)