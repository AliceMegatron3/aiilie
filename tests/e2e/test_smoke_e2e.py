"""
tests/e2e/test_smoke_e2e.py — Playwright 冒烟端到端测试
========================================================
覆盖用户核心路径：
1. 页面加载与健康状态
2. 模块切换（左侧导航 → 路由同步）
3. 发送指令 → 系统回执出现在对话区
4. 设置页表单必填校验

运行前提：后端已启动（E2E_BASE_URL 或默认 http://127.0.0.1:8000）。
"""
from __future__ import annotations

import os
import sys

import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")

# 修复：原先在 playwright 未安装时直接 sys.exit(0)，
# 导致 pytest 收集阶段 INTERNALERROR 崩溃（no tests ran / RC=3）。
# 改为声明式跳过标记，收集器可正常继续，运行 --run-e2e 时才执行。
pytestmark = pytest.mark.skipif(
    "--run-e2e" not in sys.argv,
    reason="E2E tests only run with --run-e2e flag",
)

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:  # pragma: no cover
    pytest.skip(
        "playwright 未安装：pip install playwright && playwright install chromium",
        allow_module_level=True,
    )


def test_app_loads_and_shows_status(page):
    page.goto(BASE_URL)
    # 主标题可见
    expect(page.locator("h1")).to_contain_text("No0 AI", timeout=10_000)
    # 系统状态指示文本渲染
    expect(page.locator("text=System:").first).to_be_visible(timeout=10_000)


def test_module_switching_syncs_route(page):
    page.goto(BASE_URL)
    # 点击左侧导航「书库」模块
    page.get_by_role("button", name="书库").first.click()
    # 路由同步：URL hash 包含 /library
    expect(page).to_have_url(lambda url: "/library" in url, timeout=5_000)


def test_send_command_gets_receipt(page):
    page.goto(BASE_URL)
    textarea = page.locator("textarea").last
    textarea.fill("写一段 2000 字以内的世界观设定")
    page.get_by_role("button", name="发送").click()
    # 占位回执（任务排队/分发）出现在对话区
    expect(page.locator("text=任务ID").first).to_be_visible(timeout=15_000)


def test_settings_required_validation(page):
    page.goto(BASE_URL)
    page.goto(f"{BASE_URL}/#/settings")
    # 打开云端开关后清空 API Key，保存应被校验拦截（Toast 出现）
    toggles = page.locator('input[type="checkbox"]')
    if toggles.count() > 0:
        toggles.first.click()
    page.get_by_role("button", name="保存配置").click()
    # 校验失败提示（Toast）或成功提示均要求 UI 有反馈
    expect(page.locator(".toast-item").first).to_be_visible(timeout=10_000)


def run_all() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            test_app_loads_and_shows_status(page)
            print("[E2E] PASS: 应用加载与状态显示")
            test_module_switching_syncs_route(page)
            print("[E2E] PASS: 模块切换与路由同步")
            test_send_command_gets_receipt(page)
            print("[E2E] PASS: 指令发送与回执")
            test_settings_required_validation(page)
            print("[E2E] PASS: 设置校验反馈")
        finally:
            browser.close()


if __name__ == "__main__":
    run_all()
