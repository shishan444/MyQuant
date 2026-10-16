"""研究日志 page=1≡无参三层等价断言（unify-tool-return-contract 任务 8）。

GAP-02 处置=验证性任务：team-lead 持有 2026-10-15 MCP 层实测矛盾
记录（page=1→0 条 / 无参→50 条，服务为最新代码），与静态分析
（REST log.py:37-38 clamp 数学恒等、工具恒发 page 键）矛盾——本三层
断言定位真实机制。

**升级路径（任一断言复现即触发）**：page=1 与无参响应不相等时，
说明分页链路存在状态依赖或参数处理分支——升级行为修改任务并走
变更传播（openspec-update-change），不得在本测试内静默放宽断言。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

_CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}


def _seed_logs(client, n: int = 3):
    """造 n 条研究日志（经假设登记落 register 日志）。"""
    for i in range(n):
        resp = client.post("/api/research/hypotheses", json={
            "conditions": _CONDITIONS, "statement": f"page 校验 {i}",
            "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                             "start": f"2024-01-0{i + 1}T00:00:00Z",
                             "end": "2024-03-01T00:00:00Z"}]})
        assert resp.status_code == 200, resp.text


class TestPageOneParityHttp:
    """S4a：HTTP 层（TestClient）page=1 与无参逐键相等。"""

    def test_s4a_testclient_page1_equals_default(self, api_client):
        _seed_logs(api_client)
        with_page = api_client.get("/api/research/log",
                                   params={"page": 1}).json()
        without = api_client.get("/api/research/log").json()
        assert with_page == without, \
            f"HTTP 层复现 page=1≠无参——按升级路径走变更传播：{with_page!r}"
        assert without.get("items"), "前置：日志非空（否则断言空对空无意义）"


class TestPageOneParityRest:
    """S4b：REST 层（httpx ASGITransport 直调，独立客户端栈）等价。"""

    def test_s4b_httpx_asgi_page1_equals_default(self, db_path, tmp_data_dir):
        import asyncio
        import httpx
        from api.app import create_app

        app = create_app(db_path=db_path, data_dir=tmp_data_dir)

        async def _run():
            # httpx 0.28：ASGITransport 仅异步——独立客户端栈直调 ASGI
            # （不走 TestClient 的 starlette 栈）；lifespan 经
            # router.lifespan_context 手动进入（app.state 挂载依赖它）
            async with app.router.lifespan_context(app), \
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://127.0.0.1:8000") as client:
                for i in range(3):
                    resp = await client.post("/api/research/hypotheses", json={
                        "conditions": _CONDITIONS,
                        "statement": f"page 校验 {i}",
                        "data_ranges": [{"symbol": "BTCUSDT",
                                         "timeframe": "4h",
                                         "start": f"2024-01-0{i + 1}T00:00:00Z",
                                         "end": "2024-03-01T00:00:00Z"}]})
                    assert resp.status_code == 200, resp.text
                with_page = (await client.get(
                    "/api/research/log", params={"page": 1})).json()
                without = (await client.get("/api/research/log")).json()
            return with_page, without

        with_page, without = asyncio.run(_run())
        assert with_page == without, "REST 层复现——按升级路径走变更传播"
        assert without.get("items"), "前置：日志非空"


class TestPageOneParityMcp:
    """S4c：MCP 工具层（FastMCP 注册面）参数透传等价。

    工具层职责=参数组装透传（page 恒发显式键，缺省值=1）；显式
    page=1 与缺省产生的请求 params 必须逐键一致——这是「MCP 层
    page=1≠无参」矛盾在工具层的唯一可存在点。
    """

    def test_s4c_mcp_tool_params_identical(self):
        class _FakeMcp:
            def __init__(self):
                self.registered = {}

            def tool(self):
                def deco(fn):
                    self.registered[fn.__name__] = fn
                    return fn
                return deco

        class _CapturingApi:
            def __init__(self):
                self.gets = []

            def get(self, url, params=None):
                self.gets.append((url, params))
                return {"items": [], "total": 0}

        from mcpserver.tools import register_readonly_tools

        mcp, api = _FakeMcp(), _CapturingApi()
        register_readonly_tools(mcp, api)
        tool = mcp.registered["get_research_log"]

        tool()                    # 缺省（page 不传）
        tool(page=1)              # 显式 page=1
        tool(page=1, cursor=None) # 显式全缺省形

        # 三个调用的请求 params 逐键一致（缺省=显式 1 的等价性锚）
        assert api.gets[0] == api.gets[1] == api.gets[2], \
            f"MCP 工具层复现 page=1≠无参——按升级路径走变更传播：{api.gets!r}"
        assert all("page" in params for _, params in api.gets), \
            "工具恒发 page 键（透传面契约）"
