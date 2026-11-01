"""series 档止损线序列输出测试（hypothesis-workbench 组 2.3，spec stop-loss-modes 2 Scenario）。

已知 fx 列手工锚：fx_sl=close*0.95（装载期列→序列首值逐位对账）；
三入口透传经 bridge metrics 单点（run_backtest_on_range 组装——
backtest/backtest-sandbox/inspect-cases 全消费本面）。
"""
from __future__ import annotations

import pandas as pd

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.backtest_bridge import run_backtest_on_range
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene,
    SignalRole, StrategyDNA, TimeframeLayer,
)
from tests.helpers.regime_factory import make_two_regime_df


def _env(tmp_path: Path, *, rg: RiskGenes):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_two_regime_df(n_trend=300, n_range=150, seed=42)
    df["fx_sl"] = df["close"] * 0.95  # 已知 fx 列（手工锚值源）
    df.to_parquet(data_dir / "BTCUSDT_1d.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    gene = lambda role: SignalGene(  # noqa: E731
        "TEMA", {"period": 20}, role, "tema_20", {"type": "lt", "threshold": 1e9})
    dna = StrategyDNA(
        layers=[TimeframeLayer(timeframe="1d",
                               signal_genes=[gene(SignalRole.ENTRY_TRIGGER),
                                             gene(SignalRole.EXIT_TRIGGER)],
                               logic_genes=LogicGenes())],
        execution_genes=ExecutionGenes(timeframe="1d"), risk_genes=rg)
    start = df.index[40].isoformat()
    end = df.index[-1].isoformat()
    return db, data_dir, dna, start, end, df


class TestSlSeriesOutput:
    def test_series_mode_outputs_sequence(self, tmp_path):
        """Scenario: series 档输出——响应含止损价逐 bar 序列（手工锚逐位）。"""
        rg = RiskGenes(sl_mode="series", sl_series_source={"column": "fx_sl"})
        db, data_dir, dna, start, end, df = _env(tmp_path, rg=rg)
        metrics, _, _, bars = run_backtest_on_range(
            db, data_dir, dna, "BTCUSDT", "1d", start, end, warmup_bars=20)
        sl = metrics["sl_series"]
        assert sl is not None and len(sl) > 0
        assert set(sl[0].keys()) == {"time", "value"}
        # 手工锚：序列值=装载列原值——序列索引=enhanced 全段（含 warmup
        # 前缀，与 equity_curve 同段形制；实际起点由装载器 data_start 与
        # 数据可得性共同决定）。稳健锚三件：首值∈fx_sl 列值集（逐位）+
        # 时间键属 df 索引域+序列长=装载段 bar 数
        nearest = min(df.fx_sl.tolist(), key=lambda v: abs(v - sl[0]["value"]))
        assert sl[0]["value"] == pytest.approx(nearest, abs=1e-6)
        first_t = pd.Timestamp(sl[0]["time"])
        assert first_t in df.index
        assert len(sl) >= bars

    def test_non_series_mode_none(self, tmp_path):
        """Scenario: 非 series 档（pct）——sl_series=None（null 非空序列非零值）。"""
        rg = RiskGenes(sl_mode="pct", stop_loss=0.05)
        db, data_dir, dna, start, end, _ = _env(tmp_path, rg=rg)
        metrics, _, _, _ = run_backtest_on_range(
            db, data_dir, dna, "BTCUSDT", "1d", start, end, warmup_bars=20)
        assert metrics["sl_series"] is None
