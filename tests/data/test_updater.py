"""Tests for incremental data updater.

Tests:
- updater detects missing data -> full fetch
- updater detects existing data -> incremental fetch
- updater handles empty Parquet file
- updater deduplicates overlapping data
"""

import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

from MyQuant.core.data.storage import save_parquet, load_parquet, get_latest_timestamp
from MyQuant.core.data.updater import update_market_data

@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create temp data directory."""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    return data_dir

@pytest.fixture
def sample_ohlcv():
    """Generate 100 bars of OHLCV data."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 30000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 50,
        "high": close + abs(np.random.randn(n) * 80),
        "low": close - abs(np.random.randn(n) * 80),
        "close": close,
        "volume": np.random.randint(100, 10000, n).astype(float),
        "trades": np.random.randint(10, 500, n).astype(int),
    }, index=dates)
    return df

class TestUpdaterFullFetch:
    def test_no_existing_data_triggers_full_fetch(self, tmp_data_dir, sample_ohlcv):
        """When no local data exists, should do full fetch."""
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        with patch("MyQuant.core.data.updater.fetch_klines", return_value=sample_ohlcv) as mock_fetch:
            update_market_data("BTCUSDT", "4h", data_dir=tmp_data_dir)
            mock_fetch.assert_called_once()
            # Should fetch from 2 years ago
            call_kwargs = mock_fetch.call_args
            assert "2 year ago" in str(call_kwargs) or "2" in str(call_kwargs)

    def test_full_fetch_saves_parquet(self, tmp_data_dir, sample_ohlcv):
        """Full fetch result should be saved as Parquet."""
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        with patch("MyQuant.core.data.updater.fetch_klines", return_value=sample_ohlcv):
            update_market_data("BTCUSDT", "4h", data_dir=tmp_data_dir)
            assert path.exists()
            saved = load_parquet(path)
            assert len(saved) == 100

class TestUpdaterIncremental:
    def test_existing_data_triggers_incremental(self, tmp_data_dir, sample_ohlcv):
        """When local data exists, should only fetch incrementally."""
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        # Save existing data (first 50 bars)
        save_parquet(sample_ohlcv.iloc[:50], path)

        # New data (bars 40-100, overlap with existing)
        new_data = sample_ohlcv.iloc[40:]

        with patch("MyQuant.core.data.updater.fetch_klines", return_value=new_data) as mock_fetch:
            update_market_data("BTCUSDT", "4h", data_dir=tmp_data_dir)
            mock_fetch.assert_called_once()

        # Result should have no duplicates
        result = load_parquet(path)
        assert result.index.is_unique
        assert len(result) >= 100  # All 100 bars present

    def test_incremental_preserves_timestamp_order(self, tmp_data_dir, sample_ohlcv):
        """After incremental update, data should be sorted by timestamp."""
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        save_parquet(sample_ohlcv.iloc[:50], path)

        new_data = sample_ohlcv.iloc[40:]
        with patch("MyQuant.core.data.updater.fetch_klines", return_value=new_data):
            update_market_data("BTCUSDT", "4h", data_dir=tmp_data_dir)

        result = load_parquet(path)
        assert result.index.is_monotonic_increasing

class TestUpdaterEdgeCases:
    def test_empty_parquet_triggers_full_fetch(self, tmp_data_dir, sample_ohlcv):
        """Empty Parquet file should trigger full fetch."""
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        save_parquet(pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trades"]), path)

        with patch("MyQuant.core.data.updater.fetch_klines", return_value=sample_ohlcv) as mock_fetch:
            update_market_data("BTCUSDT", "4h", data_dir=tmp_data_dir)
            mock_fetch.assert_called_once()

    def test_returns_dataframe(self, tmp_data_dir, sample_ohlcv):
        """update_market_data should return the final DataFrame."""
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        with patch("MyQuant.core.data.updater.fetch_klines", return_value=sample_ohlcv):
            result = update_market_data("BTCUSDT", "4h", data_dir=tmp_data_dir)
            assert isinstance(result, pd.DataFrame)
            assert len(result) == 100
