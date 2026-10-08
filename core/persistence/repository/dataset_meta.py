"""dataset_meta repository（B3a.3 试点域迁移）。

表访问唯一入口（绞杀者第一步）：SQLAlchemy Core 语句 + 模型元数据，
函数级事务（design.md 事务语义裁决——非进化域终态）。旧函数签名
不变，db_ext 同名函数改为委托本模块，调用方零改动。

行为等价锚点：save 裸 INSERT（重复主键 IntegrityError 与旧轨一致，
调用方先 get 后 save/update 的使用序不变）；update 沿用字段白名单
+ updated_at 刷新；delete 不存在时无错误。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select, update

from core.persistence.orm.engine import engine_for
from core.persistence.orm.models import DatasetMeta

_ALLOWED_UPDATE_FIELDS = frozenset({
    "row_count", "time_start", "time_end", "file_size_bytes",
    "format_detected", "timestamp_precision", "ohlcv_stats",
    "gap_count", "quality_status", "quality_notes", "import_batch_id",
    "last_import_at", "parquet_path",
})


# CARD-07 公共化：_engine_for 上提至 orm/engine.engine_for，此处保留
# re-export 过渡（tests/ 30+ 处 from ...dataset_meta import _engine_for
# 依赖此名；生产消费方已切公共入口）
_engine_for = engine_for

from core.persistence.timestamps import utc_now_iso as _now  # B4.1 归一


def save_dataset_meta(
    db_path: Path,
    *,
    dataset_id: str,
    symbol: str,
    interval: str,
    parquet_path: str,
    row_count: int = 0,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    file_size_bytes: int = 0,
    source: str = "csv_import",
    format_detected: Optional[str] = None,
    timestamp_precision: Optional[str] = None,
    ohlcv_stats: Optional[str] = None,
    gap_count: int = 0,
    quality_status: str = "unknown",
    quality_notes: Optional[str] = None,
    import_batch_id: Optional[str] = None,
) -> None:
    """Insert a dataset metadata record（函数级事务）。"""
    now = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(DatasetMeta).values(
            dataset_id=dataset_id, symbol=symbol, interval=interval,
            parquet_path=parquet_path, row_count=row_count,
            time_start=time_start, time_end=time_end,
            file_size_bytes=file_size_bytes, source=source,
            format_detected=format_detected,
            timestamp_precision=timestamp_precision,
            ohlcv_stats=ohlcv_stats, gap_count=gap_count,
            quality_status=quality_status, quality_notes=quality_notes,
            import_batch_id=import_batch_id,
            last_import_at=now, created_at=now, updated_at=now,
        ))


def get_dataset(db_path: Path, dataset_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single dataset by ID, or None if not found."""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(DatasetMeta).where(DatasetMeta.dataset_id == dataset_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_datasets(
    db_path: Path,
    *,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List datasets with optional filtering（created_at 倒序，旧行为）。"""
    stmt = select(DatasetMeta)
    if symbol is not None:
        stmt = stmt.where(DatasetMeta.symbol == symbol)
    if interval is not None:
        stmt = stmt.where(DatasetMeta.interval == interval)
    stmt = stmt.order_by(DatasetMeta.created_at.desc()).limit(limit)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def update_dataset_stats(
    db_path: Path,
    *,
    dataset_id: str,
    **fields: Any,
) -> None:
    """Update fields on a dataset record. Refreshes ``updated_at``（白名单不变）。"""
    values = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_FIELDS}
    if not values:
        return
    values["updated_at"] = _now()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(DatasetMeta)
            .where(DatasetMeta.dataset_id == dataset_id)
            .values(**values)
        )


def delete_dataset(db_path: Path, dataset_id: str) -> None:
    """Delete a dataset by ID. No error if not found."""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            delete(DatasetMeta).where(DatasetMeta.dataset_id == dataset_id)
        )
