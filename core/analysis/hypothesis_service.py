"""假设登记与迁移用例（add-ai-research-mcp 3.1）。

登记校验链（顺序即拒绝优先级）：
1. conditions 结构（WHEN/THEN 形制，逐条 {subject, action, target}）
   → 422 RESEARCH_CONDITION_INVALID（附位置）
2. subject 引用必须落在可用信号集（OHLCV/衍生品基础列 ∪ 内置指标
   输出列形制 ∪ 插件列）→ 422 RESEARCH_CONDITION_INVALID
3. data_ranges 非空且每条 {symbol, timeframe, start, end} 半开
   → 422 RESEARCH_RANGE_INVALID；symbol/timeframe 在数据目录
   → 422 RESEARCH_SYMBOL_NOT_FOUND
3.5 trigger_policy 形式校验与归一（非 None 写 conditions 顶层键）
3.6 strategy_params 声明块校验（0.4.2 R1：键白名单+值域按 sl_mode
   分叉，先于幂等——归一形字面值参与四分支比对）
   → 422 RESEARCH_PARAM_INVALID（detail 含 field/legal_range）
4. 幂等键 (conditions_hash, ranges_hash) WHERE active：重复登记四分支
   （键缺失/显式{}/带键全等→duplicated；带键不等→422 防静默丢弃
   既有声明——strategy-param-declaration 规格）
4.5 谱系引用校验（provenance）
4.6 登记自动预检（失败降级不阻断）
5. 原子写（含声明块列）

迁移（transition）：active --证据/接替ref--> supported|refuted|superseded；
终态拒绝（409 RESEARCH_HYPOTHESIS_TERMINAL）；条件更新先到者胜
（rowcount 0 → 重查判读：同目标幂等 / 异终态 409）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.analysis.errors import ResearchError   # re-export 兼容（见类原位注释）
from core.analysis.isolation import iter_all_pages
from core.persistence.repository import research as rr
from core.persistence.timestamps import utc_now_iso

logger = logging.getLogger(__name__)


# ResearchError re-export 兼容声明（CARD-02 轻量化）：唯一权威定义在
# core/analysis/errors.py（本模块顶层 import 链含 sqlalchemy/persistence，
# 轻消费方 import ResearchError 被迫负重加载）；模块头 import 使
# hypothesis_service.ResearchError 与 errors.ResearchError 为同一类对象
# ——既有全域 ``from core.analysis.hypothesis_service import ResearchError``
# 面（38+ 文件）零适配，isinstance/except 同身份。


def hash_json(obj: Any) -> str:
    """对象 → 稳定 sha256 指纹（json 键序归一 + 非 ASCII 保真）。

    幂等身份键的统一底座（假设 conditions/ranges_hash、模型插件
    config_hash 共用）——同对象恒同指纹，跨进程可复现。
    """
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------- 信号集

# 基础列清单权威在 column_ref（0.2.13 移入——单一事实源）
from core.analysis.column_ref import BASE_SUBJECT_COLUMNS as _BASE_SUBJECT_COLUMNS


def allowed_subject_columns(db_path: Path) -> Tuple[set, set]:
    """subject 可引用列：(精确列全集, 内置指标输出列前缀集)。

    0.2.13 起主体校验走 column_ref.resolve_column_ref（结构化混合形制）；
    本函数保留供翻译器拒绝分支的清单查询与插件域（前缀集的校验消费
    已随 R6 退役）。插件列来自 signal_registry（精确名）。
    """
    from core.features.registry import INDICATOR_REGISTRY

    cols = set(_BASE_SUBJECT_COLUMNS)
    builtin_prefixes = set()
    for defn in INDICATOR_REGISTRY.values():
        for f in defn.output_fields:
            builtin_prefixes.add(f)

    for entry in rr.list_signal_registry_entries(db_path, only_visible=True):
        cols.add(entry["column_name"])
    return cols, builtin_prefixes


# ---------------------------------------------------------------- 登记

# strategy_params 键集白名单（0.4.2 R1=风险五参数；R3 任务 4.1 扩至
# 八键——exit_fraction/add_fraction/max_exposure 启用域校验与宿主组
# 配对，未知键状态退出）
_STRATEGY_PARAM_KEYS = frozenset({
    "stop_loss", "take_profit", "sl_mode", "position_size", "leverage",
    "exit_fraction", "add_fraction", "max_exposure", "max_hold",
    "time_exit_utc", "position_mode", "risk_fraction",
})

# 分批组键（0.4.2 R3/DC-2 平铺顶层组键——进 conditions 参与身份指纹）
_SCALING_GROUP_KEYS = ("exit_partial_conditions", "add_trigger_conditions",
                       "add_suppress_conditions")

# 有组无值的缺省归一化字面（A5/DC-3：幂等与翻译比对按字面值）
_SCALING_DEFAULTS = {"exit_fraction": 0.5, "add_fraction": 0.1,
                     "max_exposure": 1.0}


def _validate_strategy_params(
    params: Dict[str, Any],
    conditions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """声明块校验（登记链 3.6 步核心；八键白名单+组间配对）。

    值域与 validator 同源引用（SL_MODES/分叉域/MAX_LEVERAGE 单源——
    DC-15 权威双点之一）；浮点值保持声明字面量原样返回（幂等四分支
    的比对=归一形字面值比对，不做数值变换）。

    组间配对（R3，与值域同链同码 RESEARCH_PARAM_INVALID）：数值键须有
    宿主组（exit_fraction←exit_partial_conditions；add_fraction/
    max_exposure←add_trigger_conditions；add_suppress 组←add_trigger
    组）；有组无值→归一化字面化写入（0.5/0.1/1.0——返回时补齐）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（detail 含 field/legal_range）
    """
    from core.strategy.dna import SL_MODES
    from core.strategy.validator import (
        MAX_HOLD_MAX, MAX_HOLD_MIN, MAX_LEVERAGE, SL_ATR_MAX, SL_ATR_MIN,
        SL_PCT_MAX, SL_PCT_MIN,
    )

    if not isinstance(params, dict):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID", "strategy_params 须为对象",
            detail={"field": "strategy_params",
                    "legal_range": "键集 " + "/".join(sorted(_STRATEGY_PARAM_KEYS))})

    # 显式 null 全链拒绝（review-042 CARD-01）：JSON null 与键缺失在
    # 校验层同为「无值」（值域检查 is not None 跳过），但 null 会落库
    # 穿透翻译层直写 RiskGenes——下游 falsy→0.0 止损静默禁用、live 链
    # float(None)/比较 None 崩溃。不声明请移除键，两种语义唯一化。
    null_keys = [k for k in params if params[k] is None]
    if null_keys:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID", "显式 null 非法（不声明请移除键）",
            detail={"field": f"strategy_params.{null_keys[0]}",
                    "null_keys": null_keys})

    unknown = sorted(set(params) - _STRATEGY_PARAM_KEYS)
    if unknown:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"strategy_params 含未知键: {', '.join(unknown)}",
            detail={"field": f"strategy_params.{unknown[0]}",
                    "unknown_keys": unknown,
                    "legal_range": "键集 " + "/".join(sorted(_STRATEGY_PARAM_KEYS))})

    sl_mode = params.get("sl_mode", "pct")
    if sl_mode not in SL_MODES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"sl_mode 须为 {SL_MODES} 之一: {sl_mode!r}",
            detail={"field": "sl_mode", "legal_range": list(SL_MODES)})

    # stop_loss/take_profit 值域按 sl_mode 分叉（量纲双关——同值异判）
    if sl_mode == "atr":
        lo, hi = SL_ATR_MIN, SL_ATR_MAX
    else:
        lo, hi = SL_PCT_MIN, SL_PCT_MAX

    stop_loss = params.get("stop_loss")
    if stop_loss is not None:
        if not isinstance(stop_loss, (int, float)) or isinstance(stop_loss, bool) \
                or not (lo <= stop_loss <= hi):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"stop_loss {stop_loss!r} 越出 sl_mode={sl_mode} 档值域 [{lo}, {hi}]",
                detail={"field": "stop_loss", "legal_range": [lo, hi],
                        "sl_mode": sl_mode})

    take_profit = params.get("take_profit")
    if take_profit is not None:
        if not isinstance(take_profit, (int, float)) or isinstance(take_profit, bool) \
                or not (lo <= take_profit <= hi):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"take_profit {take_profit!r} 越出 sl_mode={sl_mode} 档值域 [{lo}, {hi}]",
                detail={"field": "take_profit", "legal_range": [lo, hi],
                        "sl_mode": sl_mode})
        # TP>SL：trail 档豁免（回撤比例与固定止盈不构成盈亏比，A1）；
        # atr 档保留（倍数盈亏比有意义）
        if stop_loss is not None and sl_mode != "trail" \
                and take_profit <= stop_loss:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"take_profit ({take_profit}) 须大于 stop_loss ({stop_loss})"
                f"（sl_mode={sl_mode} 档不豁免）",
                detail={"field": "take_profit",
                        "legal_range": "> stop_loss",
                        "sl_mode": sl_mode})

    position_size = params.get("position_size")
    if position_size is not None:
        if not isinstance(position_size, (int, float)) or isinstance(position_size, bool) \
                or not (0.10 <= position_size <= 1.0):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"position_size {position_size!r} 越出值域 [0.10, 1.0]",
                detail={"field": "position_size", "legal_range": [0.10, 1.0]})

    leverage = params.get("leverage")
    if leverage is not None:
        if not isinstance(leverage, int) or isinstance(leverage, bool) \
                or not (1 <= leverage <= MAX_LEVERAGE):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"leverage {leverage!r} 越出值域 [1, {MAX_LEVERAGE}]",
                detail={"field": "leverage", "legal_range": [1, MAX_LEVERAGE]})

    # 时间出场声明（add-max-hold-exit D-1）：正整数 bar 数（最长持有
    # N 根 K 线的到期无条件平仓）；值域单源 validator（MAX_HOLD_MIN/
    # MAX——与 validate_dna 手工 DNA 校验双消费方同源）
    max_hold = params.get("max_hold")
    if max_hold is not None:
        if not isinstance(max_hold, int) or isinstance(max_hold, bool) \
                or not (MAX_HOLD_MIN <= max_hold <= MAX_HOLD_MAX):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"max_hold {max_hold!r} 越出值域 "
                f"[{MAX_HOLD_MIN}, {MAX_HOLD_MAX}]（正整数 bar 数）",
                detail={"field": "max_hold",
                        "legal_range": [MAX_HOLD_MIN, MAX_HOLD_MAX]})
    # 固定时刻出场（任务 6）：UTC 分钟时刻；值域单源 validator
    from core.strategy.validator import TIME_EXIT_UTC_MAX, TIME_EXIT_UTC_MIN
    time_exit = params.get("time_exit_utc")
    if time_exit is not None:
        if not isinstance(time_exit, int) or isinstance(time_exit, bool) \
                or not (TIME_EXIT_UTC_MIN <= time_exit <= TIME_EXIT_UTC_MAX):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"time_exit_utc {time_exit!r} 越出值域 "
                f"[{TIME_EXIT_UTC_MIN}, {TIME_EXIT_UTC_MAX}]"
                "（UTC 分钟时刻，0-1439）",
                detail={"field": "time_exit_utc",
                        "legal_range": [TIME_EXIT_UTC_MIN,
                                         TIME_EXIT_UTC_MAX]})

    # 动态定仓（complete-research-tooling 任务 4）：两键域校验+组合
    # 校验（值域单源 validator——RISK_FRACTION_MIN/MAX 同源引用）
    from core.strategy.validator import (  # noqa: F401  单源引用
        POSITION_MODES, RISK_FRACTION_MAX, RISK_FRACTION_MIN,
    )
    pm = params.get("position_mode")
    if pm is not None and pm not in POSITION_MODES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"position_mode {pm!r} 非法（合法值 {list(POSITION_MODES)}"
            "——fixed 固定比例/risk_budget 风险预算制定仓）",
            detail={"field": "position_mode",
                    "legal_range": list(POSITION_MODES)})
    rf = params.get("risk_fraction")
    if rf is not None:
        if not isinstance(rf, (int, float)) or isinstance(rf, bool) \
                or not (RISK_FRACTION_MIN < rf <= RISK_FRACTION_MAX):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"risk_fraction {rf!r} 越出值域 "
                f"({RISK_FRACTION_MIN}, {RISK_FRACTION_MAX}]"
                "（每笔风险预算占当時权益比例）",
                detail={"field": "risk_fraction",
                        "legal_range": f"({RISK_FRACTION_MIN}, "
                                       f"{RISK_FRACTION_MAX}]"})
    # 组合校验（沿组间配对族先例）：risk_budget 须 risk_fraction 与
    # stop_loss>0（止损距离是仓位公式分母——无止损=风险预算不可定义；
    # stop_loss 值域校验在前段已做，此处只核在场性）
    if pm == "risk_budget":
        if rf is None:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "position_mode=risk_budget 须同时声明 risk_fraction"
                "（入场 size=当時权益×risk_fraction÷止损距离）",
                detail={"field": "risk_fraction",
                        "legal_range": f"({RISK_FRACTION_MIN}, "
                                       f"{RISK_FRACTION_MAX}]"})
        if not params.get("stop_loss"):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "position_mode=risk_budget 须同时声明 stop_loss"
                "（止损距离是仓位公式分母——无止损=风险预算不可定义）",
                detail={"field": "stop_loss"})

    # ── 分批三键域校验+组间配对（R3，4.1 启用；抑制组依赖触发组的
    #    纯组间规则在登记链主体——不依赖声明块携带）──
    has_exit_group = isinstance(conditions, dict) and \
        "exit_partial_conditions" in conditions
    has_add_group = isinstance(conditions, dict) and \
        "add_trigger_conditions" in conditions

    for frac_key in ("exit_fraction", "add_fraction"):
        v = params.get(frac_key)
        if v is not None:
            if not isinstance(v, (int, float)) or isinstance(v, bool) \
                    or not (0.0 < v <= 1.0):
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"{frac_key} {v!r} 越出值域 (0, 1]",
                    detail={"field": frac_key, "legal_range": "(0, 1]"})

    effective_leverage = params.get("leverage", 1)
    max_exposure = params.get("max_exposure")
    if max_exposure is not None:
        if not isinstance(max_exposure, (int, float)) or isinstance(max_exposure, bool) \
                or not (0.0 < max_exposure <= effective_leverage):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"max_exposure {max_exposure!r} 越出生效杠杆上界 "
                f"(0, {effective_leverage}]",
                detail={"field": "max_exposure",
                        "legal_range": [0, effective_leverage],
                        "hint": "上限受 leverage（默认 1）约束，声明更大"
                                "上限须同时声明 leverage"})

    # 组间配对：数值键须有宿主组（声明比例而无触发组=悬空参数）
    if params.get("exit_fraction") is not None and not has_exit_group:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "exit_fraction 声明须伴随 exit_partial_conditions 组（比例"
            "无宿主触发组=悬空参数）",
            detail={"field": "exit_fraction",
                    "legal_range": "须与 exit_partial_conditions 组同在"})
    if (params.get("add_fraction") is not None or
            params.get("max_exposure") is not None) and not has_add_group:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "add_fraction/max_exposure 声明须伴随 add_trigger_conditions 组"
            "（比例/敞口无宿主触发组=悬空参数）",
            detail={"field": "add_fraction",
                    "legal_range": "须与 add_trigger_conditions 组同在"})

    # 有组无值→归一化字面化（A5/DC-3：幂等与翻译比对按字面值——
    # 缺省物化进声明块，与显式声明同形）
    normalized = dict(params)
    if has_exit_group and "exit_fraction" not in normalized:
        normalized["exit_fraction"] = _SCALING_DEFAULTS["exit_fraction"]
    if has_add_group:
        if "add_fraction" not in normalized:
            normalized["add_fraction"] = _SCALING_DEFAULTS["add_fraction"]
        if "max_exposure" not in normalized:
            normalized["max_exposure"] = _SCALING_DEFAULTS["max_exposure"]
    return normalized


def register_hypothesis(
    db_path: Path,
    *,
    conditions: Dict[str, Any],
    statement: str,
    data_ranges: List[Dict[str, str]],
    hypothesis_id: Optional[str] = None,
    provenance: Optional[Dict[str, Any]] = None,
    precheck_data_dir: Optional[Path] = None,
    trigger_policy: Optional[Dict[str, Any]] = None,
    strategy_params: Optional[Dict[str, Any]] = None,
    direction: Optional[str] = None,
) -> Dict[str, Any]:
    """登记假设（校验链 + 幂等 + 原子写）。

    provenance（add-screen-patterns D5）：{task_id, candidate_fingerprint}
    扫描谱系引用——task 须存在于任务表（screen_use 区间一律取自任务
    记录的 data_range，声明不供数）；不参与幂等键；不校验指纹归属
    （伪造指纹只会把无效区间拦进自己的判定并集，无作弊收益）。

    precheck_data_dir（add-hypothesis-precheck D6，Q1 裁 A）：非 None 时
    登记自动预检（canonical 化后的 when 条件 + data_ranges 多区间最差
    锚）；预检失败降级 logger.warning 不阻断登记，返回体不携带警告键；
    幂等命中不执行（早于插桩点 return）。服务唯一生产调用方是路由
    （MCP 经 HTTP 间接覆盖）；None 跳过——测试直调兼容。

    strategy_params（0.4.2 R1，strategy-param-declaration 规格）：策略层
    参数声明块（可选对象；显式空对象 {}=显式声明「无参数」，与键缺失
    语义不同）；不进 conditions/不进 conditions_hash（不参与身份指纹
    ——参数变体经终态后重登记产生）；幂等四分支详见 3.6 步与第 4 步。

    direction（add-direction-channel）：交易方向 {"long","short"}，
    缺省 None 与显式 "long" 语义相同（同指纹同实体）；归一形 short
    才写 conditions 顶层键——方向随 conditions_json 落库同源进身份
    指纹（同条件同区间异方向=两条独立假设，family/判定并集/谱系按
    hash 天然分族零改动）。

    Returns:
        {"hypothesis_id", "duplicated": bool, "precheck_warnings"?: dict,
         "existing_strategy_params"?: dict}
        ——duplicated=True 表示命中幂等键返回既有假设（provenance 不
        改变假设身份，幂等命中不补写；该路径天然不携带警告键）；
        precheck_warnings 仅预检成功时存在（纯增量键）；
        existing_strategy_params 仅 duplicated 路径携带（既有声明摘要，
        纯增量键——NULL 声明以 {} 呈现）。
    """
    # 1. conditions 结构（含分批组键同构校验——0.4.2 R3：组键可选，
    #    存在须非空（显式空列表拒绝），组内条目与 when/then 同构）
    when = conditions.get("when_conditions")
    then = conditions.get("then_conditions")
    if not isinstance(when, list) or not when:
        raise ResearchError("RESEARCH_CONDITION_INVALID", "when_conditions 须为非空列表",
                            detail={"field": "when_conditions"})
    if not isinstance(then, list) or not then:
        raise ResearchError("RESEARCH_CONDITION_INVALID", "then_conditions 须为非空列表",
                            detail={"field": "then_conditions"})
    group_conds = {"when": when, "then": then}
    for gk in _SCALING_GROUP_KEYS:
        if gk in conditions:
            gvals = conditions[gk]
            if not isinstance(gvals, list) or not gvals:
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{gk} 须为非空列表（组存在须非空——空组无语义）",
                    detail={"field": gk})
            group_conds[gk.replace("_conditions", "")] = gvals
    for group, conds in group_conds.items():
        for i, c in enumerate(conds):
            field_name = f"{group}_conditions[{i}]"
            if not isinstance(c, dict) or not all(k in c for k in ("subject", "action", "target")):
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{field_name} 须含 subject/action/target",
                    detail={"field": field_name})

    # 2. 区间（0.2.14：单周期收紧——全部条目同 symbol 同 timeframe，
    #    该周期即基准周期；timeframe 键的方向校验以它为基准）
    if not isinstance(data_ranges, list) or not data_ranges:
        raise ResearchError("RESEARCH_RANGE_INVALID", "data_ranges 须为非空列表")
    for i, r in enumerate(data_ranges):
        field_name = f"data_ranges[{i}]"
        if not isinstance(r, dict) or not all(k in r for k in ("symbol", "timeframe", "start", "end")):
            raise ResearchError("RESEARCH_RANGE_INVALID",
                                f"{field_name} 须含 symbol/timeframe/start/end",
                                detail={"field": field_name})
        if not (r["start"] < r["end"]):
            raise ResearchError("RESEARCH_RANGE_INVALID",
                                f"{field_name} 非半开区间（start >= end）",
                                detail={"field": field_name})
    _symbols = {r["symbol"] for r in data_ranges}
    _tfs = {r["timeframe"] for r in data_ranges}
    if len(_symbols) > 1 or len(_tfs) > 1:
        raise ResearchError(
            "RESEARCH_RANGE_INVALID",
            "data_ranges 须同 symbol 同 timeframe（基准周期唯一——"
            "混合周期区间在单周期求值语义下不可一致消费）",
            detail={"field": "data_ranges",
                    "symbols": sorted(_symbols), "timeframes": sorted(_tfs)})
    base_tf = data_ranges[0]["timeframe"]
    # 数据目录可达性在路由层校验（需 data_dir；此处只做结构）——见路由

    # 2.5 timeframe 键校验序（0.2.14 B3：THEN 拒 → 值域 → 方向）
    from core.data.timeframes import TF_SECONDS
    # TD-8（0.3.7）：基准周期对整个假设唯一——单次判定上提循环外
    # （原逐条件重复判；data_ranges 同 symbol/timeframe 已由 0.2.14 保证）
    if base_tf not in TF_SECONDS:
        raise ResearchError(
            "TIMEFRAME_UNKNOWN",
            f"未知基准周期: {base_tf!r}",
            detail={"field": "data_ranges[0].timeframe",
                    "timeframe": base_tf,
                    "legal_range": sorted(TF_SECONDS)})
    for group, conds in group_conds.items():
        for i, c in enumerate(conds):
            tf_key = c.get("timeframe")
            if tf_key is None:
                continue
            field_name = f"{group}_conditions[{i}].timeframe"
            # unblock-research-validation Q1 裁决：加仓触发组跟随 when
            # 侧放行（执行语义=基准轴基因级掩码投影，role 无关）；
            # then/exit_partial/add_suppress 维持拒绝
            if group not in ("when", "add_trigger"):
                raise ResearchError(
                    "TIMEFRAME_ON_THEN",
                    "THEN 条件为前向收益对照语义（forward_bars 以基准 bar "
                    "计），跨周期前向窗口归属未定义——不接受 timeframe 键"
                    "（仅 when 与加仓触发组支持）",
                    detail={"field": field_name, "timeframe": tf_key})
            if tf_key not in TF_SECONDS:
                raise ResearchError(
                    "TIMEFRAME_UNKNOWN",
                    f"未知周期: {tf_key!r}",
                    detail={"field": field_name, "timeframe": tf_key,
                            "legal_range": sorted(TF_SECONDS)})
            from core.data.mtf_alignment import assert_higher_tf
            try:
                assert_higher_tf(tf_key, base_tf)
            except ValueError as exc:
                raise ResearchError(
                    "TIMEFRAME_NOT_HIGHER", str(exc),
                    detail={"field": field_name, "timeframe": tf_key,
                            "base_timeframe": base_tf}) from exc

    # 3. subject 引用（0.2.13：混合形制统一校验 + canonical 化归一——
    #    数值形制与 field 缺省在入口统一，conditions_hash 对 canonical 形计算）；
    #    显式基准周期在此剥离（0.2.14：timeframe==base_tf 删键——显式
    #    基准与缺省同语义同指纹，幂等键字面连续的必要条件）
    from core.analysis.column_ref import ColumnRefError, resolve_column_ref
    for group, conds in group_conds.items():
        for i, c in enumerate(conds):
            field_name = f"{group}_conditions[{i}].subject"
            try:
                # base_tf/ref_tf 供给（任务 7 三面之一 + D-6② 引用周期
                # 语义——登记面取 data_ranges 基准周期与条件自身
                # timeframe 做域元数据校验，最严面；tf 剥离在 resolve
                # 之后，此处 c 的 timeframe 仍在场）
                c["subject"] = resolve_column_ref(
                    c["subject"], db_path, base_tf=base_tf,
                    ref_tf=c.get("timeframe")).ref_dict()
            except ColumnRefError as exc:
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{field_name} {exc.message}",
                    detail={"field": field_name, "code": exc.code,
                            **exc.detail}) from exc
            if c.get("timeframe") == base_tf:
                del c["timeframe"]

    # 3.5 trigger_policy 形制校验与归一（trigger-policy 卡2，D12 登记侧
    #     仅形制——bars 数据长度上界在求值时点）。归一非 None 时写入
    #     conditions 顶层键：此后指纹输入（D4——有效策略时 hash 对象
    #     含 trigger_policy 归一形）与 conditions_json 原子写（第 5 步
    #     json.dumps(conditions)）零改动自然同源；缺省与显式 none 归一
    #     为 None 不写键——与既有无策略假设同 hash 同身份（幂等键字面
    #     连续）。延迟 import：event_policy 依赖本模块 ResearchError，
    #     模块级互引成环（column_ref 同款函数内引用先例）。
    from core.analysis.event_policy import validate_policy
    normalized_policy = validate_policy(trigger_policy)
    if normalized_policy is not None:
        conditions["trigger_policy"] = normalized_policy

    # 组间依赖（0.4.2 R3，纯组间规则——无论请求是否携带声明块键）：
    # 抑制组语义依附加仓触发，有抑制无触发=抑制悬空
    if "add_suppress_conditions" in conditions and \
            "add_trigger_conditions" not in conditions:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "add_suppress_conditions 组须伴随 add_trigger_conditions 组"
            "（抑制语义依附加仓触发）",
            detail={"field": "add_suppress_conditions",
                    "legal_range": "须与 add_trigger_conditions 组同在"})

    # 3.6 strategy_params 声明块校验（0.4.2 R1/R3：先于幂等——归一形
    #     字面值参与四分支比对（有组无值在此字面化补齐）；坏值 422
    #     阻断登记。{} 与键缺失在此分流：{} 进入全等比对（显式声明
    #     「无参数」），None 不参与比对。组存在而未带声明块键：分批
    #     数值物化字面值（spec「有组无值缺省归一化」——落库与比对
    #     按字面值；幂等分支判定仍按请求是否带键的原始标志）
    params_declared = strategy_params is not None
    if params_declared:
        strategy_params = _validate_strategy_params(
            strategy_params, conditions)
    elif any(k in conditions for k in _SCALING_GROUP_KEYS):
        # 有组无值→字面化与 _validate_strategy_params 尾部归一化同源
        # （review-042 CARD-05 双份收敛）：{} 无未知键/null 键、sl_mode
        # 缺省过检，宿主组判据同 conditions——返回即物化形，与原内联
        # 构造行为逐位等价
        strategy_params = _validate_strategy_params({}, conditions)

    # 3.7 交易方向校验与归一（add-direction-channel）：值域权威校验在
    #     本步（REST/MCP 两面穿透到服务层同一校验点；非字符串值在路由
    #     层已被 pydantic string_type 拦截，服务层直调面由 not in 集合
    #     判定对跨类型值天然成立）。归一形 short 才写 conditions 顶层
    #     键（trigger_policy 同款）——写键先于第 4 步 hash 计算，指纹
    #     输入与 conditions_json 落库同源；None/显式 "long" 一律不写
    #     键（存量 conditions 字面与 hash 逐位不变）
    if direction is not None and direction not in ("long", "short"):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"direction 须为 long|short 之一: {direction!r}",
            detail={"field": "direction", "legal_range": ["long", "short"]})
    if direction == "short":
        conditions["direction"] = "short"

    # 4. 幂等（四分支——strategy-param-declaration 规格）
    conditions_hash = hash_json(conditions)
    ranges_hash = hash_json(data_ranges)
    existing = rr.find_active_hypothesis_by_identity(db_path, conditions_hash, ranges_hash)
    if existing is not None:
        # 既有声明块（列 NULL 视为 {}——与显式空对象全等）
        raw = existing.get("strategy_params")
        try:
            existing_params = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            existing_params = {}
        # 方向回显（add-direction-channel）：读既有假设 conditions 顶层
        # 键（存量无键=long）；identity 查询返回全行 dict，conditions 在场。
        # 值域收敛（security S-1）：脏值不反射——与写入侧归一形口径对称
        try:
            _existing_direction = json.loads(
                existing["conditions"]).get("direction", "long") \
                if existing.get("conditions") else "long"
        except (json.JSONDecodeError, TypeError):
            _existing_direction = "long"
        if _existing_direction not in ("long", "short"):
            _existing_direction = "long"
        if not params_declared:
            # 分支①：请求键缺失——不参与声明比对（旧调用方零适配；
            # 组存在时的字面化物化值不改变「未带键」的比对豁免）
            return {
                "hypothesis_id": existing["hypothesis_id"],
                "duplicated": True,
                "direction": _existing_direction,
                "existing_strategy_params": existing_params,
            }
        # 分支②③：带键（含显式 {}）→ 归一形字面值全等比对
        req_norm = json.dumps(strategy_params, sort_keys=True,
                              ensure_ascii=False)
        exist_norm = json.dumps(existing_params, sort_keys=True,
                                ensure_ascii=False)
        if req_norm == exist_norm:
            return {
                "hypothesis_id": existing["hypothesis_id"],
                "duplicated": True,
                "direction": _existing_direction,
                "existing_strategy_params": existing_params,
            }
        # 不等：显式拒绝（防静默丢弃既有声明），detail 附既有摘要与
        # 变体路径指引（参数变体经终态后重登记产生）
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "strategy_params 与既有假设声明块不等（防静默丢弃既有声明）",
            detail={
                "field": "strategy_params",
                "existing_strategy_params": existing_params,
                "slow_path": "假设终态后带新声明重登记产出参数变体"
                             "（同条件集+同区间集=同一研究主张，参数"
                             "变体是新假设实体）",
            })

    # 4.5 谱系引用校验（区间事实取自任务记录——D5；GAP-10 泛化两类同核）
    screen_use: Optional[Dict[str, str]] = None
    if provenance is not None:
        # ref_type 归一（GAP-10）：旧两键形制无 ref_type 补默认
        # screen_candidate（与 AnalysisProvenance 模型层同语义——直调
        # dict 形式与 API 层模型双入口一致）；非法值拒附值域
        _LEGAL_REF_TYPES = ("screen_candidate", "analysis_task")
        _ref_type = provenance.get("ref_type", "screen_candidate") \
            if isinstance(provenance, dict) else None
        if _ref_type not in _LEGAL_REF_TYPES:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"provenance.ref_type 非法: {_ref_type!r}",
                detail={"field": "provenance.ref_type",
                        "legal_range": list(_LEGAL_REF_TYPES)})
        if not (isinstance(provenance, dict) and provenance.get("task_id")):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "provenance 须含 task_id",
                detail={"field": "provenance"})
        # 指纹条件性（服务层兜底——API 层模型 after 校验同语义）：
        # screen_candidate 必填 / analysis_task 可选
        _fp = provenance.get("candidate_fingerprint")
        if _ref_type == "screen_candidate" and not _fp:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "ref_type=screen_candidate 须携带 candidate_fingerprint"
                "（候选定位语义；analysis_task 任务级引用方可省略）",
                detail={"field": "provenance.candidate_fingerprint"})
        task = rr.get_analysis_task(db_path, str(provenance["task_id"]))
        if task is None:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"provenance 引用的分析任务不存在: {provenance['task_id']}",
                detail={"field": "provenance.task_id",
                        "task_id": str(provenance["task_id"])})
        try:
            task_params = json.loads(task["params"]) if task.get("params") else {}
        except (json.JSONDecodeError, TypeError):
            task_params = {}
        # 两类同核：区间事实键=data_range（实测形制——网格/统计/预检任务
        # params 均为单对象 {symbol,timeframe,start,end}；查证记录
        # 2026-10-16 四任务实测一致）
        task_range = task_params.get("data_range")
        if not task_range or "start" not in task_range or "end" not in task_range:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"分析任务记录缺区间事实: {provenance['task_id']}",
                detail={"field": "provenance.task_id"})
        screen_use = {
            "ref_id": str(provenance["task_id"]),
            "data_ranges": json.dumps([task_range], ensure_ascii=False),
            # GAP-10 留痕：entry_type/summary 随泛化（两类统一
            # analysis_ref；消费方无 entry_type 硬过滤——路由层写入侧
            # 家族互斥已核）
            "entry_type": "analysis_ref",
            "summary": f"analysis reference: task {provenance['task_id']}"
                       f" (ref_type={_ref_type})",
        }

    # 4.6 登记自动预检（add-hypothesis-precheck D6：Q1 裁 A——全登记路径
    #     含谱系回链的程序化登记）。插桩位置=全部校验（1/2/2.5/3/4/4.5）
    #     之后、原子写之前：校验链错误码序不被预检吞掉，canonical 条件
    #     在手，幂等命中已在第 4 步早退（不跑预检）。只读（写事务外）
    #     尽力而为：失败降级 logger.warning，登记照常。对预检模块的
    #     引用必须函数内延迟 import——模块级 import 经 templates 聚合包
    #     （condition_stats→condition_kernel→本模块延迟引用）形成加载
    #     期环（design「五裁决连锁落地」节环规避依据）。
    precheck_warnings = None
    if precheck_data_dir is not None:
        try:
            from core.analysis.templates.precheck_hypothesis import (
                run_registration_precheck,
            )
            # 分批组级密度披露（0.4.2 R3/A4：组进 per_range 新键，不进锚）
            _scaling_groups = {k: conditions[k] for k in _SCALING_GROUP_KEYS
                               if conditions.get(k)}
            precheck_warnings = run_registration_precheck(
                db_path, precheck_data_dir, when, data_ranges,
                trigger_policy=normalized_policy,
                scaling_groups=_scaling_groups or None)
        except Exception as exc:  # noqa: BLE001 —— 降级语义：预检失败不阻断登记
            logger.warning("登记预检降级（登记照常）: %s", exc)

    # 4.7 阈值单位可疑警告（add-threshold-unit-warning C-2）：小数比率
    #     语义列的 |target|>1 → 纯增量警告键（不阻断登记——个别指标
    #     大阈值可能合法，误报即灾难取向的保守观察面）。只在新建路径
    #     计算与携带（幂等命中已在第 4 步早退）；判定全程只读（不改
    #     conditions/不进 hash）；异常按预检降级先例吞掉——警告通道
    #     自身不得成为登记失败源。独立于 precheck_warnings（语义正交
    #     ——静态参数检查 vs 数据预检三态，并入则预检降级吞掉静态警告）
    threshold_unit_warnings = None
    try:
        from core.analysis.column_ref import encode_column_ref
        from core.analysis.registry_meta import is_decimal_ratio_subject
        _unit_warnings = []
        for group, conds in group_conds.items():
            for i, c in enumerate(conds):
                subject = c.get("subject")
                if not is_decimal_ratio_subject(subject):
                    continue
                if c.get("action") in ("price_above", "price_below"):
                    continue  # target 为占位不进比较
                try:
                    target_val = float(c.get("target"))
                except (TypeError, ValueError):
                    continue  # 列名/序列引用形 target 豁免
                if abs(target_val) > 1.0:
                    column = (encode_column_ref(
                        subject["indicator"], subject["params"],
                        subject["field"]) if isinstance(subject, dict)
                        else str(subject))
                    _unit_warnings.append({
                        "field": f"{group}_conditions[{i}]",
                        "column": column,
                        "target": target_val,
                        "hint": "该列为小数比率语义（值域量级以 1 为界），"
                                "阈值绝对值大于 1 疑似单位误写（如 -2.33 "
                                "应为 -0.0233）；警告不阻断登记，请核对"
                                "单位后自行决定是否重登",
                    })
        if _unit_warnings:
            threshold_unit_warnings = _unit_warnings
    except Exception as exc:  # noqa: BLE001 —— 降级语义：警告失败不阻断登记
        logger.warning("阈值单位警告判定降级（登记照常）: %s", exc)

    # 5. 原子写（provenance 存在时同事务追加 screen_use 日志；声明块
    #    归一形 JSON 串入列——None=无声明列 NULL）
    import uuid
    hid = hypothesis_id or f"hyp_{uuid.uuid4().hex[:12]}"
    rr.save_hypothesis_with_log(
        db_path,
        hypothesis_id=hid,
        conditions=json.dumps(conditions, ensure_ascii=False),
        conditions_hash=conditions_hash,
        statement=statement,
        data_ranges=json.dumps(data_ranges, ensure_ascii=False),
        ranges_hash=ranges_hash,
        strategy_params=(
            json.dumps(strategy_params, sort_keys=True, ensure_ascii=False)
            if strategy_params is not None else None),
        screen_use=screen_use,
    )
    out = {"hypothesis_id": hid, "duplicated": False,
           # 方向回显（归一形——None/显式 long 均回显 "long"）
           "direction": direction or "long"}
    if threshold_unit_warnings is not None:
        out["threshold_unit_warnings"] = threshold_unit_warnings
    if precheck_warnings is not None:
        out["precheck_warnings"] = precheck_warnings   # 纯增量键（成功时）
        # 挂接预检留痕（CARD-03，spec R4「预检执行 MUST 自动落研究日志」
        # 的登记路径补齐）：entry_type=precheck 不挂假设（spec 明文——
        # 预检时假设尚未存在，结构性不进判定并集）；ref_id 区分双锚来源
        # （registration_precheck vs 显式路径 precheck_hypothesis）；
        # summary 携判定锚与逐区间明细（design「返回体精简、详细留痕」
        # 分工——审计走留痕）。留痕尽力而为（D15 降级先例）：写入失败
        # 不反噬登记主通道。
        try:
            rr.append_research_log(
                db_path,
                hypothesis_id=None,
                entry_type="precheck",
                ref_id="registration_precheck",
                data_ranges=json.dumps(data_ranges, ensure_ascii=False),
                summary=json.dumps({
                    "verdict": precheck_warnings.get("verdict"),
                    "analysis_segment_trades":
                        precheck_warnings.get("analysis_segment_trades"),
                    "per_range": precheck_warnings.get("per_range"),
                }, ensure_ascii=False, default=str),
            )
        except Exception as exc:  # noqa: BLE001 —— 留痕尽力而为，不反噬登记
            logger.warning("登记预检留痕写入失败: %s", exc)
    return out


# ---------------------------------------------------------------- 迁移

# abandoned 为免证据的主动放弃终态（区别于被证据驳倒的 refuted）
_TERMINAL = ("supported", "refuted", "superseded", "abandoned")
_VALID_TARGETS = _TERMINAL

# 证据 verdict 匹配：supported 须 pass、refuted 须 fail（M4 协议修复）
_REQUIRED_VERDICT = {"supported": "pass", "refuted": "fail"}


def transition_hypothesis(
    db_path: Path,
    hypothesis_id: str,
    *,
    target_status: str,
    evidence_ref: str,
) -> Dict[str, Any]:
    """状态迁移：证据/verdict/接替引用校验 + 条件更新 + 幂等 + 原子转正。

    supported 时谱系 draft 满足独立证据条件（自身有挂
    (hypothesis_id, draft) 的 pass 记录）则同事务转正。

    Returns:
        {"hypothesis_id", "status", "idempotent": bool,
         "promoted": bool, "promoted_strategy_id": Optional[str],
         "promotion_note": Optional[str]}
    """
    if target_status not in _VALID_TARGETS:
        raise ResearchError("RESEARCH_TRANSITION_INVALID",
                            f"非法目标态: {target_status}（合法: {_VALID_TARGETS}）")
    if not evidence_ref:
        raise ResearchError("RESEARCH_EVIDENCE_NOT_FOUND", "迁移必带证据引用")

    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND", f"假设不存在: {hypothesis_id}",
                            status=404)

    # 终态检查先于证据校验：终态假设立即拒绝（幂等判断同块），
    # 不被"证据记录不存在"的 404 掩盖终态事实
    if hyp["status"] in _TERMINAL:
        if hyp["status"] == target_status and hyp["terminal_ref"] == evidence_ref:
            return {"hypothesis_id": hypothesis_id, "status": target_status,
                    "idempotent": True, "promoted": False,
                    "promoted_strategy_id": None, "promotion_note": None}
        raise ResearchError("RESEARCH_HYPOTHESIS_TERMINAL",
                            f"假设已终态（{hyp['status']}），不可再迁移",
                            status=409)

    # 证据引用真实性：superseded 指接替假设；supported/refuted 指验证记录
    # （verdict 必须与目标态匹配——fail 记录不得迁 supported）；
    # abandoned 免证据（evidence_ref 为放弃说明文本，落日志）
    if target_status == "superseded":
        if rr.get_hypothesis(db_path, evidence_ref) is None:
            raise ResearchError("RESEARCH_EVIDENCE_NOT_FOUND",
                                f"接替假设不存在: {evidence_ref}")
    elif target_status in _REQUIRED_VERDICT:
        # 记录分页取全量：单页 200 截断会让早期记录缺席证据核验（CARD-01）
        records = list(iter_all_pages(
            lambda off, lim: rr.list_validation_records(
                db_path, hypothesis_id=hypothesis_id, offset=off, limit=lim)))
        rec = next((r for r in records if r["record_id"] == evidence_ref), None)
        if rec is None:
            raise ResearchError(
                "RESEARCH_EVIDENCE_NOT_FOUND",
                f"证据验证记录不存在或不属于该假设: {evidence_ref}")
        need = _REQUIRED_VERDICT[target_status]
        if rec["verdict"] != need:
            raise ResearchError(
                "RESEARCH_TRANSITION_INVALID",
                f"verdict 不匹配：{target_status} 须引用 verdict={need} 的记录，"
                f"而 {evidence_ref} 为 {rec['verdict']}")

    result = rr.transition_hypothesis_with_promotion(
        db_path, hypothesis_id,
        target_status=target_status,
        terminal_ref=evidence_ref,
        superseded_by=evidence_ref if target_status == "superseded" else None,
        promote=(target_status == "supported"),
    )
    if result["rows"] == 0:
        # 并发竞争：另一请求先迁移——重查判读
        fresh = rr.get_hypothesis(db_path, hypothesis_id)
        if fresh is not None and fresh["status"] == target_status:
            return {"hypothesis_id": hypothesis_id, "status": target_status,
                    "idempotent": True, "promoted": False,
                    "promoted_strategy_id": None, "promotion_note": None}
        raise ResearchError("RESEARCH_HYPOTHESIS_TERMINAL",
                            f"并发迁移冲突：假设现为 {fresh['status'] if fresh else '缺失'}",
                            status=409)
    return {"hypothesis_id": hypothesis_id, "status": target_status,
            "idempotent": False,
            "promoted": result["promoted"],
            "promoted_strategy_id": result["promoted_strategy_id"],
            "promotion_note": result["promotion_note"]}
