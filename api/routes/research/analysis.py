"""分析模板统一入口与长任务轮询（add-ai-research-mcp 3.x）。

五类模板统一入口：轻量同步返回终态；重组（pattern_mining 的引擎
fit 或大数据行数）经线程池执行器走 202+句柄（D3/D11）。
分析执行的区间强制自动落研究日志（漏报拦截的数据源）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
import pandas as pd

from api.deps import get_data_dir, get_db_path
from api.routes.research.common import (
    require_known_timeframe,
    research_error_to_http,
)
from api.schemas import AnalysisRunRequest
from core.analysis.executor import get_executor
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()

_TEMPLATES = ("condition_stats", "distribution_compare", "correlation_scan",
              "event_study", "pattern_mining", "screen_patterns", "param_sweep",
              "regime_split", "precheck_hypothesis")
# 数据行数超过此值或模板为 pattern_mining/screen_patterns → 重组路径
# （screen 恒 heavy：枚举空间 × 持有期的全谱计算天然超过快路径阈值）
_HEAVY_ROWS = 10000


def _count_rows_index_only(data_dir: Path, r: Dict[str, str]) -> int:
    """0.3.3 C-β：index-only 轻量计数——只读 timestamp 列按区间过滤
    计行（不装 OHLCV 全列），heavy 判定从全量 IO 降为单列索引扫描。
    计数口径 = len(_load_df(...))（列同源等价，测试锚定）；timestamp
    列名缺失（数据集形制变体）时回退全量装载——正确性优先于轻量。
    """
    from core.analysis.warmup_loader import to_utc
    from core.data.mtf_loader import find_parquet, safe_symbol
    from core.data.storage import load_parquet

    try:
        path = find_parquet(data_dir, safe_symbol(r["symbol"]), r["timeframe"])
        if path is None:
            return 0
        ts = load_parquet(path, columns=["timestamp"])
        # tz 归一（to_utc 单一实现）：naive 按 UTC 解释（capability 公开
        # 承诺）；未归一时 naive 与 tz-aware 索引比较抛 TypeError 不在
        # 下方回退捕获列表，会裸穿 500（CARD-01）
        lo = to_utc(pd.Timestamp(r["start"]))
        hi = to_utc(pd.Timestamp(r["end"]))
        return int(((ts.index >= lo) & (ts.index <= hi)).sum())
    except (KeyError, ValueError):
        df = _load_df(data_dir, r)
        return int(len(df))


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


def _load_with_condition_refs(
    data_dir: Path, rng: Dict[str, str],
    indicator: str, condition: Dict[str, Any],
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """跨指标条件装载（卡11：param_sweep/condition_stats 卡3a 六行
    两分支收敛）——warmup 预算并入条件双侧引用（target 指标 lookback
    计入前扩；strict=True 不可解析 target 结构化拒绝）后前扩装载，
    统计段切回请求区间。

    strict=True 单点化于此：未来新模板分支经本函数装载即不可能
    遗漏「装载必须 strict」纪律。eval_start 接线留分支（接线样板
    非判定逻辑，收进去职责糊化）。
    """
    from core.analysis.condition_kernel import collect_condition_refs
    from core.analysis.warmup_loader import load_with_warmup

    warm_names = [indicator] + [
        ind for ind, _ in collect_condition_refs(condition, strict=True)]
    return load_with_warmup(
        data_dir, rng["symbol"], rng["timeframe"],
        rng["start"], rng["end"], warm_names)


def _require_known_indicator(name: str) -> None:
    """indicator 注册表值域前置校验（param_sweep/regime_split/
    condition_stats 共用）：未知即 422——不让装载层 KeyError 裸穿
    （screen 同款纪律）。"""
    from core.features.registry import INDICATOR_REGISTRY

    if name not in INDICATOR_REGISTRY:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"indicator 未知: {name!r}",
            detail={"param": "indicator",
                    "legal_range": "注册表指标名（list_indicators 查询）"})


# 模板必填键声明表（卡1/D5 统一口径：_TEMPLATES 8 模板中 6 模板共 11
# 必填键；screen_patterns/regime_split 无必填键零成本通过——模板签名
# 变更须同步本表）。消灭 _run_template 分支内裸下标族（G1/C-α）：
# 缺键结构化 422（detail 点名键+legal_form），不让 KeyError 裸穿。
_TEMPLATE_REQUIRED_KEYS: Dict[str, tuple] = {
    "condition_stats": ("indicator", "params", "condition"),
    "distribution_compare": ("column", "group_a", "group_b"),
    "correlation_scan": ("signal_column",),
    "event_study": ("condition",),
    "pattern_mining": ("as_of",),
    "param_sweep": ("indicator", "condition"),
    "precheck_hypothesis": ("when_conditions", "validate_range"),
}


def _require_required_keys(template: str, p: Dict[str, Any]) -> None:
    """模板必填键存在性前置校验（卡1）：缺键即结构化拒绝——只判
    键缺不缺，值合法性归既有值校验链（最小差异，D3）。"""
    for key in _TEMPLATE_REQUIRED_KEYS.get(template, ()):
        if key not in p:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"params 缺必填键: {key}",
                detail={
                    "param": key,
                    "legal_form": f"{template} 必填 "
                                  f"{_TEMPLATE_REQUIRED_KEYS[template]}"})


def _with_derivatives_coverage(result: Dict[str, Any],
                               df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """0.3.3 G12：衍生品边界声明随结果出站（df.attrs 通道——装载层
    合并衍生品列时写入；未合并则 attrs 无键、结果不挂——「涉及」的
    判定天然由装载层承载：合并即涉及）。三链共用装载点，trading 链
    对 attrs 新键零感知。"""
    cov = df.attrs.get("derivatives_coverage") if df is not None else None
    if cov is not None:
        result = dict(result)
        result["derivatives_coverage"] = cov
    return result


def _run_template(payload: AnalysisRunRequest, data_dir: Path, db_path: Path) -> Dict[str, Any]:
    """执行一次模板（快路径同步；重路径经线程池）。"""
    from core.analysis.templates import (
        condition_stats, correlation_scan, distribution_compare,
        event_study, param_sweep, precheck_hypothesis, regime_split,
        screen_patterns, similar_at_time,
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
        # 全谱扫描：warmup 覆盖全部枚举指标（derive_warmup 主公式推导：
        # 周期类参数上限之和 ×3、下限 200），统计段切回请求区间；
        # features 校验前置（未知指标在此结构化拒绝，不让装载层
        # KeyError 裸穿）
        feature_names = resolve_feature_names(p.get("features", "all"))
        df, request_start = load_with_warmup(
            data_dir, rng["symbol"], rng["timeframe"],
            rng["start"], rng["end"], feature_names)
        mtf_ctx.eval_start = request_start
        return _with_derivatives_coverage(screen_patterns(
            df, data_ranges=ranges, timeframe=rng["timeframe"],
            features=p.get("features", "all"),
            horizons=p.get("horizons"),
            top_n=p.get("top_n", 20),
            min_samples=p.get("min_samples", 30),
            request_start=request_start,
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx).to_dict(), df)

    if payload.template == "param_sweep":
        # 参数稳健性检查（add-param-sweep）：warmup 前扩装载同 condition_stats
        # U3 形制（指标矩阵默认参数集）；indicator+condition 双件（指标名供
        # warmup 与矩阵，列名 subject 供求值——两层名字不同）。indicator
        # 前置校验（未知指标不让装载层 KeyError 裸穿，screen 同款纪律）
        _require_known_indicator(p.get("indicator"))
        s = p.get("sweep", {})
        # 卡3a（卡11 收敛 _load_with_condition_refs）：warmup 预算并入
        # 条件双侧引用——strict=True 不可解析 target 在此结构化拒绝
        df, request_start = _load_with_condition_refs(
            data_dir, rng, p["indicator"], p["condition"])
        mtf_ctx.eval_start = request_start
        return _with_derivatives_coverage(param_sweep(
            df, indicator=p["indicator"], condition=p["condition"],
            sweep={"axis": s.get("axis", "target"),
                   "values": s.get("values", "auto")},
            forward_bars=p.get("forward_bars", 24),
            data_ranges=ranges, timeframe=rng["timeframe"],
            request_start=request_start,
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx).to_dict(), df)

    if payload.template == "regime_split":
        # 行情分段（add-regime-split）：warmup 覆盖判读指标（ADX/ATR）；
        # condition 存在时其归属指标并入 warmup 清单（矩阵模板内计算）
        warm_names = ["ADX", "ATR"]
        if p.get("indicator"):
            _require_known_indicator(p["indicator"])
            warm_names.append(p["indicator"])
        df, request_start = load_with_warmup(
            data_dir, rng["symbol"], rng["timeframe"],
            rng["start"], rng["end"], warm_names)
        mtf_ctx.eval_start = request_start
        return _with_derivatives_coverage(regime_split(
            df, segmentation=p.get("segmentation", "auto"),
            indicator=p.get("indicator"), condition=p.get("condition"),
            forward_bars=p.get("forward_bars", 24),
            data_ranges=ranges, timeframe=rng["timeframe"],
            request_start=request_start,
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx).to_dict(), df)

    if payload.template == "condition_stats":
        # U3：condition_stats 走 warmup 前扩装载——指标 warmup 段「只用
        # 不算」（request_start 切回请求区间）；前扩落点在本分支，heavy
        # 判定装载（run_analysis 入口）不扩（design Decision 3）。
        # forward_bars/min_samples 自由 Dict 可选透传（默认 24/30）。
        # load_with_warmup 抛的 ResearchError（WARMUP/PARAM_INVALID 等）在
        # 快路径经 except ResearchError 冒泡转 422；重组路径（线程池）落
        # failed 终态，reason 前缀 [code]（executor 结构化收尾）。
        # indicator 值域前置（param_sweep 同款纪律——特征网实施暴露的
        # 补接：非法值经 derive_warmup 抛裸 KeyError 落 500，非缺键类
        # 参数错误应 422）
        _require_known_indicator(p["indicator"])
        # 卡3a（卡11 收敛 _load_with_condition_refs）：warmup 预算并入
        # 条件双侧引用（金叉类跨指标条件 target lookback 计入前扩——
        # strict=True 不可解析 target 结构化拒绝）
        df, request_start = _load_with_condition_refs(
            data_dir, rng, p["indicator"], p["condition"])
        mtf_ctx.eval_start = request_start
        return _with_derivatives_coverage(condition_stats(
            df, indicator=p["indicator"], params=p["params"],
            condition=p["condition"], data_ranges=ranges,
            request_start=request_start,
            forward_bars=p.get("forward_bars", 24),
            min_samples=p.get("min_samples", 30),
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx,
            trigger_policy=p.get("trigger_policy")).to_dict(), df)

    if payload.template == "precheck_hypothesis":
        # 假设预检（add-hypothesis-precheck 2.1）：三层预检自装载（两维
        # 前扩注入+插件列 enrich 在模板壳内——与预算计算同源）；条件
        # 结构/validate_range 一致性与可达性校验前置在模板壳（422 结构
        # 化）。异步形制与七轻模板同款：不进 heavy 白名单，行数超
        # _HEAVY_ROWS 照走 202（审查裁决 1——预检主战场大区间常态超阈
        # 值，202 通道保护请求线程）；MtfEvalContext 由模板壳自建
        # （eval_start=装载 request_start）。
        return precheck_hypothesis(
            db_path, data_dir, rng, p["validate_range"], p["when_conditions"],
            forward_bars=p.get("forward_bars", 24),
            hypothesis_id=payload.hypothesis_id,
            trigger_policy=p.get("trigger_policy"))

    df = _load_df(data_dir, rng)
    if payload.template == "distribution_compare":
        return _with_derivatives_coverage(distribution_compare(
            df, column=p["column"], transform=p.get("transform", "returns"),
            group_a=p["group_a"], group_b=p["group_b"],
            data_ranges=ranges, hypothesis_id=payload.hypothesis_id).to_dict(), df)
    if payload.template == "correlation_scan":
        return _with_derivatives_coverage(correlation_scan(
            df, signal_column=p["signal_column"],
            horizon=p.get("horizon", 24), max_lag=p.get("max_lag", 12),
            data_ranges=ranges, hypothesis_id=payload.hypothesis_id).to_dict(), df)
    if payload.template == "event_study":
        return _with_derivatives_coverage(event_study(
            df, condition=p["condition"], window=p.get("window", 12),
            data_ranges=ranges, min_events=p.get("min_events", 30),
            hypothesis_id=payload.hypothesis_id,
            mtf=mtf_ctx,
            trigger_policy=p.get("trigger_policy")).to_dict(), df)
    if payload.template == "pattern_mining":
        return _with_derivatives_coverage(similar_at_time(
            df, as_of=p["as_of"], n_neighbors=p.get("n_neighbors", 50),
            horizon=p.get("horizon", 12), data_ranges=ranges,
            hypothesis_id=payload.hypothesis_id).to_dict(), df)
    raise ResearchError("RESEARCH_TEMPLATE_UNKNOWN", f"未知模板: {payload.template}")


def _log_analysis(
    db_path: Path, payload: AnalysisRunRequest,
    failure: Optional[str] = None,
) -> None:
    """分析执行强制自动落日志（区间不可伪造——漏报拦截数据源）。

    D15/D17：failure 传入时为失败留痕（summary 携失败码，与 logger+
    响应构成「失败可见可审计」完整落点——entry_type 保持 analysis，
    研究日志消费方零适配；该区间试过什么败在哪属统计信用必需信息）。
    留痕尽力而为（D15 裁决降级语义）：写入失败不反噬主通道——降级
    logger.warning（template+failure 码），结构化响应照常返回。
    """
    summary = (f"模板 {payload.template} 执行失败 [{failure}]"
               if failure else f"模板 {payload.template} 执行")
    try:
        rr.append_research_log(
            db_path,
            hypothesis_id=payload.hypothesis_id,
            entry_type="analysis",
            ref_id=payload.template,
            data_ranges=json.dumps(
                [payload.data_range.model_dump()], ensure_ascii=False),
            summary=summary,
        )
    except Exception as exc:  # noqa: BLE001 —— 留痕尽力而为，不反噬主通道
        logger.warning("分析留痕写入失败 template=%s failure=%s: %s",
                       payload.template, failure, exc)


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


def _log_precheck(
    db_path: Path, payload: AnalysisRunRequest,
    result: Dict[str, Any],
) -> None:
    """预检留痕（add-hypothesis-precheck R4，_log_sweep 同构先例）。

    entry_type="precheck" 不挂假设（预检时假设尚未诞生——结构性不进
    判定并集：预检过的区间仍可原样用于隔离/分段验证）；summary 携
    三态结论与判定锚数值（事后审计「预检结论 vs 实际考试结果」对照
    依据）。precheck 模板不另写 analysis 日志（本条已含区间登记语义）。
    """
    p = (result or {}).get("payload", {})
    fp = p.get("frequency_projection", {}) or {}
    rr.append_research_log(
        db_path,
        hypothesis_id=None,
        entry_type="precheck",
        ref_id="precheck_hypothesis",
        data_ranges=json.dumps(
            [payload.data_range.model_dump()], ensure_ascii=False),
        summary=json.dumps({
            "verdict": p.get("verdict"),
            "projected_trades": fp.get("projected_trades"),
            "analysis_segment_trades": fp.get("analysis_segment_trades"),
            "n_triggers": (p.get("combination_stats", {}) or {}).get("n_triggers"),
            "threshold": fp.get("threshold"),
        }, ensure_ascii=False, default=str),
    )


def _log_result(
    db_path: Path, payload: AnalysisRunRequest, result: Dict[str, Any],
) -> None:
    """分析执行留痕分派单点（TD-1 收敛——快路径与异步 _job 各自完成点一行调用）。

    新增模板在此单点接线（tests/README 守护行同步语义）；screen 专属
    路径（task_id 预生成闭包+「计算完成即写与任务终态解耦」）与 failure
    路径（_log_analysis(failure=...)）语义异构不并入。
    """
    if payload.template == "param_sweep":
        _log_sweep(db_path, payload, result)
    elif payload.template == "precheck_hypothesis":
        _log_precheck(db_path, payload, result)
    else:
        _log_analysis(db_path, payload)


def _submit_analysis(
    payload: AnalysisRunRequest, job: Callable, *,
    task_id: Optional[str] = None,
):
    """重组提交路径收敛（screen 专属/通用两段共用）：executor submit +
    fast_path 竞态处理 + 202 句柄。

    task_id 可注入（screen 专属语义：留痕 ref_id 需闭包引用，预生成
    后计算完成即写 screen 留痕）；缺省由 executor 生成。
    estimated_cost_s 强制 > 快路径阈值；fast_path（阈值边缘竞态被
    快路径吃掉）结果即终态直返。
    """
    ex = get_executor()
    submit_kwargs: Dict[str, Any] = dict(
        timeout_s=600.0,
        params_json=json.dumps(
            {"template": payload.template,
             "data_range": payload.data_range.model_dump()},
            ensure_ascii=False),
        estimated_cost_s=_HEAVY_ROWS / 1000.0,  # 强制 > 快路径阈值
    )
    if task_id is not None:
        submit_kwargs["task_id"] = task_id
    handle = ex.submit(f"analysis:{payload.template}", job, **submit_kwargs)
    if handle["fast_path"]:
        # 竞态：阈值边缘被快路径吃掉——结果即终态
        return handle
    return JSONResponse(status_code=202, content={
        "task_id": handle["task_id"], "status": "pending",
        "poll": f"/api/research/analysis/tasks/{handle['task_id']}"})


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
        # P0 错误通道：非法周期前置 422——否则装载层 validate_known_interval
        # 抛裸 ValueError 落 500
        require_known_timeframe(
            payload.data_range.timeframe, "data_range.timeframe")
        # 卡1 接线：必填键存在性前置（校验先于装载——省无谓 IO；
        # 缺键 422 含键名，消灭裸 KeyError→伪 task 家族）
        _require_required_keys(payload.template, payload.params)
        # 0.3.3 C-β：heavy 判定改 index-only 计数——原全量预载（装载
        # 后丢弃/与模板首装双倍 IO）退役；各分支模板保持各自首装
        # （每请求每分支恰好一次真实装载）
        rows = _count_rows_index_only(
            data_dir, payload.data_range.model_dump())
        if rows < 50:
            # min_bars 语义对齐（load_and_prepare_df 缺省 50——切片后
            # 不足即「数据不足」）：经 _load_df 复现原 DATA_INSUFFICIENT
            # 先抛通道，预载退役不改变小样本请求的拒绝码与错误序
            rows = len(_load_df(data_dir, payload.data_range.model_dump()))
        heavy = (payload.template in ("pattern_mining", "screen_patterns")
                 or rows > _HEAVY_ROWS)

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

            return _submit_analysis(
                payload, _screen_job, task_id=screen_task_id)

        if not heavy:
            # 卡4/D15：快路径失败结构化直返——ResearchError 冒泡 422
            # （既有通道）；其余异常转 RESEARCH_TEMPLATE_INTERNAL 500
            # （不穿伪 task_id 异步外衣）。失败可见可审计完整落点=
            # 响应+logger+研究日志（raise 前落库，留痕不被异常吞掉）
            try:
                result = _run_template(payload, data_dir, db_path)
            except ResearchError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 模板内部失败
                logger.warning("分析快路径失败 %s: %s", payload.template, exc)
                _log_analysis(db_path, payload,
                              failure="RESEARCH_TEMPLATE_INTERNAL")
                raise ResearchError(
                    "RESEARCH_TEMPLATE_INTERNAL",
                    f"模板执行失败: {type(exc).__name__}",
                    status=500,
                    detail={"template": payload.template,
                            "exception_type": type(exc).__name__,
                            "summary": str(exc)[:200]}) from exc
            _log_result(db_path, payload, result)
            return result

        # 重组路径：202 + 句柄（线程池执行器；完成回调内落日志）
        def _job(stop_check):
            out = _run_template(payload, data_dir, db_path)
            _log_result(db_path, payload, out)
            return json.dumps(out, ensure_ascii=False, default=str)

        return _submit_analysis(payload, _job)
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
