"""Tests for CSV import module.

Tests:
- Binance official CSV (no header, 12 columns) parsing
- Generic OHLCV CSV (with header) parsing
- Timestamp precision detection (ms vs us)
- Filename parsing (SYMBOL-INTERVAL-YYYY-MM.csv)
- OHLC relationship validation
- Import modes: merge / replace / new
- Multi-file batch import
- Edge cases: empty file, bad OHLC, duplicate timestamps
"""

import pytest

pytestmark = [pytest.mark.unit]
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import io

from MyQuant.core.data.csv_importer import (
    detect_format,
    detect_timestamp_precision,
    parse_filename,
    validate_ohlcv,
    read_csv,
    import_csv,
    import_csv_batch,
    ImportFormat,
    TimestampPrecision,
    ImportMode,
    CsvImportResult,
)
from MyQuant.core.data.storage import save_parquet, load_parquet, merge_parquet

# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    return data_dir

@pytest.fixture
def binance_csv_content():
    """Binance official CSV: no header, 12 columns, ms timestamps."""
    return (
        "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
        "1735693200000,42310.0,43120.0,42100.0,42850.0,289.3,1735696799999,12380000,380,230.0,9850000,0\n"
        "1735696800000,42850.0,42900.0,42500.0,42680.0,198.7,1735700399999,8480000,290,160.0,6820000,0\n"
    )

@pytest.fixture
def binance_csv_us_content():
    """Binance official CSV with microsecond timestamps (2025+ Spot)."""
    return (
        "1735689600010866,42000.0,42580.0,41850.0,42310.0,312.5,1735693200108666,13120000,450,250.0,10550000,0\n"
        "1735693200010866,42310.0,43120.0,42100.0,42850.0,289.3,1735696800108666,12380000,380,230.0,9850000,0\n"
    )

@pytest.fixture
def generic_csv_content():
    """Generic OHLCV CSV with header."""
    return (
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01 00:00:00,42000.0,42580.0,41850.0,42310.0,312.5\n"
        "2024-01-01 04:00:00,42310.0,43120.0,42100.0,42850.0,289.3\n"
        "2024-01-01 08:00:00,42850.0,42900.0,42500.0,42680.0,198.7\n"
    )

@pytest.fixture
def binance_csv_file(tmp_path, binance_csv_content):
    p = tmp_path / "BTCUSDT-4h-2025-01.csv"
    p.write_text(binance_csv_content)
    return p

@pytest.fixture
def generic_csv_file(tmp_path, generic_csv_content):
    p = tmp_path / "ETHUSDT_4h_data.csv"
    p.write_text(generic_csv_content)
    return p

@pytest.fixture
def sample_ohlcv():
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
    }, index=dates)
    return df

# ── Format Detection ──────────────────────────────────────────

class TestDetectFormat:
    def test_binance_no_header(self, binance_csv_file):
        fmt = detect_format(binance_csv_file)
        assert fmt == ImportFormat.BINANCE_OFFICIAL

    def test_generic_with_header(self, generic_csv_file):
        fmt = detect_format(generic_csv_file)
        assert fmt == ImportFormat.GENERIC_OHLCV

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("")
        with pytest.raises(ValueError, match="empty"):
            detect_format(p)

# ── Timestamp Precision ───────────────────────────────────────

class TestTimestampPrecision:
    def test_millisecond_13_digits(self):
        assert detect_timestamp_precision(1735689600000) == TimestampPrecision.MILLISECOND

    def test_microsecond_16_digits(self):
        assert detect_timestamp_precision(1735689600010866) == TimestampPrecision.MICROSECOND

    def test_small_number_not_timestamp(self):
        with pytest.raises(ValueError):
            detect_timestamp_precision(42)

# ── Filename Parsing ──────────────────────────────────────────

class TestParseFilename:
    def test_binance_monthly(self):
        symbol, interval = parse_filename("BTCUSDT-4h-2025-01.csv")
        assert symbol == "BTCUSDT"
        assert interval == "4h"

    def test_binance_daily(self):
        symbol, interval = parse_filename("ETHUSDT-1h-2024-06.csv")
        assert symbol == "ETHUSDT"
        assert interval == "1h"

    def test_daily_interval(self):
        symbol, interval = parse_filename("BTCUSDT-1d-2024-12.csv")
        assert symbol == "BTCUSDT"
        assert interval == "1d"

    def test_unparseable_filename_returns_none(self):
        result = parse_filename("data.csv")
        assert result == (None, None)

    def test_path_object(self):
        symbol, interval = parse_filename(Path("downloads/BTCUSDT-4h-2025-01.csv"))
        assert symbol == "BTCUSDT"
        assert interval == "4h"

# ── OHLCV Validation ──────────────────────────────────────────

class TestValidateOhlcv:
    def test_valid_data(self):
        df = pd.DataFrame({
            "open": [100.0, 200.0],
            "high": [110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        assert len(errors) == 0

    def test_high_below_close(self):
        df = pd.DataFrame({
            "open": [100.0],
            "high": [90.0],   # high < close -> invalid
            "low": [85.0],
            "close": [105.0],
            "volume": [1000.0],
        })
        errors = validate_ohlcv(df)
        assert len(errors) > 0
        assert any("high" in e.lower() for e in errors)

    def test_low_above_close(self):
        df = pd.DataFrame({
            "open": [100.0],
            "high": [110.0],
            "low": [120.0],   # low > close -> invalid
            "close": [105.0],
            "volume": [1000.0],
        })
        errors = validate_ohlcv(df)
        assert len(errors) > 0
        assert any("low" in e.lower() for e in errors)

    def test_negative_volume(self):
        df = pd.DataFrame({
            "open": [100.0],
            "high": [110.0],
            "low": [90.0],
            "close": [105.0],
            "volume": [-100.0],  # invalid
        })
        errors = validate_ohlcv(df)
        assert len(errors) > 0
        assert any("volume" in e.lower() for e in errors)

    def test_nan_values(self):
        df = pd.DataFrame({
            "open": [100.0, float("nan")],
            "high": [110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        assert len(errors) > 0

    def test_negative_close_price(self):
        df = pd.DataFrame({
            "open": [100.0, 200.0],
            "high": [110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [-50.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        assert any("close" in e and "negative" in e for e in errors)

    def test_negative_open_price(self):
        df = pd.DataFrame({
            "open": [-100.0, 200.0],
            "high": [110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        assert any("open" in e and "negative" in e for e in errors)

    def test_negative_high_price(self):
        df = pd.DataFrame({
            "open": [100.0, 200.0],
            "high": [-110.0, 220.0],
            "low": [90.0, 180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        assert any("high" in e and "negative" in e for e in errors)

    def test_negative_low_price(self):
        df = pd.DataFrame({
            "open": [100.0, 200.0],
            "high": [110.0, 220.0],
            "low": [-90.0, 180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        assert any("low" in e and "negative" in e for e in errors)

    def test_multiple_negative_prices(self):
        df = pd.DataFrame({
            "open": [-100.0, -200.0],
            "high": [110.0, 220.0],
            "low": [-90.0, -180.0],
            "close": [105.0, 210.0],
            "volume": [1000.0, 2000.0],
        })
        errors = validate_ohlcv(df)
        negative_errors = [e for e in errors if "negative" in e]
        assert len(negative_errors) >= 2  # open and low both negative

# ── CSV Reading ───────────────────────────────────────────────

class TestReadCsv:
    def test_read_binance_csv(self, binance_csv_file):
        df = read_csv(binance_csv_file)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3
        assert set(df.columns) >= {"open", "high", "low", "close", "volume"}
        assert df.index.name == "timestamp"
        assert df.index.tz is not None  # Should be timezone-aware (UTC)

    def test_read_binance_csv_values(self, binance_csv_file):
        df = read_csv(binance_csv_file)
        assert df.iloc[0]["open"] == 42000.0
        assert df.iloc[0]["close"] == 42310.0

    def test_read_binance_us_csv(self, tmp_path, binance_csv_us_content):
        p = tmp_path / "BTCUSDT-4h-2025-06.csv"
        p.write_text(binance_csv_us_content)
        df = read_csv(p)
        assert len(df) == 2
        # Should handle microsecond timestamps correctly
        assert df.index.tz is not None

    def test_read_generic_csv(self, generic_csv_file):
        df = read_csv(generic_csv_file)
        assert len(df) == 3
        assert df.iloc[0]["open"] == 42000.0

    def test_read_returns_float_dtypes(self, binance_csv_file):
        df = read_csv(binance_csv_file)
        for col in ["open", "high", "low", "close", "volume"]:
            assert df[col].dtype in (np.float64, float)

# ── Single File Import ────────────────────────────────────────

class TestImportCsv:
    def test_import_creates_parquet(self, binance_csv_file, tmp_data_dir):
        result = import_csv(
            binance_csv_file,
            symbol="BTCUSDT",
            interval="4h",
            data_dir=tmp_data_dir,
        )
        assert isinstance(result, CsvImportResult)
        assert result.rows_imported == 3
        assert result.dataset_id == "BTCUSDT_4h"
        parquet_path = tmp_data_dir / "BTCUSDT_4h.parquet"
        assert parquet_path.exists()

    def test_import_auto_detect_symbol(self, binance_csv_file, tmp_data_dir):
        result = import_csv(binance_csv_file, data_dir=tmp_data_dir)
        assert result.dataset_id == "BTCUSDT_4h"
        assert result.symbol == "BTCUSDT"
        assert result.interval == "4h"

    def test_import_merge_mode(self, binance_csv_file, tmp_data_dir, sample_ohlcv):
        # Pre-existing data
        parquet_path = tmp_data_dir / "BTCUSDT_4h.parquet"
        save_parquet(sample_ohlcv, parquet_path)

        result = import_csv(
            binance_csv_file,
            symbol="BTCUSDT",
            interval="4h",
            data_dir=tmp_data_dir,
            mode=ImportMode.MERGE,
        )
        merged = load_parquet(parquet_path)
        assert merged.index.is_unique
        assert len(merged) >= len(sample_ohlcv)

    def test_import_replace_mode(self, binance_csv_file, tmp_data_dir, sample_ohlcv):
        parquet_path = tmp_data_dir / "BTCUSDT_4h.parquet"
        save_parquet(sample_ohlcv, parquet_path)

        import_csv(
            binance_csv_file,
            symbol="BTCUSDT",
            interval="4h",
            data_dir=tmp_data_dir,
            mode=ImportMode.REPLACE,
        )
        result = load_parquet(parquet_path)
        assert len(result) == 3  # Only the CSV data

    def test_import_validates_data(self, tmp_path, tmp_data_dir):
        bad_csv = tmp_path / "bad.csv"
        bad_csv.write_text(
            "1735689600000,100.0,90.0,80.0,95.0,1000.0,1735693199999,0,0,0,0,0\n"
        )
        with pytest.raises(ValueError, match="[Oo][Hh][Ll][Cc]"):
            import_csv(bad_csv, symbol="BTCUSDT", interval="4h", data_dir=tmp_data_dir)

# ── Batch Import ──────────────────────────────────────────────

class TestImportCsvBatch:
    def test_batch_import_multiple_files(self, tmp_path, tmp_data_dir):
        # Create 3 monthly CSV files
        files = []
        for i, (month, rows) in enumerate([(1, 180), (2, 170), (3, 186)]):
            p = tmp_path / f"BTCUSDT-4h-2024-{month:02d}.csv"
            lines = []
            base_ts = int(datetime(2024, month, 1).timestamp() * 1000)
            for j in range(rows):
                ts = base_ts + j * 4 * 3600 * 1000
                price = 42000 + j * 10
                lines.append(
                    f"{ts},{price},{price+100},{price-100},{price+50},100.0,"
                    f"{ts+4*3600*1000-1},50000,50,50.0,21000,0"
                )
            p.write_text("\n".join(lines))
            files.append(p)

        result = import_csv_batch(files, data_dir=tmp_data_dir)
        assert result.files_processed == 3
        assert result.rows_imported == 180 + 170 + 186
        assert result.dataset_id == "BTCUSDT_4h"

        parquet_path = tmp_data_dir / "BTCUSDT_4h.parquet"
        assert parquet_path.exists()
        df = load_parquet(parquet_path)
        assert len(df) == 180 + 170 + 186
        assert df.index.is_unique

    def test_batch_deduplicates_overlaps(self, tmp_path, tmp_data_dir):
        """Two files with overlapping timestamps should deduplicate."""
        base_ts = int(datetime(2024, 1, 1).timestamp() * 1000)
        lines_a = []
        for j in range(10):
            ts = base_ts + j * 4 * 3600 * 1000
            price = 42000 + j * 10
            lines_a.append(
                f"{ts},{price},{price+100},{price-100},{price+50},100.0,"
                f"{ts+4*3600*1000-1},50000,50,50.0,21000,0"
            )
        lines_b = []
        for j in range(5, 15):
            ts = base_ts + j * 4 * 3600 * 1000
            price = 42000 + j * 10
            lines_b.append(
                f"{ts},{price},{price+100},{price-100},{price+50},100.0,"
                f"{ts+4*3600*1000-1},50000,50,50.0,21000,0"
            )

        p1 = tmp_path / "BTCUSDT-4h-2024-01.csv"
        p2 = tmp_path / "BTCUSDT-4h-2024-01b.csv"
        p1.write_text("\n".join(lines_a))
        p2.write_text("\n".join(lines_b))

        result = import_csv_batch([p1, p2], data_dir=tmp_data_dir)
        parquet_path = tmp_data_dir / "BTCUSDT_4h.parquet"
        df = load_parquet(parquet_path)
        assert df.index.is_unique
        assert len(df) == 15  # 10 + 5 new (5 overlap)

# ── Storage Merge ─────────────────────────────────────────────

class TestStorageMerge:
    def test_merge_deduplicates(self, tmp_data_dir, sample_ohlcv):
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        save_parquet(sample_ohlcv.iloc[:50], path)

        # Overlapping data (bars 40-60)
        overlap = sample_ohlcv.iloc[40:60]
        merge_parquet(overlap, path)

        result = load_parquet(path)
        assert result.index.is_unique
        assert len(result) >= 60

    def test_merge_keeps_newer(self, tmp_data_dir):
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        dates = pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC")
        old = pd.DataFrame({
            "open": [100.0, 200.0, 300.0],
            "high": [110.0, 210.0, 310.0],
            "low": [90.0, 190.0, 290.0],
            "close": [105.0, 205.0, 305.0],
            "volume": [1000.0, 2000.0, 3000.0],
        }, index=dates)
        save_parquet(old, path)

        # New data with updated close for row 1
        new = pd.DataFrame({
            "open": [200.0],
            "high": [215.0],
            "low": [195.0],
            "close": [210.0],  # updated
            "volume": [2500.0],
        }, index=[dates[1]])
        merge_parquet(new, path)

        result = load_parquet(path)
        assert len(result) == 3
        assert result.loc[dates[1], "close"] == 210.0  # New value kept

    def test_merge_sorted_output(self, tmp_data_dir):
        path = tmp_data_dir / "BTCUSDT_4h.parquet"
        dates_a = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
        df_a = pd.DataFrame({
            "open": range(5), "high": range(5, 10),
            "low": range(10, 15), "close": range(15, 20),
            "volume": [100.0] * 5,
        }, index=dates_a, dtype=float)
        save_parquet(df_a, path)

        dates_b = pd.date_range("2023-12-31 20:00", periods=2, freq="4h", tz="UTC")
        df_b = pd.DataFrame({
            "open": [50.0, 51.0], "high": [52.0, 53.0],
            "low": [48.0, 49.0], "close": [51.0, 52.0],
            "volume": [200.0, 201.0],
        }, index=dates_b)
        merge_parquet(df_b, path)

        result = load_parquet(path)
        assert result.index.is_monotonic_increasing

# ── ImportResult ──────────────────────────────────────────────

class TestImportResult:
    def test_result_fields(self, binance_csv_file, tmp_data_dir):
        result = import_csv(
            binance_csv_file,
            symbol="BTCUSDT",
            interval="4h",
            data_dir=tmp_data_dir,
        )
        assert result.dataset_id == "BTCUSDT_4h"
        assert result.symbol == "BTCUSDT"
        assert result.interval == "4h"
        assert result.rows_imported > 0
        assert result.format_detected == ImportFormat.BINANCE_OFFICIAL
        assert result.timestamp_precision == TimestampPrecision.MILLISECOND
