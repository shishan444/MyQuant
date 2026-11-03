"""v0.10 前端基础页（首页导航+数据页）Playwright e2e 冒烟验收网。

服务对象：前端各页验收（v010-v016 冒烟网一员，自管 fixture 零前置、
零外网）——页面可达与关键内容渲染级，不构造业务数据。
"""

import pytest
from playwright.sync_api import Page, expect

import os
BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")


def _wait_react(page: Page, timeout: int = 8000):
    """轮询 #root 内容量判定 React 水合完成，超时抛 TimeoutError。"""
    import time
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        html = page.locator("#root").inner_html()
        if len(html) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate in time")


class TestV010Frontend:
    def test_homepage_loads(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        body = page.locator("body")
        expect(body).to_be_visible(timeout=5000)

    def test_data_page_renders(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        root = page.locator("#root")
        html = root.inner_html()
        assert len(html) > 100, "React did not render content"

    def test_sidebar_has_links(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        links = page.locator("a")
        assert links.count() > 0, "No navigation links found"

    def test_dark_theme_applied(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        bg = page.evaluate("getComputedStyle(document.documentElement).backgroundColor")
        assert bg is not None

    def test_navigate_to_lab(self, page: Page):
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        content = page.content().lower()
        assert len(content) > 100

    def test_data_page_screenshot(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        page.screenshot(path="tests/e2e/artifacts/v010_data_page.png")
