"""研究链读库 DNA 校验红锚·MCP e2e 层（signal-sources-structure-gate 0.3）。

契约出处（design 卡 R+A-4 两入口层之 MCP 工具层）：MCP 工具经
api.post 走 HTTP 到 research 路由（与 REST 直调同一执行链）——库内
策略 DNA 畸形 signal_sources 时工具错误载荷携带
{"error": {"code": "RESEARCH_DNA_INVALID", "message": …,
"detail": {"strategy_id", "errors"}, "hint": 注册值}}。

红锚语义：卡 R 挂载（含 HINTS 注册）前为红——现状拼错型带绿灯
跑完返回 metrics 载荷（无 error 键）、HINTS 未注册（断言 KeyError
表现）。形制沿 test_e2e_agent_loop.py（live_env 起真服务+唯一
MCP call_tool 通道，零 REST）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest
import uvicorn

pytestmark = [pytest.mark.integration]

from api.app import create_app
from mcpserver.errors import HINTS
from mcpserver.server import build_server


def run_async(coro):
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _call(mcp, name, **kwargs):
    """经 MCP call_tool 调用并解包 JSON 载荷（唯一调用通道——零 REST）。"""
    result = run_async(mcp.call_tool(name, kwargs))
    text = getattr(result.content[0], "text", None)
    return json.loads(text) if text else result


@pytest.fixture(scope="module")
def live_env(tmp_path_factory):
    db = tmp_path_factory.mktemp("dnagate") / "quant.db"
    data_dir = tmp_path_factory.mktemp("dnagate_data")
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    rng = np.random.default_rng(11)
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

    from core.persistence.db_ext import init_db_ext
    init_db_ext(db)

    app = create_app(db_path=db, data_dir=data_dir)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", db
    server.should_exit = True
    thread.join(timeout=5)


def test_mcp_run_backtest_rejects_malformed_dna(live_env):
    """MCP run_backtest（沙盒回测工具）消费畸形行 → 错误载荷四件
    齐：code=RESEARCH_DNA_INVALID+detail.errors 点名 signal_sources
    +hint==HINTS 注册值（静态守护 test_registry_covers_all_raised_
    codes 的 e2e 对拍半面）。"""
    base, db = live_env
    mcp, _ = build_server(profile="research", base_url=base)

    from core.persistence.repository.strategy import save_strategy
    from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA
    dna = StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi", {"type": "cross_above", "threshold": 50.0}),
        ],
        signal_sources=[{"type": "fomula"}],  # 拼错型——现状静默失真
        risk_genes=RiskGenes(),
    )
    save_strategy(db, strategy_id="st_e2e_dnagate", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="e2e dnagate")

    payload = _call(mcp, "run_backtest", strategy_id="st_e2e_dnagate",
                    symbol="BTCUSDT", timeframe="4h",
                    start="2024-02-05", end="2024-02-25")

    assert "error" in payload, (
        f"现状载荷无 error 键（拼错型静默失真——判据/挂载未生效）："
        f"{json.dumps(payload, ensure_ascii=False)[:300]}")
    err = payload["error"]
    assert err["code"] == "RESEARCH_DNA_INVALID", err
    assert any("signal_sources" in e for e in err["detail"]["errors"]), (
        err["detail"])
    assert err["detail"]["strategy_id"] == "st_e2e_dnagate"
    assert err["hint"] == HINTS["RESEARCH_DNA_INVALID"], (
        f"hint 非注册值：{err['hint']!r}")
