"""未裁决行为的现状锚定（V3 审计 D 类隔离区）。

本文件锚定的行为**不是规格**：实现与直觉/自洽性存在分歧，但产品
口径尚未裁决（无 spec 依据、无问题卡修复决定）。每条例外注明分歧
点；待口径裁决后，胜出行为迁回对应测试文件（或随修复更新），
败出行为随修复删除。不伪装成规格，不以此阻止裁决。
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.metrics import compute_metrics
from core.scoring.scorer import RequirementsConfig, compute_fitness
from core.scoring.trading_metrics import TradingMetricsInput, compute_trading_metrics


def _series(values, freq="4h"):
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def _scorer_metrics(**overrides):
    base = {
        "annual_return": 0.20, "max_drawdown": -0.25,
        "win_rate": 0.45, "total_trades": 15, "profit_factor": 1.5,
        "sharpe_ratio": 1.8, "calmar_ratio": 2.5,
    }
    base.update(overrides)
    return base


def _trading_data(**kw):
    base = dict(
        initial_cash=10000.0, balance=10500.0, total_trades=4, win_count=3,
        loss_count=1, realized_pnl=500.0, unrealized_pnl=0.0,
        position_side=None, position_margin=0.0,
        close_pnls=[200.0, 150.0, 100.0, -50.0],
        equity_snapshots=[10000.0, 10200.0, 10100.0, 10500.0],
    )
    base.update(kw)
    return TradingMetricsInput(**base)


class TestKnownUnadjudicatedBehaviors:

    def test_win_rate_zero_met_true_ratio_zero_inconsistency(self):
        """分歧点：win_rate=0 且 min_win_rate=0 时 met=True（0>=0 成立）
        而 ratio=0.0（actual>0 才给 1.0）——满足标记与比值自相矛盾。
        消费方若按 met 展示「达标」而按 ratio 展示进度条会出现 0% 达标。
        """
        result = compute_fitness(_scorer_metrics(win_rate=0.0))
        dim = result["satisfaction"]["win_rate"]
        assert dim["met"] is True
        assert dim["ratio"] == 0.0

    def test_zero_trades_early_exit_passes_objective_name_through(self):
        """分歧点：早退发生在 objective 校验之前，非法名原样透传
        （objective_name="bogus"）；非早退路径则回退 "sharpe"——同名
        参数两条路径行为不一致。
        """
        req = RequirementsConfig(objective="bogus")
        result = compute_fitness({"total_trades": 0}, requirements=req)
        assert result["objective_name"] == "bogus"
        assert result["fitness"] == 0.0

    def test_all_bad_snapshots_same_output_as_empty(self):
        """分歧点（CARD-07 有意防御但口径未裁决）：全坏点快照
        [NaN, inf, NaN] 与空序列产出完全相同（回撤 0.0）——「无数据」
        与「数据全坏」不区分，消费方无法从结果察觉上游坏数据。
        """
        m_bad = compute_trading_metrics(_trading_data(
            equity_snapshots=[float("nan"), float("inf"), float("nan")]))
        m_empty = compute_trading_metrics(_trading_data(equity_snapshots=[]))
        assert m_bad["max_drawdown"] == m_empty["max_drawdown"] == 0.0
        assert m_bad["max_drawdown_pct"] == m_empty["max_drawdown_pct"] == 0.0

    def test_plain_index_monthly_resample_exception_swallowed(self):
        """分歧点：非 DatetimeIndex 时 resample 抛异常被裸 except 吞掉
        → monthly_consistency=0.0——「无法计算」折叠为「最差一致性」，
        语义未裁决（是否应缺省 None 或显式报错）。
        """
        eq = _series([100.0 + i for i in range(45)])
        m = compute_metrics(eq.set_axis(range(45)), total_trades=2,
                            trade_returns=np.array([0.05, -0.02]))
        assert m["monthly_consistency"] == 0.0
