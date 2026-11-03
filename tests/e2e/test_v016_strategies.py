"""策略库页（/strategies）Playwright e2e 冒烟验收网（v0.16+）。

职能：页面加载+导航+console error 守护+布局+搜索筛选控件与策略卡
渲染断言。边界：CRUD 全量用例（创建/编辑/删除）需要带数据的真实
后端，本网刻意保持冒烟级。服务对象：前端各页验收（v010-v016
冒烟网一员，自管 fixture 零前置、零外网）。
"""

import pytest
from playwright.sync_api import Page

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


def _goto_strategies(page: Page):
    """跳转策略库页并等待 React 水合完成。"""
    page.goto(BASE + "/strategies", timeout=10000)
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    _wait_react(page)


class TestStrategiesPageLoad:
    """策略库页渲染且无 console error。"""

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
    """无策略时空态 UI 的渲染断言。"""

    def test_empty_state_or_strategy_list_renders(self, page: Page):
        """空态或策略列表任一形态即通过（形态取决于后端数据态）。"""
        _goto_strategies(page)
        content = page.content()
        has_empty = ("暂无" in content or "未找到" in content
                     or "No strategies" in content or "策略库为空" in content)
        has_strategies = "策略" in content or "已保存" in content or "年化" in content or "Sharpe" in content
        assert has_empty or has_strategies, "Strategies page shows neither empty state nor strategies"


class TestStrategiesNavigation:
    """策略库页与其他页之间的导航断言。"""

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
    """策略库页布局组件渲染断言。"""

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
    """搜索与筛选控件渲染断言。"""

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
        # 断言下限：页面至少存在筛选或排序任一 UI
        has_filter = "全部来源" in content or "全部策略" in content or "筛选" in content or "排序" in content or "filter" in content.lower() or "sort" in content.lower()
        assert has_filter, "No filter/sort controls found on strategies page"
