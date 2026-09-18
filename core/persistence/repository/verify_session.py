"""verify_session repository（B3b.1 迁移）。

行为等价锚点：save 裸 INSERT（status 默认 running）；update 仅更新
显式非 None 字段（与旧轨 if not None 逐字段语义一致）；list 按
created_at 倒序 + limit。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import insert, select, update

from core.persistence.orm.models import VerifySession
from core.persistence.repository.dataset_meta import _engine_for, _now


def save_verify_session(
    db_path: Path,
    *,
    session_id: str,
    strategy_ids: str,
    data_ranges: str,
    init_cash: float = 100000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    status: str = "running",
) -> None:
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(VerifySession).values(
            session_id=session_id, status=status, strategy_ids=strategy_ids,
            data_ranges=data_ranges, init_cash=init_cash, fee=fee,
            slippage=slippage, created_at=_now(),
        ))


def get_verify_session(db_path: Path, session_id: str) -> Optional[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(VerifySession).where(VerifySession.session_id == session_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def update_verify_session(
    db_path: Path,
    session_id: str,
    *,
    status: Optional[str] = None,
    summary_json: Optional[str] = None,
    total_results: Optional[int] = None,
    total_strategies: Optional[int] = None,
    error_message: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    fields: Dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if summary_json is not None:
        fields["summary_json"] = summary_json
    if total_results is not None:
        fields["total_results"] = total_results
    if total_strategies is not None:
        fields["total_strategies"] = total_strategies
    if error_message is not None:
        fields["error_message"] = error_message
    if completed_at is not None:
        fields["completed_at"] = completed_at
    if not fields:
        return
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            update(VerifySession)
            .where(VerifySession.session_id == session_id)
            .values(**fields)
        )


def list_verify_sessions(
    db_path: Path,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(VerifySession)
            .order_by(VerifySession.created_at.desc())
            .limit(limit)
        ).mappings().all()
    return [dict(r) for r in rows]
