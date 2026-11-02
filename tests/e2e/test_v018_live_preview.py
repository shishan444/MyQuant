"""E2E tests for v0.18 Live trading preview page（live-trading-preview C8）。

demo-only 目标态预览页断言：可达+demo 域标注+八区块——全程 localhost
双服务，零外网（e2e-external-api-mock-rule）。
"""

import os
import time

import pytest
from playwright.sync_api import Page

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")


def _wait_react(page: Page, timeout: int = 8000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        html = page.locator("#root").inner_html()
        if len(html) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate")


class TestV018LivePreview:
    def test_live_page_loads(self, page: Page):
        page.goto(BASE + "/live", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        assert len(page.locator("#root").inner_html()) > 200

    def test_live_demo_banner_and_sections(self, page: Page):
        """demo 域标注+八区块标题渲染（and 形制——全须在页）。"""
        page.goto(BASE + "/live", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        content = page.content()
        assert "demo 域预览" in content
        assert "目标态预览" in content
        assert "任务列表" in content
        assert "实盘仓位" in content
        assert "规则层与安全底线" in content
        assert "决策快照" in content
        assert "testnet" not in content

    def test_live_screenshot(self, page: Page):
        page.goto(BASE + "/live", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        page.wait_for_timeout(800)
        page.screenshot(path="tests/e2e/artifacts/v018_live_preview_page.png")
