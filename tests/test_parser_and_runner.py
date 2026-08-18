"""真实解析器和受控插件 Runner 测试。"""
from __future__ import annotations

import asyncio
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

from core.plugin_manager import PluginManager
from services.parser import EpubParser, ParserFactory, TxtParser
from services.plugin_runner import JsonPluginRunner, PluginExecutionError


def test_text_parser_has_offsets_and_legacy_parse():
    parser = TxtParser()
    document = parser.parse_document(BytesIO("# 第一章\n\n正文内容".encode()), "book.md")
    assert document.parser_id == "builtin.txt"
    assert document.text == "# 第一章\n\n正文内容"
    assert document.passages[0].char_start == 0
    assert parser.parse(BytesIO(b"hello"), "book.txt") == "hello"


def test_parser_factory_rejects_legacy_doc_and_supports_epub():
    with pytest.raises(ValueError):
        ParserFactory.get_parser("old.doc")
    assert isinstance(ParserFactory.get_parser("book.epub"), EpubParser)


@pytest.mark.asyncio
async def test_runner_rejects_untrusted_plugin(tmp_path):
    # 未被服务端信任核准的插件不得执行（digest/签名复核失败）
    manager = _build_runner_manager(tmp_path)

    with pytest.raises(PluginExecutionError, match="信任复核"):
        await JsonPluginRunner(manager).run("x", {})


@pytest.mark.asyncio
async def test_runner_executes_trusted_json_command(tmp_path):
    manager = _build_runner_manager(tmp_path)
    manager.approve_plugin("x", operator="operator", signer=None)

    assert manager.is_trusted("x")
    result = await JsonPluginRunner(manager).run("x", {"value": 2})
    assert result == {"ok": 3}


def _build_runner_manager(tmp_path):
    """构造含外部命令插件 x 的 PluginManager（仅供 Runner 单元测试）。"""
    manager = PluginManager()
    manager.plugins_dir = tmp_path / "plugins"
    manager.plugins_dir.mkdir(exist_ok=True)
    manager.staging_dir = manager.plugins_dir / ".staging"
    manager.staging_dir.mkdir(exist_ok=True)
    manager.trust_file = manager.plugins_dir / "trust_store.json"
    manager.policy_file = manager.plugins_dir / "trust_policy.json"

    plugin_dir = manager.plugins_dir / "x"
    plugin_dir.mkdir(exist_ok=True)
    script = plugin_dir / "plugin.py"
    script.write_text(
        "import json,sys; data=json.load(sys.stdin); print(json.dumps({'ok': data['value'] + 1}))",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "id": "x",
            "kind": "resource",
            "external_command": [sys.executable, str(script)],
            "version": "1.0.0",
            "capabilities": ["math"],
        }),
        encoding="utf-8",
    )
    manager.load_all()
    return manager
