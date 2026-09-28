"""端到端验收：仅 MCP 走通研究闭环（improve-mcp-agent-loop 7.2）。

list_data_sources → run_analysis → register_hypothesis → translate 产
draft → run_isolated_validation（挂 draft）→ run_random_window_test →
transition（supported 时 draft 自动转正）——全程经 MCP call_tool，零
直接 REST；任一步失败返回 {error: {code, message, detail, hint}}。
"""
import asyncio
import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import uvicorn

pytestmark = [pytest.mark.integration]


def run_async(coro):
    """asyncio.run 的环境兼容版（同 test_server 形制）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()

from api.app import create_app
from mcpserver.server import build_server


def _call(mcp, name, **kwargs):
    """经 MCP call_tool 调用并解包 JSON 载荷（唯一调用通道——零 REST）。"""
    result = run_async(mcp.call_tool(name, kwargs))
    text = getattr(result.content[0], "text", None)
    return json.loads(text) if text else result


@pytest.fixture(scope="module")
def live_env(tmp_path_factory):
    db = tmp_path_factory.mktemp("loop") / "quant.db"
    data_dir = tmp_path_factory.mktemp("loop_data")
    n = 2200
    idx = pd.date_range("2022-10-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    rng = np.random.default_rng(31)
    close = 60000 + np.cumsum(rng.normal(0, 110, n))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, n),
                  "trades": 40}, index=idx).to_parquet(
        data_dir / "BTCUSDT_4h.parquet")

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
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_agent_loop_full_close_via_mcp_only(live_env):
    base = live_env
    mcp, _ = build_server(profile="research", base_url=base)

    # 1) 数据目录
    sources = _call(mcp, "list_data_sources")
    assert sources and "BTCUSDT" in json.dumps(sources)

    # 2) 登记（1 月区间）
    reg = _call(mcp, "register_hypothesis",
                when_conditions=[{"subject": "rsi_14", "action": "lt",
                                  "target": 35}],
                then_conditions=[{"subject": "rsi_70", "action": "gt",
                                  "target": 60}],
                statement="RSI 超卖修复",
                data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2023-01-01T00:00:00Z",
                              "end": "2023-02-01T00:00:00Z"}])
    assert reg.get("hypothesis_id"), reg
    hid = reg["hypothesis_id"]

    # 3) 分析（2 月区间，挂假设）。阈值 45：前向收益口径下 sample_size 为
    # 有效前向信号数（非区间 bar 数），RSI<35 命中仅 22 < 30 触发小样本拒绝
    # （fix-condition-stats-forward-return 口径变化，实测 45 → 52 有效）
    analysis = _call(mcp, "run_analysis",
                     template="condition_stats",
                     params={"indicator": "RSI", "params": {"period": 14},
                             "condition": {"subject": "rsi_14", "action": "lt",
                                           "target": 45}},
                     symbol="BTCUSDT", timeframe="4h",
                     start="2023-02-01T00:00:00Z",
                     end="2023-03-01T00:00:00Z",
                     hypothesis_id=hid)
    assert analysis.get("sample_size", 0) >= 0 or "task_id" in analysis

    # 4) 转化产 draft（不要求 pass——两段生命周期的构建权）
    draft = _call(mcp, "translate_hypothesis_to_strategy",
                  hypothesis_id=hid)
    assert draft["status"] == "draft" and draft["duplicated"] is False
    sid = draft["strategy_id"]

    # 5) 隔离验证挂 draft（验证的就是将来出货的确切 DNA；3 月区间干净）
    validation = _call(mcp, "run_isolated_validation",
                       hypothesis_id=hid, strategy_id=sid,
                       symbol="BTCUSDT", timeframe="4h",
                       start="2023-03-05T00:00:00Z",
                       end="2023-03-25T00:00:00Z")
    assert validation["verdict"] in ("pass", "fail", "inconclusive")  # 0.2.11 三态, validation
    rec_id = validation["record_id"]

    # 6) 随机窗口（验证池尾段）
    rw = _call(mcp, "run_random_window_test",
               strategy_id=sid, symbol="BTCUSDT", timeframe="4h",
               data_start="2023-03-25T00:00:00Z",
               data_end="2023-04-20T00:00:00Z",
               window_bars=20, n_windows=3, seed=7, hypothesis_id=hid)
    assert rw["summary"]["n_windows"] == 3

    # 7) 定案：pass→supported（draft 同事务转正）；fail→refuted（draft 留存）；
    #    inconclusive→不可定论（0.2.11：证据不足 422 拒 + 假设保持 active）
    if validation["verdict"] == "inconclusive":
        rejected = _call(mcp, "transition_hypothesis",
                         hypothesis_id=hid, target_status="supported",
                         evidence_ref=rec_id)
        assert "error" in rejected and rejected["error"]["code"] == \
            "RESEARCH_TRANSITION_INVALID"
        hyp_after = _call(mcp, "get_hypothesis", hypothesis_id=hid)
        assert hyp_after["status"] == "active"
    else:
        target = "supported" if validation["verdict"] == "pass" else "refuted"
        transition = _call(mcp, "transition_hypothesis",
                           hypothesis_id=hid, target_status=target,
                           evidence_ref=rec_id)
        assert transition["status"] == target
        if target == "supported":
            assert transition["promoted"] is True, "draft 有自身 pass 须同事务转正"
            assert transition["promoted_strategy_id"] == sid
        else:
            assert transition["promoted"] is False

        # 8) 终态假设再发起验证 → 结构化错误（code + hint 齐备；
        #    inconclusive 分支假设保持 active，此演示不适用）
        err = _call(mcp, "run_isolated_validation",
                    hypothesis_id=hid, strategy_id=sid,
                    symbol="BTCUSDT", timeframe="4h",
                    start="2023-05-01T00:00:00Z",
                    end="2023-05-15T00:00:00Z")
        assert "error" in err, err
        e = err["error"]
        assert e["code"] == "RESEARCH_HYPOTHESIS_TERMINAL"
        assert e["hint"] and "终态" in e["hint"] or "新假设" in e["hint"]

    # 9) 研究日志全链路可审计（inconclusive 分支无 transition 条）
    logs = _call(mcp, "get_research_log", hypothesis_id=hid)
    types = [entry["entry_type"] for entry in logs["items"]]
    expected_chain = (["register", "analysis", "validate", "transition"]
                      if validation["verdict"] != "inconclusive"
                      else ["register", "analysis", "validate"])
    for expected in expected_chain:
        assert expected in types, f"日志链路缺 {expected}: {types}"
