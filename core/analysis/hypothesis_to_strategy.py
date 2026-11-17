"""假设 → 策略转化器（add-ai-research-mcp 6.4 / improve-mcp-agent-loop M4）。

草稿-转正两段生命周期（信任链不跳级保留在转正门）：
- translate 产 **draft**（不要求 pass 前置）——draft 即隔离验证对象
  （验证的就是将来出货的确切 DNA）；仅 active 假设可转化（终态拒绝）；
- WHEN 条件 → 入场信号（ENTRY_TRIGGER），THEN 条件按"预期兑现即离场"
  语义 → 出场信号（EXIT_TRIGGER）——两者同规则，不可表达均显式拒绝；
- 转正（draft→active）只经 transition(supported) 的原子路径，不经
  本转化器与 PUT（受保护字段锁死）；
- 幂等：谱系中 draft 产物存在即返回；同签名既有策略显式分流
  （active→same_dna_existing / draft→幂等），MUST NOT 静默不落库。

其他翻译纪律不变：conditions JSON + 策略层参数声明块为翻译源
（0.4.2 起「conditions 为唯一翻译源」显式扩展——声明块映射产物
risk_genes，NULL/空=平台默认逐位兼容）；可表达子集与拒绝原因
（跨币种/基础列/插件列/分布形态）；谱系回链 parent_ids。
"""

# 【retire-legacy-subsystems D-3.6】假设强制流程退役（2026-11-16）：translate_hypothesis
# 落库编排、披露系六符号、查重/等价/回执族、公式试算族已随假设链退役删除。
# 保留翻译内核（build_signal_genes/_translate_conditions 及闭包常量）——
# 保留面模板 exit_sweep 实调（条件组→内存 DNA 纯翻译入口）。
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.analysis.errors import ResearchError, load_stored_json  # 轻量定义点直取（errors.py 唯一权威）
from core.persistence.repository import research as rr

# risk_genes 完整键集（15 键，D2/DC-9 谱系）：TD-14 起分流判定已由
# _dna_equivalent 全量基因型复核承担（risk_genes 含于 to_dict 比对集，
# 比对职责被吸收），本常量的运行时消费随之退役；保留供测试锚消费
# ——test_max_hold_declaration/test_time_exit/test_dynamic_position_
# sizing 以它断言 risk_genes 键集完整性（声明块映射无丢键）
_D2_RISK_FIELDS = (
    "stop_loss", "take_profit", "sl_mode", "position_size", "leverage",
    "exit_fraction", "add_fraction", "max_exposure", "atr_period",
    "max_hold", "time_exit_utc", "position_mode", "risk_fraction",
    "initial_fraction", "max_position_loss",
)

_ACTION_MAP = {
    "gt": lambda t: {"type": "gt", "threshold": float(t)},
    "lt": lambda t: {"type": "lt", "threshold": float(t)},
    "ge": lambda t: {"type": "ge", "threshold": float(t)},
    "le": lambda t: {"type": "le", "threshold": float(t)},
    "cross_above": lambda t: {"type": "cross_above", "threshold": float(t)},
    "cross_below": lambda t: {"type": "cross_below", "threshold": float(t)},
    "breakout": lambda t: {"type": "cross_above", "threshold": float(t)},
    "breakdown": lambda t: {"type": "cross_below", "threshold": float(t)},
    # 价格对指标列比较（fix-research-c2 TC-3，用户裁决 C2 附带补）：
    # 类型直通映射——回测面消费先例 executor.py evaluate_condition 的
    # price_above/price_below 分支（close vs indicator_series）现成。
    # target 为占位不进产物（与统计面占位契约对齐）、threshold 不参与。
    "price_above": lambda t: {"type": "price_above"},
    "price_below": lambda t: {"type": "price_below"},
    # 布尔 pattern 列相等（indicator-and-action-repair R1 卡 E，A1）：
    # 布尔 pattern 列（BullishDivergence 等）eq 1——threshold 缺省=1
    # 由 executor 求值层兜底（:133-135），登记显式携带则物化
    "eq": lambda t: {"type": "eq", "threshold": float(t)},
}

# target 占位直通动作集：这两键的比较主体固定 close，target（数值/
# 数值串/列名）一律占位忽略不报错——下方「target 须为数值」校验对
# 其豁免（统计面同款占位契约；与 C6 序列形正交——price_* 不消费
# target）。
_PASSTHROUGH_ACTIONS = frozenset({"price_above", "price_below"})

# 列名 target（编码指标列）→ 序列对照类型的映射（fix-research-c6，
# D-14 八键）：数值比较四族同名 type；穿越族与 breakout/breakdown
# 别名归一到 executor 既有序列类型（cross_above_series/
# cross_below_series）；不在表内的 action+列名 target 维持不可表达。
_SERIES_TYPE_MAP = {
    "gt": "gt", "lt": "lt", "ge": "ge", "le": "le",
    "cross_above": "cross_above_series",
    "cross_below": "cross_below_series",
    "breakout": "cross_above_series",
    "breakdown": "cross_below_series",
}

# 插件列条件 → formula/model 信号源的比较算子（仅数值比较可表达；
# cross/break 类穿越语义不适用于插件连续值源，显式拒绝——见
# _translate_conditions 的插件分支）
# 插件源角色映射（R2 卡 F 6.1）：基因级 SignalRole 的 value（组翻译
# 传入角色的 .value 形）→ 源级 role 键自有值域（组短名）。五值与
# 五组一一对应；未映射值兜底 entry（防御——新增 SignalRole 不漏炸）
_PLUGIN_SOURCE_ROLE = {
    "entry_trigger": "entry",
    "exit_trigger": "exit",
    "reduce_trigger": "exit_partial",
    "add_trigger": "add_trigger",
    "add_inhibit": "add_suppress",
}

_PLUGIN_ACTION_OPERATOR = {
    "gt": ">", "lt": "<", "ge": ">=", "le": "<=",
}


def _plugin_signal_columns(db_path: Path) -> Dict[str, str]:
    """注册表插件列 → source 类型（formula|model）；与登记域同源
    （allowed_subject_columns 的插件部分即 list_signal_registry_entries
    only_visible——同一查询，不复制第二份列清单）。

    插件列名为完整精确名（fx_xxx / fx_xxx_v2），无 ``_数字`` 参数后缀。
    """
    return {
        e["column_name"]: e["source"]
        for e in rr.list_signal_registry_entries(db_path, only_visible=True)
    }


def _guard_condition_timeframe(
    group: str, conds: List[Dict[str, Any]], base_tf: str,
) -> None:
    """放行组（when/add_trigger）timeframe 三步校验链（单点，两组共用）。

    unblock-research-validation A 段四组三放行域：
    ①键值 ∈ TF_SECONDS（TIMEFRAME_UNKNOWN——与登记面错误码族对齐）；
    ②高于或等于基准（assert_higher_tf 既有 >= 语义零改动）；
    ③tf==基准 → 条件键归一剥离（与登记面 canonical 化剥离同语义——
    主库明文「显式写基准周期与缺省语义完全相同（同指纹同求值）」，
    不写入基因=翻译面归一）；tf 严格高于基准 → 键保留在条件 dict，
    由 _translate_conditions 提取写入基因 timeframe 字段（执行链
    高周期轴求值+掩码投影）。
    翻译面校验为纵深防御（对绕过登记的库内/手工数据）——权威防线
    在登记面（TIMEFRAME_UNKNOWN/TIMEFRAME_NOT_HIGHER+canonical 化
    归一齐备）。
    """
    from core.data.timeframes import TF_SECONDS
    from core.data.mtf_alignment import assert_higher_tf

    for i, c in enumerate(conds):
        # null 值键=无标记（与登记面 c.get("timeframe") is None 同语义
        # ——显式 null 不视作标记，键保留但跳过校验）
        if not isinstance(c, dict) or c.get("timeframe") is None:
            continue
        tf_key = c["timeframe"]
        field_name = f"{group}_conditions[{i}].timeframe"
        if tf_key not in TF_SECONDS:
            raise ResearchError(
                "TIMEFRAME_UNKNOWN",
                f"未知周期: {tf_key!r}",
                detail={"field": field_name, "timeframe": tf_key,
                        "legal_range": sorted(TF_SECONDS)})
        try:
            assert_higher_tf(tf_key, base_tf)
        except ValueError as exc:
            raise ResearchError(
                "TIMEFRAME_NOT_HIGHER", str(exc),
                detail={"field": field_name, "timeframe": tf_key,
                        "base_timeframe": base_tf}) from exc
        if tf_key == base_tf:
            # 归一：显式基准=缺省语义（主库明文）——剥离键不写入基因
            c.pop("timeframe")


def _reject_condition_timeframe(group: str, conds: List[Dict[str, Any]]) -> None:
    """拒绝组（then/exit_partial/add_suppress）timeframe 键拒绝（单点）。"""
    for i, c in enumerate(conds):
        if isinstance(c, dict) and c.get("timeframe") is not None:
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"{group}_conditions[{i}] 携带 timeframe 键——"
                "THEN 条件为前向收益对照语义（forward_bars 以基准 bar "
                "计），跨周期前向窗口归属未定义（仅 when 与加仓触发组"
                "支持 timeframe 键）",
                detail={
                    "field": f"{group}_conditions[{i}].timeframe",
                    "timeframe": c.get("timeframe"),
                    "slow_path": "多周期标记仅进场侧（when 与加仓触发）"
                                 "支持；出场/减仓/抑制组请以基准周期条"
                                 "件表达"})


def build_signal_genes(
    when_conditions: List[Dict[str, Any]],
    then_conditions: List[Dict[str, Any]],
    db_path: Path,
    strategy_timeframe: Optional[str] = None,
) -> tuple[List, List[Dict[str, Any]]]:
    """不落库纯翻译入口（0.4.1 S4）——条件组 → (内置基因, 插件源)。

    timeframe 防线与翻译能力同点权威单源（unblock-research-validation
    A 段四组三放行域改造）：when 组放行（_guard_condition_timeframe
    三步校验+归一）、then 组维持拒绝（_reject_condition_timeframe）。
    translate_hypothesis（落库产 draft）与 exit_sweep（构造内存 DNA
    不落库）为两消费方——禁止第二套条件→信号基因逻辑。

    不落库、不查假设、不产 draft；THEN 空=空基因列表放行（离场全靠
    risk_genes——translate_hypothesis 既有先例）；插件列条件自然
    透传（不被新拒绝面拦——timeframe 校验经条件顶层键不触碰插件源）。

    strategy_timeframe 缺省 None=继承哨兵（fix-mtf-execution-
    consistency R2）——translate_hypothesis 在调用前完成继承解析传
    具体值；exit_sweep 等其余消费方显式传请求周期。None 直达（缺
    继承解析的调用）由下游 tf 校验/装载 fail-loud 兜底。
    """
    from core.strategy.dna import SignalRole

    # 四组三放行域（unblock-research-validation）：when 放行（校验链
    # +tf==base 归一），then 拒绝；add_trigger/exit_partial/add_suppress
    # 在 translate_hypothesis 分批组循环处置（S4 入口只承 when/then）
    _guard_condition_timeframe("when", when_conditions, strategy_timeframe)
    _reject_condition_timeframe("then", then_conditions)

    # THEN 按"预期兑现即离场"语义翻译为出场信号（与 WHEN 同规则；
    # R2 修订：插件列出场源放行——源级 role="exit"，组装器 exit 桶
    # AND 组合出场通道）
    when_genes, when_sources = _translate_conditions(
        when_conditions, db_path=db_path,
        label="when_conditions", role=SignalRole.ENTRY_TRIGGER,
        base_tf=strategy_timeframe)
    then_genes, then_sources = _translate_conditions(
        then_conditions, db_path=db_path,
        label="then_conditions", role=SignalRole.EXIT_TRIGGER,
        base_tf=strategy_timeframe)
    return when_genes + then_genes, when_sources + then_sources


def _translate_conditions(
    conditions: List[Dict[str, Any]], *, db_path: Path, label: str, role,
    base_tf: str = "",
) -> tuple[List, List[Dict[str, Any]]]:
    """条件 → (内置 SignalGene 列表, 插件信号源列表)。

    插件列条件不进入 rule 基因（signal_genes 只收内置条件），按条件
    阈值翻译为 formula/model 源；不可表达即拒（WHEN/THEN 同规则）。
    """
    from core.strategy.dna import SignalGene, SignalRole
    from core.analysis.column_ref import BASE_SUBJECT_COLUMNS

    genes = []
    plugin_sources: List[Dict[str, Any]] = []
    plugin_columns = _plugin_signal_columns(db_path)
    for i, cond in enumerate(conditions):
        subject = cond.get("subject", "")
        action = cond.get("action", "")
        target = cond.get("target")
        # 基因级周期提取（unblock-research-validation）：放行组条件顶层
        # timeframe 键经防线校验后写入基因第六属性（tf==base 已在防线
        # 归一剥离——到这里的非 None 值恒为严格高于基准）；基因 condition
        # dict 为重建产物不含该键（键剥离语义天然成立）
        cond_tf = cond.get("timeframe")

        # 结构化指标引用（0.2.13）：ColumnDef 直映射 SignalGene——
        # indicator/params 原样传递（多参指标天然支持），field 传语义名
        # （N4：executor 侧 resolve 的 field 值域是语义名 upper/signal/k…，
        # 编码前缀形在多输出指标非默认 field 会静默兜底取错列）
        if isinstance(subject, dict):
            from core.analysis.column_ref import ColumnRefError, resolve_column_ref
            try:
                # base_tf/ref_tf 供给（任务 7 三面之二 + D-6②——翻译面
                # 域校验与登记面同源；条件带 timeframe 时该 tf 为求值轴）
                cdef = resolve_column_ref(
                    subject, db_path, base_tf=base_tf or None,
                    ref_tf=cond_tf)
            except ColumnRefError as exc:
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{label}[{i}] subject 非法: {exc.message}",
                    detail={"condition": cond, "code": exc.code,
                            **exc.detail}) from exc
            if action in ("touch_bounce", "role_reversal"):
                # R1 翻译专门分支（indicator-and-action-repair 卡 E）：
                # 参数透传形制（direction/proximity_pct、role/lookback）
                # ——lambda 单参收不下，沿 within_bars_after 先例。
                # 不写键即用 executor 求值层缺省（A2：direction=
                # support/proximity_pct=0.01/role=resistance/lookback=
                # 10——翻译层不物化执行层缺省值，产物只含登记携带键）；
                # condition_kernel 全量透传（D-r2 登记侧 extra 键不丢）。
                if action == "touch_bounce":
                    condition = {"type": "touch_bounce", **{
                        k: cond[k] for k in
                        ("direction", "proximity_pct") if k in cond}}
                else:
                    condition = {"type": "role_reversal", **{
                        k: cond[k] for k in
                        ("role", "lookback") if k in cond}}
                genes.append(SignalGene(
                    indicator=cdef.indicator,
                    params=dict(cdef.params),
                    role=role,
                    field_name=cdef.field,
                    condition=condition,
                    timeframe=cond_tf,
                ))
                continue
            if action == "within_bars_after":
                # R4 翻译专门分支（fix-mtf-execution-consistency）：
                # _ACTION_MAP 形制不满足（lambda 只收 target 单参）——
                # 登记面平铺键 {target 兼任内层阈值, inner_action, window}
                # → 基因面嵌套形 {type, window, inner={type, threshold}}。
                # inner_action 白名单与分析链 _LOOKBACK_INNER_ACTIONS
                # 同源五员（T⑥ 锚对拍锁；不含 within_bars_after——嵌套
                # 窗口防域）。
                inner_action = cond.get("inner_action", "price_above")
                # fix-mtf-review-consistency CARD-04：五员词表单源引用
                # （condition_kernel.LOOKBACK_INNER_ACTIONS——与
                # validation/engine 的 _LOOKBACK_INNER_ACTIONS 同一
                # 常量对象，T⑥ 锚两侧同源断言）
                from core.analysis.condition_kernel import \
                    LOOKBACK_INNER_ACTIONS as _INNER_LEGAL
                if inner_action not in _INNER_LEGAL:
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] inner_action 不可表达: "
                        f"{inner_action!r}（可表达: {sorted(_INNER_LEGAL)}）",
                        detail={"condition": cond})
                window = cond.get("window", 5)
                if (not isinstance(window, int) or isinstance(window, bool)
                        or window < 1):
                    raise ResearchError(
                        "RESEARCH_PARAM_INVALID",
                        f"{label}[{i}] window 须为 >=1 整数: {window!r}",
                        detail={"param": "condition.window",
                                "value": str(window),
                                "legal_range": ">=1 整数"})
                condition = {"type": "within_bars_after", "window": window,
                             "inner": {"type": inner_action}}
                if inner_action in ("gt", "lt", "ge", "le"):
                    # fix-mtf-review-consistency CARD-01：target 分诊复用
                    # _classify_target_ref 单点（与下方 gt 路径同款形制）
                    # ——放行面=数值（int/float）与数值串；列名形
                    # （decoded/base_column/unknown）inner 阈值无序列表达，
                    # 结构化拒绝（原裸 float() 对非数值串炸 500）
                    from core.analysis.condition_kernel import \
                        _classify_target_ref
                    t_kind, _ = _classify_target_ref(target)
                    if (t_kind not in ("non_str", "numeric")
                            or not isinstance(target, (int, float, str))
                            or isinstance(target, bool)):
                        raise ResearchError(
                            "RESEARCH_NOT_EXPRESSIBLE",
                            f"{label}[{i}] target 须为数值（内层动作 "
                            f"{inner_action} 的阈值，列名 target 不可表达）",
                            detail={"condition": cond})
                    condition["inner"]["threshold"] = float(target)
                genes.append(SignalGene(
                    indicator=cdef.indicator,
                    params=dict(cdef.params),
                    role=role,
                    field_name=cdef.field,
                    condition=condition,
                    timeframe=cond_tf,
                ))
                continue
            if action not in _ACTION_MAP:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] action 不可表达: {action}"
                    f"（可表达: {sorted(_ACTION_MAP)})"
                    f"（时序动作: within_bars_after）",
                    detail={"condition": cond})
            # fix-research-c6：target 分类复用 _classify_target_ref 单点
            # ——数值/数值串→threshold 形（现状逐字节不变）；编码列名
            # →序列形（target_* 三键，无 threshold）；基础列/unknown/
            # 非法形→维持既有拒绝边界。price_above/price_below 豁免
            # （TC-3：target 一律占位忽略不报错——与统计面占位契约对齐）
            from core.analysis.condition_kernel import _classify_target_ref
            t_kind, t_ref = _classify_target_ref(target)
            if action in _PASSTHROUGH_ACTIONS:
                condition = _ACTION_MAP[action](target)
            elif t_kind in ("non_str", "numeric"):
                if (not isinstance(target, (int, float, str))
                        or isinstance(target, bool)):
                    # bool 排除与插件分支对称（review-042 CARD-14）：
                    # True/False 是布尔断言非阈值；list/None 等非法形
                    # 维持既有拒绝
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] target 须为数值（列名 target 仅"
                        "编码指标列可表达）",
                        detail={"condition": cond})
                condition = _ACTION_MAP[action](target)
            elif t_kind == "decoded":
                series_type = _SERIES_TYPE_MAP.get(action)
                if series_type is None:
                    # 当前不可达（_ACTION_MAP−passthrough=恰 8 键=本表
                    # 键集）——保留为 _ACTION_MAP 未来扩键的防御纵深
                    # （扩键未同步本表时显式拒绝优于静默漏翻译）
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] action 不可表达列名 target: {action}"
                        f"（列名 target 可表达: {sorted(_SERIES_TYPE_MAP)}）",
                        detail={"condition": cond})
                # F3：直用 _classify 的 payload（其返回即 try_decode
                # 产物，含 indicator/params/field——消同 target 二次解码）
                condition = {
                    "type": series_type,
                    "target_indicator": t_ref["indicator"],
                    "target_params": dict(t_ref["params"]),
                    "target_field": t_ref["field"],
                }
            else:
                # base_column（基础列无指标结构化形）与 unknown：
                # 「真正不可表达」边界保留（D-14 范围外）
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] target 须为数值或编码指标列名"
                    f"（{t_kind} 形不可表达）",
                    detail={"condition": cond})
            genes.append(SignalGene(
                indicator=cdef.indicator,
                params=dict(cdef.params),
                role=role,
                field_name=cdef.field,
                condition=condition,
                timeframe=cond_tf,
            ))
            continue

        # 插件列（注册表精确名）：翻译为 formula/model 信号源
        if subject in plugin_columns:
            if cond_tf is not None:
                # 插件源无 timeframe 槽位（signal_sources 结构不带周期
                # 维）——放行组内携带即拒（与组别拒绝同族显式面）
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列条件暂不支持 "
                    "timeframe 键（插件信号源无周期槽位）",
                    detail={"field": f"{label}[{i}].timeframe",
                            "timeframe": cond_tf,
                            "condition": cond})
            source_type = plugin_columns[subject]
            operator = _PLUGIN_ACTION_OPERATOR.get(action)
            if operator is None:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列暂不支持该 action"
                    f"（需数值比较: {sorted(_PLUGIN_ACTION_OPERATOR)}）",
                    detail={"condition": cond,
                            "slow_path": "插件列条件改用数值比较 action"
                            "（gt/lt/ge/le）"})
            if not isinstance(target, (int, float)) or isinstance(target, bool):
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列条件 target 须为数值",
                    detail={"condition": cond})
            plugin_sources.append({
                "type": source_type,
                "ref": subject,
                "threshold": float(target),
                "operator": operator,
                # 角色标记（recognize-plugin-source-dna T1；R2 多角色
                # 扩展——indicator-and-action-repair 卡 F 6.1）：源级
                # role 键自有五值域 {entry, exit, exit_partial,
                # add_trigger, add_suppress}（组短名映射——与基因级
                # SignalRole 枚举为两个层次、无对齐义务）；读取侧对
                # 无 role 键存量条目同按 entry 解释（缺省=entry 全链
                # 契约，存量 11 行零迁移）。同列多角色=多条目（一条件
                # 一角色不合一）。
                "role": _PLUGIN_SOURCE_ROLE.get(
                    getattr(role, "value", role), "entry"),
            })
            continue

        # 价格主体表达力（变更一 B1）：close 基础列 subject × lt/gt ×
        # 编码列名 target——target 解码为结构化引用（复用 C6 的
        # _classify_target_ref 单点），以解码结果为基因主体产出既有
        # 价格形态（lt→price_below / gt→price_above：收盘价对该指标
        # 序列的严格比较，executor 既有求值分支零改动）。语义恒等式：
        # close lt X ≡ price_below(X 基因) ≡ close < X。对 role 无特判
        # （WHEN/THEN/分批三组同规则）；指标挂基因主体（非 condition 内
        # target_* 键），warmup 经 _dna_indicator_names 主路径天然收集。
        # ge/le 含等号无对应严格形态——显式拒绝（禁静默近似映射
        # price_above，错误语义完整性优先于表面覆盖，完整四条理由见
        # openspec 变更一 design.md 第 3 节）。
        # hour_utc 放行域（add-session-structure-expression 任务 7，
        # Q2=基础列基因通道终稿）：hour_utc × gt/lt/ge/le × 数值
        # target（[0,24) 小数域阈值——U7b 值形制）→ 基因承载
        # （indicator="hour_utc"/params={}/field=None——执行链直读
        # 白名单）；其余 action（cross/price 族）拒绝——小时列无穿越
        # 语义闭环必要（周期回绕比较需取模，当前以 gt/lt 组合可表达）
        if subject == "hour_utc":
            if action in ("gt", "lt", "ge", "le") \
                    and isinstance(target, (int, float)) \
                    and not isinstance(target, bool):
                # target 值域硬校验（security F-1）：[0,24) 小时域
                # （U7b 值形制——开盘 UTC 小时+分钟/60，23:59 → 23.9833）
                # ——域外阈值（如 25 的 lt）比较恒 False 无语义，软放行
                # 会产出永不/恒触发的基因；落地兜底文案自述的 [0,24) 域
                if not (0 <= target < 24):
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] 不可表达: hour_utc target {target}"
                        " 超出 [0,24) 小时域（UTC 开盘时刻小数）",
                        detail={"condition": cond,
                                "slow_path": "target 取 [0,24) 小数"
                                             "（bar 开盘 UTC 小时"
                                             "+分钟/60）"})
                # 引用周期交互校验（D-6② 翻译面防线——登记面为权威，
                # 此处拦库内/手工路径）：hour_utc 求值轴（条件 tf 优先，
                # 无修饰回退基准）须日内，日线及以上值恒定无意义
                from core.analysis.column_ref import is_intraday_timeframe
                _eff_tf = cond_tf or (base_tf or None)
                if _eff_tf and not is_intraday_timeframe(_eff_tf):
                    raise ResearchError(
                        "RESEARCH_CONDITION_INVALID",
                        f"{label}[{i}] 不可表达: hour_utc 求值轴 "
                        f"{_eff_tf} 为日线及以上——每日一 bar 下小时列"
                        "值恒定无意义",
                        detail={"condition": cond, "ref_tf": _eff_tf,
                                "hint": "时刻列的求值轴必须是日内周期"
                                        "（12h 及以下），或去掉 timeframe"
                                        " 修饰在日内基准上求值"})
                genes.append(SignalGene(
                    indicator="hour_utc",
                    params={},
                    role=role,
                    field_name=None,
                    condition=_ACTION_MAP[action](target),
                    timeframe=cond_tf,
                ))
                continue
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"{label}[{i}] 不可表达: hour_utc 仅支持数值比较 "
                "gt/lt/ge/le × 数值 target（[0,24) 小时域）",
                detail={"condition": cond,
                        "slow_path": "cross/price 族对小时列不支持"
                                     "（周期回绕语义未定义）"})

        if subject == "close":
            from core.analysis.condition_kernel import _classify_target_ref
            t_kind, t_ref = _classify_target_ref(target)
            if t_kind == "decoded":
                if action in ("lt", "gt"):
                    genes.append(SignalGene(
                        indicator=t_ref["indicator"],
                        params=dict(t_ref["params"]),
                        role=role,
                        field_name=t_ref["field"],
                        condition={"type":
                                   "price_below" if action == "lt"
                                   else "price_above"},
                        timeframe=cond_tf,
                    ))
                    continue
                if action in ("ge", "le"):
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] 不可表达: close 基础列 subject 支持"
                        " lt/gt 与编码列名 target 组合（价格与指标线的"
                        "严格比较）；ge/le 含等号形态暂不可表达",
                        detail={"condition": cond,
                                "slow_path": "改用 gt/lt 或提交 "
                                             "feature-requests"})
                # 其余动作（cross/breakout 族等）：穿下方既有基础列
                # 拒绝段——price_* 无穿越形态为真边界，文案逐字节不变
            elif action in ("lt", "gt"):
                # lt/gt 但 target 非 decoded（数值/基础列/未知串）：
                # close 主体仅支持编码列名 target——引导改写而非笼统拒绝
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: close 基础列主体现仅支持"
                    "编码列名 target——数值 target 组合不在本能力"
                    f"（target 分类: {t_kind}）",
                    detail={"condition": cond,
                            "slow_path": "target 改用编码指标列名"
                                         "（如 ema_20），或提交 "
                                         "feature-requests"})
            # close+ge/le+非 decoded 及其余组合：穿既有基础列拒绝段
            # （维持现状文案，与三段格局边界一致）

        # 字符串 subject（0.2.13 起）：参数化指标编码形已退役（入口关闭+
        # 存量迁移）——只可能是基础列 / 插件失效列 / 未知列，全部不可表达
        # （close 的 lt/gt+decoded 组合已在上方分支放行，不会落到这里）
        from core.analysis.condition_kernel import PLUGIN_COLUMN_PREFIXES
        if subject in BASE_SUBJECT_COLUMNS:
            reason = (f"内置基础列 subject（{subject}）不可表达为"
                      "指标信号源（参数化指标请用结构化引用 "
                      "{indicator, params, field}）")
        elif subject.startswith(PLUGIN_COLUMN_PREFIXES):
            reason = (f"插件列 subject（{subject}）已失效"
                      "（信号注册表无此列——插件被删除或未注册）")
        else:
            reason = (f"字符串 subject（{subject}）不可表达——参数化指标"
                      "统一结构化引用 {indicator, params, field}")
        raise ResearchError(
            "RESEARCH_NOT_EXPRESSIBLE",
            f"{label}[{i}] 不可表达: {reason}",
            detail={"condition": cond,
                    "slow_path": "提交 feature-requests 或改用结构化指标引用"})
    # WHEN 组至少一个可表达条件（无入场条件的策略无意义）；THEN 组空
    # = 无显式出场（出场基因可为零，不拒——此前空 then 被"无可表达条件"
    # 连坐拒绝，纯插件列假设的最小形态 when=[插件条件]/then=[] 需放行）
    if label == "when_conditions" and not genes and not plugin_sources:
        raise ResearchError("RESEARCH_NOT_EXPRESSIBLE", "无可表达条件")
    return genes, plugin_sources

