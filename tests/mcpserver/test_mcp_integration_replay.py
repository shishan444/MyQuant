"""MCP 集成实测（fix-replay-gap-alignment 3.7——验收锚 1/3 的 MCP 端）。

隔离双进程真调用（backend-tester §三口径）：API+MCP server 各起隔离
实例（随机空闲端口+临时库+合成数据），MCP 客户端经 streamable-http
真调用两条：

- 锚 1①：基因级 4h 策略（exec 1h+gene tf 4h——缺陷 A 形制）经 MCP
  挂纸盘运行（不再 indicators_unavailable 拒绝）+经 MCP 单路回放
  跑通（不做跨时间驱动形制的成交对账）；
- 锚 3②：strategy_ids-only 独立发起（不传 strategy_id——2.4 修复
  面）真调用返回 results 列表，序=strategy_ids 序逐位对位（多元素
  乱序夹具使乱序可检出，禁 set 比较）。

环境旁路：FAPI_STREAM_DISABLED=1（纸盘任务不经实时 WS——合成数据
域）；实测后进程全回收。
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

from core.data.storage import save_parquet
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from tests.helpers.data_factory import make_dna, make_ohlcv
from tests.replay._env import make_gene_tf_dna

_VENV_PY = str(Path(__file__).resolve().parents[2] / "venv" / "bin" / "python")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def stack():
    """隔离 API+MCP 双进程栈（模块级共享——进程启动重，两用例复用）。"""
    tmp = Path(tempfile.mkdtemp(prefix="mcp_it_"))
    data_dir = tmp / "market"
    data_dir.mkdir()
    # Future time axis (test_domain_isolation._DomainHarness precedent):
    # synthetic data timestamp > real clock → feed reconcile "no gap" does not
    # trigger REST backfill (isolated dual-process stack does not hit external
    # Binance — this machine's IP is banned by REST (-1003), needs no network dependency)
    save_parquet(make_ohlcv(n=1200, freq="1h", seed=7, base_price=100.0,
                            start="2030-01-01"),
                 data_dir / "BTCUSDT_1h.parquet")
    save_parquet(make_ohlcv(n=800, freq="4h", seed=11, base_price=100.0,
                            start="2030-01-01"),
                 data_dir / "BTCUSDT_4h.parquet")
    db = tmp / "it.db"
    init_db_ext(db)
    # 基因级标注形制策略（锚 1 对象）+三路正常策略（锚 3 乱序夹具）
    save_strategy(db, strategy_id="st_gtf",
                  dna_json=make_gene_tf_dna().to_json(),
                  symbol="BTCUSDT", timeframe="1h", status="active")
    for i, sid in enumerate(("st_b1", "st_b2", "st_b3")):
        save_strategy(db, strategy_id=sid,
                      dna_json=make_dna(symbol="BTCUSDT",
                                        leverage=1 + i).to_json(),
                      symbol="BTCUSDT", timeframe="4h", status="draft")

    api_port, mcp_port = _free_port(), _free_port()
    env = {**os.environ,
           "FAPI_STREAM_DISABLED": "1",
           "MYQUANT_API_URL": f"http://127.0.0.1:{api_port}",
           "MYQUANT_MCP_PORT": str(mcp_port),
           "MYQUANT_MCP_PROFILE": "research"}
    api_log = open(tmp / "api.log", "w")
    api_proc = subprocess.Popen(
        [_VENV_PY, "-c",
         "from pathlib import Path\n"
         "import uvicorn\n"
         "from api.app import create_app\n"
         f"app = create_app(db_path=Path(r'{db}'), "
         f"data_dir=Path(r'{data_dir}'))\n"
         f"uvicorn.run(app, host='127.0.0.1', port={api_port}, "
         "log_level='warning')"],
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
        stdout=api_log, stderr=subprocess.STDOUT)
    mcp_proc = subprocess.Popen(
        [_VENV_PY, "-m", "mcpserver.server"],
        cwd=str(Path(__file__).resolve().parents[2]), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{api_port}"
    try:
        for _ in range(120):  # API 就绪探活（最长 ~60s）
            try:
                if httpx.get(f"{base}/api/health", timeout=1.0,
                             trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            api_log.flush()
            tail = Path(tmp / "api.log").read_text()[-1500:]
            raise RuntimeError(f"API 未就绪（日志尾:\n{tail})")
        yield {"base": base, "mcp": f"http://127.0.0.1:{mcp_port}/mcp",
               "db": db}
    finally:
        for proc in (mcp_proc, api_proc):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _call_tool(mcp_url: str, name: str, args: dict) -> dict:
    """streamable-http 真客户端调用（独立事件循环——pytest 同步域）。"""
    async def _run():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(mcp_url) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), 30)
                result = await asyncio.wait_for(
                    session.call_tool(name, args), 240)
                return result

    return asyncio.run(_run())


def _payload(result) -> dict:
    import json

    for item in result.content:
        if getattr(item, "type", "") == "text":
            return json.loads(item.text)
    raise AssertionError(f"无文本载荷: {result.content}")


class TestAnchor1GeneTfMcp:
    def test_gene_tf_mount_and_replay_via_mcp(self, stack):
        """锚 1：基因级 4h 策略 MCP 挂盘运行+单路回放跑通。"""
        # 挂盘：不再 indicators_unavailable 拒绝（缺陷 A 修复面）
        mounted = _payload(_call_tool(stack["mcp"], "mount_paper_task", {
            "strategy_id": "st_gtf", "symbol": "BTCUSDT",
            "window_bars": 5}))
        task_id = mounted.get("task_id")
        assert task_id, f"挂盘应建任务（实测 {mounted}）"
        # 上盘不被拒绝：任务行非 error（pending→running 由调度器拾取）
        for _ in range(60):
            row = httpx.get(f"{stack['base']}/api/paper-trading/tasks/"
                            f"{task_id}", timeout=5.0,
                            trust_env=False).json()
            if row["status"] in ("running", "completed"):
                break
            if row["status"] == "error":
                pytest.fail(
                    f"基因级标注形式挂盘被拒（indicators_unavailable 面）"
                    f": {row.get('error_detail')}")
            time.sleep(0.5)
        assert row["status"] in ("running", "completed"), (
            f"挂盘运行态（实测 {row['status']}——detail "
            f"{row.get('error_detail')}）")
        # 单路回放跑通（strategy_id 形态——现役主入口）
        df = make_ohlcv(n=1200, freq="1h", seed=7, base_price=100.0,
                        start="2030-01-01")
        start, end = df.index[-300].isoformat(), df.index[-1].isoformat()
        replayed = _payload(_call_tool(stack["mcp"], "run_replay_backtest", {
            "strategy_id": "st_gtf", "start": start, "end": end}))
        assert replayed.get("status") in ("completed", "stopped"), (
            f"基因级回放跑通（实测 {replayed.get('status')}）")


class TestAnchor3BatchOnlyMcp:
    def test_strategy_ids_only_ordered_results(self, stack):
        """锚 3：strategy_ids-only 独立发起（不传 strategy_id——2.4 修复
        面），results 序=strategy_ids 序逐位对位（乱序夹具）。"""
        df = make_ohlcv(n=600, freq="4h", seed=7, base_price=100.0,
                        start="2030-01-01")
        start, end = df.index[-200].isoformat(), df.index[-1].isoformat()
        order = ["st_b3", "st_b1", "st_b2"]  # 乱序夹具（非字典序）
        out = _payload(_call_tool(stack["mcp"], "run_replay_backtest", {
            "strategy_ids": order, "start": start, "end": end}))
        results = out.get("results")
        if results is None:
            pytest.fail(f"原始载荷键集 {list(out)}: {str(out)[:600]}")
        assert isinstance(results, list) and len(results) == 3, (
            f"并发组三路 results（实测 {type(results)}）")
        assert [r["strategy_id"] for r in results] == order, (
            "results 序=strategy_ids 序（逐位对位——乱序收集必红）")
        for r in results:
            assert r.get("status") in ("completed", "stopped")
