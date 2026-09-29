"""案例抽查服务（add-inspect-cases；MCP 0.2.8「能辨真假」检查③）。

六步研究流程第③步「真假检查」的第三件：检查①（param_sweep 参数
稳健性）与检查②（regime_split 行情分段）都是统计判定——用数字判定
数字，对数据质量问题（K 线缺口、异常值、指标失真）结构性失明。本
通道在严格硬上限下让真实触发案例的原始 OHLCV 窗口进入 AI 上下文
（眼见为实），是全流程唯一原始 K 线检视入口。双通道格局：统计判定
器走 run_analysis 模板族（AnalysisResult 三要素），数据检视器走独立
通道（market_snapshot 先例：独立端点+自定义输出+硬上限纪律）。

硬上限纪律（上限即预算）：案例数 limit ∈ 1..5（缺省 3）、每案例窗口
context_bars ∈ 1..20（缺省 8）、timestamps 点名 ≤5（超发按原始名单
数拒绝——先判超发后去重，输入约束归输入）。limit=5 × context_bars=20
的极限组合是刻意设计的最大输出预算；体积自检 24576 字节兜底序列化
形制退化（逐 bar dict 膨胀），参数校验放行的调用不会被体积自检拒掉。

selector 两形态互斥：condition（{subject, action, target} 三键——扫描
候选同形制直接喂入，掩码求值复用 condition_kernel，第四消费者）或
timestamps（点名时间戳，index <= ts 归位——market_snapshot as_of 同款
解析；与 start/end 同传拒绝：点名自带定位语义，区间抽查走 condition
形态；重复点名去重、n_matched 计去重后数、selector_echo 回显原始输入、
输出时间升序与 condition 形态命中序同构）。

条件三键防御对称闭合（求值引擎对未知动作/未解析目标均静默返回全
False 掩码——前置拦截不让调用错误伪装成无命中）：action 合法集=求值
面中数据集可达 14 键；pattern/SNR 族 6 键依赖 pattern 数据列、本通道
不装载，显式拒绝分层说明；subject 须属于 indicator 默认参数输出列
（RESEARCH_COLUMN_NOT_AVAILABLE 附可用列）；target 数值可解析直过、
列名态须在装载现算后的可用列集合。

装载双路径：start 显式走 load_with_warmup（全族一致：前缀不足报
RESEARCH_WARMUP_INSUFFICIENT）；start 缺省全量直装 + 指标有效域
effective_start 披露（数据集开头天然无 warmup 前缀是事实不是错误——
命中求值只在有效域上进行，头部无效段如实披露不参与求值）。end 三态：
显式=装载终点外扩 context_bars 根（尾部窗口完整）；缺省（condition
形态）=数据末端（自然边界，尾部截断走披露义务）；timestamps 形态不
适用（同传已拒）。

等距取样：命中数超限在命中位置序列上 np.linspace 取整去重（首尾
保底、确定性、覆盖均匀）；limit=1 取中位命中（等距覆盖的单点退化是
中心位置而非端点——首点可能聚堆头部，与「前 N 个系统性偏差」同族
教训）；命中总数 n_matched 强制披露（抽查代表性）。命中 0 是合法
检查产出（200 + cases=[] + 留痕照落）。

留痕对链：每次成功调用落 research_log（entry_type="inspect_cases"，
hypothesis_id=None adhoc 形制——不进假设判定并集）；ref_id 用
canonical_condition_hash 条件指纹（与 param_sweep 的 sweep 留痕同键，
检查①③事后审计互通）或 "timestamps:N" 形态标识。拒绝路径零持久
副作用（结构校验拒绝=零装载零留痕；列校验在装载现算后属设计必然，
parquet 只读不构成副作用）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from core.analysis.condition_kernel import _mask_for
from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import default_params_for, derive_warmup
from core.analysis.warmup_loader import TF_MINUTES, load_with_warmup
from core.features.registry import INDICATOR_REGISTRY

# 硬上限（上限即预算——spec 契约参数）
_MAX_CASES = 5
_MIN_CASES = 1
_DEFAULT_LIMIT = 3
_MAX_CONTEXT_BARS = 20
_MIN_CONTEXT_BARS = 1
_DEFAULT_CONTEXT_BARS = 8
_MAX_TIMESTAMPS = 5
_MAX_SERIALIZED_BYTES = 24576  # 形制退化兜底（极限组合 5×41 行全域可用）

# 求值面中数据集可达动作集（design D7：engine action_map 20 键的可达
# 子集——spike/shrink 仅依赖 subject 序列滚动均值、lookback 依赖 df
# 历史窗口、touch 数值容差比较，均已代码核实；param_sweep 本地常量
# 同惯例，不跨模块 import engine 局部 dict）
_SUPPORTED_ACTIONS: frozenset[str] = frozenset({
    "touch", "cross_above", "cross_below", "breakout", "breakdown",
    "gt", "lt", "ge", "le",
    "spike", "shrink",
    "cross_above_series", "cross_below_series", "lookback_any",
})
# pattern/SNR 族（依赖 pattern 数据列，本通道不装载——engine.py
# _resolve_pattern/_resolve_snr_pattern 路径，结构性不可达，显式拒绝）
_PATTERN_ACTIONS: frozenset[str] = frozenset({
    "divergence_top", "divergence_bottom",
    "consecutive_up", "consecutive_down",
    "touch_bounce", "role_reversal",
})


def _to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """时间参数归一（语义对齐 warmup_loader._to_utc：naive 按 UTC 解释）。"""
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _validate_range_param(name: str, value: int, lo: int, hi: int) -> None:
    """limit/context_bars 范围校验（bool 排除——True 是 int 子类）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{name} 须为整数 {lo}..{hi}",
            detail={"param": name, "legal_range": f"{lo}..{hi}"})
    if not lo <= value <= hi:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{name} 超出范围: {value}（合法 {lo}..{hi}）",
            detail={"param": name, "value": value, "legal_range": f"{lo}..{hi}"})


def _parse_selector(
    selector: Dict[str, Any],
    start: Optional[str],
    end: Optional[str],
) -> Tuple[str, Any]:
    """selector 结构校验（装载前）：两形态互斥 + F5 同传拒 + H2 超发拒。

    Returns:
        ("condition", condition_dict) 或 ("timestamps", 归一时间戳列表)。
    """
    if not isinstance(selector, dict):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "selector 须为对象（恰含 condition 或 timestamps 之一）",
            detail={"param": "selector",
                    "legal_range": "condition 三键 {subject,action,target} "
                                   "或 timestamps 数组（互斥）"})
    keys = set(selector.keys())
    extra = keys - {"condition", "timestamps"}
    if extra:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"selector 含未知键: {sorted(extra)}（恰含 condition 或 timestamps 之一）",
            detail={"param": "selector", "illegal": sorted(extra)})
    has_cond = "condition" in keys
    has_ts = "timestamps" in keys
    if has_cond and has_ts:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "selector 双形态混用（恰含 condition 或 timestamps 之一）",
            detail={"param": "selector"})
    if not has_cond and not has_ts:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "selector 缺形态键（恰含 condition 或 timestamps 之一）",
            detail={"param": "selector"})

    if has_cond:
        cond = selector["condition"]
        if (not isinstance(cond, dict)
                or set(cond.keys()) != {"subject", "action", "target"}):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "condition 形态须恰含三键 {subject, action, target}",
                detail={"param": "selector.condition"})
        # 0.2.13：subject 混合形制——参数化指标结构化对象（resolve 校验
        # 即解析）或基础列/插件列字符串；action 恒字符串
        if not isinstance(cond["action"], str):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "condition.action 须为字符串",
                detail={"param": "selector.condition"})
        if isinstance(cond["subject"], (str, dict)):
            from core.analysis.column_ref import ColumnRefError, resolve_column_ref
            try:
                resolve_column_ref(cond["subject"])
            except ColumnRefError as exc:
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"selector.condition.subject 非法: {exc.message}",
                    detail={"param": "selector.condition",
                            "code": exc.code, **exc.detail}) from exc
        else:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "condition.subject 须为字符串或结构化指标对象",
                detail={"param": "selector.condition"})
        return "condition", cond

    # timestamps 形态：先判超发（H2 按原始名单数）后去重——输入约束归输入
    raw = selector["timestamps"]
    if not isinstance(raw, list) or not raw:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "selector.timestamps 须为非空时间戳数组",
            detail={"param": "selector.timestamps"})
    if len(raw) > _MAX_TIMESTAMPS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timestamps 超发: 提交 {len(raw)} 个（上限 {_MAX_TIMESTAMPS}，"
            "按原始名单数判定，先判超发后去重）",
            detail={"param": "selector.timestamps", "count": len(raw),
                    "legal_range": f"1..{_MAX_TIMESTAMPS}"})
    # F5 同传拒：点名自带定位语义，区间参数构成意图含混
    if start is not None or end is not None:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "timestamps 形态不传区间参数（点名自带定位；区间抽查走 condition 形态）",
            detail={"param": "selector.timestamps",
                    "legal_range": "timestamps 形态不传区间参数"
                                   "（点名自带定位；区间抽查走 condition 形态）"})
    try:
        normalized = [_to_utc(pd.Timestamp(t)) for t in raw]
    except (ValueError, TypeError) as exc:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"timestamps 含不可解析项: {exc}",
            detail={"param": "selector.timestamps"}) from exc
    return "timestamps", normalized


def _validate_action(action: str) -> None:
    """action 前置校验（F2）：14 键可达子集；pattern 族分层拒绝。"""
    if action in _PATTERN_ACTIONS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"动作 {action!r} 属 pattern/SNR 族——依赖 pattern 数据列，"
            "本通道不装载（可求值动作须为数据集可达子集）",
            detail={"param": "condition.action", "action": action,
                    "reason": "pattern 数据列依赖，本通道不装载",
                    "legal_range": sorted(_SUPPORTED_ACTIONS)})
    if action not in _SUPPORTED_ACTIONS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"未知动作 {action!r}",
            detail={"param": "condition.action", "action": action,
                    "legal_range": sorted(_SUPPORTED_ACTIONS)})


def _validate_target(target: Any, available_cols: pd.Index) -> None:
    """target 判别（F3）：float 可解析直过；列名态须在可用列集合。

    与 engine _resolve_target 同构判别、拒绝而非降级（引擎解析失败
    静默全 False 是降级路径）。跨周期格式 "tf:indicator" 落列名态
    被拒——本通道不装载跨周期数据。
    """
    if isinstance(target, bool):
        return  # 比较标志，float 可解析语义（与指纹归一同族处理）
    if isinstance(target, (int, float)):
        return
    if isinstance(target, str):
        try:
            float(target)
            return
        except ValueError:
            pass
        if target not in available_cols:
            raise ResearchError(
                "RESEARCH_COLUMN_NOT_AVAILABLE",
                f"target 列 {target!r} 不在可用列集合（数值可解析直过；"
                "列名态须为在册列）",
                detail={"param": "condition.target", "column": target,
                        "legal_range": sorted(available_cols)})
        return
    raise ResearchError(
        "RESEARCH_PARAM_INVALID",
        f"target 类型非法: {type(target).__name__}（数值或列名字符串）",
        detail={"param": "condition.target"})


def _sample_positions(hits: np.ndarray, limit: int) -> List[int]:
    """等距取样（D4 + H4）：limit=1 中位；limit ≥2 首尾保底。"""
    n = len(hits)
    if n <= limit:
        return [int(i) for i in hits]
    if limit == 1:
        # 等距覆盖的单点退化是中心位置（linspace[0] 给首个是 numpy 端点
        # 约定，非等距语义；首点可能聚堆头部）
        return [int(hits[round((n - 1) / 2)])]
    idx = sorted({int(i) for i in np.linspace(0, n - 1, limit).round()})
    return [int(hits[i]) for i in idx]


def _indicator_values_at(
    ind_cols: Dict[str, "pd.Series"], work: pd.DataFrame, pos: int,
) -> Dict[str, Optional[float]]:
    """触发点指标值（None 容错：极短数据下部分输出列返回 None/NaN）。"""
    out: Dict[str, Optional[float]] = {}
    for col in ind_cols:
        if col not in work.columns:
            out[col] = None
            continue
        v = work[col].iloc[pos]
        out[col] = None if pd.isna(v) else float(v)
    return out


def _build_case(
    df: pd.DataFrame,
    work: pd.DataFrame,
    pos: int,
    context_bars: int,
    ind_cols: Dict[str, "pd.Series"],
) -> Dict[str, Any]:
    """单案例窗口：位置 ±context_bars 切窗，边界截断如实披露。"""
    w_start = max(0, pos - context_bars)
    w_end = min(len(df), pos + context_bars + 1)
    rows: List[List[Any]] = []
    for i in range(w_start, w_end):
        r = df.iloc[i]
        rows.append([
            df.index[i].isoformat(),
            float(r["open"]), float(r["high"]),
            float(r["low"]), float(r["close"]), float(r["volume"]),
        ])
    return {
        "trigger_ts": df.index[pos].isoformat(),
        "trigger_index": pos - w_start,
        "bars_before": pos - w_start,
        "bars_after": w_end - 1 - pos,
        "window": rows,
        "indicator_values": (
            _indicator_values_at(ind_cols, work, pos) if ind_cols else {}),
    }


def inspect_cases(
    db_path: Path,
    data_dir: Path,
    *,
    symbol: str,
    timeframe: str,
    selector: Dict[str, Any],
    limit: int = _DEFAULT_LIMIT,
    context_bars: int = _DEFAULT_CONTEXT_BARS,
    start: Optional[str] = None,
    end: Optional[str] = None,
    indicator: Optional[str] = None,
) -> Dict[str, Any]:
    """案例抽查：候选条件/点名时间戳的真实触发案例窗口（眼见为实）。

    Args:
        db_path: research 库路径（调用留痕）。
        data_dir: parquet 数据目录。
        symbol: 交易对（如 BTCUSDT）。
        timeframe: bar 周期（TF_MINUTES 键；未知周期按 240 分钟兜底）。
        selector: 两形态互斥——condition（{subject, action, target} 三键，
            扫描候选同形制直喂）或 timestamps（点名时间戳 ≤5，与区间
            参数同传拒绝）。
        limit: 案例数上限 1..5（缺省 3；condition 形态等距取样）。
        context_bars: 每案例窗口前后根数 1..20（缺省 8）。
        start: 请求区间起点（显式走 load_with_warmup；缺省全量直装 +
            effective_start 披露；timestamps 形态不传）。
        end: 请求区间终点（显式时装载终点外扩 context_bars 根；缺省=
            数据末端；timestamps 形态不传）。
        indicator: 指标名（condition 形态必填；timestamps 形态可选，
            不传则纯 OHLCV 窗口）。以注册表默认参数现算。

    Returns:
        案例抽查 dict（顶层 selector_echo/n_matched/sampling/
        effective_start/effective_end/limit/context_bars/cases；每案例
        trigger_ts/trigger_index/window/bars_before/bars_after/
        indicator_values）。序列化后 <= 24576 字节。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（limit/context_bars/selector/
            action/target/timestamps 超发/同传/体积超限）；
        RESEARCH_INDICATOR_UNKNOWN（indicator 不在册）；
        RESEARCH_COLUMN_NOT_AVAILABLE（subject 不属于指标输出列/target
            列名不在册）；RESEARCH_SYMBOL_NOT_FOUND（无数据）；
        RESEARCH_DATA_INSUFFICIENT（数据不足以装载/有效域为空/点名早于
            首 bar）；RESEARCH_WARMUP_INSUFFICIENT（显式 start 前缀不足，
            透传自 load_with_warmup）。
    """
    # ---------------- 结构校验（装载前；拒绝=零装载零留痕） ----------------
    _validate_range_param("limit", limit, _MIN_CASES, _MAX_CASES)
    _validate_range_param("context_bars", context_bars,
                          _MIN_CONTEXT_BARS, _MAX_CONTEXT_BARS)
    kind, payload = _parse_selector(selector, start, end)

    if kind == "condition":
        _validate_action(payload["action"])
        if indicator is None:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "condition 形态 indicator 必填（供 warmup 推导与指标计算）",
                detail={"param": "indicator"})
    if indicator is not None and indicator not in INDICATOR_REGISTRY:
        raise ResearchError(
            "RESEARCH_INDICATOR_UNKNOWN",
            f"未知指标: {indicator}",
            detail={"param": "indicator",
                    "legal_range": sorted(INDICATOR_REGISTRY)})

    # ---------------- 装载双路径 ----------------
    # end 三态：显式=装载终点外扩 context_bars 根（尾部窗口完整）；
    # 缺省=数据末端；timestamps 形态 end 不适用（同传已拒）
    tf_minutes = TF_MINUTES.get(timeframe, 240)
    end_expanded: Optional[str] = None
    if end is not None:
        end_expanded = (_to_utc(pd.Timestamp(end))
                        + pd.Timedelta(minutes=tf_minutes * context_bars)
                        ).isoformat()

    start_ts: Optional[pd.Timestamp] = None
    if kind == "condition" and start is not None:
        # 显式 start：load_with_warmup（前缀不足报 WARMUP_INSUFFICIENT）
        df, start_ts = load_with_warmup(
            data_dir, symbol, timeframe,
            start=start, end=end_expanded, indicator_names=[indicator])
    else:
        # 缺省全量（或 timestamps 形态）：全量直装，effective_start 披露
        from core.data.mtf_loader import find_parquet, load_and_prepare_df

        safe_symbol = re.sub(r"[^A-Za-z0-9]", "", symbol)
        if find_parquet(data_dir, safe_symbol, timeframe) is None:
            raise ResearchError(
                "RESEARCH_SYMBOL_NOT_FOUND",
                f"无数据: {symbol} {timeframe}",
                detail={"symbol": symbol, "timeframe": timeframe})
        df = load_and_prepare_df(
            data_dir, symbol, timeframe,
            data_start=None, data_end=end_expanded, compute_indicators=False)
        if df is None:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"数据不可装载: {symbol} {timeframe}")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    # ---------------- 指标现算（默认参数单组）与列校验（零留痕） ----------------
    work = df.copy()
    ind_cols: Dict[str, pd.Series] = {}
    if indicator is not None:
        from core.features.indicators import _compute_indicator

        ind_cols = _compute_indicator(work, indicator, default_params_for(indicator))
        for col, series in ind_cols.items():
            work[col] = series

    if kind == "condition":
        available = list(ind_cols.keys())
        # 0.2.13：结构化 subject 经 encode 归一为精确列名后比对（默认
        # 参数边界不变——非默认参数列仍不支持，任意参数现算属后续版本）
        subject = payload["subject"]
        subject_col = subject
        if isinstance(subject, dict):
            from core.analysis.column_ref import (ColumnRefError,
                                                  resolve_column_ref)
            try:
                subject_col = resolve_column_ref(subject).encoded_name()
            except ColumnRefError as exc:
                raise ResearchError(
                    "RESEARCH_COLUMN_NOT_AVAILABLE",
                    f"subject 引用非法: {exc.message}",
                    detail={"param": "condition.subject",
                            "code": exc.code, **exc.detail}) from exc
        if subject_col not in available:
            raise ResearchError(
                "RESEARCH_COLUMN_NOT_AVAILABLE",
                f"subject 列 {subject_col!r} 不属于 {indicator} "
                "默认参数输出列（非默认参数列不支持；任意参数现算属后续版本）",
                detail={"param": "condition.subject",
                        "column": subject_col,
                        "legal_range": sorted(available)})
        _validate_target(payload["target"], work.columns)

    # ---------------- 求值域与取样 ----------------
    warmup_bars = derive_warmup([indicator]) if indicator is not None else 0
    if kind == "condition":
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
        # 求值域：[effective_start, end 上界或数据末端]——end 显式时外扩区
        # 只用于窗口取数，命中不落请求区间之外（end 缺省=数据末端自然边界）
        in_domain = pd.Series(work.index >= effective_start_ts, index=work.index)
        if end is not None:
            end_ts = _to_utc(pd.Timestamp(end))
            in_domain = in_domain & pd.Series(work.index <= end_ts, index=work.index)
        mask = _mask_for(work, payload) & in_domain
        hits = np.where(mask.values)[0]  # 升序
        n_matched = int(len(hits))
        positions = _sample_positions(hits, limit)
        sampling = "even"
    else:
        # timestamps：去重排序（超发已按原始数判过）→ 逐点名归位
        uniq = sorted(set(payload))
        n_matched = len(uniq)
        positions = []
        for ts in uniq:
            pos = int(df.index.searchsorted(ts, side="right") - 1)
            if pos < 0:
                raise ResearchError(
                    "RESEARCH_DATA_INSUFFICIENT",
                    f"点名 {ts.isoformat()} 早于数据首 bar "
                    f"{df.index[0].isoformat()}（点名不可达）",
                    detail={"timestamp": ts.isoformat(),
                            "data_start": df.index[0].isoformat()})
            positions.append(pos)
        sampling = "explicit"

    # ---------------- 案例窗口与输出契约 ----------------
    cases = [
        _build_case(df, work, pos, context_bars, ind_cols)
        for pos in positions
    ]
    # effective_start 披露基准：condition 形态=求值域起点（显式 start 即
    # 请求起点；缺省即 warmup 后首根）；timestamps 形态无求值域，有指标
    # 时披露 warmup 后位置（头部读数无效区）、无指标即数据首 bar
    if kind == "condition":
        effective_start_out = effective_start_ts
    elif indicator is not None and len(df) > warmup_bars:
        effective_start_out = df.index[warmup_bars]
    else:
        effective_start_out = df.index[0]

    out: Dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "selector_echo": selector,  # 回显原始输入（未归一）
        "n_matched": n_matched,
        "sampling": sampling,
        "effective_start": effective_start_out.isoformat(),
        "effective_end": df.index[-1].isoformat(),  # 实际装载末端（截断披露基准）
        "limit": limit,
        "context_bars": context_bars,
        "cases": cases,
    }

    # ---------------- 体积自检（形制退化兜底；上限即预算全域可用） ----------------
    serialized = json.dumps(out, ensure_ascii=False).encode("utf-8")
    if len(serialized) > _MAX_SERIALIZED_BYTES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"输出体积超限: {len(serialized)} > {_MAX_SERIALIZED_BYTES} 字节",
            detail={"bytes": len(serialized), "limit": _MAX_SERIALIZED_BYTES})

    # ---------------- adhoc 留痕（检查①③同键对链；失败即整体失败） ----------------
    from core.analysis.condition_identity import canonical_condition_hash
    from core.persistence.repository import research as rr

    if kind == "condition":
        ref_id = canonical_condition_hash(payload, timeframe)
    else:
        ref_id = f"timestamps:{n_matched}"
    rr.append_research_log(
        db_path,
        hypothesis_id=None,
        entry_type="inspect_cases",
        ref_id=ref_id,
        data_ranges=json.dumps({
            "symbol": symbol, "timeframe": timeframe,
            "start": df.index[0].isoformat(), "end": df.index[-1].isoformat(),
        }, ensure_ascii=False),
        summary=f"inspect_cases {symbol} {timeframe} n_matched={n_matched} "
                f"sampling={sampling}",
    )
    return out
