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
from api.routes.research.common import research_error_to_http
from api.schemas import AnalysisRunRequest
from core.analysis.executor import get_executor
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES = ("condition_stats", "distribution_compare", "correlation_scan",
              "event_study", "pattern_mining", "screen_patterns", "param_sweep",
              "regime_split")
# 数据行数超过此值或模板为 pattern_mining/screen_patterns → 重组路径
# （screen 恒 heavy：枚举空间 × 持有期的全谱计算天然超过快路径阈值）
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
        event_study, param_sweep, regime_split, screen_patterns,
        similar_at_time,
    )
    from core.analysis.templates.screen_patterns import resolve_feature_names
    from core.analysis.warmup_loader import load_with_warmup

    rng = payload.data_range.model_dump()
    p = dict(payload.params)
    ranges = [rng]
    # 0.2.14：路由统一构建多周期求值上下文（研究链 base_tf=请求
    # data_range.timeframe；五模板统一透传——screen 候选恒基准周期
    # 形制、ctx 自然不被消费，统一形制降低分支遗漏）
    from core.analysis.condition_kernel import MtfEvalContext
    mtf_ctx = MtfEvalContext(
        data_dir=data_dir, symbol=rng["symbol"], base_tf=rng["timeframe"])

    if payload.template == "screen_patterns":
        # 全谱扫描：warmup 覆盖全部枚举指标（derive_warmup 取最大周期×3），
        # 统计段切回请求区间；features 校验前置（未知指标在此结构化拒绝，
        # 不让装载层 KeyError 裸穿）
        feature_names = resolve_feature_names(p.get("features", "all"))
        df, request_start = load_with_warmup(
            data_dir, rng["symbol"], rng["timeframe"],
            rng["start"], rng["end"], feature_names)
        mtf_ctx.eval_start = request_start
        return screen_patterns(
            df, data_ranges=ranges, timeframe=rng["timeframe"],
            features=p.get("features", "all"),
            horizons=p.get("horizons"),
            top_n=p.get("top_n", 20),
            min_samples=p.get("min_samples", 30),
            request_start=request_start,
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx).to_dict()

    if payload.template == "param_sweep":
        # 参数稳健性检查（add-param-sweep）：warmup 前扩装载同 condition_stats
        # U3 形制（指标矩阵默认参数集）；indicator+condition 双件（指标名供
        # warmup 与矩阵，列名 subject 供求值——两层名字不同）。indicator
        # 前置校验（未知指标不让装载层 KeyError 裸穿，screen 同款纪律）
        from core.features.registry import INDICATOR_REGISTRY
        if p.get("indicator") not in INDICATOR_REGISTRY:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"indicator 未知: {p.get('indicator')!r}",
                detail={"param": "indicator",
                        "legal_range": "注册表指标名（list_indicators 查询）"})
        s = p.get("sweep", {})
        df, request_start = load_with_warmup(
            data_dir, rng["symbol"], rng["timeframe"],
            rng["start"], rng["end"], [p["indicator"]])
        mtf_ctx.eval_start = request_start
        return param_sweep(
            df, indicator=p["indicator"], condition=p["condition"],
            sweep={"axis": s.get("axis", "target"),
                   "values": s.get("values", "auto")},
            forward_bars=p.get("forward_bars", 24),
            data_ranges=ranges, timeframe=rng["timeframe"],
            request_start=request_start,
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx).to_dict()

    if payload.template == "regime_split":
        # 行情分段（add-regime-split）：warmup 覆盖判读指标（ADX/ATR）；
        # condition 存在时其归属指标并入 warmup 清单（矩阵模板内计算）
        from core.features.registry import INDICATOR_REGISTRY
        warm_names = ["ADX", "ATR"]
        if p.get("indicator"):
            if p["indicator"] not in INDICATOR_REGISTRY:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"indicator 未知: {p['indicator']!r}",
                    detail={"param": "indicator",
                            "legal_range": "注册表指标名（list_indicators 查询）"})
            warm_names.append(p["indicator"])
        df, request_start = load_with_warmup(
            data_dir, rng["symbol"], rng["timeframe"],
            rng["start"], rng["end"], warm_names)
        mtf_ctx.eval_start = request_start
        return regime_split(
            df, segmentation=p.get("segmentation", "auto"),
            indicator=p.get("indicator"), condition=p.get("condition"),
            forward_bars=p.get("forward_bars", 24),
            data_ranges=ranges, timeframe=rng["timeframe"],
            request_start=request_start,
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx).to_dict()

    if payload.template == "condition_stats":
        # U3：condition_stats 走 warmup 前扩装载——指标 warmup 段「只用
        # 不算」（request_start 切回请求区间）；前扩落点在本分支，heavy
        # 判定装载（run_analysis 入口）不扩（design Decision 3）。
        # forward_bars/min_samples 自由 Dict 可选透传（默认 24/30）。
        # load_with_warmup 抛的 ResearchError（WARMUP/PARAM_INVALID 等）在
        # 快路径经 except ResearchError 冒泡转 422；重组路径（线程池）落
        # failed 终态，reason 前缀 [code]（executor 结构化收尾）。
        df, request_start = load_with_warmup(
            data_dir, rng["symbol"], rng["timeframe"],
            rng["start"], rng["end"], [p["indicator"]])
        mtf_ctx.eval_start = request_start
        return condition_stats(df, indicator=p["indicator"], params=p["params"],
                               condition=p["condition"], data_ranges=ranges,
                               request_start=request_start,
                               forward_bars=p.get("forward_bars", 24),
                               min_samples=p.get("min_samples", 30),
                               hypothesis_id=payload.hypothesis_id,
                               mtf=mtf_ctx).to_dict()

    df = _load_df(data_dir, rng)
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
                           hypothesis_id=payload.hypothesis_id,
                           mtf=mtf_ctx).to_dict()
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


def _log_screen(
    db_path: Path, payload: AnalysisRunRequest,
    task_id: str, result: Dict[str, Any],
) -> None:
    """扫描留痕（add-screen-patterns D3）：计算完成即写，与任务终态解耦。

    entry_type="screen" 不挂假设（不进任何假设判定并集——隔离走
    screen_use 回链与反向核查两条路）；summary 携带 top 候选指纹清单
    与族口径（反向核查比对物 + 事后审计依据）。screen 模板不另写
    analysis 日志（本条已含区间登记语义，双写为重复记录）。
    """
    p = (result or {}).get("payload", {})
    rr.append_research_log(
        db_path,
        hypothesis_id=None,
        entry_type="screen",
        ref_id=task_id,
        data_ranges=json.dumps(
            [payload.data_range.model_dump()], ensure_ascii=False),
        summary=json.dumps({
            "scanned": p.get("scanned"),
            "family": p.get("family"),
            "timeframe": p.get("timeframe"),
            "top_fingerprints": [c.get("fingerprint")
                                 for c in p.get("candidates", [])],
        }, ensure_ascii=False, default=str),
    )


def _log_sweep(
    db_path: Path, payload: AnalysisRunRequest,
    result: Dict[str, Any],
) -> None:
    """稳健性检查留痕（add-param-sweep）：同构 _log_screen 先例。

    entry_type="sweep" 不挂假设（不进判定并集、不触发反向核查——检查
    不消耗验证数据区间，第 3 步与第 5 步职责边界）；ref_id=中心条件指纹
    （与 0.2.5 扫描候选可对链的事后审计依据）。param_sweep 模板不另写
    analysis 日志（本条已含区间登记语义，双写为重复记录）。
    """
    p = (result or {}).get("payload", {})
    rr.append_research_log(
        db_path,
        hypothesis_id=None,
        entry_type="sweep",
        ref_id=(p.get("condition", {}) or {}).get("fingerprint"),
        data_ranges=json.dumps(
            [payload.data_range.model_dump()], ensure_ascii=False),
        summary=json.dumps({
            "verdict": p.get("verdict"),
            "robust_range": p.get("robust_range"),
            "family": (p.get("method", {}) or {}).get("family_size"),
            "grid_size": len((p.get("sweep", {}) or {}).get("values", [])),
        }, ensure_ascii=False, default=str),
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
        heavy = (payload.template in ("pattern_mining", "screen_patterns")
                 or len(df) > _HEAVY_ROWS)

        if payload.template == "screen_patterns":
            # 专属提交路径：task_id 预生成（留痕 ref_id 供闭包引用）；
            # 计算完成后写 screen 留痕（在 stop_check 覆盖范围内——
            # 超时中断不产生留痕，计算完成的窄竞态窗口宁多留不漏拦）
            import uuid as _uuid

            screen_task_id = f"ant_{_uuid.uuid4().hex[:12]}"

            def _screen_job(stop_check):
                out = _run_template(payload, data_dir, db_path)
                _log_screen(db_path, payload, screen_task_id, out)
                return json.dumps(out, ensure_ascii=False, default=str)

            ex = get_executor()
            handle = ex.submit(
                f"analysis:{payload.template}", _screen_job,
                task_id=screen_task_id,
                timeout_s=600.0,
                params_json=json.dumps(
                    {"template": payload.template,
                     "data_range": payload.data_range.model_dump()},
                    ensure_ascii=False),
                estimated_cost_s=_HEAVY_ROWS / 1000.0,
            )
            if handle["fast_path"]:  # 阈值边缘竞态：结果即终态
                return handle
            return JSONResponse(status_code=202, content={
                "task_id": handle["task_id"], "status": "pending",
                "poll": f"/api/research/analysis/tasks/{handle['task_id']}"})

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
            if payload.template == "param_sweep":
                _log_sweep(db_path, payload, result)
            else:
                _log_analysis(db_path, payload)
            return result

        # 重组路径：202 + 句柄（线程池执行器；完成回调内落日志）
        def _job(stop_check):
            out = _run_template(payload, data_dir, db_path)
            if payload.template == "param_sweep":
                _log_sweep(db_path, payload, out)
            else:
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
