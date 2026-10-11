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

# strategy_params 键集白名单（0.4.2 R1 阶段=风险五参数；R3 任务 4.1 扩至
# 八键——exit_fraction/add_fraction/max_exposure 三键启用域校验与宿主组
# 配对，届时扩展本常量即可，校验规则同点启用）
_STRATEGY_PARAM_KEYS = frozenset({
    "stop_loss", "take_profit", "sl_mode", "position_size", "leverage",
})


def _validate_strategy_params(
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """声明块校验（登记链 3.6 步核心；R1 口径=五参数白名单）。

    值域与 validator 同源引用（SL_MODES/分叉域/MAX_LEVERAGE 单源——
    DC-15 权威双点之一）；浮点值保持声明字面量原样返回（幂等四分支
    的比对=归一形字面值比对，不做数值变换）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（detail 含 field/legal_range）
    """
    from core.strategy.dna import SL_MODES
    from core.strategy.validator import (
        MAX_LEVERAGE, SL_ATR_MAX, SL_ATR_MIN, SL_PCT_MAX, SL_PCT_MIN,
    )

    if not isinstance(params, dict):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID", "strategy_params 须为对象",
            detail={"field": "strategy_params",
                    "legal_range": "键集 " + "/".join(sorted(_STRATEGY_PARAM_KEYS))})

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

    return params


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

    Returns:
        {"hypothesis_id", "duplicated": bool, "precheck_warnings"?: dict,
         "existing_strategy_params"?: dict}
        ——duplicated=True 表示命中幂等键返回既有假设（provenance 不
        改变假设身份，幂等命中不补写；该路径天然不携带警告键）；
        precheck_warnings 仅预检成功时存在（纯增量键）；
        existing_strategy_params 仅 duplicated 路径携带（既有声明摘要，
        纯增量键——NULL 声明以 {} 呈现）。
    """
    # 1. conditions 结构
    when = conditions.get("when_conditions")
    then = conditions.get("then_conditions")
    if not isinstance(when, list) or not when:
        raise ResearchError("RESEARCH_CONDITION_INVALID", "when_conditions 须为非空列表",
                            detail={"field": "when_conditions"})
    if not isinstance(then, list) or not then:
        raise ResearchError("RESEARCH_CONDITION_INVALID", "then_conditions 须为非空列表",
                            detail={"field": "then_conditions"})
    for group, conds in (("when", when), ("then", then)):
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
    for group, conds in (("when", when), ("then", then)):
        for i, c in enumerate(conds):
            tf_key = c.get("timeframe")
            if tf_key is None:
                continue
            field_name = f"{group}_conditions[{i}].timeframe"
            if group == "then":
                raise ResearchError(
                    "TIMEFRAME_ON_THEN",
                    "THEN 条件为前向收益对照语义（forward_bars 以基准 bar "
                    "计），跨周期前向窗口归属未定义——不接受 timeframe 键",
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
    for group, conds in (("when", when), ("then", then)):
        for i, c in enumerate(conds):
            field_name = f"{group}_conditions[{i}].subject"
            try:
                c["subject"] = resolve_column_ref(
                    c["subject"], db_path).ref_dict()
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

    # 3.6 strategy_params 声明块校验（0.4.2 R1：先于幂等——归一形字面
    #     值参与四分支比对；坏值 422 阻断登记。{} 与键缺失在此分流：
    #     {} 进入全等比对（显式声明「无参数」），None 不参与比对）
    if strategy_params is not None:
        strategy_params = _validate_strategy_params(strategy_params)

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
        if strategy_params is None:
            # 分支①：请求键缺失——不参与声明比对（旧调用方零适配）
            return {
                "hypothesis_id": existing["hypothesis_id"],
                "duplicated": True,
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

    # 4.5 谱系引用校验（区间事实取自任务记录——D5）
    screen_use: Optional[Dict[str, str]] = None
    if provenance is not None:
        if not (isinstance(provenance, dict)
                and provenance.get("task_id")
                and provenance.get("candidate_fingerprint")):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "provenance 须含 task_id 与 candidate_fingerprint",
                detail={"field": "provenance"})
        task = rr.get_analysis_task(db_path, str(provenance["task_id"]))
        if task is None:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"provenance 引用的扫描任务不存在: {provenance['task_id']}",
                detail={"field": "provenance.task_id",
                        "task_id": str(provenance["task_id"])})
        try:
            task_params = json.loads(task["params"]) if task.get("params") else {}
        except (json.JSONDecodeError, TypeError):
            task_params = {}
        task_range = task_params.get("data_range")
        if not task_range or "start" not in task_range or "end" not in task_range:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"扫描任务记录缺区间事实: {provenance['task_id']}",
                detail={"field": "provenance.task_id"})
        screen_use = {
            "ref_id": str(provenance["task_id"]),
            "data_ranges": json.dumps([task_range], ensure_ascii=False),
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
            precheck_warnings = run_registration_precheck(
                db_path, precheck_data_dir, when, data_ranges,
                trigger_policy=normalized_policy)
        except Exception as exc:  # noqa: BLE001 —— 降级语义：预检失败不阻断登记
            logger.warning("登记预检降级（登记照常）: %s", exc)

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
    out = {"hypothesis_id": hid, "duplicated": False}
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
