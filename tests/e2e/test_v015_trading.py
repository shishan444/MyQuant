"""Trading 页（模拟交易）Playwright e2e 冒烟测试网（v0.15+）。

服务对象：前端 Trading 页验收（React+antd 单页应用；BASE 缺省
localhost:8080，可用环境变量 E2E_WEB_URL 覆写）。
覆盖面：空态、页面加载、导航、console error 守护、布局组件。
边界：CRUD 全量用例（创建/暂停/停止/删除）需要带数据的真实后端，
本网刻意保持冒烟级——只验页面骨架与守护面，不构造业务数据。
"""

import pytest
from playwright.sync_api import Page

import os
BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")

# 非产品级控制台噪音排除清单（fix-fullrun-baseline-4red ①出路 b）——
# 每项附定性依据（为何不属于守护对象「产品级 console error」）；
# 新增排除项合法性条件=定性依据在场+交付单列呈报（假绿禁令——
# 无定性依据的收窄不得作为修复手段）。
EXCLUDED_NOISE = (
    ("favicon",
     "浏览器自动请求 favicon 的 404 资源噪音，非页面代码错误"),
    ("devtools",
     "浏览器开发工具自身输出，非页面代码错误"),
    ("net::",
     "网络层错误码枚举前缀（ERR_*），指向资源加载失败而非产品逻辑"),
    ("failed to load resource",
     "资源加载失败（静态资源/接口 4xx-5xx）——功能性失败由功能用例守护"),
    ("is not connected to any form element",
     "antd 开发模式框架警告：Form.useForm() 实例未绑定 Form 元素"
     "（2026-10-23 定性——生产构建不打印；/trading 全部功能用例绿证"
     "零功能影响；前端代码清理属前端承接范围，A-arch-1 裁决不处理）"),
)


def _filter_console_errors(errors: list[str]) -> list[str]:
    """收集→排除管线（排除清单单源消费；子串匹配统一 lower 形）。"""
    return [
        e for e in errors
        if not any(sub in e.lower() for sub, _ in EXCLUDED_NOISE)
    ]


def _wait_react(page: Page, timeout: int = 8000):
    """轮询等待 React hydrate 完成——以 #root 出现内容为完成信号
    （默认 8000ms 超时，超时抛 TimeoutError）。"""
    import time
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        html = page.locator("#root").inner_html()
        if len(html) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate in time")


def _goto_trading(page: Page):
    """打开 /trading 并等待 React hydrate 完成（goto+装载两段等待）。"""
    page.goto(BASE + "/trading", timeout=10000)
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    _wait_react(page)


class TestTradingPageLoad:
    """Trading 页加载无产品级 console error（经排除清单过滤后判定）。"""

    def test_no_console_errors(self, page: Page):
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        _goto_trading(page)
        page.wait_for_timeout(2000)
        filtered = _filter_console_errors(errors)
        assert len(filtered) == 0, f"Console errors on /trading: {filtered}"

    def test_exclusion_keeps_collection_alive(self, page: Page):
        """排除词语义宽度变异哨兵（5.2）：注入一条非排除形态
        console.error，验证收集面未被排除词吞掉——注入消息必须出现
        在未排除集合（排除清单若吞掉收集面，本用例红）。"""
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        _goto_trading(page)
        page.evaluate("console.error('E2E_PROBE_not_in_excluded_noise')")
        page.wait_for_timeout(500)
        filtered = _filter_console_errors(errors)
        assert any("E2E_PROBE_not_in_excluded_noise" in e for e in filtered), \
            f"注入的非排除形态 error 被排除词吞掉（收集面被破坏）: {filtered}"

    def test_page_has_content(self, page: Page):
        _goto_trading(page)
        html = page.locator("#root").inner_html()
        assert len(html) > 200, f"Trading page has too little content: {len(html)} chars"


class TestTradingEmptyState:
    """无任务在库时空态 UI 的渲染形态。"""

    def test_empty_state_or_task_list_renders(self, page: Page):
        """页面显示空态或任务列表之一（实际形态取决于后端数据状态——
        冒烟网不构造业务数据，两态皆合法）。"""
        _goto_trading(page)
        content = page.content()
        # 空态文案或任务相关内容——二择断言（后端状态决定实际形态）
        has_empty = "暂无模拟交易任务" in content or "前往策略库" in content
        has_tasks = "任务" in content or "运行" in content or "停止" in content or "暂停" in content
        assert has_empty or has_tasks, "Trading page shows neither empty state nor tasks"

    def test_empty_state_has_navigation_button(self, page: Page):
        """空态在场时，验证存在指向策略库页的导航入口。"""
        _goto_trading(page)
        content = page.content()
        if "暂无模拟交易任务" in content:
            # 空态在场时校验「前往策略库」导航入口（button/a 两形制）
            strategies_btn = page.locator("button:has-text('前往策略库'), a:has-text('前往策略库')")
            assert strategies_btn.count() > 0, "Go to Strategies button missing in empty state"


class TestTradingNavigation:
    """Trading 页与其他页面间的导航。"""

    def test_trading_sidebar_link_exists(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        trading_link = page.locator("nav a[href='/trading']")
        assert trading_link.count() >= 1, "No sidebar link found for /trading"

    def test_navigate_to_trading_via_sidebar(self, page: Page):
        page.goto(BASE, timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        _wait_react(page)
        trading_link = page.locator("nav a[href='/trading']").first
        trading_link.click()
        page.wait_for_timeout(1000)
        assert "/trading" in page.url, f"Expected /trading in URL, got {page.url}"

    def test_direct_url_access(self, page: Page):
        _goto_trading(page)
        html = page.locator("#root").inner_html()
        assert len(html) > 200, "Trading page not accessible via direct URL"


class TestTradingLayout:
    """Trading 页布局组件渲染形态（header/侧边栏）。"""

    def test_header_title(self, page: Page):
        _goto_trading(page)
        header = page.locator("header")
        assert header.is_visible(), "Header not visible"
        title = header.locator("h1")
        title_text = title.text_content() or ""
        assert "模拟交易" in title_text or "交易" in title_text, \
            f"Expected 模拟交易 in header title, got '{title_text}'"

    def test_sidebar_visible(self, page: Page):
        _goto_trading(page)
        sidebar = page.locator("aside")
        assert sidebar.is_visible(), "Sidebar not visible"


class TestTradingHeader:
    """页级 header 元素。"""

    def test_trading_page_renders(self, page: Page):
        """trading 页正常渲染（当前前端 header 仅标题，无独立
        RunnerStatusBadge——旧版 'Runner Online/Offline' 断言基于
        已移除组件，改为验证 header 与内容区在场）。"""
        _goto_trading(page)
        page.wait_for_timeout(2000)
        assert page.locator("header").is_visible(), "Header not visible"
        content = page.locator("body").inner_text()
        assert "模拟交易" in content, "trading 页内容未渲染"
