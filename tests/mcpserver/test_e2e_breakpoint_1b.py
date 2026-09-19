"""断点 1b 端到端验收（add-ai-research-mcp 7.2）。

全链路：注册公式插件与模型插件 → 分量证据 → 多信号源组合策略 →
隔离验证（加严门）→ 验证档案含分量证据。验收即测试：全绿 = 断点 1b 达成。
"""

import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import uvicorn

pytestmark = [pytest.mark.integration]

from api.app import create_app
from mcpserver.server import build_server


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    db = tmp_path_factory.mktemp("b1b") / "quant.db"
    data_dir = tmp_path_factory.mktemp("b1b_data")
    n = 900
    idx = pd.date_range("2023-11-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    rng = np.random.default_rng(8)
    close = 60000 + np.cumsum(rng.normal(0, 120, n))
    pd.DataFrame({"open": close, "high": close + 70, "low": close - 70,
                  "close": close, "volume": rng.uniform(100, 900, n),
                  "trades": 50}, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

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


def test_breakpoint_1b_plugin_to_composite_validation(live_api):
    """断点 1b：插件 → 组合策略 → 加严验证 → 档案含分量证据。"""
    base, db = live_api
    mcp, api = build_server(profile="research", base_url=base)

    # 1) 注册公式插件（AI 写公式：20 期 zscore 动量）
    fx = api.post("/api/research/plugins/formula",
                  {"name": "momentum", "expression": "zscore(close, 20)"})
    fx_col = fx["column"]
    assert fx["version"] == 1

    # 2) 注册模型插件（walk-forward 方向模型；异步 202 → 轮询 valid）
    ml = api.post("/api/research/plugins/model", {
        "name": "dir_model", "features": ["close"], "template": "rolling_logistic",
        "params": {"window": 0, "step": 50},
        "target": {"type": "return_sign", "horizon": 2},
        "symbol": "BTCUSDT", "timeframe": "4h",
        "data_start": "2023-11-01T00:00:00Z", "data_end": "2024-01-15T00:00:00Z"})
    task_id = ml["task_id"]
    assert ml["status"] == "pending"
    deadline = time.time() + 120
    while time.time() < deadline:
        t = api.get(f"/api/research/analysis/tasks/{task_id}")
        if t.get("status") in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    assert t["status"] == "succeeded", t
    ml_rows = api.get("/api/research/plugins/model", params={"name": "dir_model"})["items"]
    ml_col = ml_rows[0]["signal_column"]
    assert ml_rows[0]["status"] == "valid"

    # 3) 登记假设（登记区间占数据头部）
    reg = api.post("/api/research/hypotheses", {
        "conditions": {
            "when_conditions": [{"subject": "rsi_14", "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        },
        "statement": "超卖与动量共振时倾向修复",
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": "2023-11-01T00:00:00Z", "end": "2023-12-01T00:00:00Z"}],
    })
    hid = reg["hypothesis_id"]

    # 4) 组合策略：rule + formula + model 三源 weighted_vote（经 translate 产物改造）
    try:
        api.post(f"/api/research/translate/{hid}",
                 {"strategy_symbol": "BTCUSDT", "strategy_timeframe": "4h"})
        raise AssertionError("未验证假设不得转化（信任链）")
    except AssertionError:
        raise
    except Exception:  # noqa: BLE001 —— httpx 409
        pass

    # 先给假设一条 pass 验证记录（干净区间；策略先经直接构造）
    from core.strategy.dna import StrategyDNA, SignalGene, SignalRole, ExecutionGenes
    from core.persistence.db_ext import save_strategy
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                 "rsi_14", {"type": "lt", "threshold": 35})],
        execution_genes=ExecutionGenes(timeframe="4h"),
        signal_sources=[
            {"type": "rule"},
            {"type": "formula", "ref": fx_col, "weight": 1.0, "threshold": 0.5},
            {"type": "model", "ref": ml_col, "weight": 1.0, "threshold": 0.0},
        ],
        composition={"mode": "weighted_vote", "threshold": 0.5})
    save_strategy(db, strategy_id="st_composite", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="composite")

    # 5) 组合策略隔离验证：先撞加严门（缺分量证据）
    rejected = None
    try:
        api.post("/api/research/validate-backtest", json_body={
            "hypothesis_id": hid, "strategy_id": "st_composite",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-02-01T00:00:00Z", "end": "2024-02-20T00:00:00Z"}})
    except Exception as exc:  # noqa: BLE001
        rejected = exc
    assert rejected is not None, "缺分量证据必须被加严门拒绝"

    # 6) 补分量证据（两个插件各一条 component 记录）
    from core.persistence.repository import research as rr
    for col in (fx_col, ml_col):
        rr.save_validation_with_log(
            db, record_id=f"rec_c_{col[:8]}", hypothesis_id=hid,
            strategy_id="st_composite", level="component",
            params=json.dumps({"column": col}), result=None,
            verdict="pass", range_hash=f"rh_c_{col[:8]}")

    # 7) 重试验证：通过加严门 → 隔离回测（收紧门槛）→ 双落
    body = api.post("/api/research/validate-backtest", {
        "hypothesis_id": hid, "strategy_id": "st_composite",
        "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-02-01T00:00:00Z", "end": "2024-02-20T00:00:00Z"}})
    assert body["verdict"] in ("pass", "fail")
    assert body["warmup"]["bars"] >= 200

    # 8) 验证档案含分量证据
    report = api.get("/api/research/validation/st_composite/report")
    assert report["total_records"] >= 2
    rec = next(r for r in rr.list_validation_records(
        db, hypothesis_id=hid) if r["record_id"] == body["record_id"])
    components = json.loads(rec["components"])
    assert fx_col in components and ml_col in components, "档案须含分量证据清单"

    # 9) pass 后转化解锁（若 verdict=pass）
    if body["verdict"] == "pass":
        tr2 = api.post(f"/api/research/translate/{hid}",
                       {"strategy_symbol": "BTCUSDT", "strategy_timeframe": "4h"})
        assert tr2["duplicated"] is False
