"""csv_importer 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
detect_format 兜底分支 / detect_timestamp_precision 上下界 /
import_csv NEW 模式与元数据缺失拒绝 / import_csv_batch 空清单、
坏文件中断、MERGE/NEW 落盘分支 / _get_first_timestamp_raw 兜底值 /
GENERIC "date" 列名映射。
"""

import pytest

pytestmark = [pytest.mark.unit]

from pathlib import Path

import numpy as np
import pandas as pd

from core.data.csv_importer import (
    ImportFormat,
    ImportMode,
    TimestampPrecision,
    _get_first_timestamp_raw,
    detect_format,
    detect_timestamp_precision,
    import_csv,
    import_csv_batch,
    read_csv,
)
from core.data.storage import load_parquet, save_parquet


@pytest.fixture
def tmp_data_dir(tmp_path):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    return data_dir


BINANCE_ROWS = (
    "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
    "1735693200000,42310.0,43120.0,42100.0,42850.0,289.3,1735696799999,12380000,380,230.0,9850000,0\n"
)


def _write(tmp_path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


class TestDetectFormatFallbacks:
    """detect_format 兜底链：数值列失败后看首列 int，再兜底 GENERIC。"""

    def test_numeric_second_column_routes_binance(self, tmp_path) -> None:
        p = _write(tmp_path, "x.csv", BINANCE_ROWS)
        assert detect_format(p) == ImportFormat.BINANCE_OFFICIAL

    def test_int_first_column_rescues_binance(self, tmp_path) -> None:
        # 12 列、第二列非数值、首列为 int → BINANCE_OFFICIAL（fallback 分支）
        p = _write(
            tmp_path, "x.csv",
            "1735689600000,abc,1,1,1,1,1,1,1,1,1,1\n",
        )
        assert detect_format(p) == ImportFormat.BINANCE_OFFICIAL

    def test_non_timestamp_short_line_routes_generic(self, tmp_path) -> None:
        # 非 12 列、首列非 int、非列名 → GENERIC_OHLCV 兜底
        p = _write(tmp_path, "x.csv", "foo,bar\n1,2\n")
        assert detect_format(p) == ImportFormat.GENERIC_OHLCV

    def test_twelve_non_numeric_columns_route_generic(self, tmp_path) -> None:
        # 12 列但首列非 int、第二列非数值 → GENERIC_OHLCV 兜底
        p = _write(
            tmp_path, "x.csv",
            "foo,bar,1,1,1,1,1,1,1,1,1,1\n",
        )
        assert detect_format(p) == ImportFormat.GENERIC_OHLCV


class TestDetectTimestampPrecisionBounds:
    """位数边界：<10 拒绝、10-13 毫秒、14-16 微秒、>16 拒绝。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(1_700_000_000, TimestampPrecision.MILLISECOND, id="10 位秒级按毫秒"),
            pytest.param(1_700_000_000_000, TimestampPrecision.MILLISECOND, id="13 位毫秒"),
            pytest.param(1_700_000_000_000_0, TimestampPrecision.MICROSECOND, id="14 位微秒"),
            pytest.param(1_700_000_000_000_000, TimestampPrecision.MICROSECOND, id="16 位微秒"),
        ],
    )
    def test_precision_by_digit_count(self, value, expected) -> None:
        assert detect_timestamp_precision(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(999_999_999, id="9 位过小"),
            pytest.param(10_000_000_000_000_000, id="17 位过大"),
        ],
    )
    def test_out_of_range_rejected(self, value) -> None:
        with pytest.raises(ValueError):
            detect_timestamp_precision(value)

    def test_negative_uses_absolute_digits(self) -> None:
        assert (
            detect_timestamp_precision(-1_700_000_000_000)
            == TimestampPrecision.MILLISECOND
        )


class TestReadCsvGenericDateColumn:
    """GENERIC 表头 "date" 列名映射到 timestamp 后解析。"""

    def test_date_header_mapped(self, tmp_path) -> None:
        p = _write(
            tmp_path, "x.csv",
            "date,open,high,low,close,volume\n"
            "2024-01-01 00:00:00,42000.0,42580.0,41850.0,42310.0,312.5\n"
            "2024-01-01 04:00:00,42310.0,43120.0,42100.0,42850.0,289.3\n",
        )
        df = read_csv(p)
        assert isinstance(df.index, pd.DatetimeIndex)
        # to_datetime(utc=True)：GENERIC 时间戳统一为 tz-aware UTC
        assert df.index[0] == pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
        assert df["close"].iloc[0] == pytest.approx(42310.0)


class TestImportCsvModesAndMetadata:
    """NEW 模式覆盖写、元数据缺失拒绝。"""

    def test_new_mode_overwrites_existing(self, tmp_path, tmp_data_dir) -> None:
        # 先落盘 100 根存量
        idx = pd.date_range("2024-01-01", periods=100, freq="4h", tz="UTC")
        save_parquet(
            pd.DataFrame(
                {c: np.full(100, 1.0) for c in ["open", "high", "low", "close", "volume"]},
                index=idx,
            ),
            tmp_data_dir / "TESTUSDT_4h.parquet",
        )
        csv = _write(
            tmp_path, "TESTUSDT-4h-2025-01.csv",
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n"
            "1735693200000,42310.0,43120.0,42100.0,42850.0,289.3,1735696799999,12380000,380,230.0,9850000,0\n",
        )

        result = import_csv(csv, tmp_data_dir, mode=ImportMode.NEW)

        assert result.rows_imported == 2
        assert len(load_parquet(tmp_data_dir / "TESTUSDT_4h.parquet")) == 2  # 覆盖非合并

    def test_undetectable_metadata_rejected(self, tmp_path, tmp_data_dir) -> None:
        csv = _write(
            tmp_path, "mystery.csv",
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n",
        )
        with pytest.raises(ValueError, match="Cannot determine symbol/interval"):
            import_csv(csv, tmp_data_dir)

    def test_explicit_metadata_overrides_filename(self, tmp_path, tmp_data_dir) -> None:
        csv = _write(
            tmp_path, "mystery.csv",
            BINANCE_ROWS,
        )
        result = import_csv(csv, tmp_data_dir, symbol="ETHUSDT", interval="1h")
        assert result.dataset_id == "ETHUSDT_1h"
        assert (tmp_data_dir / "ETHUSDT_1h.parquet").exists()


class TestImportCsvBatchEdges:
    """batch：空清单拒绝 / 坏文件中断 / MERGE 与 NEW 落盘分支。"""

    def test_empty_paths_rejected(self, tmp_data_dir) -> None:
        with pytest.raises(ValueError, match="No files provided"):
            import_csv_batch([], tmp_data_dir)

    def test_invalid_ohlcv_file_aborts(self, tmp_path, tmp_data_dir) -> None:
        good = _write(
            tmp_path, "TESTUSDT-4h-2025-01.csv",
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n",
        )
        bad = _write(
            tmp_path, "TESTUSDT-4h-2025-02.csv",
            "1735693200000,42310.0,42100.0,42850.0,42850.0,289.3,1735696799999,12380000,380,230.0,9850000,0\n",  # high < close
        )
        with pytest.raises(ValueError, match="OHLCV validation failed"):
            import_csv_batch([good, bad], tmp_data_dir)

    def test_merge_mode_appends_to_existing(self, tmp_path, tmp_data_dir) -> None:
        idx = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
        save_parquet(
            pd.DataFrame(
                {c: np.full(10, 1.0) for c in ["open", "high", "low", "close", "volume"]},
                index=idx,
            ),
            tmp_data_dir / "TESTUSDT_4h.parquet",
        )
        csv = _write(
            tmp_path, "TESTUSDT-4h-2025-01.csv",
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n",
        )

        import_csv_batch([csv], tmp_data_dir, mode=ImportMode.MERGE)

        merged = load_parquet(tmp_data_dir / "TESTUSDT_4h.parquet")
        assert len(merged) == 11  # 存量 10 + 新 1

    def test_undetectable_metadata_rejected(self, tmp_path, tmp_data_dir) -> None:
        csv = _write(
            tmp_path, "mystery.csv",
            "1735689600000,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n",
        )
        with pytest.raises(ValueError, match="Cannot determine symbol/interval"):
            import_csv_batch([csv], tmp_data_dir)


class TestGetFirstTimestampRawFallbacks:
    """首行时间戳原始值兜底：空文件 0 / 非法 int 0 / GENERIC 假定毫秒。"""

    def test_empty_file_returns_zero(self, tmp_path) -> None:
        p = _write(tmp_path, "x.csv", "\n")
        assert _get_first_timestamp_raw(p, ImportFormat.BINANCE_OFFICIAL) == 0

    def test_non_int_binance_first_field_returns_zero(self, tmp_path) -> None:
        p = _write(
            tmp_path, "x.csv",
            "abc,42000.0,42580.0,41850.0,42310.0,312.5,1735693199999,13120000,450,250.0,10550000,0\n",
        )
        assert _get_first_timestamp_raw(p, ImportFormat.BINANCE_OFFICIAL) == 0

    def test_generic_assumes_epoch_2025(self, tmp_path) -> None:
        p = _write(tmp_path, "x.csv", "date,open\n2024-01-01,1.0\n")
        # GENERIC 路径不解析表头，固定返回 2025-01-01 毫秒值
        assert _get_first_timestamp_raw(p, ImportFormat.GENERIC_OHLCV) == 1735689600000
