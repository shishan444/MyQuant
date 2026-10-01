"""trading_metrics 特征测试网。

tests/trading/test_trading_metrics.py 已锚定主路径（equity/win_rate/
profit_factor/max_drawdown 绝对额）；本文件补边界分支：
drawdown 百分比与 peak<=0 防御、微小亏损分母下限、零交易均值、
initial_cash<=0 出口。期望值独立手算；CARD-07 后另锚定坏数据
防御语义（涉安：非有限快照跳点、计数矛盾置 None）。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.trading_metrics import TradingMetricsInput, compute_trading_metrics


def _data(**kw):
    base = dict(
        initial_cash=10000.0,
        balance=10500.0,
        total_trades=4,
        win_count=3,
        loss_count=1,
        realized_pnl=500.0,
        unrealized_pnl=0.0,
        position_side=None,
        position_margin=0.0,
        close_pnls=[200.0, 150.0, 100.0, -50.0],
        equity_snapshots=[10000.0, 10200.0, 10100.0, 10500.0],
    )
    base.update(kw)
    return TradingMetricsInput(**base)


class TestDrawdownPercent:
    def test_known_peak_trough_percent(self):
        # peak=12000, trough=6000 → dd=6000, pct=0.5 → 50.0
        m = compute_trading_metrics(_data(equity_snapshots=[10000.0, 12000.0, 6000.0]))
        assert m["max_drawdown"] == 6000.0
        assert m["max_drawdown_pct"] == 50.0

    def test_negative_peak_pct_guarded_to_zero(self):
        # 全负权益：dd=50（-50 到 -100）但 peak<=0 → pct=0.0（现状防御）
        m = compute_trading_metrics(_data(equity_snapshots=[-50.0, -100.0, -60.0]))
        assert m["max_drawdown"] == 50.0
        assert m["max_drawdown_pct"] == 0.0


class TestProfitFactorEdge:
    def test_tiny_loss_denominator_floor(self):
        # |gross_loss|=1e-12 < 1e-8 → 分母下限 1e-8 → pf=100/1e-8=1e10
        m = compute_trading_metrics(_data(close_pnls=[100.0, -1e-12]))
        assert m["profit_factor"] == pytest.approx(1e10)

    def test_all_zero_pnls_is_none(self):
        # gross_loss=0 → inf → 出口映射 None（与全胜同路径）
        m = compute_trading_metrics(_data(close_pnls=[0.0, 0.0]))
        assert m["profit_factor"] is None


class TestZeroTradeAndZeroCash:
    def test_zero_trades_avg_pnl_divides_by_one(self):
        # max(total_trades, 1)：零交易 avg = total_pnl/1
        m = compute_trading_metrics(_data(
            total_trades=0, win_count=0, loss_count=0, close_pnls=[]))
        assert m["avg_trade_pnl"] == 500.0

    def test_zero_initial_cash_total_return_guarded(self):
        # initial_cash<=0 → total_return=0.0（不除零）
        m = compute_trading_metrics(_data(initial_cash=0.0, balance=100.0))
        assert m["total_return"] == 0.0
        assert m["total_return_pct"] == 0.0


class TestBadDataDefense:
    """CARD-07 坏数据防御（涉安锚点）：聚合层跳点不抛错。

    与统计工具层（statistics.py）显式 ValueError 是有意分层——本模块
    处理 DB 聚合快照，坏点跳过；计数矛盾产 >1/<0 毒胜率改置 None。
    """

    def test_leading_nan_no_longer_kills_drawdown(self):
        # 旧版首位 NaN 使比较链永假、peak 卡死静默丢全部回撤；
        # 现跳过 NaN 以有限点真实峰谷计算：10000→9500 → 500 / 5%
        m = compute_trading_metrics(_data(
            equity_snapshots=[float("nan"), 10000.0, 9500.0]))
        assert m["max_drawdown"] == 500.0
        assert m["max_drawdown_pct"] == 5.0

    def test_all_bad_snapshots_same_as_empty(self):
        # 全坏点与空序列同语义：回撤 0.0（不产 NaN/inf）
        m = compute_trading_metrics(_data(
            equity_snapshots=[float("nan"), float("inf"), float("nan")]))
        assert m["max_drawdown"] == 0.0
        assert m["max_drawdown_pct"] == 0.0

    def test_midway_inf_skipped(self):
        # 中途 inf 跳过：旧版会产 inf 回撤 / NaN 百分比；
        # 有限点 [10000, 9000] → 1000 / 10%
        m = compute_trading_metrics(_data(
            equity_snapshots=[10000.0, float("inf"), 9000.0]))
        assert m["max_drawdown"] == 1000.0
        assert m["max_drawdown_pct"] == 10.0

    def test_win_count_above_total_is_missing(self):
        # 计数矛盾（win>total）→ win_rate None（旧版 >1 毒值）；透传字段保留
        m = compute_trading_metrics(_data(total_trades=10, win_count=12))
        assert m["win_rate"] is None
        assert m["win_count"] == 12

    def test_negative_counts_are_missing(self):
        # win 为负 / total 为负（旧版 0/-3=-0.0 毒值）→ 均置 None
        m1 = compute_trading_metrics(_data(total_trades=10, win_count=-2))
        m2 = compute_trading_metrics(_data(total_trades=-3, win_count=0))
        assert m1["win_rate"] is None
        assert m2["win_rate"] is None


class TestRoundingAnchors:
    def test_win_rate_rounded_four_digits(self):
        # 3/7=0.428571... → 0.4286
        m = compute_trading_metrics(_data(
            total_trades=7, win_count=3, loss_count=4,
            close_pnls=[100.0, -50.0, 80.0, -30.0, -20.0, 60.0, -10.0]))
        assert m["win_rate"] == 0.4286
