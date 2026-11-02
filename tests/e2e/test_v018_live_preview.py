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
        """demo 域标注+八区块标题渲染+术语负断言（and 形制——全须在页）。

        八区块均可辨锚：B1 任务列表/B2 任务头卡/B3 实盘仓位/B4 外部干预
        待确认/B5 规则层与安全底线/B6 决策快照/B7 任务参数/M1 创建入口
        （M1 对话框开合与门槛清单在 web 单测层锚——e2e 不点开 antd
        Modal，避免脆弱选择器）。B5 四条风控真实值同锚（spec Scenario 3
        THEN）。术语负断言含中文「测试网」与 testnet 两形制（spec
        Scenario 2/编号索引 v2 修订⑦）。
        """
        page.goto(BASE + "/live", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        content = page.content()
        assert "demo 域预览" in content
        assert "目标态预览" in content
        assert "任务列表" in content
        assert "任务头卡" in content
        assert "实盘仓位" in content
        assert "外部干预待确认" in content
        assert "规则层与安全底线" in content
        assert "决策快照" in content
        assert "任务参数（demo 域）" in content
        assert "创建实盘任务（预览）" in content
        for v in ("500U", "5s", "1000U", "50%"):
            assert v in content
        assert "testnet" not in content
        assert "测试网" not in content

    def test_live_screenshot(self, page: Page):
        page.goto(BASE + "/live", timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        _wait_react(page)
        page.wait_for_timeout(800)
        page.screenshot(path="tests/e2e/artifacts/v018_live_preview_page.png")
