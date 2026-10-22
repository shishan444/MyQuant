"""退出参数扫描模板（add-research-analysis-tools 0.4.1 R6）。

语义：条件规律固定下的退出结构实验通道——按止损模式分组扫描退出参数
网格，逐组独立回测（含手续费滑点，口径与回测引擎一致），三道门判定
稳健平台，把风险结构设计从默认值变成受实验检验的选择。自装载模板壳
（precheck_hypothesis 先例——IO 面收敛在模板壳内，计算面纯）。

与隔离验证同一执行链（检查与考试同一量尺，D3）：条件组经
build_signal_genes 不落库纯翻译入口构造内存 DNA（THEN 空=离场全靠
risk_genes），run_backtest_on_range 受控复用点调引擎；走分析域掩码
口径自组 entries 反而引入「分析掩码口径 vs 引擎执行口径」偏差。

三道门（S6——回测口径谓词）：连续门与四值结论**直接复用
param_sweep._gates_verdict**（同包 import 零改动——纯游程判定器，
统计谓词在 param_sweep 主流程非判定器内）；R6 新收=state 构造谓词
（资格门 total_trades≥5 对齐 thresholds DEFAULT_PROFILE 笔级闸门线
+ 真伪门含费滑净期望>0）与两轴/跨模式平面最差聚合（全序
unsupported > spike > indeterminate > plateau）。判定标尺内建不可传。

两期分野：校验期任何失败=整批 422 无部分结果；执行期单组回测失败=
该组标注不阻塞（失败组列示）。

条件能力域边界（unblock-research-validation 后）：when 侧条件
timeframe 键放行——等于基准周期时键归一、与缺省语义完全相同
（显式写基准周期不构成高周期过滤）；严格高于基准周期时高周期轴
求值+掩码投影；低于基准周期时 TIMEFRAME_NOT_HIGHER 结构化拒绝；
不可表达形制 RESEARCH_NOT_EXPRESSIBLE 显式拒绝不静默降级。

留痕：检查型（entry_type="exit_sweep"、hypothesis_id=None 不挂不进
判定并集——param_sweep「检查不消耗验证区间」先例同构）。
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import build_result

# 组数异步阈值（6.6——资源参数可调：FAST_PATH_THRESHOLD_S=1.0s ÷ 保守
# 单组 0.1s；路由侧 heavy 判定消费）
_EXIT_SWEEP_ASYNC_GROUPS = 10
# 跨模式组数上限（Σ(len(stops)×len(take_profits)) ≤50）
_SWEEP_MAX_GROUPS = 50
# 资格门：组交易数下限（D8——对齐 thresholds DEFAULT_PROFILE 笔级闸门线）
_MIN_TRADES = 5
# 四值结论全序（最差聚合——任一侧/任一平面取更差者）
_VERDICT_ORDER = {"plateau": 0, "indeterminate": 1, "spike": 2,
                  "unsupported": 3}
_DIRECTIONS = ("long", "short")


def _mode_range(mode: str) -> Tuple[float, float]:
    """各档值域（模式感知分档——0.4.2 值域配套改造，评估问题 1）。

    引用式单点权威，不写字面副本：pct/trail 档→SL_PCT_MIN/MAX（比例
    值，trail 回撤比例与 pct 同域）；atr 档→SL_ATR_MIN/MAX（DC-11 分叉
    域，ATR 倍数——第四消费方，与声明块/validator 域校验同源）。
    """
    from core.strategy.validator import (
        SL_ATR_MAX, SL_ATR_MIN, SL_PCT_MAX, SL_PCT_MIN,
    )

    if mode == "atr":
        return SL_ATR_MIN, SL_ATR_MAX
    return SL_PCT_MIN, SL_PCT_MAX


def _validate_grid(grid: Any) -> List[Dict[str, Any]]:
    """网格校验与归一（校验确定序①→⑥——全序固定，错误码稳定性）。

    Returns:
        归一后的模式条目列表（请求序）：[{mode, stops, take_profits}]。
    """
    from core.backtest.engine import ENGINE_SUPPORTED_SL_MODES

    # ① grid 结构（形制/键完整 + mode 唯一性——重复无语义价值且同一
    # 模式平面会被重复判定）
    if (not isinstance(grid, dict) or not isinstance(grid.get("modes"), list)
            or not grid["modes"]):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "grid 须为 {modes: [{mode, stops, take_profits}, ...]}",
            detail={"param": "grid",
                    "legal_form": "{modes: [{mode, stops, take_profits}]}"})
    seen_modes: Dict[str, int] = {}
    for i, entry in enumerate(grid["modes"]):
        if (not isinstance(entry, dict)
                or not all(k in entry for k in ("mode", "stops",
                                                "take_profits"))):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"grid.modes[{i}] 须含 mode/stops/take_profits",
                detail={"param": f"grid.modes[{i}]",
                        "legal_form": "{mode, stops, take_profits}"})
        mode = entry["mode"]
        if mode in seen_modes:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"grid.modes 含重复模式条目: {mode!r}"
                f"（modes[{seen_modes[mode]}] 与 modes[{i}]——重复无语义"
                "价值且同一模式平面会被重复判定）",
                detail={"param": f"grid.modes[{i}].mode", "value": mode,
                        "duplicate_of": f"grid.modes[{seen_modes[mode]}]"})
        seen_modes[mode] = i
    # ② mode 合法性（引擎真实分派域引用式——未支持 422 披露合法集）
    for entry in grid["modes"]:
        if entry["mode"] not in ENGINE_SUPPORTED_SL_MODES:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"引擎未支持止损模式: {entry['mode']!r}"
                f"（当前合法集 {list(ENGINE_SUPPORTED_SL_MODES)}）",
                detail={"param": "grid.modes[].mode",
                        "value": entry["mode"],
                        "legal_range": list(ENGINE_SUPPORTED_SL_MODES)})
    # ③ 非空 + ④ 值域模式感知 + ⑤ 止盈必填正值
    # （0.4.2 值域配套改造：取域移入模式循环按 entry["mode"] 分档——
    #   循环外单点取 pct 域对全模式统一适用已被值域分叉取代：atr 档
    #   合法倍数（如 2.0）曾按 [0.005,0.20] 被误拒、无意义小值（如
    #   0.05 倍）被误放——量纲双关的值域义务在消费方，非自动跟随）
    total_groups = 0
    for entry in grid["modes"]:
        mode = entry["mode"]
        lo, hi = _mode_range(mode)
        for key in ("stops", "take_profits"):
            values = entry[key]
            if not isinstance(values, list) or not values:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"grid.modes[].{key} 须为非空列表（空列表乘积视 0 "
                    "会静默零行）",
                    detail={"param": f"grid.modes[].{key}",
                            "legal_form": "非空数值列表"})
            for v in values:
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise ResearchError(
                        "RESEARCH_PARAM_INVALID",
                        f"grid.modes[].{key} 含非数值: {v!r}",
                        detail={"param": f"grid.modes[].{key}", "value": v})
                if key == "stops" and not (lo <= float(v) <= hi):
                    raise ResearchError(
                        "RESEARCH_PARAM_INVALID",
                        f"{mode} 档止损值超出值域（合法 [{lo}, {hi}]——"
                        "validator 现役域同源）",
                        detail={"param": "grid.modes[].stops", "value": v,
                                "legal_range": [lo, hi]})
                if key == "take_profits" and float(v) <= 0:
                    raise ResearchError(
                        "RESEARCH_PARAM_INVALID",
                        f"止盈条目须为正值: {v!r}（A5——None/非正值 422；"
                        "「无止盈让利润奔跑」待实战需求放开）",
                        detail={"param": "grid.modes[].take_profits",
                                "value": v, "legal_range": "> 0"})
        total_groups += len(entry["stops"]) * len(entry["take_profits"])
    # ⑥ 跨模式组数上限
    if total_groups > _SWEEP_MAX_GROUPS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"网格组数超上限: {total_groups} > {_SWEEP_MAX_GROUPS}"
            f"（跨模式 Σ(len(stops)×len(take_profits))）",
            detail={"param": "grid", "actual": total_groups,
                    "limit": _SWEEP_MAX_GROUPS,
                    "hint": "收窄止损/止盈列表或分多次扫描"})
    return [dict(entry) for entry in grid["modes"]]


def _worst(a: str, b: str) -> str:
    return a if _VERDICT_ORDER[a] >= _VERDICT_ORDER[b] else b


def _axis_verdict(
    rows: List[Dict[str, Any]], axis: str,
) -> Tuple[str, Optional[List[float]]]:
    """轴式游程判定（复用 param_sweep._gates_verdict——纯游程判定器）。

    轴网格=该轴排序去重值；轴点 effective=该轴值上存在 effective 组
    （有效组在轴上的投影——「单轴平台×另一轴单点」经最差聚合整体
    判 spike 的语义来源）。
    """
    from core.analysis.templates.param_sweep import _gates_verdict

    grid = sorted({r[axis] for r in rows})
    effective_values = {r[axis] for r in rows if r["state"] == "effective"}
    points = [{"value": v, "state": ("effective" if v in effective_values
                                     else "ineffective")}
              for v in grid]
    return _gates_verdict(points, grid)


def exit_sweep(
    db_path: Path,
    data_dir: Path,
    request_range: Dict[str, str],
    *,
    condition: Optional[Dict[str, Any]] = None,
    conditions: Optional[List[Dict[str, Any]]] = None,
    direction: str,
    grid: Dict[str, Any],
    hypothesis_id: Optional[str] = None,  # D2：通用参数面透传现状——模板忽略，本模板不挂假设
) -> Dict[str, Any]:
    """退出参数扫描：网格逐组回测 + 三道门平台判定。

    Args:
        condition / conditions: 二选一（形制与条件统计一致；MUST NOT
            携带顶层 timeframe 键经翻译链放行域校验（非法/低于
            基准拒；等于基准归一）。
        direction: {long, short}（注入 risk_genes.direction——方向语义
            由 DNA 承载，One-Way 方向自适应下净期望>0 即与提交方向一致）。
        grid: {modes: [{mode, stops, take_profits}]}——网格按止损模式
            分组（量纲随模式双关：pct 比例值）。

    Returns:
        分析结果 dict（三要素经 build_result；sample_size=成功组数）。
    """
    from core.analysis.backtest_bridge import run_backtest_on_range
    from core.analysis.condition_kernel import (
        collect_condition_refs, validate_conditions_array)
    from core.analysis.hypothesis_to_strategy import build_signal_genes
    from core.analysis.registry_meta import derive_warmup_full
    from core.strategy.dna import ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA

    # 谓词参数面校验（必填键组在路由层；此处枚举与二选一）
    if direction not in _DIRECTIONS:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"direction 封闭枚举 {list(_DIRECTIONS)}（实际 {direction!r}）",
            detail={"param": "direction", "value": direction,
                    "legal_range": list(_DIRECTIONS)})
    if (condition is None) == (conditions is None):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "condition 与 conditions 须二选一（键组分派在路由层——"
            "直调方同样须遵守）",
            detail={"param": "condition|conditions",
                    "legal_form": "任选其一"})

    # 条件条数域+形制校验（共享层单点 validate_conditions_array——与
    # condition_stats/event_study/counter_profile 路由同源；先于翻译链：
    # 缺三键垃圾条件原走翻译链报 NOT_EXPRESSIBLE/静默，错误码误导）
    cond_list = conditions if conditions is not None else [condition]
    validate_conditions_array(cond_list)

    # 网格校验（校验期①-⑥——任何失败整批 422 无部分结果）
    modes = _validate_grid(grid)

    # 条件组 → 内存 DNA（S4 不落库纯翻译入口——timeframe 防线同源同码；
    # THEN 空列表=离场全靠 risk_genes，与隔离验证同一执行链）
    # unblock-research-validation：when 侧放行域生效——exit_sweep 的
    # 条件即 when 语义（求值出场），基准周期取请求区间 timeframe
    genes, plugin_sources = build_signal_genes(
        cond_list, [], db_path,
        strategy_timeframe=request_range["timeframe"])
    # 信号源组装（镜像 translate_hypothesis 同段——rule 源承载内置基因、
    # 插件源各自成源、composition 固定 AND）
    if plugin_sources:
        signal_sources = ([{"type": "rule"}] if genes else []) + plugin_sources
        composition = {"mode": "logic", "op": "and"}
    else:
        signal_sources = [{"type": "rule"}]
        composition = None

    symbol = request_range["symbol"]
    timeframe = request_range["timeframe"]
    base_dna = StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(),  # entry_logic="AND"（合取清单缺省）
        execution_genes=ExecutionGenes(symbol=symbol, timeframe=timeframe),
        risk_genes=RiskGenes(),   # 平台默认（position_size/leverage 非实验对象）
        signal_sources=signal_sources,
        composition=composition)

    # warmup=条件组引用并集（与 validation 同口径——derive_warmup_full
    # 两维式：内置指标名 ∪ 公式插件 lookback）
    indicator_names = sorted(
        {g.indicator for g in genes if getattr(g, "indicator", None)}
        | {ind for c in cond_list
           for ind, _ in collect_condition_refs(c)})
    formula_refs = {s["ref"] for s in plugin_sources
                    if s.get("type") == "formula" and s.get("ref")}
    warmup_bars = derive_warmup_full(db_path, indicator_names, formula_refs)

    # 逐组回测（执行期——展开序：modes 序→stops 序→tps 序，确定性；
    # 单组失败=该组标注不阻塞整批）
    results: List[Dict[str, Any]] = []
    failed_groups: List[Dict[str, Any]] = []
    for entry in modes:
        for stop in entry["stops"]:
            for tp in entry["take_profits"]:
                row = {"mode": entry["mode"], "stop_loss": float(stop),
                       "take_profit": float(tp)}
                try:
                    dna = copy.deepcopy(base_dna)  # 各组独立副本
                    dna.risk_genes.stop_loss = float(stop)
                    dna.risk_genes.take_profit = float(tp)
                    dna.risk_genes.sl_mode = entry["mode"]
                    dna.risk_genes.direction = direction
                    metrics, trade_returns, _, _ = run_backtest_on_range(
                        db_path, data_dir, dna, symbol, timeframe,
                        request_range["start"], request_range["end"],
                        warmup_bars)
                    # trade_returns 可能为 ndarray——is not None 判空
                    #（`or []` 对 ndarray 求真值抛 ValueError）
                    rets = list(trade_returns) if trade_returns is not None \
                        else []
                    wins = [r for r in rets if r > 0]
                    losses = [r for r in rets if r < 0]
                    mean_win = float(np.mean(wins)) if wins else 0.0
                    row.update({
                        "win_rate": metrics["win_rate"],
                        "profit_factor": (
                            float(abs(mean_win) / abs(np.mean(losses)))
                            if losses else None),  # 亏损笔数 0→null 键保留
                        "net_expectation": (float(np.mean(rets))
                                            if rets else None),
                        "max_drawdown": metrics["max_drawdown"],
                        "total_trades": metrics["total_trades"],
                        "status": "ok",
                    })
                except Exception as exc:  # noqa: BLE001 —— 执行期单组容错
                    # ResearchError 取 code、其余取类名（单 except 收敛：
                    # 两分支仅 failure_code 取法不同）
                    row.update({"status": "failed",
                                "failure_code": (getattr(exc, "code", None)
                                                 or type(exc).__name__)})
                    failed_groups.append({**row})
                # 三道门 state 构造谓词（回测口径）
                if row.get("status") == "ok":
                    row["state"] = (
                        "effective"
                        if (row["total_trades"] >= _MIN_TRADES
                            and row["net_expectation"] is not None
                            and row["net_expectation"] > 0)
                        else "ineffective")
                results.append(row)

    # 三道门判定：每模式平面内两轴各自游程判定 → 两轴最差 → 跨模式
    # 平面最差（同序）；plateau 附各轴稳健区间
    ok_rows = [r for r in results if r.get("status") == "ok"]
    robust_ranges: Dict[str, Dict[str, Any]] = {}
    overall: Optional[str] = None
    single_value_axes: List[str] = []
    for entry in modes:
        plane_rows = [r for r in ok_rows if r["mode"] == entry["mode"]]
        plane_verdict: Optional[str] = None
        plane_info: Dict[str, Any] = {}
        for axis in ("stop_loss", "take_profit"):
            axis_values = sorted({r[axis] for r in plane_rows}) \
                if plane_rows else []
            if len(axis_values) == 1:
                single_value_axes.append(f"{entry['mode']}.{axis}")
            verdict, robust = _axis_verdict(plane_rows, axis)
            plane_info[axis] = {"verdict": verdict, "robust_range": robust}
            plane_verdict = verdict if plane_verdict is None else _worst(
                plane_verdict, verdict)
        if plane_verdict is not None:
            robust_ranges[entry["mode"]] = {
                "verdict": plane_verdict, "axes": plane_info}
            overall = plane_verdict if overall is None else _worst(
                overall, plane_verdict)
    # overall 无 None 兜底：modes 恒非空（grid 校验①拒空）且
    # _gates_verdict 恒返非 None verdict——全组执行失败的 indeterminate
    # 由 _gates_verdict 空输入语义承载（无参评点 → indeterminate）

    payload: Dict[str, Any] = {
        "direction": direction,
        "results": results,
        "verdict": overall,
        "robust_ranges": robust_ranges,
        "gates": {
            "min_trades": _MIN_TRADES,
            "min_run": 2,
            "effectiveness": "net_expectation_after_fees>0",
            "axes": ["stop_loss", "take_profit"],
            "aggregation": "worst",
        },
        "warmup_bars": warmup_bars,
    }
    if conditions is not None:
        payload["conditions"] = [dict(c) for c in conditions]
    else:
        payload["condition"] = dict(condition)
    if failed_groups:
        payload["failed_groups"] = failed_groups
    if single_value_axes:
        # 单轴长度 1 推论（披露义务）：该轴游程 ≥2 恒不可满足、恒不能成
        # 平台——「单点无法证明稳健」的判定语义而非可疑信号（固定止损
        # 只扫止盈的用法下整体结论恒不超过 spike 属预期行为）
        payload["single_value_axes"] = single_value_axes

    # 三要素（sample_size=成功组数；min_samples=0——充分性由三道门承载
    # 不走通用小样本拒绝；hypothesis_id 传 None——本模板不挂假设 D2）
    return build_result(
        "exit_sweep",
        sample_size=len(ok_rows),
        confidence={"method": "gates_verdict",
                    "level": 0.05,
                    "verdict": overall,
                    "for": "platform_robustness"},
        data_ranges=[request_range],
        payload=payload,
        min_samples=0,
        hypothesis_id=None,
    ).to_dict()
