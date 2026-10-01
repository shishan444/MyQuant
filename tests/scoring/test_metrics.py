"""compute_metrics 单元测试（V3 形制：样例/契约/边界分类放置）。

覆盖：区间口径两键与 bars_per_year 公共常量（improve-backtest-
benchmark-disclosure）、出口闸门（0.2.11 D5）、早退快照、各指标
手工核算锚定、fallback 分支与边界守卫。期望值均独立手算或经
内联公式推导，不用被测实现生成自身期望。
"""
import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.metrics import (
    BARS_PER_YEAR,
    DEFAULT_BARS_PER_YEAR,
    _safe_mean,
    _safe_std,
    compute_metrics,
)


def _series(values, freq="4h"):
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq, tz="UTC",
                        name="timestamp")
    return pd.Series(values, index=idx, dtype=float)


class TestBenchmarkKeys:
    """区间口径两键（Decision 1）：零费基准，None=无基准。"""

    def test_benchmark_values_match_manual_computation(self):
        # equity 100→121（total_return=0.21）；benchmark 100→115（0.15）；
        # 手工对照：benchmark_return=0.15，excess_return=0.21-0.15=0.06
        eq = _series([100.0, 105.0, 110.0, 121.0])
        bm = _series([100.0, 102.0, 108.0, 115.0])
        m = compute_metrics(
            eq, total_trades=3, bars_per_year=2,  # 4 bar = 2 年
            trade_returns=np.array([0.05, 0.05, 0.0476]),
            benchmark_close=bm)
        assert m["benchmark_return"] == pytest.approx(0.15)
        assert m["excess_return"] == pytest.approx(0.21 - 0.15)
        # 年化口径并存（不删除既有 alpha/market_annual_return）
        assert m["market_annual_return"] == pytest.approx(1.15 ** 0.5 - 1)
        assert m["alpha"] == pytest.approx(m["annual_return"] - m["market_annual_return"])

    def test_benchmark_missing_keys_none(self):
        m = compute_metrics(
            _series([100.0, 110.0, 120.0]), total_trades=2,
            trade_returns=np.array([0.1, 0.0818]))
        assert m["benchmark_return"] is None
        assert m["excess_return"] is None

    def test_benchmark_too_short_keys_none(self):
        # len(benchmark_close) < 2：与既有 alpha 块同一守卫，区间口径同 None
        m = compute_metrics(
            _series([100.0, 110.0, 120.0]), total_trades=2,
            trade_returns=np.array([0.1, 0.0818]),
            benchmark_close=_series([100.0]))
        assert m["benchmark_return"] is None
        assert m["excess_return"] is None

    def test_no_trades_early_exit_keys_none(self):
        # 早退分支（无交易）：两键 None（容缺语义，不虚构对照）
        m = compute_metrics(
            _series([100.0, 100.0, 100.0]), total_trades=0,
            benchmark_close=_series([100.0, 120.0]))
        assert m["benchmark_return"] is None
        assert m["excess_return"] is None


class TestBarsPerYearUnification:
    """Decision 6：公共常量为单一权威源，engine 与 batch 同源引用。"""

    def test_constant_covers_all_tiers_expected_values(self):
        # batch 全集 + engine 独有 3d 档；数值与既有 batch.py:149-152 一致
        assert BARS_PER_YEAR == {
            "1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
            "1h": 8760, "2h": 4380, "4h": 2190, "1d": 365,
            "3d": 365 // 3,
        }
        assert DEFAULT_BARS_PER_YEAR == 2190

    def test_batch_processor_same_source_all_tiers(self):
        from api.routes.strategies.batch import _BatchBacktestProcessor
        for tf, expected in BARS_PER_YEAR.items():
            assert _BatchBacktestProcessor._bars_per_year(None, tf) == expected
        # 未知档缺省不变（=4h 档）
        assert _BatchBacktestProcessor._bars_per_year(None, "9h") == DEFAULT_BARS_PER_YEAR

    def test_engine_uses_constant_for_1m(self):
        # 原 engine 映射缺 1m 档 → 错落缺省 2190；统一后 1m 真跑取 525600
        from tests.helpers.data_factory import make_dna, make_engine, make_enhanced_df
        engine = make_engine()
        dna = make_dna(indicator="RSI", timeframe="1m")
        enhanced = make_enhanced_df(n=300, freq="1min")
        result = engine.run(dna, enhanced)
        assert result.bars_per_year == BARS_PER_YEAR["1m"]
        assert result.bars_per_year == 525600


class TestSafeStatsGate:
    """0.2.11 出口闸门：样本不足返回缺失语义（None），不产 NaN 不发警告。"""

    def test_safe_mean_empty_returns_none(self):
        assert _safe_mean(np.array([])) is None
        assert _safe_mean(None) is None

    def test_safe_std_single_sample_returns_none(self):
        assert _safe_std(np.array([0.1])) is None
        assert _safe_std(None) is None

    def test_safe_values_match_manual(self):
        assert _safe_mean(np.array([1.0, 2.0])) == pytest.approx(1.5)
        assert _safe_std(np.array([1.0, 2.0])) == pytest.approx(0.70710678, rel=1e-6)

    @pytest.mark.filterwarnings("error::RuntimeWarning")
    def test_sortino_single_downside_no_warning_zero(self):
        """B3 生产真警告点：多笔仅 1 笔负收益——dof<=0 警告即失败。"""
        idx = pd.date_range("2024-01-01", periods=100, freq="4h")
        eq = pd.Series(np.linspace(100000, 120000, 100), index=idx)
        m = compute_metrics(
            eq, total_trades=6,
            trade_returns=np.array([0.05, 0.03, 0.04, -0.02, 0.05, 0.03]))
        assert m["sortino_ratio"] == 0.0  # 单负收益 std 无定义 → 缺省

    @pytest.mark.filterwarnings("error::RuntimeWarning")
    def test_single_trade_sharpe_no_warning_zero(self):
        idx = pd.date_range("2024-01-01", periods=50, freq="4h")
        eq = pd.Series(np.linspace(100000, 110000, 50), index=idx)
        m = compute_metrics(eq, total_trades=1,
                            trade_returns=np.array([0.05]))
        assert m["sharpe_ratio"] == 0.0  # n<2 闸门 → None → 0.0 缺省


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

    def test_zero_trades_early_exit_returns_zero_snapshot(self):
        m = compute_metrics(_series([100.0, 105.0, 110.0]), total_trades=0)
        expected = dict(self.ZERO_SNAPSHOT, total_bars=3)
        assert m == expected

    def test_single_bar_with_trades_early_exit_zero_snapshot(self):
        # 守卫另一半：len(equity_curve) < 2 即使有交易也早退
        m = compute_metrics(_series([100.0]), total_trades=5)
        expected = dict(self.ZERO_SNAPSHOT, total_bars=1)
        assert m == expected


class TestAnnualReturnAndYears:
    """年化收益与回测年数的手工幂运算锚定。"""

    def test_annual_return_half_year_manual_compounding(self):
        # 3 bar / bpy=6 → years=0.5；total_return=0.21 → (1.21)^2 - 1
        m = compute_metrics(
            _series([100.0, 110.0, 121.0]), total_trades=2,
            bars_per_year=6, trade_returns=np.array([0.1, 0.1]))
        assert m["backtest_years"] == 0.5
        assert m["annual_return"] == pytest.approx(1.21 ** 2 - 1)

    def test_annual_return_years_below_floor_clamped_at_001(self):
        # years=2/1e7 远小于 0.01 → 按 max(years, 0.01) 年化（下限钳制）
        m = compute_metrics(
            _series([100.0, 105.0]), total_trades=1,
            bars_per_year=10_000_000, trade_returns=np.array([0.05]))
        assert m["annual_return"] == pytest.approx(1.05 ** 100 - 1)


class TestMaxDrawdownAndCalmar:
    def test_max_drawdown_known_peak_trough_exact(self):
        # cummax=[100,120,120,120] → dd 序列最小值 -0.5
        m = compute_metrics(
            _series([100.0, 120.0, 60.0, 90.0]), total_trades=2,
            bars_per_year=8, trade_returns=np.array([0.05, -0.02]))
        assert m["max_drawdown"] == pytest.approx(-0.5)

    def test_calmar_ratio_with_drawdown_absolute_ratio(self):
        # years=4/8=0.5 → annual=0.9^2-1=-0.19；calmar=|−0.19/−0.5|=0.38
        m = compute_metrics(
            _series([100.0, 120.0, 60.0, 90.0]), total_trades=2,
            bars_per_year=8, trade_returns=np.array([0.05, -0.02]))
        assert m["calmar_ratio"] == pytest.approx(abs((0.9 ** 2 - 1) / -0.5))

    def test_calmar_ratio_zero_drawdown_returns_zero(self):
        # 单调上涨 dd=0 → |dd|<=0.001 分支 → calmar=0
        m = compute_metrics(
            _series([100.0, 110.0, 121.0]), total_trades=2,
            bars_per_year=6, trade_returns=np.array([0.1, 0.1]))
        assert m["max_drawdown"] == 0.0
        assert m["calmar_ratio"] == 0.0

    def test_max_drawdown_all_nan_curve_guarded_to_zero(self):
        # 全 NaN 曲线：drawdown.min()=NaN → is nan 防御分支 → 0.0
        m = compute_metrics(
            _series([float("nan")] * 3), total_trades=1,
            trade_returns=np.array([0.01]))
        assert m["max_drawdown"] == 0.0


class TestSharpeRatio:
    def test_sharpe_trade_level_manual_formula_exact(self):
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

    def test_sharpe_bar_level_fallback_manual_formula(self):
        # trade_returns=None 且 total_trades>=5 → bar 级 fallback；
        # pct=[0.1, -0.05]，独立手算 mean/std(ddof=1)
        rets = np.array([0.1, -0.05])
        mean = rets.sum() / len(rets)
        std = math.sqrt(((rets - mean) ** 2).sum() / (len(rets) - 1))
        expected = mean / std * math.sqrt(4)
        m = compute_metrics(
            _series([100.0, 110.0, 104.5]), total_trades=5, bars_per_year=4)
        assert m["sharpe_ratio"] == pytest.approx(expected, rel=1e-9)

    def test_sharpe_bar_level_fallback_below_five_trades_zero(self):
        # total_trades<5 不触发 fallback → 0.0（门槛守卫）
        m = compute_metrics(
            _series([100.0, 110.0, 104.5]), total_trades=4, bars_per_year=4)
        assert m["sharpe_ratio"] == 0.0

    def test_sharpe_bar_level_fallback_empty_returns_zero(self):
        # 回退路径 pct 后为空（2 bar 尾 NaN）→ len 守卫假臂，sharpe 缺省 0；
        # win_rate 回退同空 → None 缺失语义
        m = compute_metrics(_series([100.0, float("nan")]), total_trades=5)
        assert m["sharpe_ratio"] == 0.0
        assert m["win_rate"] is None

    def test_sharpe_bar_level_fallback_flat_std_zero(self):
        # 平曲线 pct 全 0 → std=0 守卫假臂 → sharpe 缺省 0
        m = compute_metrics(_series([100.0] * 4), total_trades=5)
        assert m["sharpe_ratio"] == 0.0


class TestWinRate:
    def test_win_rate_explicit_zero_passes_through(self):
        # 0.0 为 falsy 但判据是 is not None → 直通不折叠
        m = compute_metrics(
            _series([100.0, 110.0, 121.0]), total_trades=3,
            trade_win_rate=0.0, trade_returns=None)
        assert m["win_rate"] == 0.0

    def test_win_rate_bar_level_fallback_two_thirds(self):
        # trade_win_rate=None → bar 级：pct=[0.1, -0.05, ~0.1005] → 2/3
        m = compute_metrics(
            _series([100.0, 110.0, 104.5, 115.0]), total_trades=2)
        assert m["win_rate"] == pytest.approx(2 / 3, abs=1e-9)

    def test_win_rate_flat_curve_fallback_zero_not_missing(self):
        # 平曲线 pct 全 0：len(returns)>0 成立 → win_rate=0.0（非缺失）
        m = compute_metrics(_series([100.0] * 50), total_trades=3)
        assert m["win_rate"] == 0.0


class TestProfitFactor:
    @pytest.mark.parametrize(
        ("trade_returns", "expected"),
        [
            pytest.param([0.05, -0.02, 0.03], 4.0, id="常规毛利比"),
            pytest.param([0.0, 0.0], 0.0, id="全零收益归零"),
        ],
    )
    def test_profit_factor_trade_returns_expected_ratio(self, trade_returns, expected):
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
    def test_max_consecutive_losses_streak_counting(self, trade_returns, expected):
        m = compute_metrics(
            _series([100.0] * (len(trade_returns) + 1)),
            total_trades=len(trade_returns),
            trade_returns=np.array(trade_returns))
        assert m["max_consecutive_losses"] == expected


class TestMonthlyConsistency:
    def test_monthly_consistency_thirty_bars_boundary_zero(self):
        # 守卫 len>30：恰好 30 bar 不计算 → 0.0
        m = compute_metrics(
            _series([100.0] * 30, freq="D"), total_trades=2,
            trade_returns=np.array([0.05, -0.02]))
        assert m["monthly_consistency"] == 0.0

    def test_monthly_consistency_three_months_manual_half(self):
        # 70 天跨 3 月：月末值 [100, 130, 110] → pct 去首月后一涨一跌 → 0.5
        idx = pd.date_range("2024-01-01", periods=70, freq="D", tz="UTC")
        values = [100.0] * 31 + [130.0] * 29 + [110.0] * 10
        eq = pd.Series(values, index=idx, dtype=float)
        m = compute_metrics(eq, total_trades=2,
                            trade_returns=np.array([0.05, -0.02]))
        assert m["monthly_consistency"] == pytest.approx(0.5)


class TestRSquared:
    def test_r_squared_ten_bars_boundary_zero(self):
        # 守卫 len>10：恰好 10 bar 不计算 → 0.0
        m = compute_metrics(
            _series([100.0 + i for i in range(10)]), total_trades=2,
            trade_returns=np.array([0.05, -0.02]))
        assert m["r_squared"] == 0.0

    def test_r_squared_eleven_bars_linear_anchored(self):
        # 11 bar 恰过守卫。CARD-01 修复后：var 与 cov 同为 ddof=1 配对，
        # slope=Sxy/Sxx，完美线性 ss_res=0、ss_tot=110 → r²=1.0 精确
        m = compute_metrics(
            _series([100.0 + i for i in range(11)]), total_trades=2,
            trade_returns=np.array([0.05, -0.02]))
        assert m["r_squared"] == pytest.approx(1.0)

    def test_r_squared_flat_curve_ss_tot_zero(self):
        # 平曲线 ss_tot=0 分支 → 0.0
        m = compute_metrics(_series([100.0] * 50), total_trades=3)
        assert m["r_squared"] == 0.0


class TestSortinoRatio:
    def test_sortino_two_downside_trades_manual_formula(self):
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


class TestMetricProperties:
    """不变量与属性（V3 方法）：成立条件随用例注明，固定种子可复现。"""

    @pytest.mark.parametrize(
        "total_return", [-0.5, -0.1, 0.0, 0.21, 1.5, 9.0])
    def test_annualization_one_year_identity(self, total_return):
        # 属性：years=1 时年化恒等区间收益，(1+r)^1 - 1 = r
        eq = _series([100.0, 100.0 * (1 + total_return)])
        m = compute_metrics(eq, total_trades=1, bars_per_year=2,
                            trade_returns=np.array([0.01]))
        assert m["backtest_years"] == 1.0
        assert m["annual_return"] == pytest.approx(total_return, abs=1e-9)

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_max_drawdown_within_unit_interval(self, seed):
        # 属性（权益恒正前提）：相对回撤非正且不低于 -100%
        rng = np.random.default_rng(seed)
        eq = _series(10000 * np.cumprod(1 + rng.normal(0.001, 0.03, 200)))
        m = compute_metrics(eq, total_trades=10,
                            trade_returns=rng.normal(0.02, 0.05, 10))
        assert -1.0 <= m["max_drawdown"] <= 0.0
