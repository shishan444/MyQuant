"""K 线 CSV 导入（Binance 官方归档与通用 OHLCV 两种格式）。

职能：把外部 CSV 转换为标准化 OHLCV Parquet 数据集（{symbol}_{interval}
命名），供研究/回测/模拟盘的数据装载层（mtf_loader）消费。服务对象
是数据管理员——经 Web 数据集管理页（/api/data/import 与
/api/data/import-batch，api/routes/data.py）人工导入；程序化数据
保新不在此（增量更新走 core/data/updater）。

能力：格式自动识别、时间戳精度探测（BINANCE ms/us；GENERIC 不可
探测 → None）、文件名解析 symbol/interval（显式参数优先）、OHLCV
完整性校验、多文件批导去重。
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


# ── 枚举 ──────────────────────────────────────────────────────

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


# ── 结果数据类 ────────────────────────────────────────────────

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


# ── 常量 ──────────────────────────────────────────────────────

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


# ── 格式探测 ──────────────────────────────────────────────────

def detect_format(path: Path) -> ImportFormat:
    """探测 CSV 格式（仅检查首行）。

    判定顺序：首字段是可识别列名 → GENERIC_OHLCV；12 列且第二列
    可解析为价格 → BINANCE_OFFICIAL；首列可解析为整数时间戳且
    12 列 → BINANCE_OFFICIAL；兜底 GENERIC_OHLCV。

    Raises:
        ValueError: 文件为空。
    """
    with open(path, "r") as f:
        first_line = f.readline().strip()

    if not first_line:
        raise ValueError(f"File is empty: {path}")

    parts = first_line.split(",")

    # 首字段是可识别的列名 → 带表头的通用格式
    first_field = parts[0].strip().lower()
    if first_field in ("open_time", "timestamp", "date", "datetime",
                       "open", "high", "low", "close", "volume"):
        return ImportFormat.GENERIC_OHLCV

    # 12 列且第二列（开盘价）可浮点解析 → Binance 官方
    if len(parts) == 12:
        try:
            float(parts[1])
            return ImportFormat.BINANCE_OFFICIAL
        except ValueError:
            pass

    # 兜底：首列可解析为整数时间戳且 12 列 → Binance 官方
    try:
        int(parts[0])
        if len(parts) == 12:
            return ImportFormat.BINANCE_OFFICIAL
    except ValueError:
        pass

    return ImportFormat.GENERIC_OHLCV


# ── 时间戳精度 ────────────────────────────────────────────────

def detect_timestamp_precision(ts_value: int) -> TimestampPrecision:
    """按整数的十进制位数探测时间戳精度。

    10-13 位 → 毫秒；14-16 位 → 微秒。

    Raises:
        ValueError: 位数 <10（不可能是 Unix 时间戳）或 >16
            （精度不可判定）。
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


# ── 文件名解析 ────────────────────────────────────────────────

def parse_filename(path: str | Path) -> tuple[str | None, str | None]:
    """从文件名解析 symbol 与 interval。

    期待形态 {SYMBOL}-{INTERVAL}-{YYYY}-{MM}.csv（如
    BTCUSDT-4h-2025-01.csv → ("BTCUSDT", "4h")）；不匹配返回
    (None, None)，由调用方走显式参数或报「无法确定」。
    """
    name = Path(path).stem
    m = FILENAME_PATTERN.match(name)
    if m:
        return m.group(1).upper(), m.group(2)
    return None, None


# ── OHLCV 校验 ────────────────────────────────────────────────

def validate_ohlcv(df: pd.DataFrame) -> list[str]:
    """校验 OHLCV 数据完整性，返回错误清单（空 = 通过）。

    检查项：必需列 NaN / high >= max(open, close, low) /
    low <= min(open, close, high) / volume >= 0 / 价格列非负。
    关系类违规只报首个样本（定位用），计数类错误报总数。
    """
    errors: list[str] = []

    # NaN 检查
    nan_counts = df[REQUIRED_OHLCV].isna().sum()
    for col, count in nan_counts.items():
        if count > 0:
            errors.append(f"Column '{col}' has {count} NaN values")

    # high >= max(open, close, low)（向量化）
    max_ocl = df[["open", "close", "low"]].max(axis=1)
    high_violations = df["high"] < max_ocl
    if high_violations.any():
        idx = df.index[high_violations.values.argmax()]
        errors.append(f"Row {idx}: high ({df.loc[idx, 'high']}) < max(O,C,L) ({max_ocl.loc[idx]})")

    # low <= min(open, close, high)（向量化）
    min_och = df[["open", "close", "high"]].min(axis=1)
    low_violations = df["low"] > min_och
    if low_violations.any():
        idx = df.index[low_violations.values.argmax()]
        errors.append(f"Row {idx}: low ({df.loc[idx, 'low']}) > min(O,C,H) ({min_och.loc[idx]})")

    # volume >= 0
    if (df["volume"] < 0).any():
        neg_count = (df["volume"] < 0).sum()
        errors.append(f"Column 'volume' has {neg_count} negative values")

    # 价格列非负（OHLC 均不得为负）
    for col in ["open", "high", "low", "close"]:
        neg_mask = df[col] < 0
        if neg_mask.any():
            neg_count = neg_mask.sum()
            errors.append(f"Column '{col}' has {neg_count} negative values")

    return errors


# ── CSV 读取 ──────────────────────────────────────────────────

def _read_csv_with_meta(
    path: Path,
) -> tuple[pd.DataFrame, ImportFormat, TimestampPrecision | None]:
    """读 CSV 并一并返回格式与时间戳精度（导入链路的单一读取实现）。

    元信息（fmt/precision）随读取探测返回：导入路径不再在读取之外
    单独调 detect_format/_get_first_timestamp_raw（原 3 次首行探测
    收敛为本函数内 2 次——格式判定 1 次 + BINANCE 结果精度取文本
    首行原始值 1 次，后者为边缘文件行为同源而有意保留）。BINANCE
    官方的 precision 与文本首行原始值同源（含 BOM 等边缘文件行为），
    GENERIC 经 pd.to_datetime 解析、无整数位可探测 → None（不再
    假报毫秒）。
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
        # 列名按映射归一（大小写/别名 → 标准名）
        rename_map = {}
        for col in raw.columns:
            lower = col.strip().lower()
            if lower in GENERIC_COLUMN_MAP:
                rename_map[col] = GENERIC_COLUMN_MAP[lower]
        raw = raw.rename(columns=rename_map)

        # 解析时间戳列（utc=True：统一 tz-aware UTC）
        df = raw[REQUIRED_OHLCV].copy()
        if "timestamp" in raw.columns:
            df["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)

    df = df.set_index("timestamp")
    df = df[REQUIRED_OHLCV]

    # 统一 float dtype（非法值 coerce 为 NaN，交由 validate_ohlcv 报告）
    for col in REQUIRED_OHLCV:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 按时间戳去重（保留首次）并排序
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
    """读取 CSV 并返回标准化 OHLCV DataFrame（公开读取入口）。

    自动识别格式（Binance 官方/通用），自动处理 ms/us 时间戳精度；
    返回 DatetimeIndex（UTC）与 open/high/low/close/volume 列。
    实现见 _read_csv_with_meta（导入链路与本函数共享单一读取实现）。
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


# ── 单文件导入 ────────────────────────────────────────────────

def import_csv(
    path: Path,
    data_dir: Path,
    symbol: str | None = None,
    interval: str | None = None,
    mode: ImportMode = ImportMode.MERGE,
) -> CsvImportResult:
    """导入单个 CSV 到 Parquet 存储。

    Args:
        path: CSV 文件路径。
        data_dir: Parquet 数据目录。
        symbol: 交易对（None 时从文件名探测）。
        interval: K 线周期（None 时从文件名探测）。
        mode: 导入模式（merge/replace/new，写策略见 _write_dataset）。

    Returns:
        CsvImportResult 导入详情（行数/格式/精度/时间范围）。

    Raises:
        ValueError: symbol/interval 无法确定、未过命名白名单或
            OHLCV 校验失败（调用方转 400）。
    """
    # 文件名探测 symbol/interval（显式参数优先）
    detected_symbol, detected_interval = parse_filename(path)
    symbol = symbol or detected_symbol
    interval = interval or detected_interval
    if not symbol or not interval:
        raise ValueError(f"Cannot determine symbol/interval for {path}. Please specify explicitly.")

    # 命名白名单拒绝式校验（dataset_id 直拼写入路径，防路径穿越）
    validate_symbol(symbol)
    validate_import_interval(interval)

    # 读取并校验（元信息随读取一并返回，不再重复探测）
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


# ── 批量导入 ──────────────────────────────────────────────────

def import_csv_batch(
    paths: Sequence[Path],
    data_dir: Path,
    symbol: str | None = None,
    interval: str | None = None,
    mode: ImportMode = ImportMode.MERGE,
) -> CsvImportResult:
    """导入多个 CSV 合并为单个 Parquet 数据集。

    逐文件读取校验（任一文件违规即整体拒绝，不落部分数据），
    拼接去重排序后一次落盘。元信息口径：格式/精度严格取首文件；
    symbol/interval 取首个文件名可解析者（首文件解析失败时顺延）。
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

        # 元信息以首文件探测为准（格式/精度/文件名探测）
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

    # 拼接全量并按时间戳去重（保留首次）排序
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


# ── 内部工具 ──────────────────────────────────────────────────

def _get_first_timestamp_raw(path: Path, fmt: ImportFormat) -> int:
    """取文本首行的原始时间戳整数值（结果字段精度探测用）。

    空文件或首字段不可解析为整数时返回 0（由
    detect_timestamp_precision 拒绝）；GENERIC 无整数位可探测，
    返回占位值 1735689600000——导入链路对 GENERIC 已不消费本值
    （见 _read_csv_with_meta 直接置 None）。
    """
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
        # GENERIC 无整数位：占位毫秒值（恒判 ms 的历史伪值来源）
        return 1735689600000
