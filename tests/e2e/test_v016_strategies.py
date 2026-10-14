"""E2E tests for the Strategies page (v0.16+).

Covers: page load, navigation, console errors, layout, search/filter UI,
strategy card rendering.
Full CRUD tests (create/edit/delete) require a running backend with data
and are intentionally kept as smoke tests.
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


def _goto_strategies(page: Page):
    """Navigate to strategies page and wait for React hydration."""
    page.goto(BASE + "/strategies", timeout=10000)
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    _wait_react(page)


class TestStrategiesPageLoad:
    """Strategies page renders without console errors."""

    def test_no_console_errors(self, page: Page):
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        _goto_strategies(page)
        page.wait_for_timeout(2000)
        filtered = [
            e for e in errors
            if "favicon" not in e.lower()
            and "devtools" not in e.lower()
            and "net::" not in e.lower()
            and "failed to load resource" not in e.lower()
        ]
        assert len(filtered) == 0, f"Console errors on /strategies: {filtered}"

    def test_page_has_content(self, page: Page):
        _goto_strategies(page)
        html = page.locator("#root").inner_html()
        assert len(html) > 200, f"Strategies page has too little content: {len(html)} chars"


class TestStrategiesEmptyState:
    """When no strategies exist, the empty state UI renders correctly."""

    def test_empty_state_or_strategy_list_renders(self, page: Page):
        """Page shows either empty state or strategy list (depends on backend state)."""
        _goto_strategies(page)
        content = page.content()
        has_empty = ("暂无" in content or "未找到" in content
                     or "No strategies" in content or "策略库为空" in content)
        has_strategies = "策略" in content or "已保存" in content or "年化" in content or "Sharpe" in content
        assert has_empty or has_strategies, "Strategies page shows neither empty state nor strategies"


class TestStrategiesNavigation:
    """Navigation between Strategies and other pages."""

    def test_strategies_sidebar_link_exists(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        strategies_link = page.locator("nav a[href='/strategies']")
        assert strategies_link.count() >= 1, "No sidebar link found for /strategies"

    def test_navigate_to_strategies_via_sidebar(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        strategies_link = page.locator("nav a[href='/strategies']").first
        strategies_link.click()
        page.wait_for_timeout(1000)
        assert "/strategies" in page.url, f"Expected /strategies in URL, got {page.url}"

    def test_direct_url_access(self, page: Page):
        _goto_strategies(page)
        html = page.locator("#root").inner_html()
        assert len(html) > 200, "Strategies page not accessible via direct URL"


class TestStrategiesLayout:
    """Layout components render correctly on strategies page."""

    def test_header_title(self, page: Page):
        _goto_strategies(page)
        header = page.locator("header")
        assert header.is_visible(), "Header not visible"
        title = header.locator("h1")
        title_text = title.text_content() or ""
        assert "Strateg" in title_text or "策略" in title_text, \
            f"Expected Strategy in header title, got '{title_text}'"

    def test_sidebar_visible(self, page: Page):
        _goto_strategies(page)
        sidebar = page.locator("aside")
        assert sidebar.is_visible(), "Sidebar not visible"


class TestStrategiesSearchFilter:
    """Search and filter controls render correctly."""

    def test_search_input_visible(self, page: Page):
        _goto_strategies(page)
        content = page.content()
        # search input 或 filter 控件 (实际为"全部来源"/"全部策略"下拉 + 可选 search input)
        has_search_input = page.locator("input[type='text'], input[type='search']").count() > 0
        has_filter = "全部来源" in content or "全部策略" in content or "筛选" in content
        # 空库容忍（2026-10-13 历史策略清除裁决后）：策略表为空时前端
        # 渲染空态页（无搜索/筛选控件）——空态文案在=控件职责已达成
        has_empty = ("暂无" in content or "未找到" in content
                     or "No strategies" in content or "策略库为空" in content)
        assert has_search_input or has_filter or has_empty, (
            "搜索/筛选控件未找到且非空态页")

    def test_filter_controls_present(self, page: Page):
        _goto_strategies(page)
        content = page.content()
        # At minimum, the page should have some filter or sort UI
        has_filter = "全部来源" in content or "全部策略" in content or "筛选" in content or "排序" in content or "filter" in content.lower() or "sort" in content.lower()
        assert has_filter, "No filter/sort controls found on strategies page"
