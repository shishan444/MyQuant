"""全谱扫描模板（add-screen-patterns；MCP 0.2.5「能发现」核心件）。

一次调用穷举「指标 × 注册表支持条件 × 阈值字典 × 持有期」的候选规律：
指标矩阵算一次、每持有期前向收益算一次（向量化），每个（条件, 持有期）
检验 = 掩码求值 + 去重叠 t 检验；全族（条件 × 持有期）Benjamini-Hochberg
FDR 校正后按可信度排序输出（不可关闭）。

条件形制复用假设验证引擎的 {subject, action, target}；指标计算复用
core.features.indicators._compute_indicator（0.2.1 授权的私有复用——
同一指标定义源，列名以计算返回的真实列名为权威，不从命名规则推导）。

注册表语义动作到可求值动作的翻译（action_map 顶层无 price_above）：
- price_above(X) ≡ close gt X；price_below(X) ≡ close lt X
- cross_above/below（价格穿越指标）≡ close cross_above/below X
- cross_above_series ≡ 同族快慢参数列互穿
- pattern 类 eq ≡ pattern 列 gt 0.5（出现即 1）

第一版不枚举（登记进 skipped_actions 声明）：lookback_any/lookback_all
（嵌套 inner_action+bars）、touch_bounce/role_reversal（SNR 模式）、
divergence 类、le/ge（与 lt/gt 语义近重合，控族大小）、spike/shrink。
guard_only 与 lazy 指标不参与枚举（与策略信号域一致）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.condition_identity import canonical_condition_hash
from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import AnalysisResult, build_result

# ---------------------------------------------------------------------------
# 阈值字典（D7 延迟决策：模板内私有常量；第二个消费者出现时提升注册表）
# 形制：指标名 -> 列选择器（None=单输出主列；str=列名包含片段）-> 动作 -> 刻度
# ---------------------------------------------------------------------------

_VALUE_GRIDS: Dict[str, Dict[Optional[str], Dict[str, List[float]]]] = {
    # momentum / 有界振荡类
    "RSI": {None: {"lt": [20, 25, 30, 35], "gt": [65, 70, 75, 80]}},
    "MFI": {None: {"lt": [20, 25, 30], "gt": [70, 75, 80]}},
    "Williams %R": {None: {"lt": [-90, -80], "gt": [-20, -10]}},
    "CMO": {None: {"lt": [-40, -25], "gt": [25, 40]}},
    "CCI": {None: {"lt": [-150, -100], "gt": [100, 150]}},
    # trend strength / 方向
    "ADX": {None: {"gt": [20, 25, 30], "lt": [15, 20]}},
    "Aroon": {"aroon_up": {"gt": [70, 80]},
              "aroon_down": {"gt": [70, 80]},
              "aroon_osc": {"gt": [50, 70], "lt": [-70, -50]}},
    "ROC": {None: {"lt": [-5, -3], "gt": [3, 5]}},
    "TRIX": {None: {"lt": [0], "gt": [0]}},
    "MACD": {"macd_histogram": {"lt": [0], "gt": [0]}},
    "Stochastic": {"stoch_k": {"lt": [20, 25], "gt": [75, 80]},
                   "stoch_d": {"lt": [20, 25], "gt": [75, 80]}},
    # volume / 量能
    "RVOL": {None: {"gt": [1.5, 2.0, 3.0], "lt": [0.5, 0.8]}},
    "VROC": {None: {"gt": [50, 100], "lt": [-50, -100]}},
    "CMF": {None: {"gt": [0.1, 0.2], "lt": [-0.1, -0.2]}},
    # derivatives（数据缺列时整指标跳过并声明）
    "OI_ZScore": {None: {"lt": [-2.0], "gt": [2.0]}},
    "FundingZScore": {None: {"lt": [-2.0], "gt": [2.0]}},
    "OI_ChangeRate": {None: {"lt": [-10, -5], "gt": [5, 10]}},
    "OIPriceDivergence": {None: {"lt": [-0.3, -0.2], "gt": [0.2, 0.3]}},
}

# 价格相对 / 交叉类：指标名 -> 参与动作清单（每根输出列 × 动作，subject=close）
_PRICE_REL_ACTIONS: Dict[str, List[str]] = {
    "EMA": ["price_above", "price_below", "cross_above", "cross_below"],
    "SMA": ["price_above", "price_below", "cross_above", "cross_below"],
    "WMA": ["price_above", "price_below"],
    "DEMA": ["price_above", "price_below"],
    "TEMA": ["price_above", "price_below"],
    "BB": ["price_above", "price_below"],
    "Keltner": ["price_above", "price_below"],
    "Donchian": ["price_above", "price_below"],
    "PSAR": ["price_above", "price_below"],
}

# 同族快慢交叉（cross_above_series）：指标名 -> 快慢参数对（短周期列 subject）
_SERIES_CROSS: Dict[str, List[tuple]] = {
    # _DEFAULT_PARAMS: EMA [10,20,50,100,200] -> 相邻对；MACD 语义已由 histogram 覆盖
    "EMA": [(10, 20), (20, 50), (50, 200)],
    "SMA": [(10, 50), (20, 100), (50, 200)],
}

# pattern 类（eq -> gt 0.5）：名称含 "pattern_" 的输出列
_PATTERN_PREFIX = "pattern_"

# 跳过的注册表动作（枚举不覆盖，输出声明）
_SKIPPED_ACTIONS = [
    "lookback_any", "lookback_all", "touch_bounce", "role_reversal",
    "le", "ge", "eq", "cross_above_series_only_explicit",
]

_DEFAULT_HORIZONS = [4, 12, 24, 48, 72]
_DEFAULT_TOP_N = 20
_DEFAULT_MIN_SAMPLES = 30
_TOP_DETAIL = 3          # 完整多持有期统计仅前 N 候选携带
_COMBINATIONS_TOP = 10   # 组合补扫的单条件基数


def resolve_feature_names(features: Any) -> List[str]:
    """features 参数解析（"all" 或子集 → 可枚举指标名列表）。

    路由侧 warmup 推导与本模板共用（未知指标在此即拒，避免装载层
    KeyError 裸穿）；all = 排除 guard_only 与 lazy 的注册表全集。
    """
    from core.features.registry import INDICATOR_REGISTRY

    if isinstance(features, str):
        if features != "all":
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"features 非法: {features!r}",
                detail={"param": "features",
                        "legal_range": '"all" | 指标名列表'})
        return [n for n, d in INDICATOR_REGISTRY.items()
                if not d.guard_only and d.compute_mode != "lazy"]
    if isinstance(features, list) and features:
        names = []
        for f in features:
            if f not in INDICATOR_REGISTRY:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"features 含未知指标: {f!r}",
                    detail={"param": "features", "unknown": str(f),
                            "legal_range": "注册表指标名（list_indicators 查询）"})
            names.append(str(f))
        return names
    raise ResearchError(
        "RESEARCH_PARAM_INVALID",
        f"features 非法: {features!r}",
        detail={"param": "features",
                "legal_range": '"all" | 非空指标名列表'})


def _validate_params(
    features: Any, horizons: Any, top_n: Any, min_samples: Any,
) -> tuple[List[str], List[int], int, int]:
    """参数边界校验（G5：RESEARCH_PARAM_INVALID 附 legal_range 先例）。"""
    names = resolve_feature_names(features)
    if horizons is None:
        horizons = list(_DEFAULT_HORIZONS)

    if (not isinstance(horizons, list) or not horizons
            or not all(isinstance(h, int) and not isinstance(h, bool) and h >= 1
                       for h in horizons)):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"horizons 非法: {horizons!r}",
            detail={"param": "horizons", "legal_range": "非空正整数列表，如 [4,12,24,48,72]"})

    for pname, val, lo, hi in (("top_n", top_n, 1, 100),
                               ("min_samples", min_samples, 5, 10000)):
        if (not isinstance(val, int) or isinstance(val, bool)
                or not lo <= val <= hi):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"{pname} 非法: {val!r}",
                detail={"param": pname, "legal_range": f"{lo}..{hi}"})
    return names, [int(h) for h in horizons], int(top_n), int(min_samples)


def _mask_for(work: pd.DataFrame, condition: Dict[str, Any]) -> pd.Series:
    """单条件掩码（复用验证引擎语义；列缺失返回全 False 安全降级）。"""
    from core.validation.engine import _evaluate_single_condition

    mask = _evaluate_single_condition(work, {
        "subject": condition["subject"],
        "action": condition["action"],
        "target": condition["target"],
    })
    return mask.fillna(False).astype(bool)


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
    picked: List[int] = []
    for loc in locs:
        if not picked or loc - picked[-1] >= horizon:
            picked.append(loc)
    subset = fwd.iloc[picked].dropna() if picked else fwd.iloc[0:0]
    if len(subset) < 2 or float(np.std(subset.to_numpy(), ddof=1)) <= 0:
        return None, int(len(subset))
    from scipy import stats  # 形制同 condition_stats：函数内按需导入

    _, p = stats.ttest_1samp(subset.to_numpy(), baseline_mean)
    return float(p), int(len(subset))


def screen_patterns(
    df: pd.DataFrame,
    *,
    data_ranges: list[dict[str, str]],
    timeframe: str,
    features: Any = "all",
    horizons: Any = None,
    top_n: int = _DEFAULT_TOP_N,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    request_start: str | pd.Timestamp | None = None,
    stop_check: Optional[Callable[[], None]] = None,
    hypothesis_id: Optional[str] = None,
) -> AnalysisResult:
    """全谱扫描：声明空间穷举 + FDR 校正 + 候选清单。

    Args:
        df: 含 warmup 前缀的 OHLCV DataFrame（路由经 load_with_warmup
            前扩装载；统计段以 request_start 切回请求区间）。
        data_ranges: 请求区间（结构化结果三要素）。
        timeframe: bar 周期键（参与候选指纹）。
        features: "all" 或指标名子集。
        horizons: 持有期列表（默认 [4,12,24,48,72]）。
        top_n: 返回候选数上限。
        min_samples: 样本数下限（低于丢弃）。
        request_start: 请求区间起点（warmup 只用不算）。
        stop_check: 协作式中断（逐指标粒度检查）。
        hypothesis_id: 挂假设标识（透传三要素）。

    Raises:
        ResearchError: 参数非法（附 legal_range）。
    """
    from core.features.indicators import _DEFAULT_PARAMS
    from core.scoring.statistics import benjamini_hochberg

    feature_names, horizon_list, top_n, min_samples = _validate_params(
        features, horizons, top_n, min_samples)
    for h in horizon_list:
        if h > len(df) - 1:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"horizon {h} 超数据长度（{len(df)} 根）",
                detail={"param": "horizons",
                        "legal_range": f"1..{len(df) - 1}"})

    # 统计段（warmup 只用不算）
    if request_start is None:
        in_stat = pd.Series(True, index=df.index)
    else:
        req_ts = pd.Timestamp(request_start)
        if req_ts.tzinfo is None:
            req_ts = req_ts.tz_localize("UTC")
        else:
            req_ts = req_ts.tz_convert("UTC")
        in_stat = pd.Series(df.index >= req_ts, index=df.index)
    stat_pos = in_stat.values
    n_total = int(in_stat.sum())

    # 指标矩阵 + 条件展开（逐指标 stop_check）
    work = df.copy()
    probes: List[Dict[str, Any]] = []
    skipped_indicators: List[str] = []
    from core.features.indicators import _compute_indicator

    for name in feature_names:
        if stop_check is not None:
            stop_check()
        param_sets = _DEFAULT_PARAMS.get(name)
        if not param_sets:
            skipped_indicators.append(name)
            continue
        produced: List[str] = []
        for params in param_sets:
            try:
                new_cols = _compute_indicator(work, name, dict(params))
            except Exception:  # noqa: BLE001 —— 跳过并声明，不拖垮扫描
                continue
            for col, series in new_cols.items():
                if col not in work.columns:
                    work[col] = series
                produced.append(col)
        if not produced:
            skipped_indicators.append(name)
            continue
        covered = _emit_conditions_for(work, name, produced, probes)

        if not covered:
            skipped_indicators.append(name)

    # 前向收益（每持有期一次全序列，全候选共享）
    fwd_by_h = {h: work["close"].pct_change(h).shift(-h) for h in horizon_list}
    baseline_mean = {
        h: (float(fwd_by_h[h][stat_pos].dropna().mean())
            if len(fwd_by_h[h][stat_pos].dropna()) else None)
        for h in horizon_list
    }

    # 逐条件掩码（一次求值，全部持有期复用）+ 逐 (条件,持有期) 检验
    records: List[Dict[str, Any]] = []
    for cond in probes:
        if stop_check is not None:
            stop_check()
        mask = _mask_for(work, cond)
        stat_mask = (mask.values & stat_pos)
        n_hit = int(stat_mask.sum())
        if n_hit < min_samples:
            continue
        for h in horizon_list:
            fwd = fwd_by_h[h]
            valid = fwd[stat_mask]
            valid = valid.dropna()
            if len(valid) < min_samples:
                continue
            p, n_overlap = _p_value_for(fwd, stat_mask, h, baseline_mean[h])
            if p is None:
                continue  # 无 p 值不入族（BH 约定：无效检验不混入）
            mean = float(valid.mean())
            records.append({
                "condition": {
                    "subject": cond["subject"], "action": cond["action"],
                    "target": cond["target"]},
                "indicator": cond["indicator"],
                "horizon": h,
                "samples": int(len(valid)),
                "mean": mean,
                "median": float(valid.median()),
                "win_rate": float((valid > 0).mean()),
                "p_value": p,
                "fingerprint": canonical_condition_hash(
                    cond, timeframe),
                "_stat_mask": stat_mask,
            })

    # FDR 全族校正（条件 × 持有期）
    scanned = len(records)
    candidates: List[Dict[str, Any]] = []
    if scanned:
        q_values, sig_flags = benjamini_hochberg([r["p_value"] for r in records])
        for r, q, sig in zip(records, q_values, sig_flags):
            r["q_value"] = q
            r["fdr_significant"] = bool(sig)
            candidates.append(r)

    # 组合补扫：top 10 单条件（按条件指纹去重——同条件多持有期算一条）
    # 两两 AND（组合候选仅从补扫产生，parts 集合去重）
    best_by_cond: Dict[str, Dict[str, Any]] = {}
    for r in _rank(candidates):
        fp = r["fingerprint"]
        if fp not in best_by_cond:
            best_by_cond[fp] = r
    singles = list(best_by_cond.values())[:_COMBINATIONS_TOP]
    combo_records: List[Dict[str, Any]] = []
    seen_combo_parts: set = set()
    for i in range(len(singles)):
        for j in range(i + 1, len(singles)):
            if stop_check is not None:
                stop_check()
            a, b = singles[i], singles[j]
            if a["condition"]["subject"] == b["condition"]["subject"]:
                continue  # 同 subject 组合语义缠绕（近重复条件），跳过
            parts_key = tuple(sorted(
                [a["fingerprint"], b["fingerprint"]]))
            if parts_key in seen_combo_parts:
                continue
            seen_combo_parts.add(parts_key)
            combo_mask = a["_stat_mask"] & b["_stat_mask"]
            if int(combo_mask.sum()) < min_samples:
                continue
            combo_fp = "".join(sorted([a["fingerprint"], b["fingerprint"]]))
            best_combo: Optional[Dict[str, Any]] = None
            for h in horizon_list:
                fwd = fwd_by_h[h]
                valid = fwd[combo_mask]
                valid = valid.dropna()
                if len(valid) < min_samples:
                    continue
                p, _ = _p_value_for(fwd, combo_mask, h, baseline_mean[h])
                if p is None:
                    continue
                rec = {
                    "condition": None,  # 组合条件在输出层组装
                    "parts": [a["condition"], b["condition"]],
                    "indicator": f'{a["indicator"]}+{b["indicator"]}',
                    "horizon": h,
                    "samples": int(len(valid)),
                    "mean": float(valid.mean()),
                    "median": float(valid.median()),
                    "win_rate": float((valid > 0).mean()),
                    "p_value": p,
                    "fingerprint": combo_fp,
                }
                # 同 parts 只保留效应量最大的持有期（跨 horizon 去重）
                if (best_combo is None
                        or abs(rec["mean"]) > abs(best_combo["mean"])):
                    best_combo = rec
            if best_combo is not None:
                combo_records.append(best_combo)
    if combo_records:
        # 组合并入族重校正（族=单条件检验 ∪ 组合检验，从严）
        all_p = [r["p_value"] for r in records] + [
            r["p_value"] for r in combo_records]
        q_all, sig_all = benjamini_hochberg(all_p)
        for r, q, sig in zip(candidates, q_all[:scanned], sig_all[:scanned]):
            r["q_value"], r["fdr_significant"] = q, bool(sig)
        for r, q, sig in zip(combo_records, q_all[scanned:], sig_all[scanned:]):
            r["q_value"], r["fdr_significant"] = q, bool(sig)
        candidates.extend(combo_records)
        scanned = len(all_p)

    # 输出聚合：按条件指纹跨持有期去重（主行=效应量最大持有期；
    # 一候选多持有期口径——同指纹多行违背候选清单形制）
    by_fp: Dict[str, Dict[str, Any]] = {}
    horizon_stats: Dict[str, List[Dict[str, Any]]] = {}
    for r in candidates:
        fp = r["fingerprint"]
        horizon_stats.setdefault(fp, []).append({
            "horizon": r["horizon"], "samples": r["samples"],
            "mean": round(r["mean"], 6), "win_rate": round(r["win_rate"], 4),
        })
        if fp not in by_fp or abs(r["mean"]) > abs(by_fp[fp]["mean"]):
            by_fp[fp] = r
    ranked = _rank(list(by_fp.values()))[:top_n]
    detail_count = min(_TOP_DETAIL, len(ranked))

    # 输出组装（两层密度：精简行全量 + 完整统计仅 top 3）
    out_candidates: List[Dict[str, Any]] = []
    for idx, r in enumerate(ranked):
        entry: Dict[str, Any] = {
            "rank": idx + 1,
            "condition": (r["parts"] if r.get("parts") else r["condition"]),
            "is_combination": bool(r.get("parts")),
            "fingerprint": r["fingerprint"],
            "samples": r["samples"],
            "best_horizon": r["horizon"],
            "mean": round(r["mean"], 6),
            "win_rate": round(r["win_rate"], 4),
            "q_value": round(r["q_value"], 6),
            "fdr_significant": r["fdr_significant"],
        }
        if idx < detail_count:
            entry["horizon_stats"] = sorted(
                horizon_stats[r["fingerprint"]], key=lambda x: x["horizon"])
        out_candidates.append(entry)

    # 单条件候选的共现伴生（描述性，不产生独立候选）
    co_occur = _co_occurrence(ranked)

    payload = {
        "timeframe": timeframe,
        "features_scanned": feature_names,
        "horizons": horizon_list,
        "scanned": scanned,
        "family": "conditions_x_horizons",
        "skipped_indicators": skipped_indicators,
        "skipped_actions": _SKIPPED_ACTIONS,
        "min_samples": min_samples,
        "candidates": out_candidates,
        "co_occurrence": co_occur,
        "review_hint": (
            "其余候选请以 condition_stats（同条件同持有期）逐条独立复核；"
            "登记引用候选时携带 provenance={task_id, candidate_fingerprint}"),
        "total_bars": int(len(work)),
        "stat_bars": n_total,
        "warmup_bars": int(len(work) - n_total),
    }
    return build_result(
        "screen_patterns",
        sample_size=n_total,  # 证据基数=统计段 bar 数（空扫描合法，不得小样本拒绝）
        confidence={
            "method": "benjamini_hochberg_fdr",
            "level": 0.05,
            "family_size": scanned,
            "note": "族=全部（条件×持有期）检验对；组合补扫并入同族重校正",
        },
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_samples,
        hypothesis_id=hypothesis_id,
    )


def _emit_conditions_for(
    work: pd.DataFrame, name: str, produced: List[str],
    probes: List[Dict[str, Any]],
) -> bool:
    """按阈值字典为单指标展开条件（返回是否覆盖到任何动作）。"""
    covered = False
    for selector, actions in _VALUE_GRIDS.get(name, {}).items():
        cols = ([c for c in produced if selector in c]
                if selector else produced[:1])
        for col in cols:
            for action, targets in actions.items():
                for t in targets:
                    probes.append({
                        "subject": col, "action": action, "target": t,
                        "indicator": name})
                    covered = True
    for action in _PRICE_REL_ACTIONS.get(name, []):
        eval_action = {"price_above": "gt", "price_below": "lt"}.get(
            action, action)
        for col in produced:
            probes.append({
                "subject": "close", "action": eval_action,
                "target": col, "indicator": name})
            covered = True
    for fast, slow in _SERIES_CROSS.get(name, []):
        # 精确列名匹配（子串匹配会让 _10 误命中 _100）
        fast_col = next(
            (c for c in produced if c == f"{name.lower()}_{fast}"), None)
        slow_col = next(
            (c for c in produced if c == f"{name.lower()}_{slow}"), None)
        if fast_col and slow_col and fast_col != slow_col:
            probes.append({
                "subject": fast_col, "action": "cross_above",
                "target": slow_col, "indicator": name})
            covered = True
    for col in produced:
        if col.startswith(_PATTERN_PREFIX):
            probes.append({
                "subject": col, "action": "gt", "target": 0.5,
                "indicator": name})
            covered = True
    return covered


def _rank(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """四级排序键（D8：fdr 优先 / q 升序 / 效应量降序 / 指纹典序）。"""
    return sorted(
        records,
        key=lambda r: (
            0 if r.get("fdr_significant") else 1,
            r["q_value"],
            -abs(r["mean"]),
            str(r["fingerprint"]),
        ))


def _co_occurrence(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """单条件候选的高频共现伴生（top 5 描述性，不算独立候选）。"""
    out = []
    singles = [r for r in ranked if not r.get("parts")][:5]
    for r in singles:
        mask = r.get("_stat_mask")
        if mask is None:
            continue
        partners = []
        for other in ranked:
            if other is r or other.get("parts"):
                continue
            om = other.get("_stat_mask")
            if om is None:
                continue
            inter = int((mask & om).sum())
            if inter >= 5:
                partners.append({
                    "with": other["condition"],
                    "co_rate": round(inter / max(int(mask.sum()), 1), 3),
                })
        partners.sort(key=lambda p: -p["co_rate"])
        out.append({"condition": r["condition"], "top_co": partners[:3]})
    return out
