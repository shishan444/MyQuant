"""隔离回测 / 随机窗口 / 验证档案端点（add-ai-research-mcp 3.2/3.3/3.5）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_data_dir, get_db_path
from api.schemas import IsolatedBacktestRequest, RandomWindowRequest
from core.analysis import validation_service as vs
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

from api.routes.research.hypotheses import research_error_to_http


@router.post("/validate-backtest")
def validate_backtest(
    payload: IsolatedBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """隔离回测：判定并集拒绝 + warmup 推导前扩 + 门槛机械判定。"""
    try:
        return vs.run_isolated_backtest(
            db_path, data_dir,
            hypothesis_id=payload.hypothesis_id,
            strategy_id=payload.strategy_id,
            request_range=payload.request_range.model_dump(),
            threshold_profile=payload.threshold_profile,
        )
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc


@router.post("/random-window-test")
def random_window_test(
    payload: RandomWindowRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """随机窗口抽查（种子记录于验证记录，可复现）。"""
    try:
        return vs.random_window_test(
            db_path, data_dir,
            strategy_id=payload.strategy_id,
            symbol=payload.symbol, timeframe=payload.timeframe,
            data_start=payload.data_start, data_end=payload.data_end,
            window_bars=payload.window_bars, n_windows=payload.n_windows,
            seed=payload.seed, hypothesis_id=payload.hypothesis_id,
        )
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc


@router.get("/validation/{strategy_id}/report")
def validation_report(
    strategy_id: str,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """验证档案：人话关键数字摘要（普通人可读，最差情况与样本量）。"""
    records = rr.list_validation_records(
        db_path, strategy_id=strategy_id, limit=200)
    if not records:
        raise HTTPException(status_code=404, detail={
            "code": "RESEARCH_REPORT_NOT_FOUND",
            "message": f"该策略无验证记录: {strategy_id}", "detail": None})

    isolated = [r for r in records if r["level"] == "isolated_backtest"]
    windows = [r for r in records if r["level"] == "random_window"]

    def _parse(r):
        try:
            return json.loads(r["result"]) if r["result"] else {}
        except json.JSONDecodeError:
            return {}

    lines: Dict[str, Any] = {
        "strategy_id": strategy_id,
        "total_records": len(records),
        "isolated_backtests": [],
        "random_window_summaries": [],
    }
    for r in isolated:
        res = _parse(r)
        m = res.get("metrics", {})
        lines["isolated_backtests"].append({
            "record_id": r["record_id"],
            "verdict": r["verdict"],
            "range": json.loads(r["params"]).get("request_range") if r["params"] else None,
            "warmup_bars": res.get("warmup", {}).get("bars"),
            "人话": (
                f"区间总收益 {m.get('total_return', 0)*100:.1f}%，"
                f"最大回撤 {m.get('max_drawdown', 0)*100:.1f}%，"
                f"交易 {m.get('total_trades', 0)} 笔，"
                f"判定：{'通过' if r['verdict']=='pass' else '未通过'}"
                f"（预热 {res.get('warmup', {}).get('bars', '?')} bar）"
            ),
        })
    for r in windows:
        res = _parse(r)
        s = res.get("summary", {})
        worst = s.get("worst_window", {})
        wm = worst.get("metrics", {})
        lines["random_window_summaries"].append({
            "record_id": r["record_id"],
            "n_windows": s.get("n_windows"),
            "all_pass": s.get("all_pass"),
            "人话": (
                f"{s.get('n_windows', '?')} 个随机窗口全部达标："
                f"{'是' if s.get('all_pass') else '否'}；"
                f"最差窗口收益 {wm.get('total_return', 0)*100:.1f}%、"
                f"回撤 {wm.get('max_drawdown', 0)*100:.1f}%"
            ),
        })
    return lines
