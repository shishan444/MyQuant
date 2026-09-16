"""Tests for train/test data split and OOS champion validation.

Validates that:
- _split_train_test produces non-overlapping chronological splits
- Too-short data falls back gracefully
- OOS results are persisted to DB columns
"""
import pytest

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd

from api.runner import _split_train_test
from tests.helpers.data_factory import make_ohlcv

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# S2.1: Data split correctness
# ---------------------------------------------------------------------------

class TestSplitTrainTest:
    """_split_train_test must produce correct chronological splits."""

    def test_split_ratio_correct(self):
        df = make_ohlcv(n=1000, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        assert len(train_df) == 700
        assert len(test_df) == 300

    def test_split_preserves_chronological_order(self):
        df = make_ohlcv(n=500, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        # All train timestamps < all test timestamps
        assert train_df.index.max() < test_df.index.min()

    def test_split_no_overlap(self):
        df = make_ohlcv(n=500, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        train_set = set(train_df.index)
        test_set = set(test_df.index)
        assert train_set.isdisjoint(test_set)

    def test_split_preserves_all_columns(self):
        df = make_ohlcv(n=300, seed=42)
        original_cols = set(df.columns)
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        assert set(train_df.columns) == original_cols
        assert set(test_df.columns) == original_cols

    def test_split_preserves_indicator_columns(self):
        """If df has indicator columns, both splits should retain them."""
        df = make_ohlcv(n=300, seed=42)
        df["rsi_14"] = 50.0
        df["ema_20"] = df["close"]
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        assert "rsi_14" in train_df.columns
        assert "rsi_14" in test_df.columns
        assert "ema_20" in train_df.columns
        assert "ema_20" in test_df.columns


# ---------------------------------------------------------------------------
# S2.1 edge cases
# ---------------------------------------------------------------------------

class TestSplitEdgeCases:

    def test_too_short_data_returns_no_test(self):
        df = make_ohlcv(n=50, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        assert test_df is None
        assert len(train_df) == 50

    def test_exactly_min_bars_splits(self):
        df = make_ohlcv(n=100, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=0.7)
        assert test_df is not None
        assert len(train_df) == 70
        assert len(test_df) == 30

    def test_train_ratio_zero_returns_no_test(self):
        df = make_ohlcv(n=500, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=0.0)
        assert test_df is None
        assert len(train_df) == 500

    def test_train_ratio_one_returns_no_test(self):
        df = make_ohlcv(n=500, seed=42)
        train_df, test_df = _split_train_test(df, train_ratio=1.0)
        assert test_df is None
        assert len(train_df) == 500

    def test_different_ratios(self):
        df = make_ohlcv(n=1000, seed=42)
        train_80, test_80 = _split_train_test(df, train_ratio=0.8)
        assert len(train_80) == 800
        assert len(test_80) == 200

        train_60, test_60 = _split_train_test(df, train_ratio=0.6)
        assert len(train_60) == 600
        assert len(test_60) == 400


# ---------------------------------------------------------------------------
# S2.4: OOS results persistence
# ---------------------------------------------------------------------------

class TestOOSPersistence:
    """OOS validation results must be written to DB."""

    @pytest.fixture
    def test_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "test_oos.db"
        from core.persistence.db_ext import init_db_ext
        init_db_ext(db_path)
        return db_path

    def test_oos_columns_exist_in_schema(self, test_db):
        conn = sqlite3.connect(str(test_db))
        cursor = conn.execute("PRAGMA table_info(evolution_task)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "oos_fitness" in columns
        assert "oos_qualified" in columns
        assert "oos_metrics" in columns

    def test_oos_values_roundtrip(self, test_db):
        """Write OOS values and read them back."""
        from core.persistence.db import save_task
        from core.evolution.population import create_random_dna

        # Create a task first
        dna = create_random_dna()
        save_task(
            test_db,
            task_id="test-oos-001",
            target_score=1.0,
            template="default",
            symbol="BTCUSDT",
            timeframe="4h",
            initial_dna=dna,
        )

        # Write OOS values
        oos_metrics = {"annual_return": 0.12, "max_drawdown": -0.08}
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "UPDATE evolution_task SET oos_fitness = ?, oos_qualified = ?, oos_metrics = ? WHERE task_id = ?",
            (0.85, 1, json.dumps(oos_metrics), "test-oos-001"),
        )
        conn.commit()
        conn.close()

        # Read back
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT oos_fitness, oos_qualified, oos_metrics FROM evolution_task WHERE task_id = ?",
            ("test-oos-001",),
        ).fetchone()
        conn.close()

        assert row[0] == 0.85
        assert row[1] == 1
        assert json.loads(row[2])["annual_return"] == 0.12
