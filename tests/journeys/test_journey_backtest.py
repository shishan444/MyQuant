"""链 B · 单回测链 L2（3.2）：构建 DNA → sandbox 核算 → 指标=裁判判定值。

链路级 L2 的核心断言：API 响应指标与独立参照实现（tests/reference/）
在共享信号面上逐指标全等——「用户看到的数字」第一次有了可追溯的判定源。
retire-legacy-subsystems：POST /api/strategies/backtest 与 compare 入口
退役（Q1=B），核算链唯一通道为 POST /api/research/backtest-sandbox
（K1 后含验证记录落库）；compare 入口用例随守护对象退役删除。
sandbox 链 warmup 前扩语义：数据集加 260 bar 前缀，请求窗口自
iloc[260] 起——参照信号面在全量（含前缀）上求值后切窗口，与引擎
「warmup 前扩装载+请求区间求值」同口径。
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

N = 560          # 300 有效窗 + 260 warmup 前缀（sandbox 前扩语义）
WARMUP_CUT = 260


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
    """裁判判定值：被测信号层产信号（共享面）→ 参照回测 → 参照指标。

    信号在全量（含 warmup 前缀）上求值后切请求窗口——与 sandbox
    「warmup 前扩装载+请求区间求值」同口径；参照回测跑窗口切片。"""
    _, _, dna, data_dir, _ = journey
    ohlcv = gen_ohlcv("normal", seed=42, n=N)
    enhanced = compute_all_indicators(ohlcv)
    sig = dna_to_signal_set(dna, enhanced)
    window = ohlcv.iloc[WARMUP_CUT:]
    w0 = window.index[0]
    ref = reference_backtest(
        window, sig.entries.loc[w0:], sig.exits.loc[w0:],
        adds=sig.adds.loc[w0:] if sig.adds is not None else None,
        reduces=sig.reduces.loc[w0:] if sig.reduces is not None else None,
        entry_direction=sig.entry_direction,
        direction="long", position_size=0.5, leverage=1,
        stop_loss=0.05, take_profit=0.10,
        init_cash=100000.0, fee=0.001, slippage=0.0005, timeframe="4h",
    )
    m = reference_metrics(ref.equity, [t.pnl for t in ref.trades], 100000.0, "4h")
    return m, ref, sig, window


def test_backtest_journey_l2_metrics_equal_reference(journey):
    """全链 L2：sandbox 响应五指标 = 参照判定值（trades 精确/浮点 1e-9）。"""
    from fastapi.testclient import TestClient

    app, payload_dna, dna, data_dir, db_path = journey
    m_ref, ref, sig, window = _reference_verdict(journey)

    with TestClient(app) as client:
        created = client.post("/api/strategies", json={
            "name": "journey-l2", "dna": payload_dna, "symbol": "BTCUSDT",
            "timeframe": "4h", "source": "manual", "tags": "", "notes": "",
        })
        assert created.status_code == 201, created.text
        sid = created.json()["strategy_id"]
        r = client.post("/api/research/backtest-sandbox", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": str(window.index[0].date()),
            "end": str(window.index[-1].date()),
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert sig.entries.any(), "场景构造失败：信号层无 entry（场景退化）"

    assert_metrics_equal(
        {
            "total_return": body["metrics"]["total_return"],
            "sharpe_ratio": body["metrics"]["sharpe_ratio"],
            "max_drawdown": body["metrics"]["max_drawdown"],
            "win_rate": body["metrics"]["win_rate"],
            "total_trades": body["metrics"]["total_trades"],
        },
        {k: m_ref[k] for k in
         ("total_return", "sharpe_ratio", "max_drawdown", "win_rate", "total_trades")},
        scenario="sandbox-journey-l2",
    )
    # 链路自洽（K1）：核算即落验证记录，明细三表随记录原子写入
    assert body.get("record_id")
    assert body.get("sandbox") is True
