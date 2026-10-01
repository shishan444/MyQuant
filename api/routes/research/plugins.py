"""插件注册与审计端点（add-ai-research-mcp 5.2/5.4/5.5）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.deps import get_data_dir, get_db_path
from core.analysis import plugin_service as ps
from core.analysis.hypothesis_service import ResearchError

logger = logging.getLogger(__name__)
router = APIRouter()

from api.routes.research.common import research_error_to_http


class FormulaPluginCreate(BaseModel):
    name: str
    expression: str
    output_column: Optional[str] = None


class ModelPluginCreate(BaseModel):
    name: str
    features: list
    template: str = "rolling_logistic"
    params: Dict[str, Any] = {"window": 0, "step": 20}
    target: Dict[str, Any] = {"type": "return_sign", "horizon": 4}
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    data_start: str
    data_end: str


@router.post("/plugins/formula")
def create_formula_plugin(
    payload: FormulaPluginCreate,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """公式插件注册：静态校验三关 → 版本化 → signal_registry（原子）。"""
    try:
        return ps.register_formula_plugin(
            db_path, name=payload.name, expression=payload.expression,
            output_column=payload.output_column)
    except ResearchError as exc:
        # 校验失败同时留 rejected 记录（审计可查，不静默丢失）
        try:
            ps.rejected_formula_record(
                db_path, name=payload.name, expression=payload.expression,
                reason=str(exc.message)[:200])
        except Exception:  # noqa: BLE001 —— 留档失败不影响拒绝语义
            logger.warning("rejected 留档失败", exc_info=True)
        raise research_error_to_http(exc) from exc


@router.post("/plugins/model")
def create_model_plugin(
    payload: ModelPluginCreate,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
):
    """模型插件配置：同步校验 + 异步训练（202 + 句柄轮询）。"""
    try:
        out = ps.register_model_plugin(
            db_path, data_dir,
            name=payload.name, features=payload.features,
            template=payload.template, params=payload.params,
            target=payload.target, symbol=payload.symbol,
            timeframe=payload.timeframe,
            data_start=payload.data_start, data_end=payload.data_end)
        if out.get("status") == "pending":
            return JSONResponse(status_code=202, content=out)
        return out
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc


def _list_plugins(db_path: Path, table: str, name: Optional[str]):
    from sqlalchemy import select
    from core.persistence.orm.models import FormulaPlugin, ModelPlugin
    from core.persistence.repository.dataset_meta import _engine_for

    model = FormulaPlugin if table == "formula" else ModelPlugin
    stmt = select(model).order_by(model.name, model.version)
    if name:
        stmt = stmt.where(model.name == name)
    with _engine_for(db_path).connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt).mappings()]
    return rows


@router.get("/plugins/formula")
def list_formula_plugins(
    name: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """公式插件版本审计（历史版本可查，5.5）。"""
    rows = _list_plugins(db_path, "formula", name)
    for r in rows:
        r["output_columns"] = json.loads(r["output_columns"]) if r["output_columns"] else []
    return {"items": rows, "total": len(rows)}


@router.get("/plugins/model")
def list_model_plugins(
    name: Optional[str] = None,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """模型插件版本审计（含 rejected 记录与原因，卡5）。"""
    rows = _list_plugins(db_path, "model", name)
    for r in rows:
        if r.get("oos_summary"):
            try:
                r["oos_summary"] = json.loads(r["oos_summary"])
            except (json.JSONDecodeError, TypeError):
                pass
    return {"items": rows, "total": len(rows)}
