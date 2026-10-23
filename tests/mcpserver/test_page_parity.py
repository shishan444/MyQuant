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

from conftest import _FakeMcp  # 同目录（pytest prepend）

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


def _key_diff(a, b, path: str = "") -> list:
    """[F1 常驻取证] 两响应逐键差异树（断言失败信息增强）。

    递归下降 dict/list，产出差异路径清单；断言语义（== 全等比较）
    不变——仅失败时可见的信息增强装置。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _key_diff(a.get(k), b.get(k), f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            av = a[i] if i < len(a) else "<缺>"
            bv = b[i] if i < len(b) else "<缺>"
            out += _key_diff(av, bv, f"{path}[{i}]")
        return out
    if a != b:
        return [{path: {"page1": a, "default": b}}]
    return []


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

    def test_s4b_httpx_asgi_page1_equals_default(
            self, db_path, tmp_data_dir, monkeypatch):
        import asyncio
        import httpx
        import threading
        from api.app import create_app
        from api.runner import EvolutionRunner
        from core.paper_trading.runtime.scheduler import PaperScheduler

        # 测试独立性隔离（design v2.4 形制，零生产代码）：本测试仅测
        # REST 分页对拍（两路由 Depends→repository 依赖链），与被测面
        # 无关的后台组件不启动。类级 setattr 遮蔽基类方法——
        # EvolutionRunner 无 start/join override（走 threading.Thread
        # 原生），join 必须同 patch：start no-op 后未启动线程在 lifespan
        # 关停段（api/app.py runner.join(timeout=5.0)）join 抛
        # RuntimeError 经 __aexit__ 传播致 error；PaperScheduler 自定义
        # join 有 _thread None 守卫，仅 patch start 即安全。不隔离面：
        # startup_recover/recover_stale_tasks/restart_crashed_tasks 为
        # lifespan 同步调用先于 POST；executor watchdog 写库面=
        # analysis_tasks 超时回写（空库无在途任务不触发）；
        # realtime.capture_loop 无写库通道（V-2a 核验）。
        monkeypatch.setattr(EvolutionRunner, "start", lambda self: None)
        monkeypatch.setattr(
            EvolutionRunner, "join",
            lambda self, timeout=None: None)
        monkeypatch.setattr(PaperScheduler, "start", lambda self: None)

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
                # 隔离生效自证（design v2.4 契约级）：lifespan 活跃窗口
                # （进入+POST+两次 GET 全程）内抓线程名清单——断言措辞
                # 用线程名字符串（Thread.name），防「patch 方法在但不被
                # lifespan 实际调用」的假隔离；任务级线程
                # （paper-recovered-{task_id} 等，lifespan 同步 recover
                # 创建，空库零任务不产生）不在断言面。
                names_during_lifespan = [t.name for t in threading.enumerate()]
            return with_page, without, names_during_lifespan

        with_page, without, names_during_lifespan = asyncio.run(_run())
        assert "evolution-runner" not in names_during_lifespan, (
            "隔离未生效：evolution-runner 线程仍在（假隔离——patch "
            f"方法未被 lifespan 实际调用）: {names_during_lifespan}")
        assert "paper-scheduler" not in names_during_lifespan, (
            "隔离未生效：paper-scheduler 线程仍在（假隔离——patch "
            f"方法未被 lifespan 实际调用）: {names_during_lifespan}")
        assert with_page == without, (
            "REST 层复现——按升级路径走变更传播\n"
            f"[F1 逐键 diff] {_key_diff(with_page, without)}")
        assert without.get("items"), "前置：日志非空"


class TestPageOneParityMcp:
    """S4c：MCP 工具层（FastMCP 注册面）参数透传等价。

    工具层职责=参数组装透传（page 恒发显式键，缺省值=1）；显式
    page=1 与缺省产生的请求 params 必须逐键一致——这是「MCP 层
    page=1≠无参」矛盾在工具层的唯一可存在点。
    """

    def test_s4c_mcp_tool_params_identical(self):
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


class TestPageOneParityFastmcpLive:
    """S4c 补全：真实 FastMCP 调用面（设计卡 2.4 第三层原义）——
    live uvicorn 服务 + build_server + call_tool 全链路（同
    test_contract_parity.TestListFolding 形制）。直击 team-lead
    2026-10-15 MCP 层实测矛盾记录（page=1→0 条/无参→50 条，服务为
    最新代码）的复现场景：本层通过则矛盾记录归因环境（如旧服务实例
    劫持）；复现则按升级路径走变更传播。
    """

    @pytest.fixture(scope="class")
    def live_server(self, tmp_path_factory):
        import threading
        import time as _time

        import uvicorn

        from api.app import create_app

        db = tmp_path_factory.mktemp("parity_mcp") / "quant.db"
        data_dir = tmp_path_factory.mktemp("parity_mcp_data")
        # 登记面校验 symbol 数据在场——造最小 BTCUSDT_4h parquet
        # （同 tests/conftest.py tmp_data_dir 形制）
        import pandas as pd

        pd.DataFrame(
            {"open": [60000], "high": [61000], "low": [59000],
             "close": [60500], "volume": [100]},
            index=pd.DatetimeIndex(["2024-01-01"], name="timestamp"),
        ).to_parquet(data_dir / "BTCUSDT_4h.parquet")
        app = create_app(db_path=db, data_dir=data_dir)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = _time.monotonic() + 15
        while _time.monotonic() < deadline:
            if server.started:
                break
            _time.sleep(0.1)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
        server.should_exit = True
        thread.join(timeout=5)

    def test_s4c_fastmcp_live_page1_equals_default(self, live_server):
        import json

        import httpx

        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        # 造 3 条研究日志（经真实 HTTP 登记面）
        for i in range(3):
            resp = httpx.post(f"{live_server}/api/research/hypotheses", json={
                "conditions": _CONDITIONS,
                "statement": f"MCP page 校验 {i}",
                "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                                 "start": f"2024-01-0{i + 1}T00:00:00Z",
                                 "end": "2024-03-01T00:00:00Z"}]})
            assert resp.status_code == 200, resp.text

        mcp, _ = build_server(profile="readonly", base_url=live_server)

        def _call(**kwargs):
            result = run_async(mcp.call_tool("get_research_log", kwargs))
            text = result.content[0].text if hasattr(
                result.content[0], "text") else result.content[0]
            return json.loads(text)

        with_page = _call(page=1)
        without = _call()
        assert without.get("items"), "前置：日志非空（否则断言空对空无意义）"
        assert with_page == without, \
            f"FastMCP 调用面复现 page=1≠无参——按升级路径走变更传播"
