"""compute_metrics 特征测试网（现状记录语义）。

基准源为特征测试网模式（openspec 主 specs 无覆盖）：期望值全部由
独立手算/内联公式推导后与当前实现比对，锚定现有行为供修复回归。
覆盖方向：早退快照、各指标数值锚定、fallback 分支、边界守卫
（30 bar 月度 / 10 bar R² / 5 笔 bar 级 Sharpe 门槛）。
"""
import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.metrics import compute_metrics


def _series(values, freq="4h"):
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq, tz="UTC",
                        name="timestamp")
    return pd.Series(values, index=idx, dtype=float)


class TestEarlyReturnSnapshot:
    """早退守卫 len<2 or total_trades==0 的完整返回快照。"""

    ZERO_SNAPSHOT = {
        "annual_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "win_rate": None,
        "calmar_ratio": 0.0,
        "total_trades": 0,
        "sortino_ratio": 0.0,
        "profit_factor": 0.0,
        "max_consecutive_losses": 0,
        "monthly_consistency": 0.0,
        "r_squared": 0.0,
        "market_annual_return": 0.0,
        "alpha": 0.0,
        "backtest_years": 0.0,
        "benchmark_return": None,
        "excess_return": None,
    }

    def test_zero_trades_full_snapshot(self):
        m = compute_metrics(_series([100.0, 105.0, 110.0]), total_trades=0)
        expected = dict(self.ZERO_SNAPSHOT, total_bars=3)
        assert m == expected

    def test_single_bar_with_trades_same_guard(self):
        # 守卫另一半：len(equity_curve) < 2 即使有交易也早退
        m = compute_metrics(_series([100.0]), total_trades=5)
        expected = dict(self.ZERO_SNAPSHOT, total_bars=1)
        assert m == expected


class TestAnnualReturnAndYears:
    """年化收益与回测年数的数值锚定（手工幂运算对照）。"""

    def test_half_year_compounding(self):
        # 3 bar / bpy=6 → years=0.5；total_return=0.21 → (1.21)^2 - 1
        m = compute_metrics(
            _series([100.0, 110.0, 121.0]), total_trades=2,
            bars_per_year=6, trade_returns=np.array([0.1, 0.1]))
        assert m["backtest_years"] == 0.5
        assert m["annual_return"] == pytest.approx(1.21 ** 2 - 1)

    def test_years_floor_at_001(self):
        # years=2/1e7 远小于 0.01 → 按 max(years, 0.01) 年化（现状钳制）
        m = compute_metrics(
            _series([100.0, 105.0]), total_trades=1,
            bars_per_year=10_000_000, trade_returns=np.array([0.05]))
        assert m["annual_return"] == pytest.approx(1.05 ** 100 - 1)


class TestMaxDrawdownAndCalmar:
    def test_known_peak_trough(self):
        # cummax=[100,120,120,120] → dd 序列最小值 -0.5
        m = compute_metrics(
            _series([100.0, 120.0, 60.0, 90.0]), total_trades=2,
            bars_per_year=8, trade_returns=np.array([0.05, -0.02]))
        assert m["max_drawdown"] == pytest.approx(-0.5)

    def test_calmar_with_drawdown(self):
        # years=4/8=0.5 → annual=0.9^2-1=-0.19；calmar=|−0.19/−0.5|=0.38
        m = compute_metrics(
            _series([100.0, 120.0, 60.0, 90.0]), total_trades=2,
            bars_per_year=8, trade_returns=np.array([0.05, -0.02]))
        assert m["calmar_ratio"] == pytest.approx(abs((0.9 ** 2 - 1) / -0.5))

    def test_calmar_zero_when_no_drawdown(self):
        # 单调上涨 dd=0 → |dd|<=0.001 分支 → calmar=0
        m = compute_metrics(
            _series([100.0, 110.0, 121.0]), total_trades=2,
            bars_per_year=6, trade_returns=np.array([0.1, 0.1]))
        assert m["max_drawdown"] == 0.0
        assert m["calmar_ratio"] == 0.0

    def test_all_nan_curve_drawdown_guarded_to_zero(self):
        # 全 NaN 曲线：drawdown.min()=NaN → is nan 防御分支 → 0.0
        m = compute_metrics(
            _series([float("nan")] * 3), total_trades=1,
            trade_returns=np.array([0.01]))
        assert m["max_drawdown"] == 0.0


class TestSharpeAnchors:
    def test_trade_level_exact_value(self):
        # 4 bar / bpy=4 → years=1 → trades_per_year=4；
        # 独立手算 mean/std(ddof=1) 后按公式组合
        tr = np.array([0.05, 0.03, -0.02, 0.04])
        mean = tr.sum() / len(tr)
        std = math.sqrt(((tr - mean) ** 2).sum() / (len(tr) - 1))
        expected = mean / std * math.sqrt(4 / 1.0)
        m = compute_metrics(
            _series([100.0, 105.0, 103.0, 108.0]), total_trades=4,
            bars_per_year=4, trade_returns=tr)
        assert m["sharpe_ratio"] == pytest.approx(expected, rel=1e-9)

    def test_bar_level_fallback_exact_value(self):
        # trade_returns=None 且 total_trades>=5 → bar 级 fallback；
        # pct=[0.1, -0.05]，独立手算 mean/std(ddof=1)
        rets = np.array([0.1, -0.05])
        mean = rets.sum() / len(rets)
        std = math.sqrt(((rets - mean) ** 2).sum() / (len(rets) - 1))
        expected = mean / std * math.sqrt(4)
        m = compute_metrics(
            _series([100.0, 110.0, 104.5]), total_trades=5, bars_per_year=4)
        assert m["sharpe_ratio"] == pytest.approx(expected, rel=1e-9)

    def test_bar_level_fallback_needs_five_trades(self):
        # total_trades<5 不触发 fallback → 0.0（现状门槛）
        m = compute_metrics(
            _series([100.0, 110.0, 104.5]), total_trades=4, bars_per_year=4)
        assert m["sharpe_ratio"] == 0.0


class TestWinRateAnchors:
    def test_trade_win_rate_zero_passes_through(self):
        # 0.0 为 falsy 但判据是 is not None → 直通不折叠
        m = compute_metrics(
            _series([100.0, 110.0, 121.0]), total_trades=3,
            trade_win_rate=0.0, trade_returns=None)
        assert m["win_rate"] == 0.0

    def test_bar_level_fallback_ratio(self):
        # trade_win_rate=None → bar 级：pct=[0.1, -0.05, ~0.1005] → 2/3
        m = compute_metrics(
            _series([100.0, 110.0, 104.5, 115.0]), total_trades=2)
        assert m["win_rate"] == pytest.approx(2 / 3, abs=1e-9)

    def test_flat_curve_fallback_is_zero_not_none(self):
        # 平曲线 pct 全 0：len(returns)>0 成立 → win_rate=0.0（非缺失）
        m = compute_metrics(_series([100.0] * 50), total_trades=3)
        assert m["win_rate"] == 0.0


class TestProfitFactorAnchors:
    @pytest.mark.parametrize(
        ("trade_returns", "expected"),
        [
            pytest.param([0.05, -0.02, 0.03], 4.0, id="常规毛利比"),
            pytest.param([0.0, 0.0], 0.0, id="全零收益归零"),
        ],
    )
    def test_profit_factor_values(self, trade_returns, expected):
        m = compute_metrics(
            _series([100.0, 105.0, 103.0]), total_trades=len(trade_returns),
            trade_returns=np.array(trade_returns))
        assert m["profit_factor"] == pytest.approx(expected)


class TestMaxConsecutiveLosses:
    @pytest.mark.parametrize(
        ("trade_returns", "expected"),
        [
            pytest.param([0.01, 0.02], 0, id="全胜为零"),
            pytest.param([0.01, -0.01, 0.02, -0.02, -0.03], 2, id="尾部连亏计数"),
            pytest.param([-0.01, -0.02, -0.03, -0.04], 4, id="全亏为全长"),
            pytest.param([0.01, -0.01, 0.0, -0.01], 1, id="零收益复位不计亏"),
        ],
    )
    def test_streak_values(self, trade_returns, expected):
        m = compute_metrics(
            _series([100.0] * (len(trade_returns) + 1)),
            total_trades=len(trade_returns),
            trade_returns=np.array(trade_returns))
        assert m["max_consecutive_losses"] == expected


class TestMonthlyConsistency:
    def test_thirty_bars_boundary_is_zero(self):
        # 守卫 len>30：恰好 30 bar 不计算 → 0.0
        m = compute_metrics(
            _series([100.0] * 30, freq="D"), total_trades=2,
            trade_returns=np.array([0.05, -0.02]))
        assert m["monthly_consistency"] == 0.0

    def test_three_month_manual_ratio(self):
        # 70 天跨 3 月：月末值 [100, 130, 110] → pct 去首月后一涨一跌 → 0.5
        idx = pd.date_range("2024-01-01", periods=70, freq="D", tz="UTC")
        values = [100.0] * 31 + [130.0] * 29 + [110.0] * 10
        eq = pd.Series(values, index=idx, dtype=float)
        m = compute_metrics(eq, total_trades=2,
                            trade_returns=np.array([0.05, -0.02]))
        assert m["monthly_consistency"] == pytest.approx(0.5)

    def test_plain_index_resample_failure_swallowed(self):
        # 非 DatetimeIndex：resample 抛异常被吞 → 0.0（现状容错）
        eq = pd.Series([100.0 + i for i in range(45)], dtype=float)
        m = compute_metrics(eq, total_trades=2,
                            trade_returns=np.array([0.05, -0.02]))
        assert m["monthly_consistency"] == 0.0


class TestRSquared:
    def test_ten_bars_boundary_is_zero(self):
        # 守卫 len>10：恰好 10 bar 不计算 → 0.0
        m = compute_metrics(
            _series([100.0 + i for i in range(10)]), total_trades=2,
            trade_returns=np.array([0.05, -0.02]))
        assert m["r_squared"] == 0.0

    def test_eleven_bars_linear_anchored(self):
        # 11 bar 恰过守卫。CARD-01 修复后：var 与 cov 同为 ddof=1 配对，
        # slope=Sxy/Sxx，完美线性 ss_res=0、ss_tot=110 → r²=1.0 精确
        m = compute_metrics(
            _series([100.0 + i for i in range(11)]), total_trades=2,
            trade_returns=np.array([0.05, -0.02]))
        assert m["r_squared"] == pytest.approx(1.0)

    def test_flat_curve_ss_tot_zero(self):
        # 平曲线 ss_tot=0 分支 → 0.0
        m = compute_metrics(_series([100.0] * 50), total_trades=3)
        assert m["r_squared"] == 0.0


class TestSortinoExactValue:
    def test_two_downside_trades(self):
        # downside=[-0.02,-0.04] 独立手算 ddof=1 下行标准差；
        # years=1 → trades_per_year=4
        tr = np.array([0.05, -0.02, -0.04, 0.03])
        downside = tr[tr < 0]
        d_mean = downside.sum() / len(downside)
        d_std = math.sqrt(((downside - d_mean) ** 2).sum() / (len(downside) - 1))
        mean = tr.sum() / len(tr)
        expected = mean / d_std * math.sqrt(4 / 1.0)
        m = compute_metrics(
            _series([100.0, 105.0, 102.0, 107.0]), total_trades=4,
            bars_per_year=4, trade_returns=tr)
        assert m["sortino_ratio"] == pytest.approx(expected, rel=1e-9)
