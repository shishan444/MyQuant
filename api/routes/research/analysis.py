"""分析模板统一入口与长任务轮询（add-ai-research-mcp 3.x）。

五类模板统一入口：轻量同步返回终态；重组（pattern_mining 的引擎
fit 或大数据行数）经线程池执行器走 202+句柄（D3/D11）。
分析执行的区间强制自动落研究日志（漏报拦截的数据源）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.deps import get_data_dir, get_db_path
from api.schemas import AnalysisRunRequest
from core.analysis.executor import get_executor
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES = ("condition_stats", "distribution_compare", "correlation_scan",
              "event_study", "pattern_mining")
# 数据行数超过此值或模板为 pattern_mining → 重组路径
_HEAVY_ROWS = 10000


def _load_df(data_dir: Path, r: Dict[str, str]):
    from core.data.mtf_loader import load_and_prepare_df

    df = load_and_prepare_df(
        data_dir, r["symbol"], r["timeframe"],
        data_start=r["start"], data_end=r["end"],
        compute_indicators=False,  # 模板按需算指标（condition_stats 内部）
    )
    if df is None:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"区间数据不足: {r['symbol']} {r['timeframe']} [{r['start']}, {r['end']})")
    return df


def _run_template(payload: AnalysisRunRequest, data_dir: Path, db_path: Path) -> Dict[str, Any]:
    """执行一次模板（快路径同步；重路径经线程池）。"""
    from core.analysis.templates import (
        condition_stats, correlation_scan, distribution_compare,
        event_study, similar_at_time,
    )

    rng = payload.data_range.model_dump()
    df = _load_df(data_dir, rng)
    p = dict(payload.params)
    ranges = [rng]

    if payload.template == "condition_stats":
        return condition_stats(df, indicator=p["indicator"], params=p["params"],
                               condition=p["condition"], data_ranges=ranges,
                               hypothesis_id=payload.hypothesis_id).to_dict()
    if payload.template == "distribution_compare":
        return distribution_compare(df, column=p["column"], transform=p.get("transform", "returns"),
                                     group_a=p["group_a"], group_b=p["group_b"],
                                     data_ranges=ranges, hypothesis_id=payload.hypothesis_id).to_dict()
    if payload.template == "correlation_scan":
        return correlation_scan(df, signal_column=p["signal_column"],
                                horizon=p.get("horizon", 24), max_lag=p.get("max_lag", 12),
                                data_ranges=ranges, hypothesis_id=payload.hypothesis_id).to_dict()
    if payload.template == "event_study":
        return event_study(df, condition=p["condition"], window=p.get("window", 12),
                           data_ranges=ranges, min_events=p.get("min_events", 30),
                           hypothesis_id=payload.hypothesis_id).to_dict()
    if payload.template == "pattern_mining":
        return similar_at_time(df, as_of=p["as_of"], n_neighbors=p.get("n_neighbors", 50),
                               horizon=p.get("horizon", 12), data_ranges=ranges,
                               hypothesis_id=payload.hypothesis_id).to_dict()
    raise ResearchError("RESEARCH_TEMPLATE_UNKNOWN", f"未知模板: {payload.template}")


def _log_analysis(db_path: Path, payload: AnalysisRunRequest) -> None:
    """分析执行强制自动落日志（区间不可伪造——漏报拦截数据源）。"""
    rr.append_research_log(
        db_path,
        hypothesis_id=payload.hypothesis_id,
        entry_type="analysis",
        ref_id=payload.template,
        data_ranges=json.dumps([payload.data_range.model_dump()], ensure_ascii=False),
        summary=f"模板 {payload.template} 执行",
    )


@router.post("/analysis")
def run_analysis(
    payload: AnalysisRunRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
):
    if payload.template not in _TEMPLATES:
        raise HTTPException(status_code=422, detail={
            "code": "RESEARCH_TEMPLATE_UNKNOWN",
            "message": f"未知模板: {payload.template}（可用: {_TEMPLATES}）",
            "detail": None})
    try:
        df = _load_df(data_dir, payload.data_range.model_dump())
        heavy = payload.template == "pattern_mining" or len(df) > _HEAVY_ROWS

        if not heavy:
            # 快路径失败语义与 executor 快路径对齐：模板异常转 failed 终态
            # （reason 携带），不裸穿 500——失败必须可见且可审计
            try:
                result = _run_template(payload, data_dir, db_path)
            except ResearchError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 模板执行失败
                logger.warning("分析快路径失败 %s: %s", payload.template, exc)
                return {"task_id": f"fast_{payload.template}", "status": "failed",
                        "reason": str(exc)[:300], "result": None, "fast_path": True}
            _log_analysis(db_path, payload)
            return result

        # 重组路径：202 + 句柄（线程池执行器；完成回调内落日志）
        def _job(stop_check):
            out = _run_template(payload, data_dir, db_path)
            _log_analysis(db_path, payload)
            return json.dumps(out, ensure_ascii=False, default=str)

        ex = get_executor()
        handle = ex.submit(
            f"analysis:{payload.template}", _job,
            timeout_s=600.0,
            params_json=json.dumps({"template": payload.template,
                                    "data_range": payload.data_range.model_dump()},
                                   ensure_ascii=False),
            estimated_cost_s=_HEAVY_ROWS / 1000.0,  # 强制 > 快路径阈值
        )
        if handle["fast_path"]:
            # 竞态：阈值边缘被快路径吃掉——结果即终态
            return handle
        return JSONResponse(status_code=202, content={
            "task_id": handle["task_id"], "status": "pending",
            "poll": f"/api/research/analysis/tasks/{handle['task_id']}"})
    except ResearchError as exc:
        from api.routes.research.hypotheses import research_error_to_http
        raise research_error_to_http(exc) from exc


@router.get("/analysis/tasks/{task_id}")
def get_analysis_task(
    task_id: str,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """长任务轮询：status ∈ pending|running|succeeded|failed。"""
    t = rr.get_analysis_task(db_path, task_id)
    if t is None:
        raise HTTPException(status_code=404, detail={
            "code": "RESEARCH_TASK_NOT_FOUND",
            "message": f"任务不存在: {task_id}", "detail": None})
    out = dict(t)
    if out.get("result_ref"):
        try:
            out["result"] = json.loads(out["result_ref"])
        except (json.JSONDecodeError, TypeError):
            pass
    return out
