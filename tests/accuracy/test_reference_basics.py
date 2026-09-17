"""裁判层基础验证：生成器确定性/自检、金样手工核对、引擎校准闭环。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from tests.reference.assert_metrics import assert_metrics_equal
from tests.reference.fixture_gen import SCENARIOS, assert_valid_ohlcv, gen_ohlcv
from tests.reference.reference_backtest import reference_backtest
from tests.reference.reference_metrics import reference_metrics


# ──────────────────────────────────────────────────────────────
# 生成器：同种子同数据 / 坏数据不进链路
# ──────────────────────────────────────────────────────────────

def test_fixture_same_seed_same_data():
    for sc in SCENARIOS:
        a, b = gen_ohlcv(sc, seed=7), gen_ohlcv(sc, seed=7)
        pd.testing.assert_frame_equal(a, b)


def test_fixture_different_seed_differs():
    a, b = gen_ohlcv("normal", seed=1), gen_ohlcv("normal", seed=2)
    assert not np.allclose(a["close"].to_numpy(), b["close"].to_numpy())


def test_fixture_invalid_ohlcv_rejected():
    df = gen_ohlcv("short_window", seed=3)
    bad = df.copy()
    bad.loc[bad.index[2], "low"] = bad["open"].iloc[2] + 1.0  # low > body
    with pytest.raises(ValueError, match="low"):
        assert_valid_ohlcv(bad)

    bad2 = df.copy().reset_index(drop=True)
    with pytest.raises(ValueError, match="DatetimeIndex|monotonic"):
        assert_valid_ohlcv(bad2)


# ──────────────────────────────────────────────────────────────
# 金样手工核对：25 根线性价，契约公式的独立应用（非 vbt 输出回抄）
# ──────────────────────────────────────────────────────────────

def _probe_setup():
    n = 25
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(np.array([100.0 + i for i in range(n)]), index=idx)
    df = pd.DataFrame(
        {"open": close, "high": close + 1.5, "low": close - 1.5,
         "close": close, "volume": 1000.0}, index=idx,
    )
    df.index.name = "timestamp"
    entries = pd.Series(False, index=idx)
    entries.iloc[5] = True
    exits = pd.Series(False, index=idx)
    exits.iloc[12] = True
    return df, entries, exits


def test_golden_trade_manual_derivation():
    """单笔交易逐字段手工推导：契约 1/3/4 的独立公式链。"""
    df, entries, exits = _probe_setup()
    res = reference_backtest(
        df, entries, exits, direction="long", position_size=0.5,
        leverage=1, stop_loss=0.0, take_profit=0.0,
        init_cash=100000.0, fee=0.001, slippage=0.0005, timeframe="4h",
    )
    assert len(res.trades) == 1
    t = res.trades[0]

    # 独立推导（契约公式直算，与实现无共享代码）
    price_in = 106.0 * 1.0005                       # bar6 close×(1+slip)
    qty = (100000.0 * 0.5) / (price_in * 1.001)     # 契约 3
    fee_in = qty * price_in * 0.001
    price_out = 113.0 * 0.9995                      # bar13 close×(1−slip)
    fee_out = qty * price_out * 0.001
    pnl = qty * (price_out - price_in) - fee_in - fee_out

    assert t.entry_idx == 6 and t.exit_idx == 13
    assert t.entry_price == pytest.approx(price_in, rel=1e-12)
    assert t.exit_price == pytest.approx(price_out, rel=1e-12)
    assert t.shares == pytest.approx(qty, rel=1e-12)
    assert t.entry_fee == pytest.approx(fee_in, rel=1e-12)
    assert t.exit_fee == pytest.approx(fee_out, rel=1e-12)
    assert t.pnl == pytest.approx(pnl, rel=1e-12)

    # 契约 4：equity[6] = cash + shares*close[6]
    cash_after_entry = 100000.0 - qty * price_in - fee_in
    assert res.raw_equity[6] == pytest.approx(cash_after_entry + qty * 106.0, rel=1e-12)
    # 权益守恒（恒等式族入口）：终值 = init + Σpnl（lev=1 无 funding）
    assert res.equity[-1] == pytest.approx(100000.0 + t.pnl, rel=1e-12)


def test_golden_metrics_manual_derivation():
    """指标手工推导：单笔交易（sharpe=0 契约）+ 多笔交易（trade-level sharpe）。"""
    df, entries, exits = _probe_setup()
    res = reference_backtest(
        df, entries, exits, direction="long", position_size=0.5,
        init_cash=100000.0, fee=0.001, slippage=0.0005,
    )
    m = reference_metrics(res.equity, [t.pnl for t in res.trades], 100000.0, "4h")
    assert m["total_trades"] == 1
    assert m["win_rate"] == pytest.approx(1.0)
    assert m["sharpe_ratio"] == 0.0  # 单笔 → fallback 链不满足 → 0.0（行为契约）
    tr = res.equity[-1] / res.equity[0] - 1
    assert m["total_return"] == pytest.approx(tr, rel=1e-12)
    assert m["max_drawdown"] == pytest.approx(-0.0007491259, rel=1e-6)  # bar6 回撤手工值


# ──────────────────────────────────────────────────────────────
# 校准闭环：参照 vs 被测引擎（同信号同参数同数据）
# ──────────────────────────────────────────────────────────────

def _run_engine(df, entries, exits, **kw):
    from core.backtest.engine import BacktestEngine
    from core.strategy.executor import SignalSet
    from tests.helpers.data_factory import make_dna

    dna = make_dna(
        direction=kw.get("direction", "long"),
        position_size=kw.get("position_size", 0.5),
        leverage=kw.get("leverage", 1),
        stop_loss=kw.get("stop_loss", 0.0),
        take_profit=kw.get("take_profit", 0.0),
    )
    zeros = pd.Series(False, index=df.index)
    eng = BacktestEngine(
        init_cash=kw.get("init_cash", 100000.0),
        fee=kw.get("fee", 0.001),
        slippage=kw.get("slippage", 0.0005),
    )
    return eng.run(
        dna, df,
        signal_set=SignalSet(entries=entries, exits=exits, adds=zeros, reduces=zeros),
    )


def _engine_metrics(res) -> dict:
    return {
        "total_return": res.total_return,
        "sharpe_ratio": res.sharpe_ratio,
        "max_drawdown": res.max_drawdown,
        "win_rate": res.win_rate,
        "total_trades": res.total_trades,
        "annual_return": (res.metrics_dict or {}).get("annual_return", 0.0),
        "liquidated": res.liquidated,
    }


def _reference_metrics_of(df, entries, exits, **kw):
    res = reference_backtest(
        df, entries, exits,
        direction=kw.get("direction", "long"),
        position_size=kw.get("position_size", 0.5),
        leverage=kw.get("leverage", 1),
        stop_loss=kw.get("stop_loss", 0.0),
        take_profit=kw.get("take_profit", 0.0),
        init_cash=kw.get("init_cash", 100000.0),
        fee=kw.get("fee", 0.001),
        slippage=kw.get("slippage", 0.0005),
        timeframe="4h",
    )
    m = reference_metrics(res.equity, [t.pnl for t in res.trades],
                          kw.get("init_cash", 100000.0), "4h")
    m["liquidated"] = res.liquidated
    return m, res


@pytest.mark.parametrize(
    "kw",
    [
        pytest.param({}, id="long-base"),
        pytest.param({"direction": "short"}, id="short"),
        pytest.param({"stop_loss": 0.02}, id="sl"),
        pytest.param({"take_profit": 0.02}, id="tp"),
    ],
)
def test_calibration_engine_vs_reference(kw):
    """探针场景：被测引擎 vs 参照全指标 1e-9 比对（先涨后跌序列）。"""
    idx = pd.date_range("2024-01-01", periods=16, freq="4h", tz="UTC")
    close = pd.Series(
        np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 107, 104,
                  100, 97, 96, 95, 94], dtype=float), index=idx,
    )
    df = pd.DataFrame(
        {"open": close, "high": close + 1.5, "low": close - 1.5,
         "close": close, "volume": 1000.0}, index=idx,
    )
    df.index.name = "timestamp"
    entries = pd.Series(False, index=idx)
    entries.iloc[5] = True
    exits = pd.Series(False, index=idx)
    exits.iloc[12] = True

    eng = _run_engine(df, entries, exits, **kw)
    m_ref, _ = _reference_metrics_of(df, entries, exits, **kw)
    assert_metrics_equal(_engine_metrics(eng), m_ref, scenario=f"probe-{kw}")
    assert m_ref["liquidated"] == eng.liquidated
