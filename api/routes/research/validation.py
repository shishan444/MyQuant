"""隔离回测 / 随机窗口 / 验证档案端点（add-ai-research-mcp 3.2/3.3/3.5）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_data_dir, get_db_path
from api.schemas import (
    IsolatedBacktestRequest,
    RandomWindowRequest,
    SandboxBacktestRequest,
    StagedValidationRequest,
)
from core.analysis import validation_service as vs
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

from api.routes.research.common import (
    require_known_timeframe,
    research_error_to_http,
)


@router.post("/validate-backtest")
def validate_backtest(
    payload: IsolatedBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """隔离回测：判定并集拒绝 + warmup 推导前扩 + 门槛机械判定。"""
    try:
        # P0 错误通道：非法周期前置 422——否则装载层裸 ValueError 落 500
        require_known_timeframe(
            payload.request_range.timeframe, "request_range.timeframe")
        return vs.run_isolated_backtest(
            db_path, data_dir,
            hypothesis_id=payload.hypothesis_id,
            strategy_id=payload.strategy_id,
            request_range=payload.request_range.model_dump(),
            threshold_profile=payload.threshold_profile,
        )
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc


@router.post("/staged-validation")
def staged_validation(
    payload: StagedValidationRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """分段验证（检查⑤分题型成绩单）：按市场状态切段逐段回测，
    段级门槛三态 + 全段汇总 verdict（挂假设时可被 transition 引用）；
    双模互斥（auto 机械判读 / explicit 段列表）；写入双路径（挂假设
    一体记录 / adhoc 纯日志）。"""
    try:
        require_known_timeframe(payload.timeframe, "timeframe")
        return vs.run_staged_validation(
            db_path, data_dir,
            strategy_id=payload.strategy_id,
            hypothesis_id=payload.hypothesis_id,
            symbol=payload.symbol, timeframe=payload.timeframe,
            start=payload.start, end=payload.end,
            segmentation=payload.segmentation,
            segments=payload.segments,
        )
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc


@router.post("/backtest-sandbox")
def backtest_sandbox(
    payload: SandboxBacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """沙盒回测：快速试错（可调 fee/slippage/init_cash），直返指标；
    不判门槛、不入验证记录；强制落研究日志（挂假设区间进判定并集）。"""
    try:
        require_known_timeframe(payload.timeframe, "timeframe")
        return vs.run_sandbox_backtest(
            db_path, data_dir,
            strategy_id=payload.strategy_id,
            symbol=payload.symbol, timeframe=payload.timeframe,
            start=payload.start, end=payload.end,
            fee=payload.fee, slippage=payload.slippage,
            init_cash=payload.init_cash,
            hypothesis_id=payload.hypothesis_id)
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
        require_known_timeframe(payload.timeframe, "timeframe")
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


@router.get("/union-balance")
def union_balance(
    symbol: str,
    timeframe: str,
    hypothesis_id: str,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """判定并集余量查询（0.4.1 R4）：已判区间/剩余可用区间/保守折算。

    三参数必填（账本为假设级）；只读零副作用（不落日志不写库——查询
    不是研究决策证据）；假设不存在 404 前置判定（不以空清单伪装成功）；
    数据范围按 parquet 首尾 bar 完整时间戳直读（半开 [首 bar, 尾
    bar+1×TF)）。与隔离验证的重叠拦截同一账本两出口——查询 remaining
    段发起隔离验证不被拒。
    """
    try:
        require_known_timeframe(timeframe, "timeframe")
        return vs.query_union_balance(
            db_path, data_dir,
            symbol=symbol, timeframe=timeframe, hypothesis_id=hypothesis_id)
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

    def _parse_params(r):
        # params 列兜底同 _parse 形制（损坏→{}，range 取值 None）
        try:
            return json.loads(r["params"]) if r["params"] else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    lines: Dict[str, Any] = {
        "strategy_id": strategy_id,
        "total_records": len(records),
        "isolated_backtests": [],
        "random_window_summaries": [],
    }
    # 0.2.11 三态人话映射：inconclusive ≠ 未通过（证据不足未定论——
    # 错义呈现会误导迁移决策）
    verdict_txt = {"pass": "通过", "fail": "未通过",
                   "inconclusive": "证据不足——样本不足未定论"}
    for r in isolated:
        res = _parse(r)
        m = res.get("metrics", {})
        # 基准对照句（improve-backtest-benchmark-disclosure 3.1）：
        # benchmark_return 存在 → 追加「同期买入持有 X%，超额 Y%」；
        # 旧记录无键/None → 标「无对照数据」（容缺不炸，判定门槛不读新键）
        bm = m.get("benchmark_return")
        if bm is not None:
            excess = m.get("excess_return")
            excess_txt = (f"，超额 {excess*100:+.1f}%"
                          if excess is not None else "")
            compare = f"，同期买入持有 {bm*100:.1f}%{excess_txt}"
        else:
            compare = "，无对照数据"
        lines["isolated_backtests"].append({
            "record_id": r["record_id"],
            "verdict": r["verdict"],
            "range": _parse_params(r).get("request_range"),
            "warmup_bars": res.get("warmup", {}).get("bars"),
            "人话": (
                f"区间总收益 {m.get('total_return', 0)*100:.1f}%，"
                f"最大回撤 {m.get('max_drawdown', 0)*100:.1f}%，"
                f"交易 {m.get('total_trades', 0)} 笔{compare}，"
                f"判定：{verdict_txt.get(r['verdict'], r['verdict'])}"
                f"（预热 {res.get('warmup', {}).get('bars', '?')} bar）"
            ),
        })
    for r in windows:
        res = _parse(r)
        s = res.get("summary", {})
        worst = s.get("worst_window", {})
        wm = worst.get("metrics", {})
        # worst_window 顺带对照（实现裁量）：2.1 起 metrics 自动携带对照键，
        # 抽查摘要与隔离行披露一致；旧记录无键/None 不追加任何文字
        wm_bm = wm.get("benchmark_return")
        wm_compare = (f"（同期买入持有 {wm_bm*100:.1f}%）"
                      if wm_bm is not None else "")
        lines["random_window_summaries"].append({
            "record_id": r["record_id"],
            "n_windows": s.get("n_windows"),
            "all_pass": s.get("all_pass"),
            "人话": (
                f"{s.get('n_windows', '?')} 个随机窗口全部达标："
                f"{'是' if s.get('all_pass') else '否'}；"
                f"最差窗口收益 {wm.get('total_return', 0)*100:.1f}%、"
                f"回撤 {wm.get('max_drawdown', 0)*100:.1f}%{wm_compare}，"
                f"总判定：{verdict_txt.get(r['verdict'], r['verdict'])}"
            ),
        })
    # 谱系段 lineage（0.4.2 R4，纯增量只读披露）：报告实体回链 parent 假设
    # → 同身份族（同 conditions_hash+ranges_hash 全部假设含终态变体）的
    # 全部翻译实体与各自记录归属（SQL 聚合不经截断）；进化/手工来源
    # parent_ids 非假设 → parent_hypothesis_id=null、entities 仅含自身
    # （容错不炸）；当前实体无记录的 404 语义已在上方保持
    lines["lineage"] = _build_lineage(db_path, strategy_id)
    return lines


def _build_lineage(db_path: Path, strategy_id: str) -> Dict[str, Any]:
    """谱系段组装（research-validation-protocol 规格「验证证据档案」lineage）。"""
    from core.persistence.repository.strategy import get_strategy

    current = get_strategy(db_path, strategy_id)
    if current is None:
        return {"parent_hypothesis_id": None,
                "entities": [{"strategy_id": strategy_id, "status": None,
                              "created_at": None, "records": {},
                              "is_current": True}]}

    parent_hypothesis_id: Optional[str] = None
    raw_parents = current.get("parent_ids")
    try:
        parents = json.loads(raw_parents) if raw_parents else []
        if not isinstance(parents, list):
            parents = [str(parents)]  # 单值 JSON 形兼容
    except (json.JSONDecodeError, TypeError):
        # 裸串形制（翻译器落库为单 id 串非 JSON 数组）——当单 id 处理
        parents = [raw_parents] if raw_parents else []
    if parents:
        candidate = str(parents[0])
        if rr.get_hypothesis(db_path, candidate) is not None:
            parent_hypothesis_id = candidate

    if parent_hypothesis_id is None:
        # 进化/手工来源（parent_ids 非假设）：entities 仅含自身（容错）
        counts = rr.count_validation_records_by_strategy(db_path, [strategy_id])
        return {"parent_hypothesis_id": None,
                "entities": [{"strategy_id": strategy_id,
                              "status": current.get("status"),
                              "created_at": current.get("created_at"),
                              "records": counts.get(strategy_id, {}),
                              "is_current": True}]}

    parent_hyp = rr.get_hypothesis(db_path, parent_hypothesis_id)
    family = rr.list_hypotheses_by_identity(
        db_path, parent_hyp["conditions_hash"], parent_hyp["ranges_hash"])
    family_ids = [h["hypothesis_id"] for h in family]
    family_strategies = rr.list_strategies_by_parent_hypotheses(
        db_path, family_ids)
    counts = rr.count_validation_records_by_strategy(
        db_path, [s["strategy_id"] for s in family_strategies])
    entities = [{
        "strategy_id": s["strategy_id"],
        "status": s.get("status"),
        "created_at": s.get("created_at"),
        "records": counts.get(s["strategy_id"], {}),
        "is_current": s["strategy_id"] == strategy_id,
    } for s in family_strategies]  # created_at 升序（repository 排序）
    return {"parent_hypothesis_id": parent_hypothesis_id,
            "entities": entities}


class TranslateRequest(BaseModel):
    strategy_symbol: str = "BTCUSDT"
    strategy_timeframe: str = "4h"


@router.post("/translate/{hypothesis_id}")
def translate_hypothesis(
    hypothesis_id: str,
    payload: TranslateRequest | None = None,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """假设→策略转化器：conditions 为唯一翻译源；不可表达显式拒绝。"""
    from core.analysis.hypothesis_to_strategy import translate_hypothesis as _t
    from pydantic import BaseModel as _BM  # noqa: F401

    try:
        kw = payload.model_dump() if payload else {}
        return _t(db_path, hypothesis_id=hypothesis_id, **kw)
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc
