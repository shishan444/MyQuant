"""09 数据管理两端点（data-management-revamp 组 A/B）。

完整性月矩阵 v2（分档两段）+guardian 状态纯透传。挂 data.py 路由族
（prefix /api/data）——独立文件避免 data.py 继续膨胀，经 include 注册。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.deps import get_db_path, get_data_dir

logger = logging.getLogger("data_workbench")

router = APIRouter(prefix="/api/data", tags=["data"])

# ---------------------------------------------------------------------------
# 组 B：guardian 状态纯透传（09-B6r 十二路保鲜表唯一数据源）
# ---------------------------------------------------------------------------


class FreshnessStatusResponse(BaseModel):
    # 纯透传零改写（六态语义权威在 guardian——端点不解释，呈现归前端）
    kline: Optional[Dict[str, Any]] = None
    derivs: Optional[Dict[str, Any]] = None


@router.get("/freshness/status", response_model=FreshnessStatusResponse)
def freshness_status(
    request: Request,
    db_path=Depends(get_db_path),
    data_dir=Depends(get_data_dir),
) -> FreshnessStatusResponse:
    """透传 guardian.status()——挂点 request.app.state.paper_scheduler.registry.guardian。

    未装配（scheduler 未启动/守护器未建）→503 不伪装空态（区分「未运行」
    与「运行无数据」——data-freshness-guardian spec 降级语义）。
    """
    scheduler = getattr(request.app.state, "paper_scheduler", None)
    guardian = getattr(getattr(scheduler, "registry", None), "guardian", None)
    if guardian is None or not hasattr(guardian, "status"):
        raise HTTPException(
            status_code=503,
            detail={"code": "FRESHNESS_GUARDIAN_UNAVAILABLE",
                    "message": "freshness guardian 未装配（调度器未启动或守护器未建）"})
    status = guardian.status()
    return FreshnessStatusResponse(
        kline=status.get("kline"), derivs=status.get("derivs"))


# ---------------------------------------------------------------------------
# 组 A：完整性月矩阵 v2（分档两段——design §5.1 全文）
# ---------------------------------------------------------------------------


class MonthCell(BaseModel):
    timeframe: str
    status: str          # green|yellow|gray
    bars_actual: int
    bars_expected: int
    gap_count: int
    first_gap: Optional[Dict[str, str]] = None   # {start, end}|null=无缺口


class MonthRow(BaseModel):
    month: str           # "2026-05"
    cells: List[MonthCell]


class LongPeriodRow(BaseModel):
    timeframe: str
    status: str
    coverage: Dict[str, Any]    # {first_bar, last_bar, bars}
    gap_count: int
    gaps: List[Dict[str, str]]


class MonthlyMatrixResponse(BaseModel):
    symbol: str
    generated_at: str
    months_back: int
    short_period: Dict[str, Any]        # {timeframes: [...], matrix: [MonthRow]}
    long_period: List[LongPeriodRow]


def _tf_seconds(tf: str) -> Optional[int]:
    from core.data.timeframes import TF_SECONDS
    return TF_SECONDS.get(tf)


def _scan_timeframes(data_dir: Path, symbol: str) -> List[str]:
    """目录现算周期档集（find_parquet 白名单枚举——不硬编码）。"""
    from core.data.mtf_loader import safe_symbol
    from core.data.timeframes import TF_SECONDS
    sym = safe_symbol(symbol)
    found = []
    for tf in TF_SECONDS:
        f = data_dir / f"{sym}_{tf}.parquet"
        if f.exists():
            found.append(tf)
    return found


def _check_frame(df) -> Dict[str, Any]:
    """三检查项单段（连续性/空值/OHLC 非法）——返回检查结果（不含 status 定级）。"""
    import pandas as pd
    n = len(df)
    if n == 0:
        return {"empty": True, "gap_count": 0, "gaps": [],
                "has_null": False, "has_invalid": False}
    ts = df.index
    gaps: List[Dict[str, str]] = []
    if n >= 2:
        diffs = ts.to_series().diff().dropna()
        # 连续性：相邻间隔==TF（首尾容差由调用方处理——首行 diff 为 NaT 天然跳过）
        expected = diffs.mode().iloc[0] if not diffs.empty else None
        if expected is not None:
            bad = diffs[diffs != expected]
            # 合并相邻异常为缺口段（简化：逐异常段记 start/end）
            running: Optional[Any] = None
            for idx, _ in bad.items():
                prev = ts[ts < idx]
                start = prev[-1] if len(prev) else ts[0]
                if running is not None and start == running["end_obj"]:
                    running["end_obj"] = idx
                    continue
                if running is not None:
                    gaps.append({"start": str(running["start_obj"]),
                                 "end": str(running["end_obj"])})
                running = {"start_obj": start, "end_obj": idx}
            if running is not None:
                gaps.append({"start": str(running["start_obj"]),
                             "end": str(running["end_obj"])})
    has_null = bool(df[["open", "high", "low", "close"]].isna().any().any()) if n else False
    if n:
        ohlc_ok = ((df["high"] >= df["low"])
                   & (df[["open", "close", "high", "low"]] >= 0).all(axis=1)
                   & (df["open"] <= df["high"]) & (df["open"] >= df["low"])
                   & (df["close"] <= df["high"]) & (df["close"] >= df["low"]))
        has_invalid = bool((~ohlc_ok).any())
    else:
        has_invalid = False
    return {"empty": False, "gap_count": len(gaps), "gaps": gaps,
            "has_null": has_null, "has_invalid": has_invalid,
            "first_bar": str(ts[0]), "last_bar": str(ts[-1]), "bars": n}


def _status_of(check: Dict[str, Any]) -> str:
    if check.get("empty"):
        return "gray"
    if check["gap_count"] > 0 or check["has_null"] or check["has_invalid"]:
        return "yellow"
    return "green"


@router.get("/integrity/monthly-matrix", response_model=MonthlyMatrixResponse)
def integrity_monthly_matrix(
    request: Request,
    symbol: str = Query(...),
    months_back: int = Query(6, ge=1, le=12),
    db_path=Depends(get_db_path),
    data_dir=Depends(get_data_dir),
) -> MonthlyMatrixResponse:
    """月矩阵 v2 分档两段（design §5.1）——只读现算零持久化（REJ-04 零修复建议）。"""
    from datetime import datetime, timedelta, timezone as tz
    from core.data.mtf_loader import safe_symbol
    from core.data.storage import load_parquet

    sym = safe_symbol(symbol)
    timeframes = _scan_timeframes(data_dir, sym)
    if not timeframes:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    short_tfs = [tf for tf in timeframes if (_tf_seconds(tf) or 0) <= 4 * 3600]
    long_tfs = [tf for tf in timeframes if (_tf_seconds(tf) or 0) > 4 * 3600]

    now = datetime.now(tz.utc)
    months = []
    base = now.replace(day=1)
    for i in range(months_back):
        m_end = base - timedelta(days=0 if i == 0 else 1)
        m_start = (base - timedelta(days=28 * i)).replace(day=1)
        months.append((m_start, m_end))
    # 简化月序列：从当月往回取 months_back 个自然月（YYYY-MM）
    month_keys: List[str] = []
    y, m = now.year, now.month
    for _ in range(months_back):
        month_keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    month_keys.reverse()

    matrix: List[MonthRow] = []
    for mk in month_keys:
        cells = []
        for tf in short_tfs:
            f = data_dir / f"{sym}_{tf}.parquet"
            df = load_parquet(f)
            if df is None or len(df) == 0:
                cells.append(MonthCell(
                    timeframe=tf, status="gray", bars_actual=0,
                    bars_expected=0, gap_count=0, first_gap=None))
                continue
            month_df = df[df.index.strftime("%Y-%m") == mk]
            check = _check_frame(month_df)
            tf_sec = _tf_seconds(tf) or 14400
            bars_expected = int(
                (month_df.index.max() - month_df.index.min()).total_seconds()
                // tf_sec) + 1 if len(month_df) else 0
            first_gap = check["gaps"][0] if check["gaps"] else None
            cells.append(MonthCell(
                timeframe=tf, status=_status_of(check),
                bars_actual=check.get("bars", 0),
                bars_expected=max(bars_expected, check.get("bars", 0)),
                gap_count=check["gap_count"], first_gap=first_gap))
        matrix.append(MonthRow(month=mk, cells=cells))

    long_rows: List[LongPeriodRow] = []
    for tf in long_tfs:
        f = data_dir / f"{sym}_{tf}.parquet"
        df = load_parquet(f)
        if df is None or len(df) == 0:
            long_rows.append(LongPeriodRow(
                timeframe=tf, status="gray",
                coverage={"first_bar": None, "last_bar": None, "bars": 0},
                gap_count=0, gaps=[]))
            continue
        check = _check_frame(df)
        long_rows.append(LongPeriodRow(
            timeframe=tf, status=_status_of(check),
            coverage={"first_bar": check["first_bar"],
                      "last_bar": check["last_bar"], "bars": check["bars"]},
            gap_count=check["gap_count"],
            gaps=[{"start": g["start"], "end": g["end"]} for g in check["gaps"]]))

    return MonthlyMatrixResponse(
        symbol=sym,
        generated_at=now.isoformat(),
        months_back=months_back,
        short_period={"timeframes": short_tfs, "matrix": [r.model_dump() for r in matrix]},
        long_period=long_rows)
