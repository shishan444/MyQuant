"""退役写入通道后的验证记录 SQL 直插工厂（retire-legacy 任务 5.6 口径）。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Optional
from sqlalchemy import text
from core.persistence.orm.engine import engine_for

def insert_validation_record(
    db: Path, *,
    record_id: str,
    hypothesis_id: str,
    strategy_id: Optional[str],
    level: str,
    params: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    verdict: str = "",
    segmentation: str = "",
    range_hash: str = "",
    created_at: str = "2026-11-01T00:00:00",
) -> str:
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, params, result, verdict,"
            " range_hash, created_at) VALUES (:r, :h, :s, :l, :g, :p,"
            " :v, :w, :x, :c)"),
            {"r": record_id, "h": hypothesis_id, "s": strategy_id,
             "l": level, "g": segmentation,
             "p": json.dumps(params, ensure_ascii=False)
             if isinstance(params, dict) else params,
             "v": json.dumps(result, ensure_ascii=False)
             if isinstance(result, dict) else result,
             "w": verdict, "x": range_hash, "c": created_at})
    return record_id
