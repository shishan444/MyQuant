"""Tests for stop_check propagation through data loading and evolution.

Covers:
1. compute_all_indicators respects stop_check (data loading blind spot fix)
2. load_and_prepare_df propagates stop_check
3. EvolutionEngine.evolve respects stop_check (evaluation blind spot fix)
4. Runner passes controller.check_stop through the full chain
5. _compute_indicator returns dict (no df.copy overhead)
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 500):
    """Generate synthetic OHLCV data for testing."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(42)
    close = 40000.0 * np.cumprod(1 + rng.standard_normal(n) * 0.01)
    df = pd.DataFrame({
        "open": close * (1 + rng.standard_normal(n) * 0.002),
        "high": close * (1 + np.abs(rng.standard_normal(n)) * 0.005),
        "low": close * (1 - np.abs(rng.standard_normal(n)) * 0.005),
        "close": close,
        "volume": rng.integers(100, 10000, size=n).astype(float),
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))
    df.index.name = "timestamp"
    return df


# ---------------------------------------------------------------------------
# Test 1: _compute_indicator returns dict (no df.copy overhead)
# ---------------------------------------------------------------------------

class TestComputeIndicatorReturnsDict:
    """Verify _compute_indicator returns dict[str, Series], not DataFrame."""

    def test_returns_dict_for_ema(self):
        from core.features.indicators import _compute_indicator
        df = _make_ohlcv()
        result = _compute_indicator(df, "EMA", {"period": 20})
        assert isinstance(result, dict)
        assert "ema_20" in result

    def test_returns_dict_for_macd(self):
        from core.features.indicators import _compute_indicator
        df = _make_ohlcv()
        result = _compute_indicator(df, "MACD", {"fast": 12, "slow": 26, "signal": 9})
        assert isinstance(result, dict)
        assert "macd_12_26_9" in result
        assert "macd_signal_12_26_9" in result

    def test_returns_empty_dict_for_unknown(self):
        from core.features.indicators import _compute_indicator
        df = _make_ohlcv()
        result = _compute_indicator(df, "NonExistentIndicator", {})
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_does_not_modify_input(self):
        from core.features.indicators import _compute_indicator
        df = _make_ohlcv()
        original_cols = set(df.columns)
        _compute_indicator(df, "EMA", {"period": 20})
        # Input DataFrame should not have new columns added
        assert set(df.columns) == original_cols


# ---------------------------------------------------------------------------
# Test 2: compute_all_indicators with stop_check
# ---------------------------------------------------------------------------

class TestComputeAllIndicatorsStopCheck:
    """Verify stop_check is honored during indicator computation."""

    def test_stop_check_called_during_computation(self):
        """stop_check callback should be called multiple times."""
        from core.features.indicators import compute_all_indicators

        df = _make_ohlcv()
        call_count = 0

        def counting_stop_check():
            nonlocal call_count
            call_count += 1

        compute_all_indicators(df, stop_check=counting_stop_check)
        # Should be called at least once per indicator param set
        assert call_count >= 10, f"stop_check only called {call_count} times"

    def test_stop_check_raises_stops_computation(self):
        """If stop_check raises, computation should stop immediately."""
        from core.features.indicators import compute_all_indicators

        df = _make_ohlcv()
        call_count = 0

        def raising_stop_check():
            nonlocal call_count
            call_count += 1
            if call_count >= 5:
                raise KeyboardInterrupt("stop requested")

        with pytest.raises(KeyboardInterrupt):
            compute_all_indicators(df, stop_check=raising_stop_check)

        # Should have stopped early (not computed all ~56 indicators)
        assert call_count >= 5

    def test_without_stop_check_works_as_before(self):
        """Without stop_check, behavior should be identical to before."""
        from core.features.indicators import compute_all_indicators

        df = _make_ohlcv()
        result = compute_all_indicators(df)
        # Should have computed many indicator columns
        indicator_cols = [c for c in result.columns if c not in df.columns]
        assert len(indicator_cols) >= 20

    def test_preserves_original_data(self):
        """Input DataFrame should not be modified."""
        from core.features.indicators import compute_all_indicators

        df = _make_ohlcv()
        original_cols = list(df.columns)
        original_len = len(df)

        result = compute_all_indicators(df)

        # Input should be unchanged
        assert list(df.columns) == original_cols
        assert len(df) == original_len
        # Result should have more columns
        assert len(result.columns) > len(df.columns)


# ---------------------------------------------------------------------------
# Test 3: load_and_prepare_df propagates stop_check
# ---------------------------------------------------------------------------

class TestLoadAndPrepareDfStopCheck:
    """Verify stop_check propagates through load_and_prepare_df."""

    def test_stop_check_propagated(self, tmp_path: Path):
        """stop_check should be passed to compute_all_indicators."""
        import pandas as pd
        from core.data.mtf_loader import load_and_prepare_df

        # Create a data directory with a parquet file
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df = _make_ohlcv()
        df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        call_count = 0

        def counting_stop_check():
            nonlocal call_count
            call_count += 1

        result = load_and_prepare_df(
            data_dir, "BTCUSDT", "4h",
            stop_check=counting_stop_check,
        )

        assert result is not None
        assert call_count >= 5

    def test_stop_check_raises_aborts_loading(self, tmp_path: Path):
        """If stop_check raises during indicator computation, loading should abort."""
        import pandas as pd
        from core.data.mtf_loader import load_and_prepare_df

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df = _make_ohlcv()
        df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        call_count = 0

        def raising_stop_check():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise KeyboardInterrupt("stop")

        with pytest.raises(KeyboardInterrupt):
            load_and_prepare_df(
                data_dir, "BTCUSDT", "4h",
                stop_check=raising_stop_check,
            )


# ---------------------------------------------------------------------------
# Test 4: EvolutionEngine.evolve with stop_check
# ---------------------------------------------------------------------------

class TestEvolutionEngineStopCheck:
    """Verify stop_check is honored between individual evaluations."""

    def _make_simple_dna(self):
        from core.strategy.dna import (
            SignalGene, SignalRole, LogicGenes, RiskGenes, StrategyDNA,
        )
        return StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           None, {"type": "gt", "threshold": 70}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.3),
        )

    def test_stop_check_stops_evaluation(self):
        """stop_check raising during evaluation should stop evolve()."""
        from core.evolution.engine import EvolutionEngine

        engine = EvolutionEngine(
            target_score=80, max_generations=5, population_size=6,
        )
        dna = self._make_simple_dna()

        eval_count = 0

        def slow_evaluate(ind):
            nonlocal eval_count
            eval_count += 1
            return float(eval_count)

        call_count = 0

        def raising_stop_check():
            nonlocal call_count
            call_count += 1
            # Let a few individuals evaluate, then stop
            if call_count >= 4:
                raise KeyboardInterrupt("stop evaluation")

        with pytest.raises(KeyboardInterrupt):
            engine.evolve(
                ancestor=dna,
                evaluate_fn=slow_evaluate,
                stop_check=raising_stop_check,
            )

        # Should have evaluated some individuals but not all
        assert eval_count >= 3, f"Only evaluated {eval_count} individuals before stop"

    def test_stop_check_called_between_individuals(self):
        """stop_check should be called every 3 individuals."""
        from core.evolution.engine import EvolutionEngine

        engine = EvolutionEngine(
            target_score=80, max_generations=2, population_size=6,
        )
        dna = self._make_simple_dna()

        check_count = 0

        def counting_stop_check():
            nonlocal check_count
            check_count += 1

        def simple_evaluate(ind):
            return 50.0

        engine.evolve(
            ancestor=dna,
            evaluate_fn=simple_evaluate,
            stop_check=counting_stop_check,
        )

        # With 6 individuals per gen, 2 gens:
        # Each gen: checks at idx=3 (one check per gen)
        # = 2 checks total minimum
        assert check_count >= 2, f"stop_check called {check_count} times, expected >= 2"


# ---------------------------------------------------------------------------
# Test 5: Runner passes stop_check through full chain
# ---------------------------------------------------------------------------

class TestRunnerStopCheckChain:
    """Verify runner.py passes controller.check_stop to data loading and engine."""

    def test_stop_during_data_loading(self, tmp_path: Path):
        """Stop signal during data loading should be detected."""
        from api.runner import EvolutionRunner, TaskController, TaskStopRequested
        from core.persistence.db import save_task, get_task
        from core.persistence.db_ext import init_db_ext as init_db  # B3d.1：对齐生产全列库
        from core.strategy.dna import (
            SignalGene, SignalRole, LogicGenes, RiskGenes, ExecutionGenes, StrategyDNA,
        )

        db_path = tmp_path / "test_stop.db"
        init_db(db_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           None, {"type": "lt", "threshold": 30}),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.3),
        )
        save_task(db_path, "stop-during-load", 80.0, "profit_first", "BTCUSDT", "4h", dna)
        task_row = dict(get_task(db_path, "stop-during-load"))

        # Create a data file so load_and_prepare_df proceeds past file check
        import pandas as pd
        df = _make_ohlcv()
        df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        runner = EvolutionRunner(db_path=db_path, data_dir=data_dir)

        # Patch compute_all_indicators to simulate stop during indicator computation
        original_compute = __import__(
            "core.features.indicators", fromlist=["compute_all_indicators"]
        ).compute_all_indicators

        stop_check_calls = 0

        def compute_with_stop(*args, **kwargs):
            # B3d.1 重锚定：在指标计算的 stop 检查序列第 5 次触发停止——
            # 模拟「数据加载中途 stop 信号到达」。旧实现依赖第二次
            # compute 调用（缺列库异常 fallback 路径的偶发产物，全列库下
            # 不再发生；真跑进化会触发 vectorbt merge_dicts 死循环的
            # 既有病）；stop 检查贯穿加载链的契约由本形态直接验证
            nonlocal stop_check_calls
            inner_stop = kwargs.get("stop_check")

            def stop_at_nth():
                nonlocal stop_check_calls
                stop_check_calls += 1
                if stop_check_calls >= 5:
                    raise TaskStopRequested()
                if inner_stop:
                    inner_stop()

            return original_compute(*args, stop_check=stop_at_nth)

        with patch("core.features.indicators.compute_all_indicators", side_effect=compute_with_stop):
            runner._run_task(task_row)

        # Task should be stopped (via TaskStopRequested)
        row = get_task(db_path, "stop-during-load")
        assert row["status"] == "stopped"
        assert runner._active_task_id is None
