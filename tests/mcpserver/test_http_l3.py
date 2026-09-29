"""MCP streamable-http L3 全链（switch-mcp-http-transport 3.1）。

L3 形制：真实 server 子进程（HTTP transport，随机端口）+ 真实 API 服务 +
streamable_http_client 协议握手——从公开 HTTP 协议面证明完成。
"""

import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import uvicorn

pytestmark = [pytest.mark.integration]


def _run_async_compat(coro):
    """asyncio.run 的环境兼容版（同 test_server 形制——playwright e2e
    泄漏活动 loop 时全量顺序下 asyncio.run 抛 RuntimeError）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()

from api.app import create_app

from _expected_tools import EXPECTED_RESEARCH_TOOLS  # 同目录（pytest prepend）


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    db = tmp_path_factory.mktemp("http_l3") / "quant.db"
    data_dir = tmp_path_factory.mktemp("http_l3_data")
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    rng = np.random.default_rng(4)
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    pd.DataFrame({"open": close, "high": close + 50, "low": close - 50,
                  "close": close, "volume": rng.uniform(100, 800, n),
                  "trades": 10}, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

    app = create_app(db_path=db, data_dir=data_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def mcp_http_server(live_api):
    """真 server 子进程（streamable-http，随机端口）。"""
    import os
    import socket
    import subprocess

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {
        **{k: v for k, v in os.environ.items() if k == "PATH"},
        "MYQUANT_MCP_PROFILE": "research",
        "MYQUANT_API_URL": live_api,
        "MYQUANT_MCP_PORT": str(port),
    }
    proc = subprocess.Popen(
        ["venv/bin/python", "-m", "mcpserver.server"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 健康等待：端口就绪
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with socket.socket() as s2:
            s2.settimeout(0.5)
            if s2.connect_ex(("127.0.0.1", port)) == 0:
                break
        if proc.poll() is not None:
            pytest.fail("MCP HTTP server 子进程提前退出")
        time.sleep(0.3)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def test_http_transport_full_chain(mcp_http_server):
    """HTTP 全链：握手 → 22 工具 → 调用（读+写）→ resource 四部分。"""
    import json

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def _run():
        async with streamable_http_client(mcp_http_server) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert names == EXPECTED_RESEARCH_TOOLS, (
                    f"研究档工具集失联: 缺 "
                    f"{sorted(EXPECTED_RESEARCH_TOOLS - names)} / "
                    f"多 {sorted(names - EXPECTED_RESEARCH_TOOLS)}")

                res = await session.call_tool("list_indicators", {})
                payload = [json.loads(c.text) for c in res.content]
                assert len(payload) == 47

                reg = await session.call_tool("register_hypothesis", {
                    "when_conditions": [{{"indicator": "RSI", "params": {"period": 14}}, "action": "lt",
                                         "target": 35}],
                    "then_conditions": [{"subject": "close", "action": "gt",
                                         "target": 0}],
                    "statement": "http L3 验证",
                    "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-01-01T00:00:00Z",
                                     "end": "2024-01-10T00:00:00Z"}],
                })
                reg_payload = json.loads(reg.content[0].text)
                assert reg_payload["duplicated"] is False

                caps = await session.read_resource("quant://capabilities")
                text = caps.contents[0].text
                for part in ("工具清单", "验证协议要点", "策略可表达边界", "假设状态机"):
                    assert part in text
                # 接入形态说明（本变更新增）
                assert "streamable HTTP" in text

    _run_async_compat(asyncio.wait_for(_run(), timeout=60))


def test_api_unreachable_structured_error(tmp_path):
    """API 不在时工具调用返回结构化可达性错误（不裸抛连接异常）。"""
    from mcpserver.api_client import ApiUnreachable, QuantApiClient

    api = QuantApiClient(base_url="http://127.0.0.1:1", profile="research")
    with pytest.raises(ApiUnreachable, match="API 不可达"):
        api.get("/api/research/indicators")
