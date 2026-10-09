"""Tests for extended database operations (v0.8 schema).

Covers:
- init_db_ext creates all 6 tables + schema_version
- Strategy CRUD (create, read, list with filtering/sorting, update, delete)
- Backtest result save and query
- Dataset metadata CRUD
- evolution_task extended columns read/write
- Migration idempotency (run twice without error)
- Backward compatibility (existing core db.py operations still work)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db import (
    get_task,
    init_db,
    list_all_tasks,
    save_history,
    save_task,
    update_task,
)
from core.persistence.db_ext import (
    delete_dataset,
    delete_strategy,
    get_dataset,
    get_strategy,
    get_backtest_result,
    get_verify_session,
    init_db_ext,
    list_backtest_results,
    list_datasets,
    list_strategies,
    list_verify_sessions,
    save_backtest_result,
    save_dataset_meta,
    save_strategy,
    save_verify_session,
    update_dataset_stats,
    update_strategy,
    update_verify_session,
)

# ── Fixtures ──

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_ext.db"

@pytest.fixture
def initialized_db(db_path: Path) -> Path:
    """Initialize with the extended schema."""
    init_db_ext(db_path)
    return db_path

def _sample_strategy_data(**overrides: Any) -> Dict[str, Any]:
    """Build a minimal strategy record."""
    base = {
        "strategy_id": "strat-001",
        "name": "RSI Reversal",
        "dna_json": json.dumps({"signal_genes": [{"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger", "condition": {"type": "lt", "threshold": 30}}]}),
        "source": "manual",
        "source_task_id": None,
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "best_score": 75.5,
        "generation": 0,
        "parent_ids": None,
        "tags": "reversal,rsi",
        "notes": "Test strategy",
    }
    base.update(overrides)
    return base

def _sample_backtest_data(**overrides: Any) -> Dict[str, Any]:
    """Build a minimal backtest_result record."""
    base = {
        "result_id": "bt-001",
        "strategy_id": "strat-001",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "data_start": "2024-01-01T00:00:00Z",
        "data_end": "2024-06-01T00:00:00Z",
        "init_cash": 100000.0,
        "fee": 0.001,
        "slippage": 0.0005,
        "total_return": 25.3,
        "sharpe_ratio": 1.8,
        "max_drawdown": -12.5,
        "win_rate": 0.62,
        "total_trades": 150,
        "total_score": 82.1,
        "template_name": "profit_first",
        "dimension_scores": json.dumps({"return": 90, "risk": 80}),
        "equity_curve": None,
        "trades_json": None,
        "run_source": "lab",
    }
    base.update(overrides)
    return base

def _sample_dataset_data(**overrides: Any) -> Dict[str, Any]:
    """Build a minimal dataset_meta record."""
    base = {
        "dataset_id": "ds-001",
        "symbol": "BTCUSDT",
        "interval": "4h",
        "parquet_path": "/data/btcusdt_4h.parquet",
        "row_count": 10000,
        "time_start": "2023-01-01T00:00:00Z",
        "time_end": "2024-01-01T00:00:00Z",
        "file_size_bytes": 2048000,
        "source": "csv_import",
        "format_detected": "ohlcv",
        "timestamp_precision": "h",
        "ohlcv_stats": json.dumps({"null_rows": 0}),
        "gap_count": 0,
        "quality_status": "good",
        "quality_notes": None,
        "import_batch_id": "batch-001",
    }
    base.update(overrides)
    return base

# ── Test: init_db_ext ──

class TestInitDbExt:
    def test_creates_all_six_tables(self, initialized_db: Path) -> None:
        """init_db_ext should create all 6 tables (3 original + 3 new)."""
        conn = sqlite3.connect(str(initialized_db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        conn.close()

        # Original 3 tables
        assert "evolution_task" in table_names
        assert "generation_snapshot" in table_names
        assert "evolution_history" in table_names
        # New 3 tables
        assert "strategy" in table_names
        assert "backtest_result" in table_names
        assert "dataset_meta" in table_names
        # 版本记账表（TD-10 换芯：schema_version → alembic_version）
        assert "alembic_version" in table_names

    def test_schema_version_recorded(self, initialized_db: Path) -> None:
        """版本记账行（TD-10 换芯后经 alembic_version）。"""
        conn = sqlite3.connect(str(initialized_db))
        rows = conn.execute(
            "SELECT version_num FROM alembic_version").fetchall()
        conn.close()
        assert len(rows) == 1, "恰一行当前版本"

    def test_evolution_task_has_extended_columns(self, initialized_db: Path) -> None:
        """evolution_task should have the new columns after migration."""
        conn = sqlite3.connect(str(initialized_db))
        cursor = conn.execute("PRAGMA table_info(evolution_task)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        # Original columns
        assert "task_id" in columns
        assert "status" in columns
        # Extended columns
        assert "champion_strategy_id" in columns
        assert "population_size" in columns
        assert "max_generations" in columns
        assert "elite_ratio" in columns
        assert "current_generation" in columns

    def test_migration_idempotent(self, db_path: Path) -> None:
        """Running init_db_ext twice must not raise any error."""
        init_db_ext(db_path)
        init_db_ext(db_path)  # Second call should be safe

        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        conn.close()
        assert "strategy" in table_names

# ── Test: Backward Compatibility ──

class TestBackwardCompatibility:
    """Existing core db.py operations must still work after migration."""

    def test_save_and_get_task_still_works(self, initialized_db: Path) -> None:
        from core.strategy.dna import StrategyDNA

        dna = StrategyDNA()
        save_task(
            db_path=initialized_db,
            task_id="compat-001",
            target_score=80.0,
            template="profit_first",
            symbol="BTCUSDT",
            timeframe="4h",
            initial_dna=dna,
        )
        task = get_task(initialized_db, "compat-001")
        assert task is not None
        assert task["task_id"] == "compat-001"

    def test_update_task_still_works(self, initialized_db: Path) -> None:
        from core.strategy.dna import StrategyDNA

        dna = StrategyDNA()
        save_task(initialized_db, "compat-002", 80.0, "profit_first",
                  "BTCUSDT", "4h", dna)
        update_task(initialized_db, "compat-002", status="completed",
                    stop_reason="target_reached")
        task = get_task(initialized_db, "compat-002")
        assert task["status"] == "completed"

    def test_list_all_tasks_still_works(self, initialized_db: Path) -> None:
        from core.strategy.dna import StrategyDNA

        dna = StrategyDNA()
        for i in range(3):
            save_task(initialized_db, f"compat-list-{i}", 80.0,
                      "profit_first", "BTCUSDT", "4h", dna)
        tasks = list_all_tasks(initialized_db)
        assert len(tasks) == 3

    def test_save_history_still_works(self, initialized_db: Path) -> None:
        from core.persistence.db import save_history, get_history

        save_history(initialized_db, "task-hist", 1, 75.0, 60.0,
                     json.dumps([]))
        hist = get_history(initialized_db, "task-hist")
        assert len(hist) == 1

# ── Test: Strategy CRUD ──

class TestStrategyCRUD:
    def test_save_and_get_strategy(self, initialized_db: Path) -> None:
        data = _sample_strategy_data()
        save_strategy(initialized_db, **data)
        result = get_strategy(initialized_db, "strat-001")
        assert result is not None
        assert result["strategy_id"] == "strat-001"
        assert result["name"] == "RSI Reversal"
        assert result["symbol"] == "BTCUSDT"
        assert result["timeframe"] == "4h"
        assert result["best_score"] == 75.5

    def test_get_strategy_not_found(self, initialized_db: Path) -> None:
        result = get_strategy(initialized_db, "nonexistent")
        assert result is None

    def test_list_strategies_basic(self, initialized_db: Path) -> None:
        indicators = [("RSI", 14), ("EMA", 20), ("MACD", 12)]
        for i, (ind, per) in enumerate(indicators):
            dna = json.dumps({"signal_genes": [{"indicator": ind, "params": {"period": per}, "role": "entry_trigger", "condition": {"type": "lt", "threshold": 30}}]})
            save_strategy(initialized_db, **_sample_strategy_data(
                strategy_id=f"strat-{i}", name=f"Strategy {i}", dna_json=dna,
            ))
        results = list_strategies(initialized_db)
        assert len(results) == 3

    def test_list_strategies_filter_by_symbol(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="btc-1", symbol="BTCUSDT",
        ))
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="eth-1", symbol="ETHUSDT",
        ))
        results = list_strategies(initialized_db, symbol="BTCUSDT")
        assert len(results) == 1
        assert results[0]["strategy_id"] == "btc-1"

    def test_list_strategies_filter_by_source(self, initialized_db: Path) -> None:
        dna_manual = json.dumps({"signal_genes": [{"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger", "condition": {"type": "lt", "threshold": 30}}]})
        dna_evo = json.dumps({"signal_genes": [{"indicator": "EMA", "params": {"period": 20}, "role": "entry_trigger", "condition": {"type": "price_above"}}]})
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="s-manual", source="manual", dna_json=dna_manual,
        ))
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="s-evo", source="evolution", dna_json=dna_evo,
        ))
        results = list_strategies(initialized_db, source="evolution")
        assert len(results) == 1
        assert results[0]["strategy_id"] == "s-evo"

    def test_list_strategies_sort_by_best_score_desc(self, initialized_db: Path) -> None:
        dna_low = json.dumps({"signal_genes": [{"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger", "condition": {"type": "lt", "threshold": 30}}]})
        dna_high = json.dumps({"signal_genes": [{"indicator": "EMA", "params": {"period": 20}, "role": "entry_trigger", "condition": {"type": "price_above"}}]})
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="low", best_score=50.0, dna_json=dna_low,
        ))
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="high", best_score=90.0, dna_json=dna_high,
        ))
        results = list_strategies(initialized_db, sort_by="best_score", sort_order="desc")
        assert results[0]["strategy_id"] == "high"

    def test_list_strategies_sort_by_created_at_asc(self, initialized_db: Path) -> None:
        dna_first = json.dumps({"signal_genes": [{"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger", "condition": {"type": "lt", "threshold": 30}}]})
        dna_second = json.dumps({"signal_genes": [{"indicator": "EMA", "params": {"period": 20}, "role": "entry_trigger", "condition": {"type": "price_above"}}]})
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="first", dna_json=dna_first,
        ))
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="second", dna_json=dna_second,
        ))
        results = list_strategies(initialized_db, sort_by="created_at", sort_order="asc")
        assert results[0]["strategy_id"] == "first"

    def test_list_strategies_with_limit(self, initialized_db: Path) -> None:
        indicators = [("RSI", 14), ("EMA", 20), ("MACD", 12), ("SMA", 50), ("BB", 20)]
        for i, (ind, per) in enumerate(indicators):
            dna = json.dumps({"signal_genes": [{"indicator": ind, "params": {"period": per}, "role": "entry_trigger", "condition": {"type": "lt", "threshold": 30}}]})
            save_strategy(initialized_db, **_sample_strategy_data(
                strategy_id=f"strat-{i}", dna_json=dna,
            ))
        results = list_strategies(initialized_db, limit=3)
        assert len(results) == 3

    def test_list_strategies_filter_by_tags(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="tagged-rsi", tags="reversal,rsi",
        ))
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="tagged-macd", tags="trend,macd",
        ))
        results = list_strategies(initialized_db, tags="rsi")
        assert len(results) == 1
        assert results[0]["strategy_id"] == "tagged-rsi"

    def test_update_strategy(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        update_strategy(
            initialized_db,
            strategy_id="strat-001",
            name="Updated Name",
            best_score=88.0,
            notes="Updated notes",
        )
        result = get_strategy(initialized_db, "strat-001")
        assert result["name"] == "Updated Name"
        assert result["best_score"] == 88.0
        assert result["notes"] == "Updated notes"

    def test_update_strategy_partial(self, initialized_db: Path) -> None:
        """Updating only one field should not touch others."""
        save_strategy(initialized_db, **_sample_strategy_data())
        update_strategy(initialized_db, strategy_id="strat-001", name="Only Name Changed")
        result = get_strategy(initialized_db, "strat-001")
        assert result["name"] == "Only Name Changed"
        assert result["symbol"] == "BTCUSDT"  # unchanged

    def test_delete_strategy(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        delete_strategy(initialized_db, "strat-001")
        result = get_strategy(initialized_db, "strat-001")
        assert result is None

    def test_delete_strategy_not_found_no_error(self, initialized_db: Path) -> None:
        """Deleting a nonexistent strategy should not raise."""
        delete_strategy(initialized_db, "nonexistent")

    def test_save_strategy_updates_updated_at(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        first = get_strategy(initialized_db, "strat-001")
        import time
        time.sleep(0.01)
        update_strategy(initialized_db, strategy_id="strat-001", name="New")
        second = get_strategy(initialized_db, "strat-001")
        assert second["updated_at"] >= first["updated_at"]

# ── Test: Backtest Result ──

class TestBacktestResult:
    def test_save_and_get_backtest_result(self, initialized_db: Path) -> None:
        # Need a strategy first due to FK
        save_strategy(initialized_db, **_sample_strategy_data())
        data = _sample_backtest_data()
        save_backtest_result(initialized_db, **data)
        result = get_backtest_result(initialized_db, "bt-001")
        assert result is not None
        assert result["result_id"] == "bt-001"
        assert result["strategy_id"] == "strat-001"
        assert result["total_return"] == 25.3
        assert result["sharpe_ratio"] == 1.8
        assert result["total_trades"] == 150

    def test_get_backtest_result_not_found(self, initialized_db: Path) -> None:
        result = get_backtest_result(initialized_db, "nonexistent")
        assert result is None

    def test_list_backtest_results_by_strategy(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="strat-002", name="Strategy 2",
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-1", strategy_id="strat-001",
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-2", strategy_id="strat-001",
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-3", strategy_id="strat-002",
        ))
        results = list_backtest_results(initialized_db, strategy_id="strat-001")
        assert len(results) == 2

    def test_list_backtest_results_by_symbol(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_strategy(initialized_db, **_sample_strategy_data(
            strategy_id="strat-002", name="Strategy 2",
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-btc", strategy_id="strat-001", symbol="BTCUSDT",
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-eth", strategy_id="strat-002", symbol="ETHUSDT",
        ))
        results = list_backtest_results(initialized_db, symbol="ETHUSDT")
        assert len(results) == 1
        assert results[0]["result_id"] == "bt-eth"

    def test_list_backtest_results_ordered_by_score(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-low", total_score=50.0,
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-high", total_score=95.0,
        ))
        results = list_backtest_results(
            initialized_db, sort_by="total_score", sort_order="desc",
        )
        assert results[0]["result_id"] == "bt-high"

    def test_list_backtest_results_with_limit(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        for i in range(5):
            save_backtest_result(initialized_db, **_sample_backtest_data(
                result_id=f"bt-{i}",
            ))
        results = list_backtest_results(initialized_db, limit=3)
        assert len(results) == 3

    def test_list_backtest_results_by_run_source(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-lab", run_source="lab",
        ))
        save_backtest_result(initialized_db, **_sample_backtest_data(
            result_id="bt-evo", run_source="evolution",
        ))
        results = list_backtest_results(initialized_db, run_source="evolution")
        assert len(results) == 1
        assert results[0]["result_id"] == "bt-evo"

    def test_save_and_get_backtest_result_with_fitness(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_backtest_result(
            initialized_db,
            **_sample_backtest_data(
                result_id="bt-fit",
                run_source="verify",
                fitness=1.35,
                qualified=1,
                satisfaction_json='{"sharpe": 0.9}',
            ),
        )
        result = get_backtest_result(initialized_db, "bt-fit")
        assert result is not None
        assert result["fitness"] == 1.35
        assert result["qualified"] == 1
        assert result["satisfaction_json"] == '{"sharpe": 0.9}'

    def test_save_backtest_result_defaults_fitness(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_backtest_result(initialized_db, **_sample_backtest_data(result_id="bt-def"))
        result = get_backtest_result(initialized_db, "bt-def")
        assert result is not None
        assert result["fitness"] == 0.0
        assert result["qualified"] == 0
        assert result["satisfaction_json"] is None

# ── Test: Verify Session CRUD ──

class TestVerifySessionCRUD:
    def test_save_and_get_session(self, initialized_db: Path) -> None:
        save_verify_session(
            initialized_db,
            session_id="sess-001",
            strategy_ids='["s1","s2"]',
            data_ranges='[{"start":"2024-01-01","end":"2024-06-30"}]',
        )
        session = get_verify_session(initialized_db, "sess-001")
        assert session is not None
        assert session["status"] == "running"
        assert session["strategy_ids"] == '["s1","s2"]'

    def test_get_session_not_found(self, initialized_db: Path) -> None:
        assert get_verify_session(initialized_db, "nonexistent") is None

    def test_update_session_completed(self, initialized_db: Path) -> None:
        save_verify_session(
            initialized_db,
            session_id="sess-002",
            strategy_ids='["s1"]',
            data_ranges='[{"start":"2024-01-01","end":"2024-03-31"}]',
        )
        update_verify_session(
            initialized_db,
            "sess-002",
            status="completed",
            summary_json='[{"strategy_id":"s1","score":1.5}]',
            total_results=3,
            total_strategies=1,
            completed_at="2025-06-05T12:00:00Z",
        )
        session = get_verify_session(initialized_db, "sess-002")
        assert session["status"] == "completed"
        assert session["total_results"] == 3
        assert session["summary_json"] is not None

    def test_update_session_failed(self, initialized_db: Path) -> None:
        save_verify_session(
            initialized_db,
            session_id="sess-003",
            strategy_ids='["s1"]',
            data_ranges='[]',
        )
        update_verify_session(
            initialized_db,
            "sess-003",
            status="failed",
            error_message="No data ranges",
        )
        session = get_verify_session(initialized_db, "sess-003")
        assert session["status"] == "failed"
        assert session["error_message"] == "No data ranges"

    def test_list_sessions_ordered(self, initialized_db: Path) -> None:
        for i in range(3):
            save_verify_session(
                initialized_db,
                session_id=f"sess-list-{i}",
                strategy_ids='[]',
                data_ranges='[]',
            )
        sessions = list_verify_sessions(initialized_db)
        assert len(sessions) >= 3

    def test_backtest_result_with_session_id(self, initialized_db: Path) -> None:
        save_strategy(initialized_db, **_sample_strategy_data())
        save_backtest_result(
            initialized_db,
            **_sample_backtest_data(result_id="bt-sess", session_id="sess-001"),
        )
        result = get_backtest_result(initialized_db, "bt-sess")
        assert result is not None
        assert result["session_id"] == "sess-001"


# ── Test: Dataset Metadata CRUD ──

class TestDatasetMetaCRUD:
    def test_save_and_get_dataset(self, initialized_db: Path) -> None:
        data = _sample_dataset_data()
        save_dataset_meta(initialized_db, **data)
        result = get_dataset(initialized_db, "ds-001")
        assert result is not None
        assert result["dataset_id"] == "ds-001"
        assert result["symbol"] == "BTCUSDT"
        assert result["interval"] == "4h"
        assert result["row_count"] == 10000
        assert result["parquet_path"] == "/data/btcusdt_4h.parquet"

    def test_get_dataset_not_found(self, initialized_db: Path) -> None:
        result = get_dataset(initialized_db, "nonexistent")
        assert result is None

    def test_list_datasets_basic(self, initialized_db: Path) -> None:
        for i in range(3):
            save_dataset_meta(initialized_db, **_sample_dataset_data(
                dataset_id=f"ds-{i}", parquet_path=f"/data/{i}.parquet",
            ))
        results = list_datasets(initialized_db)
        assert len(results) == 3

    def test_list_datasets_filter_by_symbol(self, initialized_db: Path) -> None:
        save_dataset_meta(initialized_db, **_sample_dataset_data(
            dataset_id="ds-btc", symbol="BTCUSDT",
        ))
        save_dataset_meta(initialized_db, **_sample_dataset_data(
            dataset_id="ds-eth", symbol="ETHUSDT",
        ))
        results = list_datasets(initialized_db, symbol="ETHUSDT")
        assert len(results) == 1
        assert results[0]["dataset_id"] == "ds-eth"

    def test_list_datasets_filter_by_interval(self, initialized_db: Path) -> None:
        save_dataset_meta(initialized_db, **_sample_dataset_data(
            dataset_id="ds-4h", interval="4h",
        ))
        save_dataset_meta(initialized_db, **_sample_dataset_data(
            dataset_id="ds-1d", interval="1d",
        ))
        results = list_datasets(initialized_db, interval="1d")
        assert len(results) == 1
        assert results[0]["dataset_id"] == "ds-1d"

    def test_update_dataset_stats(self, initialized_db: Path) -> None:
        save_dataset_meta(initialized_db, **_sample_dataset_data())
        update_dataset_stats(
            initialized_db,
            dataset_id="ds-001",
            row_count=15000,
            time_end="2024-06-01T00:00:00Z",
            quality_status="good",
        )
        result = get_dataset(initialized_db, "ds-001")
        assert result["row_count"] == 15000
        assert result["time_end"] == "2024-06-01T00:00:00Z"
        assert result["quality_status"] == "good"

    def test_delete_dataset(self, initialized_db: Path) -> None:
        save_dataset_meta(initialized_db, **_sample_dataset_data())
        delete_dataset(initialized_db, "ds-001")
        result = get_dataset(initialized_db, "ds-001")
        assert result is None

    def test_delete_dataset_not_found_no_error(self, initialized_db: Path) -> None:
        """Deleting a nonexistent dataset should not raise."""
        delete_dataset(initialized_db, "nonexistent")

    def test_save_dataset_updates_updated_at(self, initialized_db: Path) -> None:
        save_dataset_meta(initialized_db, **_sample_dataset_data())
        first = get_dataset(initialized_db, "ds-001")
        import time
        time.sleep(0.01)
        update_dataset_stats(initialized_db, dataset_id="ds-001", row_count=999)
        second = get_dataset(initialized_db, "ds-001")
        assert second["updated_at"] >= first["updated_at"]

# ── Test: Evolution Task Extended Columns ──

class TestEvolutionTaskExtended:
    def test_extended_columns_have_defaults(self, initialized_db: Path) -> None:
        """After migration, extended columns should have sensible defaults."""
        conn = sqlite3.connect(str(initialized_db))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO evolution_task
               (task_id, status, target_score, score_template, symbol, timeframe,
                initial_dna, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("ext-001", "running", 80.0, "profit_first", "BTCUSDT", "4h",
             "{}", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM evolution_task WHERE task_id = ?", ("ext-001",)
        ).fetchone()
        conn.close()

        assert row["population_size"] == 15
        assert row["max_generations"] == 200
        assert row["elite_ratio"] == 0.5
        assert row["current_generation"] == 0
        assert row["champion_strategy_id"] is None

    def test_write_and_read_extended_columns(self, initialized_db: Path) -> None:
        """Extended columns should be writable and readable."""
        conn = sqlite3.connect(str(initialized_db))
        conn.execute(
            """INSERT INTO evolution_task
               (task_id, status, target_score, score_template, symbol, timeframe,
                initial_dna, created_at, updated_at,
                champion_strategy_id, population_size, max_generations,
                elite_ratio, current_generation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("ext-002", "running", 80.0, "profit_first", "BTCUSDT", "4h",
             "{}", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z",
             "strat-champ", 20, 300, 0.3, 5),
        )
        conn.commit()

        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM evolution_task WHERE task_id = ?", ("ext-002",)
        ).fetchone()
        conn.close()

        assert row["champion_strategy_id"] == "strat-champ"
        assert row["population_size"] == 20
        assert row["max_generations"] == 300
        assert row["elite_ratio"] == pytest.approx(0.3)
        assert row["current_generation"] == 5

