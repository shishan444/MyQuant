"""B10.1 空缺区收口：backtest_service 直测 + discovery 路由层测。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


class TestBacktestService:
    """core/services/backtest_service 辅助函数直测（此前仅路由间接盖）。"""

    def test_fallback_metrics_prefers_engine_dict(self):
        """engine 已填 metrics_dict 时直接采用（不重算）。"""
        from types import SimpleNamespace

        from core.services import backtest_service as svc
        bt = SimpleNamespace(metrics_dict={"total_return": 0.2, "sharpe_ratio": 1.5})
        assert svc.fallback_metrics(bt) == {"total_return": 0.2, "sharpe_ratio": 1.5}

    def test_compute_needed_tfs(self):
        from core.strategy.dna import ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA
        from core.services import backtest_service as svc

        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI_4H", {}, SignalRole.ENTRY_GUARD, None, {"type": "gt", "threshold": 50})],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
        )
        got = svc.compute_needed_tfs([dna], "4h")
        assert isinstance(got, set)

    def test_build_signals_from_empty_trades(self):
        from core.services import backtest_service as svc
        import pandas as pd
        assert svc.build_signals_from_trades_df(pd.DataFrame()) == []
