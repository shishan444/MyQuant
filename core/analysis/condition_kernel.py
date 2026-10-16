"""条件统计内核（掩码求值 + 去重叠 t 检验；add-regime-split 提升）。

0.2.5 全谱扫描定义于 screen_patterns.py 私有；0.2.6 param_sweep 以同包
import 成为第二消费者（其 design D4 明文「第三个消费者出现时再议提升，
现在搬是为重排而重排」）——add-regime-split regime_split 即第三消费者，
时机兑现，提升为分析域共享模块。内容自 screen_patterns 逐字保真。

multi-timeframe-conditions（0.2.14）：``mask_for`` 加 ``mtf`` 上下文
参数与掩码投影三分派（高周期条件整条件在高周期轴求值、布尔掩码按
asof_completed 投影）；额外键全量透传修复既有三键重建静默丢键缺陷。

add-research-analysis-tools（0.4.1）：``mask_for_all`` 条件数组交集
（逐条 ``mask_for`` 后 AND）——三分析模板组合条件共用，高周期条目
经 ``MtfEvalContext`` 缓存零重算。

消费者：screen_patterns / param_sweep / regime_split / event_study /
inspect_cases / validation engine / rule_engine（改动须按 tests/README
守护清单回归全部消费者）；mask_for_all 消费者=condition_stats /
event_study / counter_profile（0.4.2 组合求值后续共用）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.analysis.registry_meta import WARMUP_RULE_TEXT
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


def mask_for(
    work: pd.DataFrame, condition: Dict[str, Any],
    mtf: Optional[MtfEvalContext] = None,
) -> pd.Series:
    """单条件掩码（0.2.14 三分派——七消费者共用）。

    公共契约（0.3.7 B4 改名去私有前缀）：跨模块消费面稳定入口——
    六模板/case_inspection/validation/validate 路由直接 import。

    - 有效周期 = base 或无 timeframe 键 → 现行两段式（缺省零变化）：
      结构化 subject resolve → 按 params 现算指标列（encode 得精确
      列名）→ 精确列名求值；字符串 subject 精确列名命中。列缺失降级
      双侧分野（卡3b）：subject 侧缺列返回全 False 安全降级（既有
      契约）；target 侧不可解码且非基础列结构化拒绝
      RESEARCH_CONDITION_INVALID，基础列/数值 target 缺失仍全 False。
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


def mask_for_all(
    work: pd.DataFrame, conditions: List[Dict[str, Any]],
    mtf: Optional[MtfEvalContext] = None,
) -> pd.Series:
    """条件数组交集掩码（0.4.1 R2 S1——逐条 mask_for 后按位 AND）。

    公共契约：condition_stats / event_study / counter_profile 三模板
    与 0.4.2 组合求值共用。带 timeframe 键条目各自走投影路径
    （``MtfEvalContext`` 惰性缓存 + ``__mtf_mask_*`` 幂等列天然去重，
    同请求多条件共用零重算）；AND 结果不缓存（每请求一次求值无重复
    场景）。内核自洽锚：mask_for_all([c]) 与 mask_for(c) 逐位一致
    （单条 AND 数学恒等，差异仅在装载路径——键形制判据 D9）。

    长度 1 包装与条数校验（空/>5）不在内核——路由/模板入口与参数
    校验层职责（空数组到达本函数属调用方接线缺陷，按内部不变量
    ValueError 拒绝而非外部 422 语义）。
    """
    if not conditions:
        raise ValueError(
            "mask_for_all 收到空 conditions（条数校验归路由/校验层——"
            "调用方接线缺陷）")
    result: Optional[pd.Series] = None
    for condition in conditions:
        mask = mask_for(work, condition, mtf=mtf)
        result = mask if result is None else (result & mask)
    return result


def _classify_target_ref(target) -> Tuple[str, Optional[Dict[str, Any]]]:
    """target 引用五分类（卡9：三处内嵌判定链的公共分类底座）。

    纯函数无 df 依赖无副作用——分类语义单点化，消费方（refs 收集 /
    基准现算 / 高周期现算）只分派不重复判定。BASE_SUBJECT_COLUMNS
    判定收口于此（消除 raise 条件的第二因子散布）。

    Returns:
        ("non_str", None)——非字符串（数值阈值等），不进列判定链；
        ("numeric", None)——数值串（float 可解析），语义为阈值非列名；
        ("decoded", payload)——编码指标列名，payload 含 indicator/params；
        ("base_column", None)——基础列名（BASE_SUBJECT_COLUMNS）；
        ("unknown", None)——不可解码且非基础列。
    """
    from core.analysis.column_ref import (
        BASE_SUBJECT_COLUMNS, try_decode_column_ref,
    )
    if not isinstance(target, str):
        return "non_str", None
    try:
        float(target)
    except ValueError:
        pass
    else:
        return "numeric", None
    decoded = try_decode_column_ref(target)
    if decoded is not None:
        return "decoded", decoded
    if target in BASE_SUBJECT_COLUMNS:
        return "base_column", None
    return "unknown", None


def _reject_unresolvable_target(target: str) -> None:
    """RESEARCH_CONDITION_INVALID 单点构造（卡9：detail 形制先例逐字保持）。

    Raises:
        ResearchError: 不可解码且非基础列的 target（strict 消费方调用）。
    """
    from core.analysis.hypothesis_service import ResearchError

    raise ResearchError(
        "RESEARCH_CONDITION_INVALID",
        f"condition.target 不可解析: {target!r}",
        detail={"param": "condition.target", "value": target,
                "legal_form": "编码指标列名或基础列"})


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
        ResearchError: RESEARCH_CONDITION_INVALID——dict subject 经
            resolve_column_ref 解析失败（ColumnRefError 转译，不限
            strict）；或 strict=True 且 target 不可解码且非基础列。
    """
    from core.analysis.column_ref import (
        ColumnRefError, resolve_column_ref, try_decode_column_ref,
    )
    from core.analysis.hypothesis_service import ResearchError

    refs: List[tuple] = []
    subject = condition.get("subject")
    if isinstance(subject, dict):
        # ColumnRefError 转译（形制沿 hypothesis_to_strategy 翻译链先例）：
        # 不转译则裸穿 ValueError → 路由 500；掩码路径（strict=False）的
        # dict subject 解析失败同经此转译收敛为 422（宽容面仅 str subject
        # 不可解码静默跳过与 target unknown 不并入——语义冻结不动）
        try:
            cdef = resolve_column_ref(subject)
        except ColumnRefError as exc:
            raise ResearchError(
                "RESEARCH_CONDITION_INVALID",
                f"condition.subject 非法: {exc.message}",
                detail={"code": exc.code, **exc.detail}) from exc
        refs.append((cdef.indicator, dict(cdef.params)))
    elif isinstance(subject, str):
        decoded = try_decode_column_ref(subject)
        if decoded is not None:
            refs.append((decoded["indicator"], decoded.get("params") or {}))
    # 卡9：target 判定链收敛到 _classify_target_ref 单点分类
    target = condition.get("target")
    kind, payload = _classify_target_ref(target)
    if kind == "decoded":
        refs.append((payload["indicator"], payload.get("params") or {}))
    elif kind == "unknown" and strict:
        _reject_unresolvable_target(target)
    return refs


# 条件条目允许键集（unblock-research-validation T6：与登记面
# ConditionItem extra=forbid 同语义共享单点——全部条件消费者同语义）
_CONDITION_ITEM_KEYS = frozenset({"subject", "action", "target", "timeframe"})


def validate_conditions_array(conditions: Any) -> None:
    """conditions 数组参数校验（0.4.1 任务 1.4——必填键组通过后、
    装载前；CARD-05 自路由提升为条件域共享单点，第四消费者 exit_sweep
    模板接入）。

    条数 1..5：空数组与超限均 422 披露条数域（空集合是无意义请求
    而非合法零结果；交集触发频率随条数指数衰减，5 条以上在既有数据
    规模上几乎必然零样本）。每条形制沿单条件链（subject/action/target
    三键结构，hypothesis_service 登记链同款）。"""
    from core.analysis.hypothesis_service import ResearchError

    if not isinstance(conditions, list) or not (1 <= len(conditions) <= 5):
        actual = (len(conditions) if isinstance(conditions, list)
                  else type(conditions).__name__)
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"conditions 条数须在 1..5（实际 {actual}）",
            detail={"param": "conditions", "legal_range": "1..5"})
    for i, c in enumerate(conditions):
        if (not isinstance(c, dict)
                or not all(k in c for k in ("subject", "action", "target"))):
            raise ResearchError(
                "RESEARCH_CONDITION_INVALID",
                f"conditions[{i}] 须含 subject/action/target",
                detail={"param": f"conditions[{i}]",
                        "legal_form": "{subject, action, target}"})
        # 逐键白名单（unblock-research-validation T6c）：与登记面
        # ConditionItem（extra=forbid）同语义——未知键附 {unknown_keys,
        # legal_keys} 与 timeframe 指路；四消费者（condition_stats/
        # counter_profile/event_study/exit_sweep）自动获得
        unknown = sorted(set(c) - _CONDITION_ITEM_KEYS)
        if unknown:
            raise ResearchError(
                "RESEARCH_CONDITION_INVALID",
                f"conditions[{i}] 含未知键 {unknown}"
                f"（合法键集 {sorted(_CONDITION_ITEM_KEYS)}；周期标记"
                " timeframe 是条件顶层键，不在 subject 内部）",
                detail={"param": f"conditions[{i}]",
                        "unknown_keys": unknown,
                        "legal_keys": sorted(_CONDITION_ITEM_KEYS),
                        "hint": "timeframe 是条件顶层键，不在 subject 内部"})


# 插件列命名前缀（fx_* 公式 / model_* 模型）——前缀面收集的判定依据。
# 可达性权威判定在 signal_registry（column_ref._visible_plugin_columns），
# 前缀匹配是保守宽面：收集结果须再经调用方可达性校验（预检装载入口）。
_PLUGIN_COLUMN_PREFIXES = ("fx_", "model_")


def collect_condition_plugin_columns(conditions) -> set:
    """条件集插件列引用收集（预检 warmup 两维推导的插件维度输入）。

    subject 混合形制（canonical 化前后均同）：指标→结构化 dict 不参与；
    基础列与插件列→str 原样（ref_dict 对非 indicator 类返回 column_name，
    插件列 str 是收集主入口）。target 不被 canonical 化，保持原始混合
    形制（数值或字符串——_classify_target_ref 的 numeric 分支），仅
    字符串形参与前缀匹配，数值形跳过。

    前缀匹配为保守宽面（registry 不可达列也收进来）——可达性校验归
    调用方（预检装载入口，不可达 422 结构化拒绝，禁止静默降维）。

    Args:
        conditions: 条件列表（{subject, action, target} 形制）。

    Returns:
        插件列名集合（可为空——纯指标/基础列/数值条件集）。
    """
    cols: set = set()
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        for key in ("subject", "target"):
            ref = condition.get(key)
            if isinstance(ref, str) and ref.startswith(_PLUGIN_COLUMN_PREFIXES):
                cols.add(ref)
    return cols


def _ensure_target_column(
    df: pd.DataFrame, target, *, strict: bool,
) -> None:
    """列名类 target 现算入 df（卡9：_mask_base/_mask_higher 消费收敛）。

    幂等前置（列在前置先于一切——不重算已有列）→ 数值串/非 str 透传
    （阈值语义非列名）→ decode 现算；strict 时未知列名结构化拒绝
    （卡3b 语义），宽容路径静默跳过（列缺失全 False 降级归求值层）。

    Args:
        df: 就地富集目标（基准 work / 高周期 eval_df）。
        target: 条件 target（str 列名 / 数值 / 数值串混合形制）。
        strict: True=不可解码且非基础列 raise（基准路径卡3b）；
            False=高周期轴宽容（历史语义）。
    """
    if not isinstance(target, str) or target in df.columns:
        return
    from core.analysis.column_ref import resolve_column_ref
    kind, payload = _classify_target_ref(target)
    if kind == "decoded":
        _compute_into(df, resolve_column_ref(payload))
    elif kind == "unknown" and strict:
        _reject_unresolvable_target(target)


def _mask_base(work: pd.DataFrame, condition: Dict[str, Any]) -> pd.Series:
    """基准周期两段式（0.2.13 形制 + 额外键全量透传修复 + 卡3b target 链）。"""
    from core.analysis.column_ref import resolve_column_ref
    subject = condition["subject"]
    col = subject
    if isinstance(subject, dict):
        cdef = resolve_column_ref(subject)      # 校验前置（入口已过）
        col = _compute_into(work, cdef)
    # 卡3b target 四段判定链（卡9 收敛 _ensure_target_column 单点）：
    # 列在幂等 / 数值透传 / decode 现算 / 不可解码且非基础列结构化拒绝
    # （消灭「入口放行+求值不算」的静默 0 假象——case_inspection/
    # condition_stats 等 mask_for 全消费方统一获得该语义）
    _ensure_target_column(work, condition.get("target"), strict=True)
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
                    "data_start": raw.index[0].isoformat(),
                    "rule": WARMUP_RULE_TEXT})

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
    # 卡9：target 段消费 _ensure_target_column（宽容语义；原内嵌链缺
    # 数值串前置的漂移随单点化同愈——行为等价、形制收敛）
    _ensure_target_column(eval_df, condition.get("target"), strict=False)
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


def p_value_for(
    fwd: pd.Series, stat_mask: np.ndarray, horizon: int,
    baseline_mean: Optional[float],
) -> tuple[Optional[float], int]:
    """去重叠子集上的单样本 t 检验 p 值（0.2.1 condition_stats 同口径）。

    公共契约（0.3.7 B4 改名去私有前缀）：统计模板与特征网共用。

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
