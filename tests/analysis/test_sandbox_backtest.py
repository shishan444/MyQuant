"""M6 沙盒回测测试（improve-mcp-agent-loop 6.1/6.2）。

三断言：直返 metrics 不产生验证记录；挂假设后同区间隔离验证被 409
（窥探拦截——沙盒区间计入判定并集）；超 bar 上限拒绝。
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import (
    ResearchError, register_hypothesis)
from core.analysis.validation_service import (
    SANDBOX_MAX_BARS, run_isolated_backtest, run_sandbox_backtest)
from core.persistence.db_ext import init_db_ext, save_strategy
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)

_CONDS = {
    "when_conditions": [{"subject": "rsi_14", "action": "lt", "target": 35}],
    "then_conditions": [{"subject": "rsi_70", "action": "gt", "target": 60}],
}


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(9)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


@pytest.fixture(scope="module")
def strategy_id_module(tmp_path_factory):
    """模块级策略 + 库（沙盒/隔离共用一条策略）。"""
    db = tmp_path_factory.mktemp("sb_mod") / "q.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_sb", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return db, "st_sb"


class TestSandboxBacktest:
    def test_returns_metrics_no_validation_record(
            self, strategy_id_module, data_dir):
        db, sid = strategy_id_module
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-20",
            fee=0.002, slippage=0.001, init_cash=50000.0)
        assert out["sandbox"] is True
        assert set(out["metrics"]) >= {"total_return", "sharpe_ratio",
                                       "max_drawdown", "total_trades"}
        # 不产生验证记录（快速试错不是证据）
        assert rr.list_validation_records(db) == []
        # 0.2.11 四元组改造冒烟：trade_returns 是判定通道不进沙盒响应
        # （沙盒不判门槛不披露 CI——评估差异 1 回归锚）
        assert "trade_returns" not in out
        assert "trade_returns" not in out["metrics"]

    def test_hypothesis_linked_sandbox_enters_union(
            self, data_dir, strategy_id_module):
        """挂假设的沙盒区间计入判定并集——同区间隔离验证被 409。"""
        db, sid = strategy_id_module
        hid = register_hypothesis(
            db, conditions=_CONDS, statement="s",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2023-01-01T00:00:00Z",
                          "end": "2023-02-01T00:00:00Z"}])["hypothesis_id"]
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-20",
            hypothesis_id=hid)
        assert out["hypothesis_id"] == hid
        # 同区间隔离验证：区间重叠被拒（窥探拦截）
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(
                db, data_dir, hypothesis_id=hid, strategy_id=sid,
                request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                               "start": "2023-03-05T00:00:00Z",
                               "end": "2023-03-15T00:00:00Z"})
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP"
        judged = ei.value.detail["judged_ranges"]
        assert any(r["start"].startswith("2023-03-01") for r in judged), \
            "沙盒区间应出现在判定并集明细中"

    def test_over_limit_rejected(self, strategy_id_module, data_dir):
        db, sid = strategy_id_module
        # 4h × 60000 bar ≈ 27 年 > 50k 上限
        with pytest.raises(ResearchError) as ei:
            run_sandbox_backtest(
                db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                timeframe="4h", start="1990-01-01", end="2026-01-01")
        assert ei.value.code == "RESEARCH_SANDBOX_LIMIT"
        assert ei.value.detail["max"] == SANDBOX_MAX_BARS

    def test_adhoc_log_without_hypothesis(self, strategy_id_module, data_dir):
        """不挂假设：adhoc 日志留痕（hypothesis_id=None）。"""
        db, sid = strategy_id_module
        run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10")
        logs = rr.list_research_log(db)
        assert any(e["entry_type"] == "sandbox_backtest"
                   and e["hypothesis_id"] is None for e in logs)
