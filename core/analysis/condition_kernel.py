"""条件统计内核（掩码求值 + 去重叠 t 检验；add-regime-split 提升）。

0.2.5 全谱扫描定义于 screen_patterns.py 私有；0.2.6 param_sweep 以同包
import 成为第二消费者（其 design D4 明文「第三个消费者出现时再议提升，
现在搬是为重排而重排」）——add-regime-split regime_split 即第三消费者，
时机兑现，提升为分析域共享模块。内容自 screen_patterns 逐字保真。

multi-timeframe-conditions（0.2.14）：``_mask_for`` 加 ``mtf`` 上下文
参数与掩码投影三分派（高周期条件整条件在高周期轴求值、布尔掩码按
asof_completed 投影）；额外键全量透传修复既有三键重建静默丢键缺陷。

消费者：screen_patterns / param_sweep / regime_split / event_study /
inspect_cases / validation engine / rule_engine（改动须按 tests/README
守护清单回归全部消费者）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.analysis.warmup_loader import to_utc


def stat_window_mask(
    df: pd.DataFrame,
    request_start: "str | pd.Timestamp | None",
) -> pd.Series:
    """统计段掩码（warmup 前缀只用不算——四分析模板共用切回样板）。

    request_start 之前的前缀排除（warmup 装载只为指标收敛，统计只算
    请求区间）；None → 全 True（全 df 皆统计段）。返回布尔 Series
    （index 对齐 df），消费方掩码求值/直切两形制通用。
    """
    if request_start is None:
        return pd.Series(True, index=df.index)
    req = to_utc(pd.Timestamp(request_start))
    return pd.Series(df.index >= req, index=df.index)


@dataclass
class MtfEvalContext:
    """多周期求值上下文（路由层构建、模板透传、内核消费）。

    data_dir/symbol/base_tf 即路由既有入参三元组；raw 高周期数据按
    tf 惰性装载缓存（同请求内多条件共用）。base_tf 语义（F2）：Lab
    链=effective_base（base_timeframe or timeframe）、研究链=请求
    data_range.timeframe。

    eval_start：求值域（统计域）起点——高周期 warmup 检查基准
    （研究链=request_start / inspect_cases=effective_start / 缺省=
    work.index[0] 装载域起点，保守方向：早于统计域起点的检查只会
    更严不会漏）。
    """
    data_dir: Path
    symbol: str
    base_tf: str
    eval_start: Optional[pd.Timestamp] = None
    _raw: Dict[str, pd.DataFrame] = field(default_factory=dict)

    def load_raw(self, tf: str) -> pd.DataFrame:
        """惰性装载高周期 raw OHLCV 全量（按 tf 缓存；warmup 可得性由
        调用方基于数据原始起点判定——切片后再判会因 load_start 落在
        两根 bar 之间误判不足）。

        Raises:
            ResearchError: RESEARCH_SYMBOL_NOT_FOUND（该周期无数据文件）。
        """
        if tf in self._raw:
            return self._raw[tf]
        from core.analysis.hypothesis_service import ResearchError
        from core.data.mtf_loader import find_parquet, safe_symbol
        from core.data.storage import load_parquet

        path = find_parquet(Path(self.data_dir), safe_symbol(self.symbol), tf)
        if path is None:
            raise ResearchError(
                "RESEARCH_SYMBOL_NOT_FOUND",
                f"无数据: {self.symbol} {tf}（高周期条件引用的周期不可达）",
                detail={"symbol": self.symbol, "timeframe": tf})
        df = load_parquet(path)
        if df is None or len(df) == 0:
            raise ResearchError(
                "RESEARCH_SYMBOL_NOT_FOUND",
                f"数据不可装载: {self.symbol} {tf}",
                detail={"symbol": self.symbol, "timeframe": tf})
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        self._raw[tf] = df
        return df


def _mask_for(
    work: pd.DataFrame, condition: Dict[str, Any],
    mtf: Optional[MtfEvalContext] = None,
) -> pd.Series:
    """单条件掩码（0.2.14 三分派——七消费者共用）。

    - 有效周期 = base 或无 timeframe 键 → 现行两段式（缺省零变化）：
      结构化 subject resolve → 按 params 现算指标列（encode 得精确
      列名）→ 精确列名求值；字符串 subject 精确列名命中。列缺失返回
      全 False 安全降级（既有契约）。
    - 有效周期 > base 且有 ctx → 掩码投影：整条件在高周期轴求值
      （subject 与列名类 target 高周期轴解析、action 高周期轴自洽），
      布尔掩码按 asof_completed 投影到基准索引，结果入 work 后缀列
      幂等缓存。
    - 带 timeframe 键而无 ctx → 显式 ResearchError（工程接线错误，
      兜底退役 D-final——静默回退值投影违反掩码投影 MUST）。

    额外键全量透传（D-r2）：subject 解析替换逻辑不变（0.2.13 契约
    零变化），其余键（window/inner_action/direction/proximity_pct/
    bounce_pct/role/lookback 及扩展键）随 condition 全量透传、不再
    三键丢弃——两条路径同适用。
    """
    from core.analysis.hypothesis_service import ResearchError

    tf_key = condition.get("timeframe")
    if tf_key is None or (mtf is not None and tf_key == mtf.base_tf):
        return _mask_base(work, condition)
    if mtf is None:
        raise ResearchError(
            "RESEARCH_MTF_CONTEXT_MISSING",
            f"条件携带 timeframe={tf_key!r} 但求值入口未构建多周期上下文"
            "（调用方接线缺陷，非输入错误）",
            detail={"timeframe": tf_key})
    from core.data.mtf_alignment import TF_SECONDS, assert_higher_tf
    if tf_key not in TF_SECONDS:
        raise ResearchError(
            "TIMEFRAME_UNKNOWN",
            f"未知周期: {tf_key!r}",
            detail={"timeframe": tf_key, "legal_range": sorted(TF_SECONDS)})
    try:
        assert_higher_tf(tf_key, mtf.base_tf)
    except ValueError as exc:
        raise ResearchError(
            "TIMEFRAME_NOT_HIGHER",
            f"timeframe {tf_key} 低于基准周期 {mtf.base_tf}——"
            "低周期条件请降基准表达（基准降为该低周期、其余条件挂高周期）",
            detail={"timeframe": tf_key, "base_timeframe": mtf.base_tf}
        ) from exc
    return _mask_higher(work, condition, mtf, tf_key)


def collect_condition_refs(
    condition: Dict[str, Any], *, strict: bool = False,
) -> List[tuple]:
    """条件指标引用收集（warmup 推导与装载校验公共入口，卡2）。

    subject（dict 结构化经 resolve_column_ref / str 编码列名宽容解码）
    与 target（str 编码列名可解析即并入）两侧的 (indicator, params)
    清单——掩码路径 warmup 推导输入（strict=False 保留历史宽容语义：
    不可解析静默跳过）；路由装载路径 strict=True：target 不可解码且
    非基础列即结构化拒绝（禁止静默 0 样本——与卡3 strict 合流）。

    Args:
        condition: {subject, action, target} 条件（subject 混合形制）。
        strict: True 时 target 不可解析即 raise（装载/校验通道）；
            False 保持掩码路径宽容（不可解析不并入）。

    Returns:
        [(indicator, params_dict)] 引用清单（可为空——纯数值条件）。

    Raises:
        ResearchError: RESEARCH_CONDITION_INVALID（strict=True 且
            target 不可解码且非基础列）。
    """
    from core.analysis.column_ref import (
        BASE_SUBJECT_COLUMNS, resolve_column_ref, try_decode_column_ref,
    )
    from core.analysis.hypothesis_service import ResearchError

    refs: List[tuple] = []
    subject = condition.get("subject")
    if isinstance(subject, dict):
        cdef = resolve_column_ref(subject)
        refs.append((cdef.indicator, dict(cdef.params)))
    elif isinstance(subject, str):
        decoded = try_decode_column_ref(subject)
        if decoded is not None:
            refs.append((decoded["indicator"], decoded.get("params") or {}))
    target = condition.get("target")
    if isinstance(target, str):
        try:
            float(target)
        except ValueError:
            decoded = try_decode_column_ref(target)
            if decoded is not None:
                refs.append((decoded["indicator"],
                             decoded.get("params") or {}))
            elif strict and target not in BASE_SUBJECT_COLUMNS:
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"condition.target 不可解析: {target!r}",
                    detail={"param": "condition.target", "value": target,
                            "legal_form": "编码指标列名或基础列"})
    return refs


def _mask_base(work: pd.DataFrame, condition: Dict[str, Any]) -> pd.Series:
    """基准周期两段式（0.2.13 形制 + 额外键全量透传修复 + 卡3b target 链）。"""
    from core.analysis.column_ref import (
        BASE_SUBJECT_COLUMNS, resolve_column_ref, try_decode_column_ref,
    )
    subject = condition["subject"]
    col = subject
    if isinstance(subject, dict):
        cdef = resolve_column_ref(subject)      # 校验前置（入口已过）
        col = _compute_into(work, cdef)
    # 卡3b target 四段判定链：列在幂等 / 数值透传 / decode 现算 /
    # 不可解码且非基础列结构化拒绝（对称 _mask_higher 先例；消灭
    # 「入口放行+求值不算」的静默 0 假象——case_inspection/condition_stats
    # 等 _mask_for 全消费方统一获得该语义）
    target = condition.get("target")
    if isinstance(target, str) and target not in work.columns:
        try:
            float(target)
        except ValueError:
            decoded = try_decode_column_ref(target)
            if decoded is not None:
                _compute_into(work, resolve_column_ref(decoded))
            elif target not in BASE_SUBJECT_COLUMNS:
                from core.analysis.hypothesis_service import ResearchError
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"condition.target 不可解析: {target!r}",
                    detail={"param": "condition.target", "value": target,
                            "legal_form": "编码指标列名或基础列"})
    eval_cond = dict(condition)
    eval_cond["subject"] = col   # subject 解析替换；其余键全量透传（D-r2）
    from core.validation.engine import _evaluate_single_condition
    mask = _evaluate_single_condition(work, eval_cond)
    return mask.fillna(False).astype(bool)


def _compute_into(df: pd.DataFrame, cdef) -> str:
    """结构化引用现算入 df（列已存在则幂等跳过），返回精确列名。

    pandas_ta 数据不足（bar 数 < 指标 length）返回 None——收口为
    float 全 NaN 列：engine 数值比较安全得全 False（subject/target
    两侧同愈，本函数为条件现算唯一入口），不让 object 全 None 列
    炸 TypeError→500。
    """
    col = cdef.encoded_name()
    if col not in df.columns:
        from core.features.indicators import _compute_indicator
        for cname, series in _compute_indicator(
                df, cdef.indicator, dict(cdef.params)).items():
            df[cname] = (series if series is not None
                         else pd.Series(np.nan, index=df.index))
    return col


def _mask_higher(
    work: pd.DataFrame, condition: Dict[str, Any],
    mtf: MtfEvalContext, tf: str,
) -> pd.Series:
    """掩码投影路径：整条件高周期轴求值 → asof_completed 布尔投影。"""
    from core.analysis.column_ref import (
        resolve_column_ref, try_decode_column_ref,
    )
    from core.data.mtf_alignment import (
        TF_SECONDS, high_tf_warmup_span, project_completed_bool,
    )
    from core.analysis.hypothesis_service import ResearchError

    # 幂等缓存命中（同条件重复求值零重算）
    cache_col = _mtf_cache_column(condition, tf)
    if cache_col in work.columns:
        return work[cache_col].fillna(False).astype(bool)

    # subject/target 指标引用收集（warmup 推导输入——条件实际参数粒度；
    # 卡2 提炼为 collect_condition_refs 公共入口，本路径 strict=False
    # 宽容语义不变：不可解析不并入）
    subject = condition["subject"]
    refs = collect_condition_refs(condition)
    cdef = None
    if isinstance(subject, dict):
        cdef = resolve_column_ref(subject)

    # 惰性装载高周期 raw（warmup 检查基准=求值域起点 eval_start——
    # 无则 work.index[0] 装载域起点，保守方向：更早的基准只会更严）
    warmup_delta = high_tf_warmup_span(refs, tf)
    check_base = mtf.eval_start if mtf.eval_start is not None else work.index[0]
    load_start = check_base - warmup_delta
    raw = mtf.load_raw(tf)
    if raw.index[0] > load_start:
        need_bars = int(warmup_delta.total_seconds() // TF_SECONDS[tf])
        avail_bars = int(
            (check_base - raw.index[0]).total_seconds() // TF_SECONDS[tf])
        raise ResearchError(
            "RESEARCH_WARMUP_INSUFFICIENT",
            f"高周期 {tf} warmup 不足：需 {need_bars} bar 前扩，"
            f"数据起点前实际可得 {avail_bars} bar（数据起点 {raw.index[0]}）",
            detail={"timeframe": tf, "warmup_bars": need_bars,
                    "available_bars": avail_bars,
                    "data_start": raw.index[0].isoformat()})

    # 高周期轴求值（subject 现算 + 列名类 target 现算 + 额外键透传；
    # decode 就地重取——纯函数幂等，与收集段解耦）
    eval_df = raw.copy()
    col = subject
    if cdef is not None:
        col = _compute_into(eval_df, cdef)
    elif isinstance(subject, str) and subject not in eval_df.columns:
        # P-5：与 target 对称——编码列名高周期轴 decode 现算；
        # 不可解析保持列缺失全 False 契约（基准路径同款）
        decoded = try_decode_column_ref(subject)
        if decoded is not None:
            col = _compute_into(eval_df, resolve_column_ref(decoded))
    target = condition.get("target")
    if isinstance(target, str) and target not in eval_df.columns:
        decoded = try_decode_column_ref(target)
        if decoded is not None:
            _compute_into(eval_df, resolve_column_ref(decoded))
    eval_cond = dict(condition)
    eval_cond["subject"] = col
    from core.validation.engine import _evaluate_single_condition
    mask = _evaluate_single_condition(eval_df, eval_cond)
    mask = mask.fillna(False).astype(bool)

    projected = project_completed_bool(mask, work.index, tf)
    work[cache_col] = projected   # 幂等富集（同条件零重算）
    return projected


def _mtf_cache_column(condition: Dict[str, Any], tf: str) -> str:
    """掩码缓存列名（含额外键——canonical 指纹只归三键，不足以区分）。"""
    payload = json.dumps(condition, sort_keys=True, ensure_ascii=False,
                         default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"__mtf_mask_{tf}_{digest}"


def _p_value_for(
    fwd: pd.Series, stat_mask: np.ndarray, horizon: int,
    baseline_mean: Optional[float],
) -> tuple[Optional[float], int]:
    """去重叠子集上的单样本 t 检验 p 值（0.2.1 condition_stats 同口径）。

    Returns:
        (p_value, n_valid)——p 为 None 表示不可检（样本<2/零方差/无基线）。
    """
    if baseline_mean is None:
        return None, 0
    locs = np.where(stat_mask)[0]
    picked: list[int] = []
    for loc in locs:
        if not picked or loc - picked[-1] >= horizon:
            picked.append(loc)
    subset = fwd.iloc[picked].dropna() if picked else fwd.iloc[0:0]
    if len(subset) < 2 or float(np.std(subset.to_numpy(), ddof=1)) <= 0:
        return None, int(len(subset))
    from scipy import stats  # 形制同 condition_stats：函数内按需导入

    _, p = stats.ttest_1samp(subset.to_numpy(), baseline_mean)
    return float(p), int(len(subset))
