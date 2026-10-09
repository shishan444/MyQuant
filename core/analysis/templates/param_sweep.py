"""参数稳健性检查模板（add-param-sweep；MCP 0.2.6「能辨真假」检查①）。

对候选条件的阈值参数在邻域网格上逐点重算（去重叠前向收益 + 对照
基线 t 检验，0.2.1 condition_stats 同口径），全族 Benjamini-Hochberg
FDR 校正后按三道门判定，回答「参数邻域有效性是连成片（plateau）
还是孤立一点（spike）」——过拟合拦截器，位于登记假设之前。

三道门（判定参数内建不可调——防自欺纪律内建，与 0.2.5 FDR 不可
关闭同线；调判定线重跑构成元级多重比较）：
1. 资格门：去重叠有效样本 < _MIN_SAMPLES 判 insufficient_sample
   （旁路——不进 BH 族、不计入 plateau 判定分母；边缘阈值天然
   稀疏，算无效会系统性偏向中心参数）
2. 真伪门：BH 后 q < _Q_THRESHOLD 且效应方向与中心点一致判
   effective，否则 ineffective
3. 连续门：effective 点在参数轴（排序索引）相邻连续段 >= _MIN_RUN
   判 plateau（附稳健区间）；effective 孤立判 spike；全部旁路判
   indeterminate（证据不足，非证伪——与 0.2.11 inconclusive 同族
   语义）；无 effective 但有参评点判 unsupported（候选该区间整体
   否定——判定空间 2×2 完备覆盖，四值各映射单一处置动作）

指标矩阵与 0.2.5 全谱扫描同款（默认参数矩阵——扫描产出的候选列名
天然可复现；非默认参数列的条件在列名校验处结构化拒绝并列出可用
列）。阈值网格与扫描共享同一份刻度（threshold_grids；扫描域与检查
域同源）。指标计算参数轴（period 类）不在本模板支持范围（输出
skipped 声明）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.analysis.condition_identity import canonical_condition_hash
from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.param_guard import _require_int_range
from core.analysis.result import AnalysisResult, build_result
from core.analysis.condition_kernel import MtfEvalContext, mask_for, p_value_for, stat_window_mask
from core.analysis.threshold_grids import VALUE_GRIDS

# 三道门常量（design Decision 3：模块内固定，输出 gates 披露；
# 修正走常量+规划文档+spec 版本化，不走运行时旋钮）
_Q_THRESHOLD = 0.05
_MIN_SAMPLES = 30
_MIN_RUN = 2

# sweep 轴支持的数值 target 动作（其余注册表动作目标非数值刻度）
_SUPPORTED_ACTIONS = ("lt", "gt", "cross_above", "cross_below")

_SKIPPED_AXES = {
    "period": "指标计算参数轴不参与稳健性检查（列名管理与 warmup 推导"
              "随参数变化，第一版只扫条件阈值轴）",
}


def _validate_inputs(
    indicator: str, condition: Dict[str, Any], sweep: Dict[str, Any],
    forward_bars: int, n_df: int,
) -> None:
    """输入契约校验（indicator+condition 双件；非法结构化拒绝附合法范围）。"""
    from core.features.registry import INDICATOR_REGISTRY

    try:
        float(condition["target"])
    except (KeyError, TypeError, ValueError):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition.target 须为数值（sweep 轴）：{condition!r}",
            detail={"param": "condition.target",
                    "legal_range": "数值（列名类 target 的系列交叉条件"
                                   "不适用阈值网格）"}) from None

    if indicator not in INDICATOR_REGISTRY:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID", f"indicator 未知: {indicator!r}",
            detail={"param": "indicator", "unknown": str(indicator),
                    "legal_range": "注册表指标名（list_indicators 查询）"})
    if not isinstance(condition, dict) or not {
            "subject", "action", "target"} <= set(condition):
        # 0.2.14：三键必备、额外键允许（可选 timeframe 键=高周期中心
        # 条件经掩码投影求值；动作额外键随条件全量透传）
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"condition 须含 subject/action/target 三键: {condition!r}",
            detail={"param": "condition",
                    "legal_range": "与扫描候选/假设登记同形式的单条件"
                                   "（MAY 携带 timeframe 键）"})
    if condition["action"] not in _SUPPORTED_ACTIONS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"action 不支持: {condition['action']!r}",
            detail={"param": "condition.action",
                    "legal_range": f"{list(_SUPPORTED_ACTIONS)}（数值阈值动作族）"})
    # TD-3.1 迁移 param_guard（输出形制与旧内联逐字节一致）
    _require_int_range(forward_bars, name="forward_bars",
                       lo=1, hi=n_df - 1)
    if not isinstance(sweep, dict) or sweep.get("axis", "target") != "target":
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"sweep.axis 非法: {sweep.get('axis')!r}",
            detail={"param": "sweep.axis", "legal_range": '["target"]',
                    "skipped": _SKIPPED_AXES})


def _resolve_grid(
    indicator: str, subject: str, action: str,
    center: float, sweep: Dict[str, Any],
) -> List[float]:
    """网格解析：auto=共享刻度表（列选择器匹配）∪ 中心点；显式=校验去重排序。"""
    values = sweep.get("values", "auto")
    if values == "auto":
        grids = VALUE_GRIDS.get(indicator, {})
        scale: List[float] = []
        for selector, actions in grids.items():
            if action not in actions:
                continue
            if selector is None or selector in subject:
                scale = [float(v) for v in actions[action]]
                break
        if not scale:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"共享刻度表无 {indicator} 的 {action} 刻度（subject={subject!r}）",
                detail={"param": "sweep.values",
                        "legal_range": "显式传入数值列表，或换用刻度表覆盖的"
                                       "指标/动作（screen_patterns 同源刻度）"})
        grid = sorted(set(scale) | {float(center)})
    else:
        if not isinstance(values, list) or not values:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"sweep.values 非法: {values!r}",
                detail={"param": "sweep.values",
                        "legal_range": '"auto" 或非空数值列表'})
        try:
            grid = sorted({float(v) for v in values} | {float(center)})
        except (TypeError, ValueError):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"sweep.values 含非数值: {values!r}",
                detail={"param": "sweep.values", "legal_range": "数值列表"}) from None
    if len(grid) < 2:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "网格去重后不足 2 个点（中心点与网格值重合）——无法做邻域检查",
            detail={"param": "sweep.values", "legal_range": "至少 2 个不同数值"})
    return grid


def _gates_verdict(
    points: List[Dict[str, Any]], grid: List[float],
) -> Tuple[str, Optional[List[float]]]:
    """连续门：effective 点在排序网格上的相邻连续段 >= _MIN_RUN 判 plateau。

    判定空间 2×2 完备（正证据有无 × 证据可得性）：无 effective 点时，
    全旁路 → indeterminate（证据不足非证伪）；有参评点 → unsupported
    （候选该区间整体否定——非 spike 的孤证语义，处置=丢弃或彻底换
    条件，换阈值复查无意义）。
    """
    eff_set = {p["value"] for p in points if p["state"] == "effective"}
    if not eff_set:
        any_scored = any(p["state"] == "ineffective" for p in points)
        return ("indeterminate", None) if not any_scored else ("unsupported", None)
    best_run: List[float] = []
    cur: List[float] = []
    for v in grid:
        if v in eff_set:
            cur.append(v)
            if len(cur) > len(best_run):
                best_run = cur
        else:
            cur = []
    if len(best_run) >= _MIN_RUN:
        return "plateau", [best_run[0], best_run[-1]]
    return "spike", None


def param_sweep(
    df: pd.DataFrame,
    *,
    indicator: str,
    condition: Dict[str, Any],
    sweep: Dict[str, Any],
    forward_bars: int = 24,
    data_ranges: List[Dict[str, str]] | None = None,
    timeframe: str = "1h",
    request_start: str | pd.Timestamp | None = None,
    hypothesis_id: str | None = None,
    mtf: "MtfEvalContext | None" = None,
) -> AnalysisResult:
    """参数稳健性检查：阈值邻域逐点重算 + 三道门判定 + 三值结论。

    Args:
        df: 含 warmup 前缀的 OHLCV DataFrame（路由经 load_with_warmup
            前扩装载；统计段以 request_start 切回请求区间）。
        indicator: 指标名（warmup 推导与指标矩阵计算依据）。
        condition: 中心条件 {subject, action, target}——subject 为列名
            （须属于该指标默认参数矩阵的输出列；扫描候选同形制直接喂入）。
        sweep: {axis: "target", values: "auto" | 数值列表}。
        forward_bars: 前向持有期 N（bar 数）。
        data_ranges: 请求区间（结构化结果三要素）。
        timeframe: bar 周期键（参与条件指纹）。
        request_start: 请求区间起点（warmup 只用不算）。
        hypothesis_id: 挂假设标识（透传三要素）。

    Raises:
        ResearchError: 参数非法（附 legal_range）；subject 不属于指标输出列。
    """
    from core.features.indicators import _DEFAULT_PARAMS, _compute_indicator
    from core.scoring.statistics import benjamini_hochberg

    _validate_inputs(indicator, condition, sweep, forward_bars, len(df))
    center = float(condition["target"])

    # 指标矩阵（默认参数集逐组计算——与 0.2.5 全谱扫描同款，候选列名
    # 天然可复现；非默认参数列在下方列名校验处结构化拒绝）
    work = df.copy()
    produced: List[str] = []
    for params in _DEFAULT_PARAMS.get(indicator, []):
        new_cols = _compute_indicator(work, indicator, dict(params))
        for col, series in new_cols.items():
            if col not in work.columns:
                work[col] = series
            produced.append(col)
    # 0.2.13：结构化 subject 经 encode 归一精确列名（默认参数矩阵边界
    # 不变）；字符串 subject 保持精确命中
    subject_col = condition["subject"]
    if isinstance(subject_col, dict):
        from core.analysis.column_ref import ColumnRefError, resolve_column_ref
        try:
            subject_col = resolve_column_ref(subject_col).encoded_name()
        except ColumnRefError as exc:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"subject 引用非法: {exc.message}",
                detail={"param": "condition.subject",
                        "code": exc.code, **exc.detail}) from exc
    if subject_col not in work.columns:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"subject 列 {subject_col!r} 不属于 {indicator} 输出列"
            "（本模板以默认参数矩阵求值，非默认参数列不支持）",
            detail={"param": "condition.subject",
                    "legal_range": sorted(set(produced))})
    condition = dict(condition, subject=subject_col)   # 求值/网格按精确列名

    grid = _resolve_grid(indicator, condition["subject"], condition["action"],
                         center, sweep)

    # 统计段切分与 baseline（0.2.1 condition_stats 同口径——共享掩码样板）
    in_stat = stat_window_mask(work, request_start)
    fwd = work["close"].pct_change(forward_bars).shift(-forward_bars)
    bas_fwd = fwd[in_stat].dropna()
    baseline_mean = float(bas_fwd.mean()) if len(bas_fwd) else None

    # 逐点求值：中心 target 替换为网格值（掩码求值 + 去重叠 t 检验）。
    # 资格门口径 = 原始有效触发数（与 0.2.5/condition_stats 的 min_samples
    # 同语义）；去重叠数另行披露（t 检验样本）
    raw: List[Dict[str, Any]] = []
    for value in grid:
        # 0.2.14：网格点条件全量继承中心条件（含 timeframe 键与其余
        # 额外键——高周期中心条件整条件在高周期轴求值，掩码投影）
        cond_v = {**condition, "target": value}
        stat_mask = mask_for(work, cond_v, mtf=mtf) & in_stat
        p_value, n_valid = p_value_for(
            fwd, stat_mask.values, forward_bars, baseline_mean)
        hit = fwd[stat_mask].dropna()
        mean = float(hit.mean()) if len(hit) else None
        raw.append({"value": value, "n_hit": int(len(hit)),
                    "n_overlapped": int(n_valid),
                    "forward_return_mean": mean, "p_value": p_value})

    # 真伪门（BH 族 = 取得有效 p 值的点——None 旁路不进族不稀释校正力度）
    family = [i for i, r in enumerate(raw) if r["p_value"] is not None]
    q_values: Dict[int, float] = {}
    if family:
        qs, _ = benjamini_hochberg([raw[i]["p_value"] for i in family])
        q_values = dict(zip(family, qs))

    # 中心点效应方向（效应锚：邻域点与中心同向才算支撑）
    center_point = next((r for r in raw if r["value"] == center), None)
    center_mean = center_point["forward_return_mean"] if center_point else None
    center_dir = (None if center_mean is None or baseline_mean is None
                  else int(np.sign(center_mean - baseline_mean)))

    points: List[Dict[str, Any]] = []
    for i, r in enumerate(raw):
        if r["n_hit"] < _MIN_SAMPLES or r["p_value"] is None:
            state = "insufficient_sample"
        else:
            q = q_values[i]
            eff_dir = (None if r["forward_return_mean"] is None
                       or baseline_mean is None
                       else int(np.sign(r["forward_return_mean"] - baseline_mean)))
            # 中心方向锚缺失（中心点样本不足）时方向维度退场，只按显著性判
            same_dir = center_dir is None or eff_dir == center_dir
            state = ("effective"
                     if q < _Q_THRESHOLD and same_dir
                     else "ineffective")
        points.append({
            "value": r["value"], "samples": r["n_hit"],
            "overlapped_samples": r["n_overlapped"],
            "forward_return_mean": r["forward_return_mean"],
            "baseline_mean": baseline_mean,
            "p_value": r["p_value"],
            "q_value": q_values.get(i),
            "effect_direction": (
                None if r["forward_return_mean"] is None or baseline_mean is None
                else int(np.sign(r["forward_return_mean"] - baseline_mean))),
            "state": state,
        })

    verdict, robust_range = _gates_verdict(points, grid)

    payload = {
        "condition": {**condition, "fingerprint":
                      canonical_condition_hash(condition, timeframe)},
        "sweep": {"axis": "target", "values": grid, "forward_bars": forward_bars},
        "points": points,
        "verdict": verdict,
        "robust_range": robust_range,
        # 判定参数透明披露（design Decision 3：给全信息、不给改尺权）
        "gates": {"q_threshold": _Q_THRESHOLD,
                  "min_samples": _MIN_SAMPLES, "min_run": _MIN_RUN},
        "method": {"test": "t_test_vs_baseline",
                   "correction": "benjamini_hochberg_fdr",
                   "family_size": len(family)},
        "skipped_axes": _SKIPPED_AXES,
    }
    return build_result(
        template="param_sweep",
        sample_size=int(in_stat.sum()),  # 统计段 bar 数（分析数据体量）
        confidence={"method": "gates_verdict",
                    "level": _Q_THRESHOLD,
                    "verdict": verdict,
                    "family_size": len(family)},
        data_ranges=data_ranges,
        payload=payload,
        hypothesis_id=hypothesis_id,
    )
