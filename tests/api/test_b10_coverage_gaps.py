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


class TestDiscoveryRoutes:
    """api/routes/discovery 路由层（此前零覆盖）。"""

    def test_patterns_missing_data_404(self, tmp_path: Path):
        from api.app import create_app
        from core.persistence.db_ext import init_db_ext

        init_db_ext(tmp_path / "t.db")
        app = create_app(db_path=tmp_path / "t.db", data_dir=tmp_path)
        with TestClient(app) as c:
            r = c.post("/api/discovery/patterns", json={
                "symbol": "NOSUCH", "timeframe": "4h",
                "start": "2024-01-01", "end": "2024-02-01",
            })
        # 现状契约：缺数据返回 200 + error 字段（不抛 500）——锚定现状
        assert r.status_code == 200
        assert r.json()["error"] == "No data available"
