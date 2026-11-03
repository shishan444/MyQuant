"""v0.12 进化中心+v0.13 组件库与设置页 Playwright e2e 冒烟验收网。

服务对象：前端 Evolution/Library/Settings 页验收（v010-v016 冒烟网
一员，自管 fixture 零前置、零外网）——页面可达与渲染级冒烟。
"""

import pytest
from playwright.sync_api import Page

import os
BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")


def _wait_react(page: Page, timeout: int = 8000):
    import time
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        html = page.locator("#root").inner_html()
        if len(html) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate")


class TestV012Evolution:
    def test_evolution_page_loads(self, page: Page):
        page.goto(BASE + "/evolution", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        root = page.locator("#root")
        assert len(root.inner_html()) > 200

    def test_evolution_has_task_info(self, page: Page):
        page.goto(BASE + "/evolution", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        content = page.content().lower()
        assert "evolution" in content or "task" in content

    def test_evolution_screenshot(self, page: Page):
        page.goto(BASE + "/evolution", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        page.screenshot(path="tests/e2e/artifacts/v012_evolution_page.png")


class TestV013Library:
    def test_library_page_loads(self, page: Page):
        page.goto(BASE + "/library", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        root = page.locator("#root")
        assert len(root.inner_html()) > 200

    def test_library_has_filter_bar(self, page: Page):
        page.goto(BASE + "/library", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        content = page.content().lower()
        assert "filter" in content or "search" in content or "symbol" in content

    def test_library_screenshot(self, page: Page):
        page.goto(BASE + "/library", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        page.screenshot(path="tests/e2e/artifacts/v013_library_page.png")


class TestV013Settings:
    def test_settings_page_loads(self, page: Page):
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        root = page.locator("#root")
        assert len(root.inner_html()) > 200

    def test_settings_has_evolution_config(self, page: Page):
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        content = page.content().lower()
        assert "通用" in content or "指标参数" in content or "config" in content or "设置" in content

    def test_settings_screenshot(self, page: Page):
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        page.screenshot(path="tests/e2e/artifacts/v013_settings_page.png")
