"""Comprehensive E2E test for frontend - console errors and navigation.

Updated for current frontend: i18n (中文) + route restructure
(/library → /strategies, +/verify /batch-backtest /trading) + settings tab 重组
(通用/指标参数/数据管理/关于, 移除 Evolution Config/API Keys) + lab 改为假设验证工具.
"""
import pytest
from playwright.sync_api import Page

import os
BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")


def _wait_react(page: Page, timeout: int = 8000):
    """Wait for React to hydrate by polling for content in #root."""
    import time
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        html = page.locator("#root").inner_html()
        if len(html) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate in time")


class TestConsoleErrors:
    """Check for JavaScript console errors on each page."""

    def _collect_errors(self, page: Page, url: str) -> list[str]:
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(url, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        page.wait_for_timeout(2000)
        return [
            e for e in errors
            if "favicon" not in e.lower()
            and "devtools" not in e.lower()
            and "net::" not in e.lower()
            and "failed to load resource" not in e.lower()
        ]

    def test_no_console_errors_data_page(self, page: Page):
        assert len(self._collect_errors(page, BASE + "/data")) == 0

    def test_no_console_errors_lab_page(self, page: Page):
        assert len(self._collect_errors(page, BASE + "/lab")) == 0

    def test_no_console_errors_evolution_page(self, page: Page):
        assert len(self._collect_errors(page, BASE + "/evolution")) == 0

    def test_no_console_errors_strategies_page(self, page: Page):
        assert len(self._collect_errors(page, BASE + "/strategies")) == 0

    def test_no_console_errors_settings_page(self, page: Page):
        assert len(self._collect_errors(page, BASE + "/settings")) == 0


class TestNavigation:
    """Test client-side navigation between pages (当前 8 个路由)."""

    ROUTES = ["/lab", "/evolution", "/strategies", "/verify", "/batch-backtest", "/trading", "/data", "/settings"]

    def test_sidebar_navigation_links_work(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        links = page.locator("nav a")
        assert links.count() >= 5, f"Expected at least 5 nav links, found {links.count()}"
        for route in self.ROUTES:
            assert page.locator(f"nav a[href='{route}']").count() >= 1, f"No sidebar link for {route}"

    def test_navigate_via_sidebar_click(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        for route in ["/lab", "/evolution", "/strategies", "/data", "/settings"]:
            page.locator(f"nav a[href='{route}']").first.click()
            page.wait_for_timeout(1000)
            assert route in page.url, f"Expected {route} in URL, got {page.url}"

    def test_root_redirects_to_lab(self, page: Page):
        """根路径重定向到 /lab (非 /data)."""
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "/lab" in page.url, f"Expected redirect to /lab, got {page.url}"

    def test_direct_url_access(self, page: Page):
        for route in ["/data", "/lab", "/evolution", "/strategies", "/settings"]:
            page.goto(BASE + route, timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            _wait_react(page)
            html = page.locator("#root").inner_html()
            assert len(html) > 200, f"Page {route} has too little content: {len(html)} chars"


class TestLayoutComponents:
    """Test shared layout components render correctly."""

    def _goto_and_wait(self, page: Page, route: str = "/data"):
        page.goto(BASE + route, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)

    def test_sidebar_visible(self, page: Page):
        self._goto_and_wait(page)
        assert page.locator("aside").is_visible(), "Sidebar not visible"

    def test_header_visible_with_title(self, page: Page):
        self._goto_and_wait(page, "/lab")
        header = page.locator("header")
        assert header.is_visible()
        title = header.locator("h1")
        assert title.is_visible()
        assert "策略实验室" in (title.text_content() or ""), (
            f"Expected '策略实验室' in header, got '{title.text_content()}'"
        )

    def test_sidebar_has_branding(self, page: Page):
        self._goto_and_wait(page)
        # 品牌为 MyQuant (非旧版 QT)
        assert page.locator("text=MyQuant").count() > 0, "MyQuant branding not found"

    def test_header_title_updates_per_page(self, page: Page):
        titles_map = {
            "/lab": "策略实验室",
            "/evolution": "进化中心",
            "/strategies": "策略库",
            "/data": "数据管理",
            "/settings": "设置",
        }
        for route, expected_title in titles_map.items():
            page.goto(BASE + route, timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            _wait_react(page)
            actual = page.locator("header h1").first.text_content() or ""
            assert expected_title in actual, f"On {route}: expected '{expected_title}', got '{actual}'"


class TestPageContent:
    """Test page-specific content renders correctly (当前中文文案)."""

    def test_data_page_has_import_button(self, page: Page):
        page.goto(BASE + "/data", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        content = page.content()
        assert "上传 CSV" in content or "CSV" in content, "CSV 导入按钮未找到"

    def test_data_page_has_data_table(self, page: Page):
        page.goto(BASE + "/data", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        content = page.content()
        assert "BTCUSDT" in content or "币种" in content, "数据表格未找到"

    def test_lab_page_has_strategy_lab_heading(self, page: Page):
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "策略实验室" in page.content(), "策略实验室标题未找到"

    def test_lab_page_has_entry_rules_section(self, page: Page):
        """lab 现为假设验证工具: 入场规则 (替代旧 Risk Control)."""
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "入场规则" in page.content(), "入场规则区未找到"

    def test_lab_page_has_exit_rules_section(self, page: Page):
        """出场规则 (替代旧 Execution)."""
        page.goto(BASE + "/lab", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "出场规则" in page.content(), "出场规则区未找到"

    def test_evolution_page_has_heading(self, page: Page):
        page.goto(BASE + "/evolution", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "进化中心" in page.content(), "进化中心标题未找到"

    def test_strategies_page_has_search(self, page: Page):
        """策略库 (/strategies, 替代旧 /library)."""
        page.goto(BASE + "/strategies", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "策略" in page.content(), "策略库内容未找到"

    def test_strategies_page_has_symbol_filter(self, page: Page):
        page.goto(BASE + "/strategies", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        content = page.content().lower()
        assert "btc" in content or "symbol" in content, "币种过滤未找到"

    def test_settings_page_has_general_tab(self, page: Page):
        """settings tab: 通用 (替代旧 Evolution Config)."""
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "通用" in page.content(), "通用 tab 未找到"

    def test_settings_page_has_indicator_tab(self, page: Page):
        """指标参数 tab (替代旧 API Keys)."""
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "指标参数" in page.content(), "指标参数 tab 未找到"

    def test_settings_page_has_about_section(self, page: Page):
        """关于 (替代旧 Population Size)."""
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        assert "关于" in page.content(), "关于区未找到"

    def test_settings_page_tab_switching(self, page: Page):
        page.goto(BASE + "/settings", timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        # antd Tabs 的 tab 节点渲染为 role="tab"（div.ant-tabs-tab +
        # .ant-tabs-tab-btn），非 button 元素——旧 button 定位器恒 0 匹配
        indicator_tab = page.get_by_role("tab", name="指标参数")
        assert indicator_tab.count() > 0, "指标参数 tab 未找到"
        indicator_tab.click()
        page.wait_for_timeout(500)
        # tab 切换后页面仍有内容
        assert len(page.locator("#root").inner_html()) > 200
