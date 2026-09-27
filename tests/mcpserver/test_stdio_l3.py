"""MCP stdio L3 全链（harden-research-test-coverage 3.6）。

L3 形制（TESTING.md）：真实子进程 + 真实 stdio 协议握手 + 真实 API 服务，
从公开协议面（工具调用/resource 读取）证明完成——不用进程内装配冒充。
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
    db = tmp_path_factory.mktemp("l3") / "quant.db"
    data_dir = tmp_path_factory.mktemp("l3_data")
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    rng = np.random.default_rng(1)
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


def test_stdio_full_chain(live_api):
    """真进程 stdio：握手 → 工具列表 → 工具调用（读+写）→ resource 读取。"""
    import json
    import os

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {
        "MYQUANT_MCP_PROFILE": "research",
        "MYQUANT_API_URL": live_api,
        # 默认 transport 已切 streamable-http——stdio 为备选形态，显式声明
        "MYQUANT_MCP_TRANSPORT": "stdio",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    params = StdioServerParameters(
        command="venv/bin/python", args=["-m", "mcpserver.server"], env=env)

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # 1) 工具列表（精确集合对照——期望单处权威 _expected_tools）
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert names == EXPECTED_RESEARCH_TOOLS, (
                    f"研究档工具集失联: 缺 "
                    f"{sorted(EXPECTED_RESEARCH_TOOLS - names)} / "
                    f"多 {sorted(names - EXPECTED_RESEARCH_TOOLS)}")
                assert "register_hypothesis" in names
                assert "translate_hypothesis_to_strategy" in names

                # 2) 工具调用（真调用真服务）：指标元数据 47 项
                # （SDK 2.x 把 list 结果逐元素分段——每段一个对象）
                res = await session.call_tool("list_indicators", {})
                payload = [json.loads(c.text) for c in res.content]
                assert len(payload) == 47
                assert payload[0]["name"]  # 元素结构完整

                # 3) 写路径工具：登记假设（真落库）
                reg = await session.call_tool("register_hypothesis", {
                    "when_conditions": [{"subject": "rsi_14", "action": "lt",
                                         "target": 35}],
                    "then_conditions": [{"subject": "close", "action": "gt",
                                         "target": 0}],
                    "statement": "stdio L3 验证",
                    "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-01-01T00:00:00Z",
                                     "end": "2024-01-10T00:00:00Z"}],
                })
                reg_text = reg.content[0].text
                reg_payload = json.loads(reg_text)
                assert reg_payload["duplicated"] is False

                # 4) resource 读取：能力自述四部分
                caps = await session.read_resource("quant://capabilities")
                text = caps.contents[0].text
                for part in ("工具清单", "验证协议要点", "策略可表达边界", "假设状态机"):
                    assert part in text

    _run_async_compat(asyncio.wait_for(_run(), timeout=60))
