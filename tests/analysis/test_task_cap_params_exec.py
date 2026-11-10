"""任务级两参进隔离验证执行参数面（A3——_params_exec_keys 五键化）。

D2：ValidationRecord.params 实际生效实参键集纯扩两可选键（传了才
写）；未传两参的验证记录 params 逐位不变（存量锚——三键）；halt
参数不进键集（D3 范围界定——隔离验证路不进熔断）；ruler 的
init_cash 取值链零影响（键集扩而 init_cash 键语义与取值不动——
ruler 回归由既有模板测试覆盖，本文件钉 params 键集面）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.validation_service import (
    _params_exec_keys,
    random_window_test,
    run_isolated_backtest,
)
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)


class TestParamsExecKeysUnit:
    """纯函数四态：三键逐位／五键纯扩／halt 不进／缺键容错。"""

    def test_three_keys_bitwise_when_unset(self):
        out = _params_exec_keys({"init_cash": 100000.0, "fee": 0.001,
                                 "slippage": 0.0005})
        assert out == {"init_cash": 100000.0, "fee": 0.001,
                       "slippage": 0.0005}
        assert list(out) == ["init_cash", "fee", "slippage"], \
            "键序逐位不变（json 序列化面）"

    def test_five_keys_when_passed(self):
        out = _params_exec_keys({"init_cash": 1.0, "fee": 0.0,
                                 "slippage": 0.0,
                                 "leverage": 3, "margin_cap": 0.6})
        assert out == {"init_cash": 1.0, "fee": 0.0, "slippage": 0.0,
                       "leverage": 3, "margin_cap": 0.6}

    def test_single_param_partial(self):
        """只传一参：只写一参键（③层不生效但 params 回显实参）。"""
        out = _params_exec_keys({"init_cash": 1.0, "fee": 0.0,
                                 "slippage": 0.0, "margin_cap": 0.45})
        assert "leverage" not in out
        assert out["margin_cap"] == 0.45

    def test_halt_param_never_in_keyset(self):
        """halt_drawdown_pct 不进 _params_exec_keys（D3 范围界定——
        隔离验证路不进熔断；detail 即使携带也不提取）。"""
        out = _params_exec_keys({"init_cash": 1.0, "fee": 0.0,
                                 "slippage": 0.0,
                                 "halt_drawdown_pct": 0.1})
        assert "halt_drawdown_pct" not in out
        assert set(out) == {"init_cash", "fee", "slippage"}


def _dna() -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 65}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())


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
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


@pytest.fixture()
def hypothesis_id(db) -> str:
    return register_hypothesis(
        db,
        conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        },
        statement="A3 执行参数五键化",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]


def _saved_params(db, record_id) -> dict:
    rec = rr.get_validation_record_by_id(db, record_id)
    return json.loads(rec["params"])


class TestIsolatedParamsKeyset:
    def test_passed_params_in_record(self, db, data_dir, hypothesis_id):
        save_strategy(db, strategy_id="st_a3", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_a3",
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01T00:00:00Z",
                           "end": "2023-04-01T00:00:00Z"},
            leverage=3, margin_cap=0.6)
        params = _saved_params(db, out["record_id"])
        assert params["leverage"] == 3
        assert params["margin_cap"] == 0.6
        # 三键语义不动（init_cash 取值链零影响——键与值两锚）
        assert params["init_cash"] == 100000.0
        assert params["fee"] == 0.001

    def test_unset_params_three_keys_bitwise(self, db, data_dir,
                                             hypothesis_id):
        """未传两参：params 键集与值逐位=存量三键（A3 存量锚）。"""
        save_strategy(db, strategy_id="st_a3b", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_a3b",
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-05-01T00:00:00Z",
                           "end": "2023-06-01T00:00:00Z"})
        params = _saved_params(db, out["record_id"])
        assert set(params) == {"request_range", "range_hash",
                               "threshold_profile", "init_cash", "fee",
                               "slippage"}, "键集不含任务级两键"
        assert "leverage" not in params and "margin_cap" not in params

    def test_random_window_task_params_passed(
            self, db, data_dir, hypothesis_id):
        save_strategy(db, strategy_id="st_a3c", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = random_window_test(
            db, data_dir, strategy_id="st_a3c",
            symbol="BTCUSDT", timeframe="4h",
            data_start="2023-03-01T00:00:00Z",
            data_end="2023-06-01T00:00:00Z",
            hypothesis_id=hypothesis_id,
            leverage=2, margin_cap=0.45)
        params = _saved_params(db, out["record_id"])
        assert params["leverage"] == 2
        assert params["margin_cap"] == 0.45
