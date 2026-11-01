"""E2E tests for v0.17 Plugins page（alignment-batch D6 新页）。

只读页五区断言：页面可达+React 挂载+关键区标题渲染——全程 localhost
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


class TestV017Plugins:
    def test_plugins_page_loads(self, page: Page):
        page.goto(BASE + "/plugins", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        root = page.locator("#root")
        assert len(root.inner_html()) > 200

    def test_plugins_has_sections(self, page: Page):
        """关键区标题渲染：公式插件/词表/四层池/协议要点至少可辨。"""
        page.goto(BASE + "/plugins", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        content = page.content()
        assert "插件" in content
        # 词表卡与四层池是静态+registry 混合区，标题独立可辨
        assert "词表" in content or "四层" in content or "协议" in content

    def test_plugins_screenshot(self, page: Page):
        page.goto(BASE + "/plugins", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        page.wait_for_timeout(800)
        page.screenshot(path="tests/e2e/artifacts/v017_plugins_page.png")
