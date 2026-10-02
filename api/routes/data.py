"""Data management routes: datasets CRUD, CSV import, preview, OHLCV."""
from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from core.persistence.db_ext import (
    delete_dataset as db_delete_dataset,
    get_dataset,
    list_datasets,
    save_dataset_meta,
)
from api.deps import get_data_dir, get_db_path
from api.schemas import (
    AvailableSource,
    AvailableSourcesResponse,
    DataImportResponse,
    DatasetListResponse,
    DatasetPreviewResponse,
    DatasetResponse,
    OhlcvResponse,
)
from core.data.csv_importer import import_csv, import_csv_batch, parse_filename, ImportMode
from core.data.mtf_loader import find_parquet, safe_symbol
from core.data.storage import load_parquet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])

# 上传防护上限：单文件 256MB（1m 级全量 CSV 约 100-200MB，留裕量）；批量文件数 20
MAX_IMPORT_FILE_BYTES = 256 * 1024 * 1024
MAX_IMPORT_BATCH_FILES = 20

_COPY_CHUNK_BYTES = 1024 * 1024


def _check_upload_size(upload: UploadFile) -> None:
    """读前按实际接收字节数拒绝超限上传（UploadFile.size 由解析器记录）。"""
    if upload.size is not None and upload.size > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {upload.filename} exceeds "
                   f"{MAX_IMPORT_FILE_BYTES // (1024 * 1024)}MB limit",
        )


def _copy_limited(dest, src) -> None:
    """分块落盘上传内容，累计超限即拒（size 缺失时的纵深防线）。"""
    total = 0
    while chunk := src.read(_COPY_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_IMPORT_FILE_BYTES:
            raise HTTPException(status_code=413, detail="File too large")
        dest.write(chunk)


@router.get("/datasets")
def list_datasets_endpoint(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
) -> DatasetListResponse:
    """List datasets with optional filtering."""
    rows = list_datasets(db_path, symbol=symbol, interval=interval, limit=limit)
    items = [
        DatasetResponse(
            dataset_id=r["dataset_id"],
            symbol=r["symbol"],
            interval=r["interval"],
            parquet_path=r["parquet_path"],
            row_count=r.get("row_count", 0),
            time_start=r.get("time_start"),
            time_end=r.get("time_end"),
            file_size_bytes=r.get("file_size_bytes", 0),
            source=r.get("source", "csv_import"),
            format_detected=r.get("format_detected"),
            timestamp_precision=r.get("timestamp_precision"),
            quality_status=r.get("quality_status", "unknown"),
            quality_notes=r.get("quality_notes"),
            gap_count=r.get("gap_count", 0),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]
    return DatasetListResponse(items=items, total=len(items))


@router.get("/datasets/{dataset_id}")
def get_dataset_endpoint(
    dataset_id: str,
    db_path: Path = Depends(get_db_path),
) -> DatasetResponse:
    """Get dataset details by ID."""
    row = get_dataset(db_path, dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DatasetResponse(
        dataset_id=row["dataset_id"],
        symbol=row["symbol"],
        interval=row["interval"],
        parquet_path=row["parquet_path"],
        row_count=row.get("row_count", 0),
        time_start=row.get("time_start"),
        time_end=row.get("time_end"),
        file_size_bytes=row.get("file_size_bytes", 0),
        source=row.get("source", "csv_import"),
        format_detected=row.get("format_detected"),
        timestamp_precision=row.get("timestamp_precision"),
        quality_status=row.get("quality_status", "unknown"),
        quality_notes=row.get("quality_notes"),
        gap_count=row.get("gap_count", 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post("/import")
def import_csv_endpoint(
    file: UploadFile = File(...),
    symbol: Optional[str] = Form(None),
    interval: Optional[str] = Form(None),
    mode: str = Form("merge"),
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> DataImportResponse:
    """Import a CSV file into Parquet storage."""
    # Save uploaded file to a temp location
    data_dir.mkdir(parents=True, exist_ok=True)
    _check_upload_size(file)

    try:
        import_mode = ImportMode(mode)
    except ValueError:
        import_mode = ImportMode.MERGE

    original_stem = Path(file.filename).stem if file.filename else None
    prefix = original_stem + "-" if original_stem else None
    # 先记路径再写：copy 中途超限拒绝时 finally 仍可清理临时文件
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".csv", prefix=prefix, delete=False, dir=data_dir
        ) as tmp:
            tmp_path = Path(tmp.name)
            _copy_limited(tmp, file.file)

        result = import_csv(
            path=tmp_path,
            data_dir=data_dir,
            symbol=symbol,
            interval=interval,
            mode=import_mode,
        )

        # Save dataset metadata
        parquet_path = data_dir / f"{result.dataset_id}.parquet"
        row_count = result.rows_imported
        file_size = parquet_path.stat().st_size if parquet_path.exists() else 0
        # GENERIC 无整数位可探测 → None（不入库伪值）
        precision_str = (
            result.timestamp_precision.value if result.timestamp_precision else None
        )

        # Upsert dataset metadata
        existing = get_dataset(db_path, result.dataset_id)
        if existing is None:
            save_dataset_meta(
                db_path,
                dataset_id=result.dataset_id,
                symbol=result.symbol,
                interval=result.interval,
                parquet_path=str(parquet_path),
                row_count=row_count,
                time_start=result.time_range[0] if result.time_range else None,
                time_end=result.time_range[1] if result.time_range else None,
                file_size_bytes=file_size,
                source="csv_import",
                format_detected=result.format_detected.value,
                timestamp_precision=precision_str,
            )
        else:
            from core.persistence.db_ext import update_dataset_stats
            update_dataset_stats(
                db_path,
                dataset_id=result.dataset_id,
                row_count=row_count,
                time_start=result.time_range[0] if result.time_range else None,
                time_end=result.time_range[1] if result.time_range else None,
                file_size_bytes=file_size,
                format_detected=result.format_detected.value,
                timestamp_precision=precision_str,
            )

        return DataImportResponse(
            dataset_id=result.dataset_id,
            symbol=result.symbol,
            interval=result.interval,
            rows_imported=result.rows_imported,
            format_detected=result.format_detected.value,
            timestamp_precision=precision_str,
            files_processed=result.files_processed,
            time_range=list(result.time_range) if result.time_range else None,
        )
    except ValueError as exc:
        # 非法参数（命名白名单/元数据不可探测/OHLCV 校验失败）→ 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        # Clean up temp file
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@router.post("/import-batch")
def import_csv_batch_endpoint(
    files: List[UploadFile] = File(...),
    symbol: Optional[str] = Form(None),
    interval: Optional[str] = Form(None),
    mode: str = Form("merge"),
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> DataImportResponse:
    """Import multiple CSV files into a single Parquet dataset."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_IMPORT_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files: max {MAX_IMPORT_BATCH_FILES}",
        )
    # 全部预检通过后再落盘，避免中途拒绝时泄漏已写临时文件
    for f in files:
        _check_upload_size(f)

    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        import_mode = ImportMode(mode)
    except ValueError:
        import_mode = ImportMode.MERGE

    tmp_paths: list[Path] = []
    try:
        for f in files:
            # Use original filename stem as prefix so parse_filename can detect symbol/interval
            original_stem = Path(f.filename).stem if f.filename else None
            prefix = original_stem + "-" if original_stem else None
            tmp = tempfile.NamedTemporaryFile(
                suffix=".csv", prefix=prefix, delete=False, dir=data_dir
            )
            # 先记路径再写：copy 中途超限拒绝时 finally 仍可清理临时文件
            tmp_paths.append(Path(tmp.name))
            _copy_limited(tmp, f.file)
            tmp.close()

        result = import_csv_batch(
            paths=tmp_paths,
            data_dir=data_dir,
            symbol=symbol,
            interval=interval,
            mode=import_mode,
        )

        parquet_path = data_dir / f"{result.dataset_id}.parquet"
        row_count = result.rows_imported
        file_size = parquet_path.stat().st_size if parquet_path.exists() else 0
        # GENERIC 无整数位可探测 → None（不入库伪值）
        precision_str = (
            result.timestamp_precision.value if result.timestamp_precision else None
        )

        existing = get_dataset(db_path, result.dataset_id)
        if existing is None:
            save_dataset_meta(
                db_path,
                dataset_id=result.dataset_id,
                symbol=result.symbol,
                interval=result.interval,
                parquet_path=str(parquet_path),
                row_count=row_count,
                time_start=result.time_range[0] if result.time_range else None,
                time_end=result.time_range[1] if result.time_range else None,
                file_size_bytes=file_size,
                source="csv_import",
                format_detected=result.format_detected.value,
                timestamp_precision=precision_str,
            )
        else:
            from core.persistence.db_ext import update_dataset_stats
            update_dataset_stats(
                db_path,
                dataset_id=result.dataset_id,
                row_count=row_count,
                time_start=result.time_range[0] if result.time_range else None,
                time_end=result.time_range[1] if result.time_range else None,
                file_size_bytes=file_size,
                format_detected=result.format_detected.value,
                timestamp_precision=precision_str,
            )

        return DataImportResponse(
            dataset_id=result.dataset_id,
            symbol=result.symbol,
            interval=result.interval,
            rows_imported=result.rows_imported,
            format_detected=result.format_detected.value,
            timestamp_precision=precision_str,
            files_processed=result.files_processed,
            time_range=list(result.time_range) if result.time_range else None,
        )
    except ValueError as exc:
        # 非法参数（命名白名单/元数据不可探测/OHLCV 校验失败）→ 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset_endpoint(
    dataset_id: str,
    db_path: Path = Depends(get_db_path),
) -> None:
    """Delete a dataset by ID."""
    row = get_dataset(db_path, dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    db_delete_dataset(db_path, dataset_id)


@router.get("/datasets/{dataset_id}/preview")
def preview_dataset(
    dataset_id: str,
    limit: int = Query(20, ge=1, le=500),
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> DatasetPreviewResponse:
    """Preview the first N rows of a dataset."""
    row = get_dataset(db_path, dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    parquet_path = Path(row["parquet_path"])
    if not parquet_path.exists():
        # Try data_dir fallback
        parquet_path = data_dir / f"{dataset_id}.parquet"

    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Parquet file not found on disk",
        )

    df = load_parquet(parquet_path)
    preview_df = df.head(limit)

    rows = []
    for idx, row_data in preview_df.iterrows():
        row_dict = {"timestamp": str(idx)}
        for col in row_data.index:
            val = row_data[col]
            # Convert numpy types to native Python types
            if hasattr(val, "item"):
                val = val.item()
            row_dict[col] = val
        rows.append(row_dict)

    return DatasetPreviewResponse(
        dataset_id=dataset_id,
        total_rows=len(df),
        rows=rows,
    )


@router.get("/datasets/{dataset_id}/ohlcv")
def get_ohlcv(
    dataset_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = Query(1000, ge=1, le=50000),
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> OhlcvResponse:
    """Get OHLCV data for a dataset with optional time range filter."""
    row = get_dataset(db_path, dataset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found")

    parquet_path = Path(row["parquet_path"])
    if not parquet_path.exists():
        parquet_path = data_dir / f"{dataset_id}.parquet"

    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Parquet file not found on disk",
        )

    df = load_parquet(parquet_path)

    # Apply time range filter
    import pandas as pd
    if start is not None:
        start_ts = pd.Timestamp(start, tz="UTC") if df.index.tz else pd.Timestamp(start)
        df = df[df.index >= start_ts]
    if end is not None:
        end_ts = pd.Timestamp(end, tz="UTC") if df.index.tz else pd.Timestamp(end)
        df = df[df.index <= end_ts]

    # Apply limit
    df = df.head(limit)

    ohlcv_data = []
    for idx, row_data in df.iterrows():
        ohlcv_data.append({
            "timestamp": str(idx),
            "open": float(row_data.get("open", 0)),
            "high": float(row_data.get("high", 0)),
            "low": float(row_data.get("low", 0)),
            "close": float(row_data.get("close", 0)),
            "volume": float(row_data.get("volume", 0)),
        })

    return OhlcvResponse(
        dataset_id=dataset_id,
        data=ohlcv_data,
    )


@router.get("/available-sources", response_model=AvailableSourcesResponse)
def get_available_sources(
    data_dir: Path = Depends(get_data_dir),
) -> AvailableSourcesResponse:
    """Scan parquet files and return available symbol/timeframe combinations."""
    import re
    import pandas as pd

    sources: list[AvailableSource] = []
    if not data_dir.exists():
        return AvailableSourcesResponse(sources=sources)

    for pq_file in sorted(data_dir.glob("*.parquet")):
        # Parse symbol and timeframe from filename: e.g. BTCUSDT_4h.parquet
        stem = pq_file.stem
        match = re.match(r"^([A-Za-z]+)_(\w+)$", stem)
        if not match:
            continue
        symbol = match.group(1)
        timeframe = match.group(2)

        try:
            df = load_parquet(pq_file)
            if df is None or len(df) == 0:
                continue
            time_start = str(df.index[0]) if len(df.index) > 0 else None
            time_end = str(df.index[-1]) if len(df.index) > 0 else None
        except Exception:
            time_start = None
            time_end = None

        sources.append(AvailableSource(
            symbol=symbol,
            timeframe=timeframe,
            time_start=time_start[:10] if time_start else None,
            time_end=time_end[:10] if time_end else None,
        ))

    return AvailableSourcesResponse(sources=sources)


def _safe_timestamp(date_str: Optional[str], tz_aware: bool):
    """Parse a date string safely; return None on empty or invalid input."""
    if not date_str:
        return None
    import pandas as pd
    try:
        return pd.Timestamp(date_str, tz="UTC") if tz_aware else pd.Timestamp(date_str)
    except Exception:
        return None


def _apply_date_filter(df, start: Optional[str], end: Optional[str]):
    """Filter DataFrame by date range, silently skipping invalid dates."""
    tz_aware = df.index.tz is not None
    start_ts = _safe_timestamp(start, tz_aware)
    end_ts = _safe_timestamp(end, tz_aware)
    if start_ts is not None:
        df = df[df.index >= start_ts]
    if end_ts is not None:
        df = df[df.index <= end_ts]
    return df


@router.get("/ohlcv/{symbol}/{timeframe}")
def get_ohlcv_by_symbol(
    symbol: str,
    timeframe: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = Query(50000, ge=1, le=50000),
    data_dir: Path = Depends(get_data_dir),
) -> OhlcvResponse:
    """Get OHLCV data by symbol+timeframe (resolves parquet file directly)."""
    # 路径解析收口 core find_parquet：周期白名单 + 别名表，杜绝 timeframe 旁路拼接
    symbol_ = safe_symbol(symbol)
    try:
        parquet_path = find_parquet(data_dir, symbol_, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if parquet_path is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}/{timeframe}")

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        raise HTTPException(status_code=404, detail="Empty dataset")

    df = _apply_date_filter(df, start, end)

    df = df.sort_index()
    df = df.head(limit)

    ohlcv_data = []
    for idx, row_data in df.iterrows():
        ohlcv_data.append({
            "timestamp": str(idx),
            "open": float(row_data.get("open", 0)),
            "high": float(row_data.get("high", 0)),
            "low": float(row_data.get("low", 0)),
            "close": float(row_data.get("close", 0)),
            "volume": float(row_data.get("volume", 0)),
        })

    return OhlcvResponse(
        dataset_id=f"{symbol_}_{timeframe}",
        data=ohlcv_data,
    )


@router.get("/chart-indicators/{symbol}/{timeframe}")
def get_chart_indicators(
    symbol: str,
    timeframe: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    ema_periods: Optional[str] = None,
    boll_enabled: bool = True,
    boll_period: int = 20,
    boll_std: float = 2.0,
    rsi_enabled: bool = True,
    rsi_period: int = 14,
    rvol_enabled: bool = False,
    rvol_period: int = 20,
    vwma_enabled: bool = False,
    vwma_period: int = 20,
    limit: int = Query(50000, ge=1, le=50000),
    macd_enabled: bool = False,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    kdj_enabled: bool = False,
    kdj_k_period: int = 14,
    kdj_d_period: int = 3,
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """Compute chart indicators (EMA, BOLL, RSI, RVOL, VWMA, MACD, KDJ) for chart rendering."""
    from core.features.indicators import _compute_indicator

    # 路径解析收口 core find_parquet：周期白名单 + 别名表，杜绝 timeframe 旁路拼接
    symbol_ = safe_symbol(symbol)
    try:
        parquet_path = find_parquet(data_dir, symbol_, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if parquet_path is None:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}/{timeframe}")

    df = load_parquet(parquet_path)
    if df is None or len(df) == 0:
        raise HTTPException(status_code=404, detail="Empty dataset")

    df = _apply_date_filter(df, start, end)

    df = df.sort_index()

    if limit > 0:
        df = df.head(limit)

    result: Dict[str, Any] = {"ema": {}, "boll": None, "rsi": None, "rvol": None, "vwma": None, "macd": None, "kdj": None}

    # Compute EMA for requested periods
    if ema_periods:
        periods = [int(p.strip()) for p in ema_periods.split(",") if p.strip()]
        for period in periods:
            try:
                ema_cols = _compute_indicator(df, "EMA", {"period": period})
                col_name = f"ema_{period}"
                if col_name in ema_cols:
                    series = ema_cols[col_name].dropna()
                    result["ema"][str(period)] = [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in series.items()
                    ]
            except Exception:
                # B1.5: 图表层缺失必须可区分「未开启」与「计算失败」
                logger.warning(
                    "chart-indicators %s/%s: EMA(%d) 计算失败，该层将缺失",
                    symbol_, timeframe, period,
                    exc_info=True,
                )
                continue

    # Compute Bollinger Bands
    if boll_enabled:
        try:
            bb_cols = _compute_indicator(df, "BB", {"period": boll_period, "std": boll_std})
            std_str = str(boll_std).replace(".0", "")
            upper_col = f"bb_upper_{boll_period}_{std_str}"
            middle_col = f"bb_middle_{boll_period}_{std_str}"
            lower_col = f"bb_lower_{boll_period}_{std_str}"
            if all(col in bb_cols for col in [upper_col, middle_col, lower_col]):
                result["boll"] = {
                    "upper": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in bb_cols[upper_col].dropna().items()
                    ],
                    "middle": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in bb_cols[middle_col].dropna().items()
                    ],
                    "lower": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in bb_cols[lower_col].dropna().items()
                    ],
                }
        except Exception:
            logger.warning(
                "chart-indicators %s/%s: BOLL 计算失败，该层将缺失",
                symbol_, timeframe,
                exc_info=True,
            )

    # Compute RSI
    if rsi_enabled:
        try:
            rsi_cols = _compute_indicator(df, "RSI", {"period": rsi_period})
            col_name = f"rsi_{rsi_period}"
            if col_name in rsi_cols:
                series = rsi_cols[col_name].dropna()
                result["rsi"] = [
                    {"time": str(idx), "value": float(val)}
                    for idx, val in series.items()
                ]
        except Exception:
            logger.warning(
                "chart-indicators %s/%s: RSI 计算失败，该层将缺失",
                symbol_, timeframe,
                exc_info=True,
            )

    # Compute RVOL (Relative Volume)
    if rvol_enabled:
        try:
            rvol_cols = _compute_indicator(df, "RVOL", {"period": rvol_period})
            col_name = f"rvol_{rvol_period}"
            if col_name in rvol_cols:
                series = rvol_cols[col_name].dropna()
                result["rvol"] = [
                    {"time": str(idx), "value": float(val)}
                    for idx, val in series.items()
                ]
        except Exception:
            logger.warning(
                "chart-indicators %s/%s: RVOL 计算失败，该层将缺失",
                symbol_, timeframe,
                exc_info=True,
            )

    # Compute VWMA (Volume Weighted Moving Average)
    if vwma_enabled:
        try:
            vwma_cols = _compute_indicator(df, "VWMA", {"period": vwma_period})
            col_name = f"vwma_{vwma_period}"
            if col_name in vwma_cols:
                series = vwma_cols[col_name].dropna()
                result["vwma"] = [
                    {"time": str(idx), "value": float(val)}
                    for idx, val in series.items()
                ]
        except Exception:
            logger.warning(
                "chart-indicators %s/%s: VWMA 计算失败，该层将缺失",
                symbol_, timeframe,
                exc_info=True,
            )

    # Compute MACD
    if macd_enabled:
        try:
            macd_cols = _compute_indicator(df, "MACD", {"fast": macd_fast, "slow": macd_slow, "signal": macd_signal})
            macd_col = f"macd_{macd_fast}_{macd_slow}_{macd_signal}"
            signal_col = f"macd_signal_{macd_fast}_{macd_slow}_{macd_signal}"
            hist_col = f"macd_histogram_{macd_fast}_{macd_slow}_{macd_signal}"
            if all(col in macd_cols for col in [macd_col, signal_col, hist_col]):
                result["macd"] = {
                    "macd": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in macd_cols[macd_col].dropna().items()
                    ],
                    "signal": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in macd_cols[signal_col].dropna().items()
                    ],
                    "histogram": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in macd_cols[hist_col].dropna().items()
                    ],
                }
        except Exception:
            logger.warning(
                "chart-indicators %s/%s: MACD 计算失败，该层将缺失",
                symbol_, timeframe,
                exc_info=True,
            )

    # Compute KDJ
    if kdj_enabled:
        try:
            stoch_cols = _compute_indicator(df, "Stochastic", {"k_period": kdj_k_period, "d_period": kdj_d_period})
            k_col = f"stoch_k_{kdj_k_period}_{kdj_d_period}"
            d_col = f"stoch_d_{kdj_k_period}_{kdj_d_period}"
            if all(col in stoch_cols for col in [k_col, d_col]):
                k_series = stoch_cols[k_col].dropna()
                d_series = stoch_cols[d_col].dropna()
                # Align indices for J calculation
                common_idx = k_series.index.intersection(d_series.index)
                k_aligned = k_series.loc[common_idx]
                d_aligned = d_series.loc[common_idx]
                j_series = 3 * k_aligned - 2 * d_aligned

                result["kdj"] = {
                    "k": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in k_series.items()
                    ],
                    "d": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in d_series.items()
                    ],
                    "j": [
                        {"time": str(idx), "value": float(val)}
                        for idx, val in j_series.items()
                    ],
                }
        except Exception:
            logger.warning(
                "chart-indicators %s/%s: KDJ 计算失败，该层将缺失",
                symbol_, timeframe,
                exc_info=True,
            )

    return result