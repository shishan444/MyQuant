"""滑窗不变性（2.2）：指标是窗口长度的函数且仅是。

- 年化因子（bars_per_year）仅由 timeframe 决定，不随窗口漂移
- annual_return 按统一年化公式换算（每窗独立重算一致）
- 短窗 years 下限保护行为显式锚定（确定值，不静默崩溃/发散）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from core.features.indicators import compute_all_indicators
from core.strategy.executor import SignalSet
from tests.helpers.data_factory import make_dna, make_ohlcv
from tests.reference.reference_metrics import BARS_PER_YEAR


def _window(df, frac, mode):
    n = len(df)
    w = int(n * frac)
    if mode == "front":
        return df.iloc[:w]
    if mode == "back":
        return df.iloc[n - w:]
    off = (n - w) // 2
    return df.iloc[off:off + w]


@pytest.mark.parametrize("mode", ["front", "mid", "back"])
def test_sliding_window_bars_per_year_invariant(mode):
    """同 timeframe 三窗：bars_per_year 同值且=公开事实 365*6。"""
    df = make_ohlcv(n=300, seed=42)
    dna = make_dna(direction="long", leverage=1)
    win = _window(df, 0.6, mode)
    enhanced = compute_all_indicators(win)
    eng = BacktestEngine(init_cash=100000.0, fee=0.001, slippage=0.0005)
    res = eng.run(dna, enhanced)
    assert res.bars_per_year == BARS_PER_YEAR["4h"] == 365 * 6


@pytest.mark.parametrize("mode", ["front", "mid", "back"])
def test_sliding_window_annual_formula_consistent(mode):
    """每窗 annual_return 满足统一换算式（独立重算）：(1+tr)^(1/max(years,0.01))−1。"""
    df = make_ohlcv(n=300, seed=42)
    dna = make_dna(direction="long", leverage=1)
    win = _window(df, 0.6, mode)
    enhanced = compute_all_indicators(win)
    eng = BacktestEngine(init_cash=100000.0, fee=0.001, slippage=0.0005)
    res = eng.run(dna, enhanced)
    ann = (res.metrics_dict or {})["annual_return"]

    years = len(res.equity_curve) / BARS_PER_YEAR["4h"]
    expected = (1 + res.total_return) ** (1 / max(years, 0.01)) - 1
    assert ann == pytest.approx(expected, rel=1e-12), (
        f"{mode} 窗年化换算不一致: {ann} != {expected}"
    )


def test_short_window_floor_behavior_locked():
    """短窗下限锚定：注入信号保证有交易（绕开 RSI warmup），years<0.01
    时 annual_return 按 (1+tr)^(1/0.01)−1 产出确定值（锁定，不发散）。"""
    n = 15
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(np.linspace(100.0, 130.0, n), index=idx)
    df = pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5,
         "close": close, "volume": 100.0}, index=idx,
    )
    df.index.name = "timestamp"
    entries = pd.Series(False, index=idx)
    entries.iloc[2] = True
    exits = pd.Series(False, index=idx)
    exits.iloc[8] = True
    zeros = pd.Series(False, index=idx)

    dna = make_dna(direction="long", leverage=1, stop_loss=0.0, take_profit=0.0)
    eng = BacktestEngine(init_cash=100000.0, fee=0.001, slippage=0.0005)
    res = eng.run(dna, df, signal_set=SignalSet(
        entries=entries, exits=exits, adds=zeros, reduces=zeros))

    assert res.total_trades >= 1, "短窗场景应有交易（注入信号）"
    years = len(res.equity_curve) / BARS_PER_YEAR["4h"]
    assert years < 0.01, "场景构造失败：应触发 years 下限"
    expected = (1 + res.total_return) ** (1 / 0.01) - 1
    ann = (res.metrics_dict or {})["annual_return"]
    assert ann == pytest.approx(expected, rel=1e-9), (
        f"短窗下限行为漂移: {ann} != {expected}"
    )
    assert np.isfinite(ann), "短窗年化发散（应按下限公式收敛）"
