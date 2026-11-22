"""v0.11 Strategy Lab 页（策略实验室）Playwright e2e 冒烟验收网。

服务对象：前端 Lab 页验收（v010-v016 冒烟网一员，自管 fixture 零前置、
零外网）——页面可达+信号编辑面存在性，冒烟级不构造业务数据。
回测入口用例已随 fix-replay-gap-alignment U5 Lab 回测模式退役删除
（FT-01：适配停留工作区被 checkout 吞掉，本次补落地）。
"""

import pytest
from playwright.sync_api import Page, expect

import os
BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")


def _wait_react(page: Page, timeout: int = 8000):
    """等待 React 水合完成（v010 同款轮询判据）。"""
    import time
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        html = page.locator("#root").inner_html()
        if len(html) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate in time")


class TestV011Lab:
    def test_lab_page_loads(self, page: Page):
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        root = page.locator("#root")
        html = root.inner_html()
        assert len(html) > 200, "Lab page did not render"

    def test_lab_has_signal_editor(self, page: Page):
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        # 信号面存在性断言口径：中英文关键词宽匹配（页面文案语言不锁定）
        content = page.content().lower()
        assert "信号" in content or "入场" in content or "出场" in content or "entry" in content or "signal" in content or "rsi" in content, \
            "No signal editor content found"

    def test_lab_screenshot(self, page: Page):
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        page.screenshot(path="tests/e2e/artifacts/v011_lab_page.png")
