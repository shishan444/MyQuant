"""bridge 装配段提取基线锚（paper-trading-revamp 组 A1）。

固定样本（make_two_regime_df seed=42+TEMA 恒真基因）跑 run_backtest_on_range，
metrics 全量数值断言=组 A2 装配段提取前后「逐位一致」的对照源
（A2 提取共享装配函数后本文件零改动仍绿=bridge 行为不变锚）。
TEMA 双基因同阈同向致同 bar 出场冲突——entries=0/exits=计数 为
确定性形态（非退化：signal_counts.exits/warmup/bars 均非平凡）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.backtest_bridge import run_backtest_on_range
from tests.analysis.test_staged_validation import _tema_dna
from tests.helpers.regime_factory import make_two_regime_df

SYMBOL = "BTCUSDT"


@pytest.fixture()
def env(tmp_path: Path):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_two_regime_df(n_trend=300, n_range=150, seed=42)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    return {"data_dir": data_dir, "df": df,
            "start": df.index[50].isoformat(), "end": df.index[-1].isoformat()}


class TestBridgeBaseline:
    def test_fixed_sample_metrics_baseline(self, env, tmp_path):
        metrics, trade_returns, warmup, bars = run_backtest_on_range(
            tmp_path / "q.db", env["data_dir"], _tema_dna(), SYMBOL, "1d",
            env["start"], env["end"], warmup_bars=30,
        )
        # A1 基线（2026-11-01 固化）——A2 提取后须逐位一致
        assert metrics == {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": None,
            "total_trades": 0,
            "alpha": 0.0,
            "benchmark_return": None,
            "excess_return": None,
            "market_annual_return": 0.0,
            "liquidated": False,
            "signal_counts": {"entries": 0, "exits": 400, "adds": 0, "reduces": 0},
        }
        assert warmup == 30
        assert bars == 400
        assert trade_returns is None
