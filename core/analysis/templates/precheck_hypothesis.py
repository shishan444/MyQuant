"""假设预检器（add-hypothesis-precheck 0.3.4：G3 组合复核 + G11 频率
外推 + G5 事前预算）。

三层预检单入口三态结论（阈值 ``_MIN_TRADES_WARN=5`` 模块级内建不可
配——判定标尺与 param_sweep 三道门同构，不接受调用方配置）：

1. 组合统计：when 条件全集 AND 交集逐 bar 求值（condition_kernel.
   ``_mask_for`` 逐条件 → ``&`` 交集；高周期条件经 MtfEvalContext
   掩码投影零改）——触发数/触发率/前向收益分布/基准对照（全统计段
   无条件前向收益）。口径一致性由 test_cross_engine_parity 互证锚
   守护（语义一致而非实现同源，design D1）。
2. 频率外推：触发密度（触发数/统计段 bar 数）线性外推拟验证区间
   预估笔数。口径差披露（specs R2 MUST）：预估为触发密度外推上界
   （basis: trigger_density_upper_bound）——验证引擎空仓进场抑制未
   计入，预估单向偏大于实际成交笔数（方向保守：偏多警示而非漏报
   空转）。
3. 预算对照：两维 warmup（``derive_warmup_full``——与隔离回测
   ``_derive_dna_warmup`` 薄壳同源同值）对照数据目录实际边界，报
   分析段/拟验证段余量（index-only 探测，负值照报不报错——事前
   预算检查的价值恰在提前暴露不足）。

三态双锚（design D6）：显式模板锚=拟验证区间预估笔数
``projected_trades``；登记挂接锚=分析段实际 AND 触发数
``analysis_segment_trades``（登记时点唯一真实的数据内证据，不做
虚构外推；data_ranges 多条目时锚=逐区间触发数最小值——最差锚，
与随机窗口抽查保守聚合同构）。两锚共用阈值与同一三态映射。
比较规则直接比较不取整：≥5 pass / >0 且 <5 warn / 触发数 0
reject_suggestion（4.9 笔落 warn——保守方向，宁可多警示不漏空转）。

三要素张力解法（design 详设）：sample_size=统计段 bar 数（可信度锚=
看了多少根 bar）；build_result(min_samples=0)——零触发从「被小样本
闸门拒绝」变「结论：别送考」。

消费者：路由 ``_run_template`` precheck 分支（显式模板路径，独立
装载）与 ``hypothesis_service.register_hypothesis`` 挂接（函数内延迟
import ``run_registration_precheck``——服务侧模块级引用经 templates
聚合包会成加载期环，design「五裁决连锁落地」节）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.analysis.condition_kernel import (
    MtfEvalContext,
    _mask_for,
    collect_condition_plugin_columns,
    collect_condition_refs,
    stat_window_mask,
)
from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.param_guard import _require_int_range
from core.analysis.registry_meta import WARMUP_RULE_TEXT, derive_warmup_full
from core.analysis.result import build_result
from core.analysis.warmup_loader import load_with_warmup, to_utc

# 三态警示阈值（笔数）：模块级内建，禁止进入任何参数面。
_MIN_TRADES_WARN = 5

# 外推口径声明（specs R2 MUST：口径差披露的机器可读键）。
_PROJECTION_BASIS = "trigger_density_upper_bound"
_BASIS_NOTE = (
    "预估=触发密度外推上界：验证引擎空仓进场抑制未计入，"
    "预估单向偏大于实际成交笔数（方向保守——偏多警示而非漏报空转）"
)


# ---------------------------------------------------------------- 校验

def _require_condition_structure(when_conditions: Any) -> None:
    """when 条件列表结构校验（登记同构：非空 + 逐条三键）。"""
    if not isinstance(when_conditions, list) or not when_conditions:
        raise ResearchError(
            "RESEARCH_CONDITION_INVALID",
            "when_conditions 须为非空列表",
            detail={"field": "when_conditions",
                    "legal_form": "[{subject, action, target}, ...]"})
    for i, c in enumerate(when_conditions):
        if not isinstance(c, dict) or not all(
                k in c for k in ("subject", "action", "target")):
            raise ResearchError(
                "RESEARCH_CONDITION_INVALID",
                f"when_conditions[{i}] 须含 subject/action/target",
                detail={"field": f"when_conditions[{i}]",
                        "legal_form": "{subject, action, target}"})


def _assert_plugin_columns_reachable(
    db_path: Path, plugin_columns: set,
) -> None:
    """插件列 registry 可达性校验（specs R3 MUST：不可达 422 结构化
    拒绝，禁止静默降维——plugin_columns_lookback 对不可达列静默贡献
    0，不校验即预算系统性低估）。"""
    if not plugin_columns:
        return
    from core.analysis.hypothesis_service import allowed_subject_columns

    cols, _ = allowed_subject_columns(db_path)
    missing = set(plugin_columns) - cols
    if missing:
        raise ResearchError(
            "RESEARCH_PLUGIN_COLUMN_NOT_FOUND",
            f"条件引用的插件列不可达: {sorted(missing)}",
            detail={"plugin_columns": sorted(missing),
                    "hint": "可用列经 GET /api/research/signals 查询"})


def _collect_precheck_refs(
    when_conditions: List[Dict[str, Any]],
) -> Tuple[List[str], set]:
    """条件全集引用收集：内置指标名（warmup 内置维度）+ 插件列集
    （warmup 插件维度与 df enrich 同源——同一集合喂两处，覆盖面=
    求值面，隔离回测「装载算指标/组装插件信号」同构）。"""
    indicator_names = sorted({
        ind for cond in when_conditions
        for ind, _ in collect_condition_refs(cond, strict=True)})
    return indicator_names, collect_condition_plugin_columns(when_conditions)


# ---------------------------------------------------------------- 装载

def _load_precheck_df(
    db_path: Path, data_dir: Path,
    symbol: str, timeframe: str, start: str, end: str,
    when_conditions: List[Dict[str, Any]],
) -> Tuple[pd.DataFrame, pd.Timestamp, int]:
    """预检装载（模板路径与挂接壳共用）。

    三件套同源：① warmup=``derive_warmup_full`` 两维值（与隔离回测
    同式）；② 装载前扩注入 ``min_warmup_bars=两维值``——
    ``load_with_warmup`` 内部仅内置维度，不注入则含插件列条件组前扩
    不足且同源断言测不出（断言两侧均计算值非装载值——测试盲区）；
    ③ 装载后 ``enrich_with_plugin_columns`` 附加插件列（不 enrich 则
    含插件列条件的 mask 求值列缺失即崩）。

    Returns:
        (df, start_ts, warmup_bars)——df 含 warmup 前缀与插件列。
    """
    from core.analysis.plugin_service import enrich_with_plugin_columns

    indicator_names, plugin_columns = _collect_precheck_refs(when_conditions)
    _assert_plugin_columns_reachable(db_path, plugin_columns)
    warmup_bars = derive_warmup_full(db_path, indicator_names, plugin_columns)
    df, start_ts = load_with_warmup(
        data_dir, symbol, timeframe, start, end,
        indicator_names, min_warmup_bars=warmup_bars)
    df = enrich_with_plugin_columns(db_path, df, plugin_columns)
    return df, start_ts, warmup_bars


def _probe_directory_ts(
    data_dir: Path, symbol: str, timeframe: str,
) -> Optional[pd.DataFrame]:
    """数据目录 timestamp 列 index-only 探测（0.3.3 C-β 先例——单列
    扫描，预算对照与可达性校验的轻量通道）。

    列名形制变体（index 名非 timestamp）回退全列装载取 index
    （_count_rows_index_only 同款回退——正确性优先于轻量；C-β 实踩
    过的变体，当时防御未随行到本探测点）。

    Returns:
        timestamp 单列 df（变体时为全列 df——消费方只用 .index）；
        目录无该品种/周期数据返回 None。
    """
    from core.data.mtf_loader import find_parquet, safe_symbol
    from core.data.storage import load_parquet

    path = find_parquet(data_dir, safe_symbol(symbol), timeframe)
    if path is None:
        return None
    try:
        return load_parquet(path, columns=["timestamp"])
    except (KeyError, ValueError):
        return load_parquet(path)


def _budget_probe(
    ts: pd.DataFrame, request_start: str, validate_start: str,
    warmup_bars: int,
) -> Dict[str, Any]:
    """预算边界对照（specs R3：分析段/拟验证段各自余量；负值照报
    不报错——事前暴露不足是预检价值本身）。"""
    analysis_prefix = int((ts.index < to_utc(pd.Timestamp(request_start))).sum())
    validate_prefix = int((ts.index < to_utc(pd.Timestamp(validate_start))).sum())
    return {
        "data_start_actual": ts.index[0].isoformat(),
        "analysis_prefix_bars": analysis_prefix,
        "analysis_margin": analysis_prefix - warmup_bars,
        "validate_prefix_bars": validate_prefix,
        "validate_margin": validate_prefix - warmup_bars,
    }


# ---------------------------------------------------------------- 三态

def _verdict_for(anchor_value: float) -> str:
    """三态映射（比较规则：直接比较不取整——4.9 笔落 warn）。"""
    if anchor_value <= 0:
        return "reject_suggestion"
    if anchor_value < _MIN_TRADES_WARN:
        return "warn"
    return "pass"


# ---------------------------------------------------------------- 纯计算

def _combo_mask(
    work: pd.DataFrame, when_conditions: List[Dict[str, Any]],
    mtf: Optional[MtfEvalContext] = None,
) -> pd.Series:
    """组合 AND 交集（design D1：逐条件 _mask_for 后 & 累积——掩码
    求值就地富集 work，指标现算/mtf 缓存列幂等）。"""
    combo = pd.Series(True, index=work.index)
    for cond in when_conditions:
        combo = combo & _mask_for(work, cond, mtf)
    return combo


def _returns_summary(s: pd.Series) -> Dict[str, Any]:
    """前向收益分布摘要（空样本 count=0——零触发是结论非缺陷）。"""
    s = s.dropna()
    if s.empty:
        return {"count": 0}
    return {
        "count": int(s.size),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "q25": float(s.quantile(0.25)),
        "q75": float(s.quantile(0.75)),
        "min": float(s.min()),
        "max": float(s.max()),
    }


def compute_precheck_report(
    work: pd.DataFrame,
    when_conditions: List[Dict[str, Any]],
    *,
    anchor: str,
    request_start: "str | pd.Timestamp | None" = None,
    validate_bars: Optional[int] = None,
    per_range: Optional[List[Dict[str, str]]] = None,
    forward_bars: int = 24,
    warmup_bars: int = 200,
    budget: Optional[Dict[str, Any]] = None,
    mtf: Optional[MtfEvalContext] = None,
    trigger_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """三层预检纯计算（design 详设：df + when 条件集 + 锚口径参数 →
    三层报告 dict）。

    Args:
        work: 装载产物（含 warmup 前缀与插件列；指标在含前缀全 df 上
            现算收敛，统计只算统计段——warmup 只用不算）。
        when_conditions: 条件全集（canonical 化前后形制均可）。
        anchor: "projected"（显式模板锚）| "analysis_segment"（登记
            挂接锚）——双锚共用阈值与映射（design D6）。
        request_start: 统计段起点（projected 锚；warmup 前缀切回）。
        validate_bars: 拟验证段 bar 数（projected 锚外推基数）。
        per_range: analysis_segment 锚的区间清单（半开 [start, end)；
            逐区间切统计段数触发，最小值进锚——最差锚）。
        forward_bars: 前向持有期（close.pct_change(N).shift(-N)，A1）。
        warmup_bars: 两维 warmup 值（调用方 derive_warmup_full 产物）。
        budget: 壳层预算探测四件套（data_start_actual/margins）。
        mtf: 高周期求值上下文（timeframe 键条件掩码投影）。

    Returns:
        {verdict, combination_stats, frequency_projection, budget_check}。
    """
    combo = _combo_mask(work, when_conditions, mtf)
    fwd = work["close"].pct_change(forward_bars).shift(-forward_bars)

    if anchor == "projected":
        stat = stat_window_mask(work, request_start)
        n_triggers = int((combo & stat).sum())
        stat_bars = int(stat.sum())
        density = (n_triggers / stat_bars) if stat_bars else 0.0
        projected_trades = density * (validate_bars or 0)
        anchor_value: float = projected_trades
        per_range_out = [{
            "start": (to_utc(pd.Timestamp(request_start)).isoformat()
                      if request_start is not None else None),
            "end": None,
            "n_triggers": n_triggers,
        }]
        anchor_payload: Dict[str, Any] = {
            "projected_trades": projected_trades,
            "validate_bars": validate_bars,
        }
    elif anchor == "analysis_segment":
        if not per_range:
            raise ValueError("analysis_segment 锚须提供 per_range（编程错误）")
        parts = []
        per_range_out = []
        for r in per_range:
            lo = to_utc(pd.Timestamp(r["start"]))
            hi = to_utc(pd.Timestamp(r["end"]))
            seg = pd.Series((work.index >= lo) & (work.index < hi),
                            index=work.index)
            parts.append(seg)
            per_range_out.append({
                "start": r["start"], "end": r["end"],
                "n_triggers": int((combo & seg).sum())})
        stat = parts[0].copy()
        for seg in parts[1:]:
            stat = stat | seg
        n_triggers = int((combo & stat).sum())
        stat_bars = int(stat.sum())
        density = (n_triggers / stat_bars) if stat_bars else 0.0
        anchor_value = float(min(x["n_triggers"] for x in per_range_out))
        anchor_payload = {
            "analysis_segment_trades": int(anchor_value),
        }
    else:
        raise ValueError(f"未知锚口径: {anchor!r}（编程错误）")

    return {
        "verdict": _verdict_for(anchor_value),
        "combination_stats": {
            "n_triggers": n_triggers,
            "trigger_rate": density,
            "stat_bars": stat_bars,
            "forward_bars": forward_bars,
            "forward_returns": _returns_summary(fwd[combo & stat]),
            "baseline": _returns_summary(fwd[stat]),
        },
        "frequency_projection": {
            "anchor": anchor,
            "basis": _PROJECTION_BASIS,
            "basis_note": _BASIS_NOTE,
            "threshold": _MIN_TRADES_WARN,
            "density": density,
            "stat_bars": stat_bars,
            "per_range": per_range_out,
            **anchor_payload,
        },
        "budget_check": {
            "warmup_bars": warmup_bars,
            "rule": WARMUP_RULE_TEXT,
            **(budget or {}),
        },
    }


# ---------------------------------------------------------------- 模板壳

def precheck_hypothesis(
    db_path: Path,
    data_dir: Path,
    rng: Dict[str, str],
    validate_range: Dict[str, str],
    when_conditions: List[Dict[str, Any]],
    *,
    forward_bars: int = 24,
    hypothesis_id: Optional[str] = None,
) -> Dict[str, Any]:
    """显式模板路径（run_analysis 第九模板）——AnalysisResult 形式 +
    顶层 verdict（design payload 终态 schema）。

    Raises:
        ResearchError: 422 族——条件结构/插件列不可达/validate_range
            异构或无数据/装载不足；内部失败由路由层收编
            RESEARCH_TEMPLATE_INTERNAL。
    """
    _require_condition_structure(when_conditions)

    # validate_range 结构（A2 冻结：预检必填拟验证区间）
    if not isinstance(validate_range, dict) or not all(
            k in validate_range for k in ("symbol", "timeframe", "start", "end")):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "validate_range 须含 symbol/timeframe/start/end",
            detail={"field": "validate_range",
                    "legal_form": "{symbol, timeframe, start, end}"})
    # 时间串可解析性前置（CARD-02）：validate_range 为自由 Dict 值，
    # 不经 DataRangeItem 的双层防护（pydantic 类型层 start: str +
    # field_validator）——此处须双层同构：① 非 str 直接拒（int 形经
    # epoch 纳秒解析成 1970 时间戳会静默 200 产出语义错误外推；
    # None 形 pd.Timestamp 返回 NaT 落下游被 SYMBOL_NOT_FOUND 歪拦）；
    # ② str 不可解析在目录探测后 pd.Timestamp 处裸穿 ValueError 落
    # 500。文案对齐 schemas._require_parseable_timestamp 先例；
    # start>=end 倒置全域一致不查（已裁定不修项）
    for _ts_key in ("start", "end"):
        _ts_val = validate_range[_ts_key]
        try:
            if not isinstance(_ts_val, str):
                raise TypeError(f"实得 {type(_ts_val).__name__}")
            pd.Timestamp(_ts_val)
        except (ValueError, TypeError) as exc:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"validate_range.{_ts_key} 时间参数须为可解析的 UTC "
                "ISO8601 串（如 2024-01-01 或 2024-01-01T00:00:00Z），"
                f"got {_ts_val!r}",
                detail={"field": f"validate_range.{_ts_key}",
                        "value": _ts_val}) from exc
    # 同 symbol 同 timeframe（审查裁决 3：外推量纲前提——异品种对照
    # 属 G13 截面工具域，Non-Goals 排除）
    if (validate_range["symbol"] != rng["symbol"]
            or validate_range["timeframe"] != rng["timeframe"]):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "validate_range 须与 data_range 同 symbol 同 timeframe"
            "（外推量纲前提：密度可比性）",
            detail={"field": "validate_range",
                    "data_range": {"symbol": rng["symbol"],
                                   "timeframe": rng["timeframe"]},
                    "validate_range": {
                        "symbol": validate_range["symbol"],
                        "timeframe": validate_range["timeframe"]}})

    # 目录探测（可达性 + 拟验证段 bar 数 + 预算边界）
    ts = _probe_directory_ts(data_dir, rng["symbol"], rng["timeframe"])
    if ts is None or len(ts) == 0:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"无数据: {rng['symbol']} {rng['timeframe']}",
            detail={"symbol": rng["symbol"], "timeframe": rng["timeframe"]})
    vs = to_utc(pd.Timestamp(validate_range["start"]))
    ve = to_utc(pd.Timestamp(validate_range["end"]))
    validate_bars = int(((ts.index >= vs) & (ts.index < ve)).sum())
    if validate_bars == 0:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"拟验证区间无数据: {rng['symbol']} {rng['timeframe']} "
            f"[{validate_range['start']}, {validate_range['end']})",
            detail={"symbol": rng["symbol"],
                    "timeframe": rng["timeframe"],
                    "validate_range": validate_range})

    # 装载（两维前扩注入 + 插件列 enrich）与预算探测
    df, request_start, warmup_bars = _load_precheck_df(
        db_path, data_dir, rng["symbol"], rng["timeframe"],
        rng["start"], rng["end"], when_conditions)
    budget = _budget_probe(ts, rng["start"], validate_range["start"],
                           warmup_bars)

    # 持有期校验（CARD-01 形制；TD-3.1 迁移 param_guard——上界基于全 df
    # 含 warmup 前缀，输出形制与旧内联逐字节一致）
    _require_int_range(forward_bars, name="forward_bars",
                       lo=1, hi=len(df) - 1)

    mtf = MtfEvalContext(data_dir=data_dir, symbol=rng["symbol"],
                         base_tf=rng["timeframe"], eval_start=request_start)

    payload = compute_precheck_report(
        df, when_conditions, anchor="projected",
        request_start=request_start, validate_bars=validate_bars,
        forward_bars=forward_bars, warmup_bars=warmup_bars,
        budget=budget, mtf=mtf)

    # 密度外推置信区间（Wilson——condition_stats 同源权威实现；
    # 零触发也给合理上界，正态近似在密度 0 退化）
    from core.scoring.statistics import wilson_interval

    stat_bars = payload["combination_stats"]["stat_bars"]
    lo, hi = wilson_interval(payload["combination_stats"]["n_triggers"],
                             stat_bars)
    result = build_result(
        "precheck_hypothesis",
        sample_size=stat_bars,   # 统计段 bar 数口径（三要素张力解法）
        confidence={
            "method": "trigger_density_projection",
            "basis": _PROJECTION_BASIS,
            "interval": [lo * validate_bars, hi * validate_bars],
        },
        data_ranges=[rng],
        payload=payload,
        min_samples=0,           # 零触发照常构造（结论：别送考）
        hypothesis_id=hypothesis_id,
    )
    out = result.to_dict()
    out["verdict"] = payload["verdict"]   # 顶层三态（design 终态 schema）
    return out


# ---------------------------------------------------------------- 挂接壳

def run_registration_precheck(
    db_path: Path,
    data_dir: Path,
    when_conditions: List[Dict[str, Any]],
    data_ranges: List[Dict[str, str]],
    trigger_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """登记自动挂接预检（design D6）——多区间最差锚，返回警告段。

    调用方（hypothesis_service，函数内延迟 import）负责 try/except
    降级 logger.warning：本函数任何异常（数据不足/内部错误）都使
    登记照常、返回体不携带警告键。when_conditions 为服务 canonical
    化产物（结构已过第 1 步校验）；data_ranges 已过同质校验（同
    symbol 同 timeframe——服务第 2 步）。trigger_policy 为归一形
    （validate_policy 产物；None=无策略——逐 bar 现状口径），登记
    挂接的传参通道（trigger-policy 卡2），事件口径消费见
    compute_precheck_report（任务 5.3）。
    """
    symbol = data_ranges[0]["symbol"]
    timeframe = data_ranges[0]["timeframe"]
    union_start = min(r["start"] for r in data_ranges)
    union_end = max(r["end"] for r in data_ranges)

    df, request_start, warmup_bars = _load_precheck_df(
        db_path, data_dir, symbol, timeframe,
        union_start, union_end, when_conditions)

    mtf = MtfEvalContext(
        data_dir=data_dir, symbol=symbol, base_tf=timeframe,
        eval_start=to_utc(pd.Timestamp(union_start)))

    payload = compute_precheck_report(
        df, when_conditions, anchor="analysis_segment",
        per_range=data_ranges, warmup_bars=warmup_bars, mtf=mtf,
        trigger_policy=trigger_policy)

    return {
        "verdict": payload["verdict"],
        "analysis_segment_trades":
            payload["frequency_projection"]["analysis_segment_trades"],
        "threshold": _MIN_TRADES_WARN,
        "per_range": payload["frequency_projection"]["per_range"],
        "warmup_bars": payload["budget_check"]["warmup_bars"],
        "basis": _PROJECTION_BASIS,
        "note": (
            "登记预检锚=分析段实际 AND 触发数（data_ranges 多条目时"
            "取逐区间最小值——最差锚）；显式外推（projected_trades）"
            "经 precheck_hypothesis 模板"),
    }
