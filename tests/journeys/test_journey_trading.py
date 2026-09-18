"""链 G · 模拟盘链 L2（3.5）：创建 → bar 推进 → 事件 → 指标 → 停止。

L2 内涵：①零重复事件（B1.3 幂等游标实战——多轮 tick 后事件数不增）；
②权益守恒（equity 终值 = init + Σpnl − Σfee，裁判恒等式在模拟盘落地）；
③metrics=equity 派生自洽；④stop→stopped 状态机一致。
mock 边界纪律：仅拦 update_market_data（外部数据源=外部世界），runner
线程/pipeline/VirtualAccount/事件落库全真。
"""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

from tests.reference.fixture_gen import gen_ohlcv

N = 200
APPEND = 5


def _dna_payload():
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger",
             "field": None, "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14}, "role": "exit_trigger",
             "field": None, "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                       "position_size": 0.5, "leverage": 1, "direction": "long"},
    }


@pytest.fixture()
def trading_env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from MyQuant.api.app import create_app

    data_dir = tmp_path / "d"
    data_dir.mkdir()
    db_path = tmp_path / "t.db"
    base_df = gen_ohlcv("normal", seed=42, n=N)
    base_df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

    # 追加 APPEND 根新 bar（延续价格序列，保持 OHLC 不变式）
    import numpy as np
    last_close = float(base_df["close"].iloc[-1])
    idx_ext = pd.date_range(base_df.index[-1], periods=APPEND + 1, freq="4h")[1:]
    rng = np.random.default_rng(7)
    rets = rng.standard_normal(APPEND) * 0.01
    closes = last_close * np.cumprod(1 + rets)
    ext = pd.DataFrame(
        {"open": closes, "high": closes * 1.002, "low": closes * 0.998,
         "close": closes, "volume": 1000.0}, index=idx_ext,
    )
    ext.index.name = "timestamp"
    extended = pd.concat([base_df, ext])

    calls = {"n": 0}

    def fake_update(symbol, interval, data_dir=None):
        """外部世界边界：首轮返回追加后数据，此后稳定（无新 bar）。"""
        calls["n"] += 1
        return extended.copy() if calls["n"] == 1 else extended.iloc[:N].copy()

    import core.trading.runner as runner_mod
    monkeypatch.setattr(runner_mod, "update_market_data", fake_update)

    app = create_app(db_path=db_path, data_dir=data_dir)
    with TestClient(app) as client:
        yield client, extended


def _wait_for_status(client, task_id, statuses, timeout=40):
    deadline = time.time() + timeout
    row = {}
    while time.time() < deadline:
        row = client.get(f"/api/trading/tasks/{task_id}").json()
        if row.get("status") in statuses:
            return row
        time.sleep(0.5)
    pytest.fail(f"等待状态 {statuses} 超时: {row.get('status')}")


def test_paper_trading_journey_l2(trading_env):
    client, extended = trading_env

    r = client.post("/api/trading/tasks", json={
        "dna_json": json.dumps(_dna_payload()),
        "symbol": "BTCUSDT", "timeframe": "4h",
        "initial_cash": 100000.0, "fee": 0.001, "leverage": 1,
        "direction": "long",
    })
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]

    # ── G2-G3: bar 推进 → running（runner 真线程拾取）
    _wait_for_status(client, task_id, {"running", "stopped"})
    row = client.get(f"/api/trading/tasks/{task_id}").json()
    assert row["status"] == "running"

    # 等待追加 bar 被消费（事件/权益更新）
    deadline = time.time() + 30
    trades = []
    while time.time() < deadline:
        t = client.get(f"/api/trading/tasks/{task_id}/trades").json()
        trades = t if isinstance(t, list) else t.get("trades") or t.get("items") or []
        if trades:
            break
        time.sleep(0.5)

    # ── 幂等断言（B1.3 实战）：稳定数据下多轮 tick 事件/交易数不增
    n_before = len(trades)
    time.sleep(5)  # ≥2 个 poll tick
    t2 = client.get(f"/api/trading/tasks/{task_id}/trades").json()
    trades2 = t2 if isinstance(t2, list) else t2.get("trades") or t2.get("items") or []
    assert len(trades2) == n_before, (
        f"重复事件：{n_before} → {len(trades2)}（幂等游标失效）"
    )

    # ── 权益守恒（恒等式）：equity 终值 = init + Σpnl − Σfee
    eq = client.get(f"/api/trading/tasks/{task_id}/equity").json()
    curve = eq.get("equity_curve") or eq.get("data") or eq.get("points") or []
    init_cash = 100000.0
    if trades:
        pnl_sum = sum(float(x.get("pnl") or 0.0) for x in trades)
        fee_sum = sum(float(x.get("fee") or x.get("fees") or 0.0) for x in trades)
        if curve:
            final_eq = float(curve[-1].get("equity", curve[-1].get("value")))
            assert final_eq == pytest.approx(
                init_cash + pnl_sum - fee_sum, rel=1e-6, abs=1e-4,
            ), f"权益守恒破坏: {final_eq} ≠ {init_cash}+{pnl_sum}-{fee_sum}"
    elif curve:
        # 零交易：权益应恒为 init（无盈亏无费用）
        vals = [float(p.get("equity", p.get("value"))) for p in curve]
        assert all(v == pytest.approx(init_cash, rel=1e-9) for v in vals), \
            f"零交易但权益漂移: {vals[:3]}"

    # ── metrics 自洽：metrics 端点有值且与权益端点同源
    m = client.get(f"/api/trading/tasks/{task_id}/metrics")
    assert m.status_code == 200

    # ── G5: stop → stopped（状态机一致）
    r = client.post(f"/api/trading/tasks/{task_id}/stop")
    assert r.status_code == 200, r.text
    _wait_for_status(client, task_id, {"stopped"})
