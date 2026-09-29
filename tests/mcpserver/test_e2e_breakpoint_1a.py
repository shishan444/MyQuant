"""断点 1a 端到端验收（add-ai-research-mcp 4.6）。

经 MCP client（模拟 AI 调用方式）完成研究全链路：
登记假设 → 分析 → 干净区间隔离验证 → 随机窗口 → 验证档案 → 状态迁移。
验收即测试：全绿 = 断点 1a 达成。
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


def run_async(coro):
    """asyncio.run 的环境兼容版。

    playwright e2e fixture 会在进程内泄漏运行中的事件循环，此后
    asyncio.run 抛 RuntimeError——检测到活动 loop 时改在新线程跑。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()

from api.app import create_app
from mcpserver.api_client import QuantApiClient
from mcpserver.server import build_server


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    db = tmp_path_factory.mktemp("b1a") / "quant.db"
    data_dir = tmp_path_factory.mktemp("b1a_data")
    n = 600
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    rng = np.random.default_rng(21)
    close = 60000 + np.cumsum(rng.normal(0, 130, n))
    pd.DataFrame({"open": close, "high": close + 70, "low": close - 70,
                  "close": close, "volume": rng.uniform(100, 900, n),
                  "trades": 50}, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

    # 预置一个可回测策略（RSI 入场）
    from core.persistence.db_ext import init_db_ext, save_strategy
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)
    init_db_ext(db)
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                 "rsi_14", {"type": "lt", "threshold": 35})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_e2e", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="e2e")

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
    yield f"http://127.0.0.1:{port}", db
    server.should_exit = True
    thread.join(timeout=5)


def test_breakpoint_1a_full_research_loop(live_api):
    """全链路：登记→分析→隔离验证→随机窗口→档案→迁移。"""
    base, db = live_api
    # 研究档装配（与宿主一致的全链路路径）
    mcp, api = build_server(profile="research", base_url=base)
    tool_names = {t.name for t in run_async(mcp.list_tools())}
    assert "register_hypothesis" in tool_names

    # 1) 读能力自述（AI 第一步：知道自己能做什么、边界在哪）
    res = run_async(mcp.read_resource("quant://capabilities"))
    text = res[0].content if isinstance(res, list) else str(res)
    assert "隔离纪律" in text

    # 2) 查可用信号列（subject 校验前置）
    signals = api.get("/api/research/signals")
    assert any(s["column_key"] == "RSI" for s in signals)

    # 3) 登记假设（声明 1 月区间）
    reg = api.post("/api/research/hypotheses", {
        "conditions": {
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        },
        "statement": "RSI 超卖后价格倾向修复",
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}],
    })
    assert reg["duplicated"] is False
    hid = reg["hypothesis_id"]

    # 4) 分析（同假设关联：2 月区间——登记区间外；U3 起起点让出 RSI 的
    #    200-bar warmup 段=2024-02-05（bar 210），end 保持 03-01 不变——
    #    不得侵入步骤 6 的 3 月干净验证区间）
    analysis = api.post("/api/research/analysis", {
        "template": "condition_stats",
        "params": {"indicator": "RSI", "params": {"period": 14},
                   "condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}},
        "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                       "start": "2024-02-05T00:00:00Z", "end": "2024-03-01T00:00:00Z"},
        "hypothesis_id": hid,
    })
    assert analysis["sample_size"] >= 30

    # 5) 隔离验证：2 月区间已被分析日志占用 → 必须被拒（错误码级断言防假绿）
    from mcpserver.api_client import ApiError
    try:
        api.post("/api/research/validate-backtest", {
            "hypothesis_id": hid, "strategy_id": "st_e2e",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-02-10T00:00:00Z", "end": "2024-02-25T00:00:00Z"}})
        raise AssertionError("与已分析区间重叠的验证必须被拒")
    except ApiError as exc:
        assert exc.status == 409, f"须为重叠拒绝，实得 {exc.status}: {exc.message}"
        assert exc.code == "RESEARCH_RANGE_OVERLAP"

    # 6) 干净区间隔离验证（3 月起，warmup 足够）
    validation = api.post("/api/research/validate-backtest", {
        "hypothesis_id": hid, "strategy_id": "st_e2e",
        "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-03-01T00:00:00Z", "end": "2024-03-25T00:00:00Z"}})
    assert validation["verdict"] in ("pass", "fail", "inconclusive")  # 0.2.11 三态
    assert validation["warmup"]["bars"] >= 200
    rec_id = validation["record_id"]

    # 7) 随机窗口抽查（4 月池）
    rw = api.post("/api/research/random-window-test", {
        "strategy_id": "st_e2e", "symbol": "BTCUSDT", "timeframe": "4h",
        "data_start": "2024-03-25T00:00:00Z", "data_end": "2024-04-05T00:00:00Z",
        "window_bars": 15, "n_windows": 3, "seed": 11, "hypothesis_id": hid})
    assert rw["summary"]["n_windows"] == 3

    # 8) 验证档案（人话摘要）
    report = api.get("/api/research/validation/st_e2e/report")
    assert report["total_records"] >= 2
    assert any("收益" in e["人话"] for e in report["isolated_backtests"])

    # 9) 状态迁移（引用隔离验证记录——0.2.11 三态：inconclusive 证据
    #    不可定论须被 422 拒且假设保持 active；pass/fail 照常迁移）
    if validation["verdict"] == "inconclusive":
        try:
            api.post(f"/api/research/hypotheses/{hid}/transition", {
                "target_status": "supported", "evidence_ref": rec_id})
            raise AssertionError("inconclusive 证据必须被拒（证据不足不可定论）")
        except ApiError as exc:
            assert exc.status == 422, f"须 422，实得 {exc.status}: {exc.message}"
        # 假设保持 active（0.2.11 验收③——可换区间继续验证）
        hyp_after = api.get(f"/api/research/hypotheses/{hid}")
        assert hyp_after["status"] == "active"
    else:
        transition = api.post(f"/api/research/hypotheses/{hid}/transition", {
            "target_status": "refuted" if validation["verdict"] == "fail" else "supported",
            "evidence_ref": rec_id})
        assert transition["status"] in ("supported", "refuted")

    # 10) 研究日志全链路可审计（inconclusive 分支无迁移故无 transition 条）
    logs = api.get("/api/research/log", params={"hypothesis_id": hid})
    types = [e["entry_type"] for e in logs["items"]]
    expected_chain = (["register", "analysis", "validate", "transition"]
                      if validation["verdict"] != "inconclusive"
                      else ["register", "analysis", "validate"])
    for expected in expected_chain:
        assert expected in types, f"日志链路缺 {expected}: {types}"
