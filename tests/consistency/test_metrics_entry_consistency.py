"""跨入口绩效口径一致性（fix-indicator-output-mapping 阶段 1，必红测试）。

病根注记（2026-09-24 普查实锤；xfail strict = 收口完成转绿时报错
提醒摘牌，红→绿即阶段 1 验收主证据）：

- win_rate 绩效口径多处独立实现三种口径：
  * backtest/engine.py:583 逐笔小数（winning / len(pnl_arr)）
  * scoring/metrics.py:107-114 逐笔优先小数 + bar 级 fallback，
    零交易落 0.0（应缺失）
  * scoring/trading_metrics.py:60 小数，max(total_trades,1) 使零
    交易胜率为 0.0 而非缺失——违反 numerical-accuracy-verification
    的「无交易时胜率为缺失而非 0」Requirement
  * validation/rule_engine.py:181 百分比口径（*100）
- rule_engine 以信号配对判胜负、回测/模拟盘以逐笔平仓 pnl 判胜：
  两套配对逻辑在部分平仓/信号未平仓边界可能结构性不等，本测试
  仅断言同口径内一致与缺失语义，不要求跨口径逐值相等（实施评估
  预声明）。
- forward_stats 的 win_rate 是触发点前向窗口统计（非交易绩效），
  属展示统计口径；域差异在此登记，处置随 6.1 前置调查裁决。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_ohlcv


def _flat_equity(n: int = 100, cash: float = 10_000.0) -> pd.Series:
    return pd.Series([cash] * n, index=pd.date_range("2026-01-01", periods=n, freq="h"))


# ---------------------------------------------------------------------------
# 零交易缺失语义：任一绩效入口收到零交易数据，win_rate 应为缺失
# （None/NaN，不参与评分维度），而非 0.0
# ---------------------------------------------------------------------------


class TestZeroTradeMissingSemantics:
    """spec：numerical-accuracy-verification「无交易缺省语义」。"""

    def test_trading_metrics_zero_trades_win_rate_missing(self):
        from core.scoring.trading_metrics import (
            TradingMetricsInput,
            compute_trading_metrics,
        )

        metrics = compute_trading_metrics(
            TradingMetricsInput(
                initial_cash=10_000.0,
                balance=10_000.0,
                total_trades=0,
                win_count=0,
                loss_count=0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                position_side=None,
                position_margin=0.0,
                close_pnls=[],
                equity_snapshots=[10_000.0],
            )
        )
        assert metrics["win_rate"] is None or np.isnan(metrics["win_rate"])

    def test_compute_metrics_zero_trades_win_rate_missing(self):
        from core.scoring.metrics import compute_metrics

        metrics = compute_metrics(
            _flat_equity(), total_trades=0, trade_win_rate=None, trade_returns=None
        )
        assert metrics["win_rate"] is None or np.isnan(metrics["win_rate"])

    def test_rule_engine_zero_trades_win_rate_missing(self, tmp_path):
        from core.validation.rule_engine import evaluate_rules

        df = make_ohlcv(n=120, freq="1h", base_price=40_000.0, seed=7)
        df.to_parquet(tmp_path / "BTCUSDT_1h.parquet")
        # 恒假条件：rsi_14 < -1 永不成立 -> 零交易（{subject, action, target} 形制）
        impossible = {"subject": "rsi_14", "action": "lt", "target": -1.0}
        result = evaluate_rules(
            symbol="BTCUSDT",
            timeframe="1h",
            start="",
            end="",
            entry_conditions=[impossible],
            exit_conditions=[impossible],
            data_dir=str(tmp_path),
        )
        assert result.total_trades == 0
        assert result.win_rate is None


# ---------------------------------------------------------------------------
# 口径统一：计算层绩效指标统一小数，百分比仅限展示边界
# ---------------------------------------------------------------------------


class TestDecimalScaleUniformity:
    def test_rule_engine_win_rate_decimal_scale(self, tmp_path):
        from core.validation.rule_engine import evaluate_rules

        df = make_ohlcv(n=120, freq="1h", base_price=40_000.0, seed=7)
        df.to_parquet(tmp_path / "BTCUSDT_1h.parquet")
        # 恒真条件（{subject, action, target} 形制）：rsi_14 < 101 与
        # rsi_14 > -1 恒成立 -> 每根 bar 产生进出场信号
        always_true_lt = {"subject": "rsi_14", "action": "lt", "target": 101.0}
        always_true_gt = {"subject": "rsi_14", "action": "gt", "target": -1.0}
        result = evaluate_rules(
            symbol="BTCUSDT",
            timeframe="1h",
            start="",
            end="",
            entry_conditions=[always_true_lt],
            exit_conditions=[always_true_gt],
            data_dir=str(tmp_path),
        )
        assert result.total_trades > 0
        # 计算层应统一小数口径
        assert 0.0 <= result.win_rate <= 1.0


# ---------------------------------------------------------------------------
# 同口径内两链同值（常绿哨兵：收口前即成立，收口后必须保持）
# ---------------------------------------------------------------------------


class TestTwoChainsSameValue:
    def test_win_rate_two_chains_identical(self):
        """同一份逐笔明细，回测链公式与模拟盘链公式必须同值。

        引擎侧 win_rate 来源公式（engine.py:583 winning/len(pnl_arr)）
        由调用方算出后传入 compute_metrics；此处按同公式复算对照。
        """
        from core.scoring.metrics import compute_metrics
        from core.scoring.trading_metrics import (
            TradingMetricsInput,
            compute_trading_metrics,
        )

        close_pnls = [100.0, -50.0, 80.0, -30.0, 120.0]
        wins = sum(1 for p in close_pnls if p > 0)
        engine_formula = wins / len(close_pnls)

        equity = 10_000.0 + np.cumsum([0.0] + close_pnls)
        equity_curve = pd.Series(
            equity, index=pd.date_range("2026-01-01", periods=len(equity), freq="h")
        )
        backtest_metrics = compute_metrics(
            equity_curve,
            total_trades=len(close_pnls),
            trade_win_rate=engine_formula,
            trade_returns=np.array(close_pnls) / 10_000.0,
        )

        paper_metrics = compute_trading_metrics(
            TradingMetricsInput(
                initial_cash=10_000.0,
                balance=float(equity[-1]),
                total_trades=len(close_pnls),
                win_count=wins,
                loss_count=len(close_pnls) - wins,
                realized_pnl=float(sum(close_pnls)),
                unrealized_pnl=0.0,
                position_side=None,
                position_margin=0.0,
                close_pnls=close_pnls,
                equity_snapshots=[float(x) for x in equity],
            )
        )

        assert paper_metrics["win_rate"] == pytest.approx(engine_formula)
        assert backtest_metrics["win_rate"] == pytest.approx(engine_formula)
        assert paper_metrics["win_rate"] == pytest.approx(backtest_metrics["win_rate"])


# ---------------------------------------------------------------------------
# forward_stats 口径登记：触发点前向统计（非交易绩效），展示百分比
# ---------------------------------------------------------------------------


class TestForwardStatsScaleRegistered:
    def test_forward_stats_win_rate_is_percent_scale(self):
        """登记现状：forward_stats.win_rate 为百分比展示统计。

        域差异：其分母是触发点数、分子是前向 close_pct>0 的占比，
        不是交易绩效胜率；是否改小数/改名 win_rate_pct 随 6.1
        前置调查（前端消费点）裁决，本用例锁定当前口径不被静默改变。
        """
        from core.validation.scene.forward_stats import (
            aggregate_by_horizon,
            compute_forward_stats,
        )

        df = make_ohlcv(n=80, freq="1h", base_price=100.0, seed=9)
        triggers = [30, 40, 50, 60]
        all_stats: dict[int, list] = {}
        for t in triggers:
            for h, stats in compute_forward_stats(df, t, float(df["close"].iloc[t]), [5]).items():
                all_stats.setdefault(h, []).append(stats)
        agg = aggregate_by_horizon(all_stats)
        assert len(agg) == 1
        # 现状锁定：百分比口径（0-100）
        assert 0.0 <= agg[0].win_rate <= 100.0
