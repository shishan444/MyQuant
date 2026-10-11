"""指标读数服务（add-query-indicator；MCP 0.2.9「能辨真假」检查④）。

六步研究流程「真假检查」链的最后一件：①指标清单（list_indicators
静态元数据）②信号列（list_signal_columns）③案例抽查（inspect_cases
原始 K 线检视）④指标读数（本通道）。「参数是策略的一部分」的读数
落地——同一指标不同参数是不同信号源，条件设计阶段（登记假设前）
对参数化指标的典型水平与分布的读数此前无处可得，只能借道统计判定
器间接试错。

通道格局（Q1 裁决）：POST 独立端点 + research 档，与 inspect_cases
同型（params 嵌套 dict 走 GET 需 JSON 编码传输——契约含糊，创建
inspect_cases 时已否决同型方案）；同步直返、零 DB 写入、零研究日志
留痕（Q2 裁决：读数是无研究决策证据的无副作用动作，与
market_snapshot/list_indicators 元数据查询档同级）。

四归约封闭枚举（spec 契约）：latest（求值域末端有效值 + timestamp）
/ quantiles（固定七点 p01/p05/p25/p50/p75/p95/p99，覆盖尾部，不开放
可配置分位点）/ regime_means（按 classify_series 权威判定分组——
四态：震荡/过渡/趋势/高波动覆盖态；组均值 + count，ADX/ATR 自身
warmup 的头部 NaN 态落组外如实缩小）/ trend_slope（值对 bar 序号
最小二乘斜率，原始量纲不归一化）。缺省 ["latest","quantiles"]。

装载与求值域（复用 0.2.8 契约）：start 显式走 load_with_warmup
（前缀不足报 RESEARCH_WARMUP_INSUFFICIENT；regime_means 场景
indicator_names 传 [目标, ADX, ATR]，warmup 经 derive_warmup 的
max 聚合自动覆盖最严要求）；start 缺省全量直装 + effective_start
披露。求值域 [effective_start, end]（end 缺省=数据末端，range 以
effective_end 披露）；warmup 段不入任何归约（分位数不被冷启动
失真段污染——inspect_cases 求值域上界契约的镜像下界）。

全 NaN 双层语义（review 修复 B 裁决 2026-09-27）：全部输出列皆无
有效值→RESEARCH_DATA_INSUFFICIENT；个别列无有效值（各输出列冷启动
长度不同——嵌套平滑层越深的列越晚产出首值）→该列各归约输出 null
（键保留，显式缺值语义）——列级显式判空置 None，不依赖
np.nanpercentile 对全 NaN 列返回 NaN 数组的行为（伴随 RuntimeWarning
且以数值形态污染输出）。

参数校验前置（D7）：内核取参 int(params["period"]) 缺键抛 KeyError、
多余键静默忽略——前置拦截键集合与值域（拼错参数名若被静默忽略，
调用方会得到「按部分参数计算」的假象成功）。固定校验顺序（先拒
先报）：在册→参数→reductions 枚举→品种数据→warmup。params 缺省
拼注册表 default（Q1 裁决 D8，echo 区披露实际生效值）。

体积自检（报表压缩契约）：序列不外发只发归约摘要，上限 2048 字节
（inspect_cases 的 24576 是案例窗口预算——两个产品级约束不合并）；
超限结构化拒绝不静默截断；自检定位=形制退化兜底，参数校验放行的
调用不会被体积自检拒掉（0.2.8 先例哲学）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import default_params_for, derive_warmup
from core.analysis.warmup_loader import TF_MINUTES, load_with_warmup, to_utc
from core.features.registry import INDICATOR_REGISTRY

# 归约封闭枚举（spec 契约；未知值结构化拒绝）
_REDUCTIONS: frozenset[str] = frozenset(
    {"latest", "quantiles", "regime_means", "trend_slope"})
_DEFAULT_REDUCTIONS: List[str] = ["latest", "quantiles"]
_QUANTILE_POINTS = (1, 5, 25, 50, 75, 95, 99)  # 固定七点（覆盖尾部）
_MAX_SERIALIZED_BYTES = 2048  # 归约摘要字节上限（形制退化兜底；单组档）
# 批量档上限（0.4.1 R1 / A3 假设值：8 组上界外推——实战频繁触限则
# 调档为契约参数修订而非结构变更）；组成员合法键（单组专属字段形制）
_MAX_BATCH_SERIALIZED_BYTES = 16384
_GROUP_KEYS = frozenset({"indicator", "params", "reductions", "start", "end"})
_MAX_GROUPS = 8

# regime 标注用辅助指标（classify_series 需要 df 上有 ADX/ATR 列与
# close；注册表 default 参数现算——键名 adx_14/atr_14 由内核拼接惯例
# 决定，实施锚定见 tests）
_REGIME_HELPERS = ("ADX", "ATR")


def _validate_indicator(indicator: str) -> None:
    """在册校验（校验链第 1 环；detail 附可用清单指引）。"""
    if indicator not in INDICATOR_REGISTRY:
        raise ResearchError(
            "RESEARCH_INDICATOR_UNKNOWN",
            f"未知指标: {indicator}",
            detail={"param": "indicator", "value": indicator,
                    "available_hint": "经 list_indicators 查询注册表"})


def validate_params(indicator: str, params: Dict[str, Any]) -> None:
    """参数前置校验（D7）：键集合 ⊆ 注册表键、类型可解析、值在 [min,max]。

    内核取参 int(params[...])——缺键抛 KeyError（500 化）、多余键静默
    忽略（假象成功）；本拦截让两类调用错误都成为结构化拒绝。读数通道
    与 condition_stats 模板共用（共享导出——参数空间的校验权威）。
    """
    defn = INDICATOR_REGISTRY[indicator]
    extra = set(params) - set(defn.params)
    if extra:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"参数名不在册: {sorted(extra)}（合法: {sorted(defn.params)}）",
            detail={"param": "params", "illegal_keys": sorted(extra),
                    "legal_keys": sorted(defn.params)})
    for key, value in params.items():
        p = defn.params[key]
        try:
            if p.type == "int":
                num: float = float(int(value)) if not isinstance(value, bool) \
                    else float("nan")
            else:
                num = float(value)
        except (TypeError, ValueError):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"参数 {key}={value!r} 不可解析为数值",
                detail={"param": f"params.{key}", "value": value,
                        "expected_type": p.type}) from None
        if not (p.min <= num <= p.max):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"参数 {key}={value} 超出范围（合法 {p.min}..{p.max}）",
                detail={"param": f"params.{key}", "value": value,
                        "legal_range": [p.min, p.max]})


def _validate_reductions(reductions: Optional[List[str]]) -> List[str]:
    """reductions 封闭枚举校验（缺省 ["latest","quantiles"]）。"""
    if reductions is None:
        return list(_DEFAULT_REDUCTIONS)
    unknown = [r for r in reductions if r not in _REDUCTIONS]
    if unknown:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"reductions 含未知值: {unknown}（合法 {sorted(_REDUCTIONS)}）",
            detail={"param": "reductions", "illegal_values": unknown,
                    "legal_values": sorted(_REDUCTIONS)})
    return list(reductions)


def _reduce_latest(valid: pd.Series) -> Dict[str, Any]:
    """末端有效值（求值域内最后一个非 NaN + 所属 timestamp）。"""
    return {"value": float(valid.iloc[-1]),
            "timestamp": valid.index[-1].isoformat()}


def _reduce_quantiles(valid: pd.Series) -> Dict[str, Any]:
    """固定七点分位（NaN 已由列级 dropna 排除——残缺点不污染分位）。"""
    qs = np.percentile(valid.values, _QUANTILE_POINTS)
    return {f"p{pt:02d}": float(v) for pt, v in zip(_QUANTILE_POINTS, qs)}


def _reduce_regime_means(
    work: pd.DataFrame, col: str, state: pd.Series,
) -> Dict[str, Any]:
    """按 classify_series 四态分组均值 + count（样本量强制披露）。"""
    out: Dict[str, Any] = {}
    for group in sorted(state.dropna().unique()):
        mask = state == group
        group_vals = work.loc[mask, col].dropna()
        out[str(group)] = {"mean": float(group_vals.mean()),
                           "count": int(len(group_vals))} \
            if len(group_vals) else None
    return out


def _reduce_trend_slope(valid: pd.Series) -> Dict[str, Any]:
    """值对 bar 序号最小二乘斜率（原始量纲：指标单位/bar，不归一化）。"""
    y = valid.values.astype(float)
    slope = float(np.polyfit(np.arange(len(y)), y, 1)[0])
    return {"slope_per_bar": slope, "n_used": int(len(y))}


_REDUCERS = {
    "latest": _reduce_latest,
    "quantiles": _reduce_quantiles,
    "trend_slope": _reduce_trend_slope,
}


def _query_single(
    data_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    indicator: str,
    params: Optional[Dict[str, Any]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    reductions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """单组读数计算体（校验链→装载双路径→现算→逐列归约；无体积自检）。

    0.4.1 R1 自 query_indicator 原体拆出（行为逐字保真）：单组形制的
    体积自检（2048 档）留在 query_indicator 公共壳，批量形制在
    query_indicator_groups 汇总后走批量档（16384）——两档分属两形制。
    """
    # ---------------- 校验链（固定顺序：在册→参数→枚举→品种→warmup） ----------------
    _validate_indicator(indicator)
    if params is None:
        effective_params = default_params_for(indicator)
    else:
        validate_params(indicator, params)
        effective_params = dict(params)
    selected = _validate_reductions(reductions)

    indicator_names = [indicator]
    if "regime_means" in selected:
        indicator_names.extend(_REGIME_HELPERS)

    # ---------------- 装载双路径（复用 0.2.8 契约） ----------------
    start_ts: Optional[pd.Timestamp] = None
    if start is not None:
        df, start_ts = load_with_warmup(
            data_dir, symbol, timeframe,
            start=start, end=end, indicator_names=indicator_names)
    else:
        from core.data.mtf_loader import (
            find_parquet, load_and_prepare_df, safe_symbol)

        if find_parquet(data_dir, safe_symbol(symbol), timeframe) is None:
            raise ResearchError(
                "RESEARCH_SYMBOL_NOT_FOUND",
                f"无数据: {symbol} {timeframe}",
                detail={"symbol": symbol, "timeframe": timeframe})
        df = load_and_prepare_df(
            data_dir, symbol, timeframe,
            data_start=None, data_end=end, compute_indicators=False)
        if df is None:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"数据不可装载: {symbol} {timeframe}")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    # ---------------- 现算（目标指标 + regime 辅助列） ----------------
    from core.features.indicators import _compute_indicator

    work = df.copy()
    ind_cols = _compute_indicator(work, indicator, effective_params)
    for col, series in ind_cols.items():
        work[col] = series

    warmup_bars = derive_warmup(indicator_names)
    if start_ts is not None:
        effective_start_ts = start_ts
    else:
        if len(df) <= warmup_bars:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"数据集不足以形成指标有效域: 共 {len(df)} bar，"
                f"warmup 需 {warmup_bars} bar",
                detail={"bars": int(len(df)), "warmup_bars": warmup_bars})
        effective_start_ts = df.index[warmup_bars]

    # ---------------- 求值域 [effective_start, end 上界或数据末端] ----------------
    domain_mask = pd.Series(work.index >= effective_start_ts, index=work.index)
    effective_end_ts: Optional[pd.Timestamp] = None
    if end is not None:
        effective_end_ts = to_utc(pd.Timestamp(end))
        domain_mask = domain_mask & pd.Series(
            work.index <= effective_end_ts, index=work.index)
    domain = work.loc[domain_mask]
    if domain.empty:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"求值域为空: effective_start={effective_start_ts.isoformat()}",
            detail={"effective_start": effective_start_ts.isoformat(),
                    "effective_end": (effective_end_ts or work.index[-1]).isoformat()})
    effective_end_out = effective_end_ts or work.index[-1]

    # regime 标注（regime_means 场景；classify_series 吃拼好列的 df——
    # 头部 NaN 态（ADX/ATR 冷启动 + 滚动分位 min_periods）落组外）
    regime_state: Optional[pd.Series] = None
    if "regime_means" in selected:
        helper_cols: Dict[str, pd.Series] = {}
        for helper in _REGIME_HELPERS:
            for col, series in _compute_indicator(
                    work, helper, default_params_for(helper)).items():
                work[col] = series
                helper_cols[helper] = col
        from core.analysis.regime_segments import classify_series
        regime_state = classify_series(
            work, adx_col=helper_cols["ADX"], atr_col=helper_cols["ATR"])

    # ---------------- 逐列归约（B 语义：个别列全 NaN→null 键保留） ----------------
    any_valid = False
    out_reductions: Dict[str, Dict[str, Any]] = {}
    for col in ind_cols:
        valid = domain[col].dropna()
        col_result: Dict[str, Any] = {}
        if valid.empty:
            # 个别列无有效值：各归约输出 null（键保留）——全部列皆空
            # 在循环后整体拒绝
            for red in selected:
                col_result[red] = None
        else:
            any_valid = True
            for red in selected:
                if red == "regime_means":
                    col_result[red] = _reduce_regime_means(
                        domain, col, regime_state)
                else:
                    col_result[red] = _REDUCERS[red](valid)
        out_reductions[col] = col_result
    if not any_valid:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"求值域内全部输出列皆无有效值: {sorted(ind_cols)}",
            detail={"columns": sorted(ind_cols),
                    "effective_start": effective_start_ts.isoformat()})

    result: Dict[str, Any] = {
        "indicator": indicator,
        "params": effective_params,
        "symbol": symbol,
        "timeframe": timeframe,
        "range": {
            "effective_start": effective_start_ts.isoformat(),
            "effective_end": effective_end_out.isoformat(),
            "bars": int(len(domain)),
        },
        "warmup_bars": int(warmup_bars),
        "reductions": out_reductions,
    }
    return result


def query_indicator(
    data_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    indicator: str,
    params: Optional[Dict[str, Any]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    reductions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """单指标任意参数现算读数（检查④；归约摘要直返，零写入零留痕）。

    单组公共壳：_query_single 计算体 + 单组档体积自检（2048——存量
    语义不变）。Args/Raises 语义见 _query_single 与模块 docstring。

    Raises:
        ResearchError: RESEARCH_INDICATOR_UNKNOWN（不在册）；
        RESEARCH_PARAM_INVALID（参数/枚举非法/体积超限）；
        RESEARCH_SYMBOL_NOT_FOUND（无数据）；
        RESEARCH_DATA_INSUFFICIENT（不可装载/有效域为空/全部输出列
        皆无有效值）；RESEARCH_WARMUP_INSUFFICIENT（显式 start 前缀
        不足，透传自 load_with_warmup）。
    """
    result = _query_single(
        data_dir, symbol=symbol, timeframe=timeframe, indicator=indicator,
        params=params, start=start, end=end, reductions=reductions)

    # ---------------- 体积自检（超限拒绝不静默截断；单组档 2048） ----------------
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload.encode("utf-8")) > _MAX_SERIALIZED_BYTES:
        first_col = next(iter(result["reductions"]))
        selected = list(result["reductions"][first_col])
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"归约摘要超体积上限: {len(payload.encode('utf-8'))} 字节"
            f"（上限 {_MAX_SERIALIZED_BYTES}——输出列数 {len(result['reductions'])}，"
            "可减少 reductions 项）",
            detail={"bytes": len(payload.encode("utf-8")),
                    "limit": _MAX_SERIALIZED_BYTES,
                    "tier": "single",
                    "columns": len(result["reductions"]),
                    "reductions": selected})
    return result


def query_indicator_groups(
    data_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    groups: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """批量指标读数（0.4.1 R1）——一次调用多组「指标 × 参数 × 归约」。

    校验确定序全序固定（错误码稳定性）：①groups 结构（逐组成员为
    dict、必含 indicator、成员键 ⊆ 单组专属键——未知键结构化拒绝，
    防静默部分计算假象）→②组数 1..8（空数组=无意义请求非合法零
    结果）→③全组逐组校验（沿单组链与序：在册→参数→reductions——
    先于任何装载 IO）→④逐组双路径装载现算（各组区间独立）→⑤请求
    序数组汇总→⑥载荷自检（批量档 16384）。

    整批原子含执行期：任一组失败（校验期或执行期装载失败
    SYMBOL_NOT_FOUND/WARMUP/DATA_INSUFFICIENT）整批结构化拒绝、无
    部分结果（纯只读可安全重试）；错误 message/detail 以 groups[N]
    指明组序。零写入零留痕同步直返（单组形制同族）。

    Returns:
        {"groups": [单组同形结果…]}（数组序=请求序）。
    """
    # ① groups 结构
    if not isinstance(groups, list):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID", "groups 须为数组",
            detail={"param": "groups",
                    "legal_form": "[{indicator, params?, reductions?, "
                                  "start?, end?}]"})
    for i, g in enumerate(groups):
        if (not isinstance(g, dict) or "indicator" not in g
                or not isinstance(g["indicator"], str)):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"groups[{i}] 结构非法（须为含 indicator 的 dict）",
                detail={"param": f"groups[{i}]",
                        "legal_form": "{indicator, params?, reductions?, "
                                      "start?, end?}"})
        extra = sorted(set(g) - _GROUP_KEYS)
        if extra:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"groups[{i}] 含未知键: {extra}",
                detail={"param": f"groups[{i}]", "illegal_keys": extra,
                        "legal_keys": sorted(_GROUP_KEYS)})
    # ② 组数 1..8
    if not 1 <= len(groups) <= _MAX_GROUPS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"groups 组数须在 1..{_MAX_GROUPS}（实际 {len(groups)}）",
            detail={"param": "groups", "legal_range": f"1..{_MAX_GROUPS}",
                    "actual": len(groups)})
    # 组序包装（③④共用——错误以 groups[N] 指明组序，code/status 原样）
    def _wrap_group_error(i: int, exc: ResearchError) -> ResearchError:
        return ResearchError(
            exc.code, f"groups[{i}]: {exc.message}", status=exc.status,
            detail={"group": f"groups[{i}]", **(exc.detail or {})})

    # ③ 全组逐组校验（在册→参数→reductions；先于装载 IO——确定序）
    for i, g in enumerate(groups):
        try:
            _validate_indicator(g["indicator"])
            if g.get("params") is not None:
                validate_params(g["indicator"], g["params"])
            _validate_reductions(g.get("reductions"))
        except ResearchError as exc:
            raise _wrap_group_error(i, exc) from exc
    # ④ 逐组双路径装载现算（执行期整批原子——异常中止天然无部分结果）
    results: List[Dict[str, Any]] = []
    for i, g in enumerate(groups):
        try:
            results.append(_query_single(
                data_dir, symbol=symbol, timeframe=timeframe,
                indicator=g["indicator"], params=g.get("params"),
                start=g.get("start"), end=g.get("end"),
                reductions=g.get("reductions")))
        except ResearchError as exc:
            raise _wrap_group_error(i, exc) from exc
    # ⑤ 请求序数组汇总 → ⑥ 批量档载荷自检
    result = {"groups": results}
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload.encode("utf-8")) > _MAX_BATCH_SERIALIZED_BYTES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"批量归约摘要超体积上限: {len(payload.encode('utf-8'))} 字节"
            f"（批量档上限 {_MAX_BATCH_SERIALIZED_BYTES}，单组档 "
            f"{_MAX_SERIALIZED_BYTES}）",
            detail={"bytes": len(payload.encode("utf-8")),
                    "limit": _MAX_BATCH_SERIALIZED_BYTES,
                    "tier": "batch",
                    "groups": len(results)})
    return result
