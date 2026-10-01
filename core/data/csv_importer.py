"""CSV import module for K-line data.

Supports:
- Binance official CSV (no header, 12 columns)
- Generic OHLCV CSV (with header row)
- Automatic timestamp precision detection (ms / us)
- Filename-based symbol/interval parsing
- OHLCV data validation
- Multi-file batch import with deduplication
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from core.data.storage import merge_parquet, save_parquet
from core.data.validation import (
    ensure_path_within,
    validate_import_interval,
    validate_symbol,
)


# ── Enums ─────────────────────────────────────────────────────

class ImportFormat(Enum):
    BINANCE_OFFICIAL = "binance_official"
    GENERIC_OHLCV = "generic_ohlcv"


class TimestampPrecision(Enum):
    MILLISECOND = "ms"
    MICROSECOND = "us"


class ImportMode(Enum):
    MERGE = "merge"
    REPLACE = "replace"
    NEW = "new"


# ── Result Dataclass ──────────────────────────────────────────

@dataclass
class CsvImportResult:
    dataset_id: str
    symbol: str
    interval: str
    rows_imported: int
    format_detected: ImportFormat
    # GENERIC 格式经 pd.to_datetime 解析、无整数位可探测 → None
    # （不可探测）；BINANCE 官方为 ms/us 探测结果
    timestamp_precision: TimestampPrecision | None
    files_processed: int = 1
    time_range: tuple[str, str] | None = None


# ── Constants ─────────────────────────────────────────────────

FILENAME_PATTERN = re.compile(
    r"^([A-Z]{3,10}USDT)-(\d+[mhdw])-",
    re.IGNORECASE,
)

GENERIC_COLUMN_MAP: dict[str, str] = {
    "open_time": "timestamp",
    "timestamp": "timestamp",
    "date": "timestamp",
    "datetime": "timestamp",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
}

BINANCE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

REQUIRED_OHLCV = ["open", "high", "low", "close", "volume"]


# ── Format Detection ──────────────────────────────────────────

def detect_format(path: Path) -> ImportFormat:
    """Detect CSV format by inspecting the first line.

    - Binance official: no header, 12 numeric columns
    - Generic OHLCV: has header with recognizable column names
    """
    with open(path, "r") as f:
        first_line = f.readline().strip()

    if not first_line:
        raise ValueError(f"File is empty: {path}")

    parts = first_line.split(",")

    # Check if first field is a recognizable column name
    first_field = parts[0].strip().lower()
    if first_field in ("open_time", "timestamp", "date", "datetime",
                       "open", "high", "low", "close", "volume"):
        return ImportFormat.GENERIC_OHLCV

    # Check if it looks like numeric Binance data (12 columns)
    if len(parts) == 12:
        try:
            float(parts[1])  # open price
            return ImportFormat.BINANCE_OFFICIAL
        except ValueError:
            pass

    # Fallback: try to see if first column is a timestamp number
    try:
        int(parts[0])
        if len(parts) == 12:
            return ImportFormat.BINANCE_OFFICIAL
    except ValueError:
        pass

    return ImportFormat.GENERIC_OHLCV


# ── Timestamp Precision ───────────────────────────────────────

def detect_timestamp_precision(ts_value: int) -> TimestampPrecision:
    """Detect timestamp precision from the integer value.

    - 13 digits -> millisecond
    - 16 digits -> microsecond
    """
    digits = len(str(abs(ts_value)))
    if digits < 10:
        raise ValueError(f"Value too small to be a Unix timestamp: {ts_value}")
    elif digits <= 13:
        return TimestampPrecision.MILLISECOND
    elif digits <= 16:
        return TimestampPrecision.MICROSECOND
    else:
        raise ValueError(f"Cannot determine timestamp precision for value: {ts_value}")


# ── Filename Parsing ──────────────────────────────────────────

def parse_filename(path: str | Path) -> tuple[str | None, str | None]:
    """Parse symbol and interval from filename.

    Expected pattern: {SYMBOL}-{INTERVAL}-{YYYY}-{MM}.csv
    Example: BTCUSDT-4h-2025-01.csv -> ("BTCUSDT", "4h")
    """
    name = Path(path).stem
    m = FILENAME_PATTERN.match(name)
    if m:
        return m.group(1).upper(), m.group(2)
    return None, None


# ── OHLCV Validation ──────────────────────────────────────────

def validate_ohlcv(df: pd.DataFrame) -> list[str]:
    """Validate OHLCV data integrity.

    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    # Check NaN
    nan_counts = df[REQUIRED_OHLCV].isna().sum()
    for col, count in nan_counts.items():
        if count > 0:
            errors.append(f"Column '{col}' has {count} NaN values")

    # Check high >= max(open, close, low) -- vectorized
    max_ocl = df[["open", "close", "low"]].max(axis=1)
    high_violations = df["high"] < max_ocl
    if high_violations.any():
        idx = df.index[high_violations.values.argmax()]
        errors.append(f"Row {idx}: high ({df.loc[idx, 'high']}) < max(O,C,L) ({max_ocl.loc[idx]})")

    # Check low <= min(open, close, high) -- vectorized
    min_och = df[["open", "close", "high"]].min(axis=1)
    low_violations = df["low"] > min_och
    if low_violations.any():
        idx = df.index[low_violations.values.argmax()]
        errors.append(f"Row {idx}: low ({df.loc[idx, 'low']}) > min(O,C,H) ({min_och.loc[idx]})")

    # Check volume >= 0
    if (df["volume"] < 0).any():
        neg_count = (df["volume"] < 0).sum()
        errors.append(f"Column 'volume' has {neg_count} negative values")

    # Check negative prices (OHLC must be non-negative)
    for col in ["open", "high", "low", "close"]:
        neg_mask = df[col] < 0
        if neg_mask.any():
            neg_count = neg_mask.sum()
            errors.append(f"Column '{col}' has {neg_count} negative values")

    return errors


# ── CSV Reading ───────────────────────────────────────────────

def _read_csv_with_meta(
    path: Path,
) -> tuple[pd.DataFrame, ImportFormat, TimestampPrecision | None]:
    """读 CSV 并一并返回格式与时间戳精度（导入链路的单一读取实现）。

    元信息（fmt/precision）随首次读取探测返回，消除导入路径上
    detect_format/_get_first_timestamp_raw 的重复首行读；BINANCE
    官方的 precision 与文本首行原始值同源（含 BOM 等边缘文件
    行为），GENERIC 经 pd.to_datetime 解析、无整数位可探测 →
    None（不可探测，不再假报毫秒）。
    """
    fmt = detect_format(path)

    if fmt == ImportFormat.BINANCE_OFFICIAL:
        df = pd.read_csv(path, header=None, names=BINANCE_COLUMNS)
        ts_col = df["open_time"].iloc[0]
        unit_precision = detect_timestamp_precision(int(ts_col))

        if unit_precision == TimestampPrecision.MICROSECOND:
            df["timestamp"] = pd.to_datetime(df["open_time"], unit="us", utc=True)
        else:
            df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

    else:  # GENERIC_OHLCV
        raw = pd.read_csv(path)
        # Rename columns using mapping
        rename_map = {}
        for col in raw.columns:
            lower = col.strip().lower()
            if lower in GENERIC_COLUMN_MAP:
                rename_map[col] = GENERIC_COLUMN_MAP[lower]
        raw = raw.rename(columns=rename_map)

        # Parse timestamp
        df = raw[REQUIRED_OHLCV].copy()
        if "timestamp" in raw.columns:
            df["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)

    df = df.set_index("timestamp")
    df = df[REQUIRED_OHLCV]

    # Ensure float dtypes
    for col in REQUIRED_OHLCV:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Remove duplicates
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    # 结果字段精度：BINANCE 官方与文本首行原始值同源（read_csv 内部
    # 用于选 unit 的解析值仅作解析用途，结果语义以原始值探测为准）；
    # GENERIC 无整数位可探测 → None
    if fmt == ImportFormat.BINANCE_OFFICIAL:
        precision = detect_timestamp_precision(_get_first_timestamp_raw(path, fmt))
    else:
        precision = None
    return df, fmt, precision


def read_csv(path: Path) -> pd.DataFrame:
    """Read CSV file and return standardized OHLCV DataFrame.

    Auto-detects format (Binance official or generic).
    Handles ms/us timestamp precision automatically.
    Returns DataFrame with DatetimeIndex (UTC) and columns:
    open, high, low, close, volume.
    """
    df, _, _ = _read_csv_with_meta(path)
    return df


def _write_dataset(df: pd.DataFrame, parquet_path: Path, mode: ImportMode) -> None:
    """按导入模式落盘（mode → 写策略单一实现）。

    MERGE 为增量合并（存在即合并去重）；REPLACE 与 NEW 均为覆盖写
    （NEW 的差异化语义未定，维持与 REPLACE 同行为的现状——C3 已
    登记技术债务）。
    """
    if mode == ImportMode.MERGE and parquet_path.exists():
        merge_parquet(df, parquet_path)
    else:
        save_parquet(df, parquet_path)


# ── Single File Import ────────────────────────────────────────

def import_csv(
    path: Path,
    data_dir: Path,
    symbol: str | None = None,
    interval: str | None = None,
    mode: ImportMode = ImportMode.MERGE,
) -> CsvImportResult:
    """Import a single CSV file into Parquet storage.

    Args:
        path: Path to CSV file.
        data_dir: Directory for Parquet files.
        symbol: Trading pair (auto-detected from filename if None).
        interval: Timeframe (auto-detected from filename if None).
        mode: Import mode (merge/replace/new).

    Returns:
        CsvImportResult with import details.
    """
    # Auto-detect symbol/interval
    detected_symbol, detected_interval = parse_filename(path)
    symbol = symbol or detected_symbol
    interval = interval or detected_interval
    if not symbol or not interval:
        raise ValueError(f"Cannot determine symbol/interval for {path}. Please specify explicitly.")

    # 命名白名单拒绝式校验（dataset_id 直拼写入路径，防路径穿越）
    validate_symbol(symbol)
    validate_import_interval(interval)

    # Read and validate（元信息随读取一并返回，不再重复探测）
    df, fmt, precision = _read_csv_with_meta(path)
    errors = validate_ohlcv(df)
    if errors:
        raise ValueError(f"OHLCV validation failed: {'; '.join(errors)}")

    dataset_id = f"{symbol}_{interval}"
    parquet_path = data_dir / f"{dataset_id}.parquet"
    # 纵深防御：写入路径不得越出 data_dir
    ensure_path_within(parquet_path, data_dir)

    _write_dataset(df, parquet_path, mode)

    time_start = str(df.index.min())
    time_end = str(df.index.max())

    return CsvImportResult(
        dataset_id=dataset_id,
        symbol=symbol,
        interval=interval,
        rows_imported=len(df),
        format_detected=fmt,
        timestamp_precision=precision,
        time_range=(time_start, time_end),
    )


# ── Batch Import ──────────────────────────────────────────────

def import_csv_batch(
    paths: Sequence[Path],
    data_dir: Path,
    symbol: str | None = None,
    interval: str | None = None,
    mode: ImportMode = ImportMode.MERGE,
) -> CsvImportResult:
    """Import multiple CSV files into a single Parquet dataset.

    Files are read, concatenated, deduplicated, and written as one dataset.
    """
    if not paths:
        raise ValueError("No files provided")

    all_dfs: list[pd.DataFrame] = []
    detected_symbol = symbol
    detected_interval = interval
    detected_fmt = None
    detected_precision = None

    for p in paths:
        # 元信息随读取一并返回（首文件探测不再重复读首行）
        df, fmt, precision = _read_csv_with_meta(p)
        errors = validate_ohlcv(df)
        if errors:
            raise ValueError(f"OHLCV validation failed for {p.name}: {'; '.join(errors)}")

        all_dfs.append(df)

        # Detect metadata from first file
        if detected_symbol is None or detected_interval is None:
            s, iv = parse_filename(p)
            detected_symbol = detected_symbol or s
            detected_interval = detected_interval or iv

        if detected_fmt is None:
            detected_fmt = fmt
            detected_precision = precision

    if not detected_symbol or not detected_interval:
        raise ValueError("Cannot determine symbol/interval from files")

    # 命名白名单拒绝式校验（dataset_id 直拼写入路径，防路径穿越）
    validate_symbol(detected_symbol)
    validate_import_interval(detected_interval)

    # Concatenate all
    merged = pd.concat(all_dfs)
    merged = merged[~merged.index.duplicated(keep="first")]
    merged = merged.sort_index()

    dataset_id = f"{detected_symbol}_{detected_interval}"
    parquet_path = data_dir / f"{dataset_id}.parquet"
    # 纵深防御：写入路径不得越出 data_dir
    ensure_path_within(parquet_path, data_dir)

    _write_dataset(merged, parquet_path, mode)

    time_start = str(merged.index.min())
    time_end = str(merged.index.max())

    return CsvImportResult(
        dataset_id=dataset_id,
        symbol=detected_symbol,
        interval=detected_interval,
        rows_imported=len(merged),
        format_detected=detected_fmt or ImportFormat.BINANCE_OFFICIAL,
        # GENERIC 不可探测如实返回 None（原 or 兜底 MILLISECOND 是伪值来源）
        timestamp_precision=detected_precision,
        files_processed=len(paths),
        time_range=(time_start, time_end),
    )


# ── Internal Helpers ──────────────────────────────────────────

def _get_first_timestamp_raw(path: Path, fmt: ImportFormat) -> int:
    """Get the raw timestamp value from the first data row."""
    with open(path, "r") as f:
        first_line = f.readline().strip()
    if not first_line:
        return 0

    parts = first_line.split(",")
    if fmt == ImportFormat.BINANCE_OFFICIAL:
        try:
            return int(parts[0])
        except (ValueError, IndexError):
            return 0
    else:
        # For generic, assume millisecond
        return 1735689600000
