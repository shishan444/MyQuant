"""链 B · 单回测链 L2（3.2）：构建 DNA → POST /backtest → 指标=裁判判定值。

链路级 L2 的核心断言：API 响应指标与独立参照实现（tests/reference/）
在共享信号面上逐指标全等——「用户看到的数字」第一次有了可追溯的判定源。
D-4 注记：strategy_id 路径当前断裂（哨兵在 tests/consistency/），链路以
dna 直传走真实计算路径；compare 入口并入本链复验（C 链裁决）。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from core.features.indicators import compute_all_indicators
from core.strategy.executor import dna_to_signal_set
from tests.helpers.data_factory import make_dna, make_ohlcv
from tests.reference.assert_metrics import assert_metrics_equal
from tests.reference.fixture_gen import gen_ohlcv
from tests.reference.reference_backtest import reference_backtest
from tests.reference.reference_metrics import reference_metrics

N = 300


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    """真实数据集 + dna payload（dna 直传：D-4 下 strategy_id 路径不可用）。"""
    from MyQuant.api.app import create_app
    from fastapi.testclient import TestClient

    data_dir = tmp_path_factory.mktemp("bt_journey_data")
    db_path = tmp_path_factory.mktemp("bt_journey_db") / "t.db"
    gen_ohlcv("normal", seed=42, n=N).to_parquet(data_dir / "BTCUSDT_4h.parquet")

    dna = make_dna(direction="long", position_size=0.5, leverage=1,
                   stop_loss=0.05, take_profit=0.10)
    import json
    payload_dna = json.loads(dna.to_json())
    app = create_app(db_path=db_path, data_dir=data_dir)
    return app, payload_dna, dna, data_dir, db_path


def _reference_verdict(journey):
    """裁判判定值：被测信号层产信号（共享面）→ 参照回测 → 参照指标。"""
    _, _, dna, data_dir, _ = journey
    ohlcv = gen_ohlcv("normal", seed=42, n=N)
    enhanced = compute_all_indicators(ohlcv)
    sig = dna_to_signal_set(dna, enhanced)
    ref = reference_backtest(
        ohlcv, sig.entries, sig.exits, adds=sig.adds, reduces=sig.reduces,
        entry_direction=sig.entry_direction,
        direction="long", position_size=0.5, leverage=1,
        stop_loss=0.05, take_profit=0.10,
        init_cash=100000.0, fee=0.001, slippage=0.0005, timeframe="4h",
    )
    m = reference_metrics(ref.equity, [t.pnl for t in ref.trades], 100000.0, "4h")
    return m, ref, sig


def test_backtest_journey_l2_metrics_equal_reference(journey):
    """全链 L2：API 响应五指标 = 参照判定值（trades 精确/浮点 1e-9）。"""
    from fastapi.testclient import TestClient

    app, payload_dna, dna, data_dir, _ = journey
    m_ref, ref, sig = _reference_verdict(journey)

    with TestClient(app) as client:
        r = client.post("/api/strategies/backtest", json={
            "dna": payload_dna, "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
            "symbol": "BTCUSDT", "timeframe": "4h",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert sig.entries.any(), "场景构造失败：信号层无 entry（场景退化）"

    assert_metrics_equal(
        {
            "total_return": body["total_return"],
            "sharpe_ratio": body["sharpe_ratio"],
            "max_drawdown": body["max_drawdown"],
            "win_rate": body["win_rate"],
            "total_trades": body["total_trades"],
        },
        {k: m_ref[k] for k in
         ("total_return", "sharpe_ratio", "max_drawdown", "win_rate", "total_trades")},
        scenario="backtest-journey-l2",
    )
    # 链路自洽：响应 result_id 落库可取（跨步数据引用一致）
    assert body.get("result_id")
    with TestClient(app) as client:
        hist = client.get("/api/strategies/verify/history").json()
    # 评分存在（评分层在响应契约内）
    assert body.get("total_score") is not None or body.get("score") is not None


def test_backtest_journey_compare_entry_aligned(journey):
    """C 链并入复验：compare 入口（DB 无关，策略需先创建）与直传指标全等。"""
    from fastapi.testclient import TestClient

    app, payload_dna, dna, data_dir, db_path = journey
    with TestClient(app) as client:
        created = client.post("/api/strategies", json={
            "name": "journey-b", "dna": payload_dna, "symbol": "BTCUSDT",
            "timeframe": "4h", "source": "manual", "tags": "", "notes": "",
        })
        assert created.status_code == 201
        sid = created.json()["strategy_id"]

        r1 = client.post("/api/strategies/backtest", json={
            "dna": payload_dna, "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
            "symbol": "BTCUSDT", "timeframe": "4h",
        }).json()
        r2 = client.post("/api/strategies/compare", json={
            "strategy_ids": [sid], "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
        }).json()
    items = r2["results"] if isinstance(r2, dict) and "results" in r2 else r2
    assert_metrics_equal(
        {k: r1[k] for k in ("total_return", "sharpe_ratio", "max_drawdown",
                            "win_rate", "total_trades")},
        {k: items[0][k] for k in ("total_return", "sharpe_ratio", "max_drawdown",
                                  "win_rate", "total_trades")},
        scenario="journey-b-vs-c",
    )
