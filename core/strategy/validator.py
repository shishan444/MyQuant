"""策略 DNA 合法性校验（回测前置闸门）。

职能：validate_dna 对 StrategyDNA 做结构完整性与值域合法性检查，
产出 ValidationResult（errors 拒绝面 / warnings 提示面）。

价值与服务对象：策略 DNA 进引擎前的唯一合法性闸门——消费方为
进化链 population（种群初始化）与 operators（变异子代校验），
非法产出回退保底以此为据；SL_PCT_MIN/MAX 与 SL_ATR_MIN/MAX 同时是
exit_sweep 网格校验的引用式值域（扫描通道各档单点权威，改域单点
生效），MAX_LEVERAGE 为杠杆红线单源（api/schemas 各处 Field 同源
引用——D4 家族）。

职责边界：只判合法性与结构完整，不判策略优劣（表现评估归回测
引擎与评估链）；不修改 DNA。
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from typing import List

from core.strategy.dna import StrategyDNA, SignalRole, SL_MODES

# 止损 pct 值域（0.4.1 R6 提升为模块级常量——exit_sweep 网格校验引用式
# 消费，改域单点生效；原函数内字面等价替换，行为零变化）
SL_PCT_MIN = 0.005
SL_PCT_MAX = 0.20
# 止损 atr 倍数值域（0.4.2 DC-11 分叉域——量纲双关：atr 模式下
# stop_loss/take_profit 为 ATR 倍数而非比例值；消费方：validator
# risk 域、登记声明块校验（hypothesis_service）、exit_sweep atr 档）
SL_ATR_MIN = 0.5
SL_ATR_MAX = 5.0
# 杠杆红线单源（D4/DC-5）：全部消费方（api/schemas.py 的 Field(le=MAX_
# LEVERAGE) 各处〔含请求/响应两侧〕、api/routes/paper_trading.py 的
# Field 同源、validator risk 域、登记声明块校验）一律经本常量同源引用，
# 禁散写字面量——改值代价=一处常量+边界用例（原「三处/五处」计数式
# 表述随消费方增删漂移，改为不依赖计数的同源表述——improve-schema-
# bound-symmetry 盘点附带核对）
MAX_LEVERAGE = 10
# 时间出场持有上界（add-max-hold-exit D-4）：max_hold 正整数 bar 数
# 值域单源——登记声明块校验（hypothesis_service）与 validate_dna 手工
# DNA 校验双消费方（DC-15 权威双点同族）。MIN=1=「持有 1 根 bar 即
# 离场」的最短合法语义（N=1 物理退化为与 N=2 等价——入场 bar 时点被
# 入场单占据，见 max-hold-exit spec）；MAX=200 与指标周期类参数上界
# （ParamDef period max=200）的 bar 数语义量级习惯一致
MAX_HOLD_MIN = 1
MAX_HOLD_MAX = 200
# 固定时刻出场（add-session-structure-expression 任务 6）：UTC 分钟
# 时刻 0-1439（0=0:00 合法值非禁用哨兵——引擎哨兵 -1.0 区别于
# max_hold 的 0.0=禁用）；值域单源（登记声明块与 validate_dna 同源）
TIME_EXIT_UTC_MIN = 0
TIME_EXIT_UTC_MAX = 1439
# 动态定仓（complete-research-tooling 任务 4）：枚举与值域单源——登记
# 面声明块校验与 validate_dna 双消费方同源（MAX_HOLD 同款形制）
POSITION_MODES = ("fixed", "risk_budget")
RISK_FRACTION_MIN = 0.0   # 开区间下界（0 排除）
RISK_FRACTION_MAX = 0.5   # 闭区间上界（P9 上界核对——validator 惯例）
# 组合段 mode 封闭白名单（H4-a，dna-strong-typing-and-disclosure-hygiene
# 件②）：单源常量——validate_dna 结构判据与组装器求值期拒绝消息
# （signal_assembly.py 未知 mode raise）同源引用渲染，改域单点生效；
# 值域分工：仅 mode 枚举+类型形归本校验（前置），weights 各值/
# threshold/operator 数值域维持组装求值单点既有权威
COMPOSITION_MODES = ("weighted_vote", "gate", "logic")
# 组合段 mode 缺省值（CARD-07）：结构校验（validate_dna）与求值
# （signal_assembly）两处 get 缺省的单源常量——双写字面量在求值侧
# 行号漂移后无从对齐，符号锚取代行号锚
DEFAULT_COMPOSITION_MODE = "weighted_vote"
# 仓位参数（add-position-sizing-params）：首笔比例 (0,1] 开下闭上；
# 金额防线只有下界（>0），上界完全放开——考场无观点（不替用户判断
# 「多大的金额防线不合理」）
INITIAL_FRACTION_MIN = 0.0   # 开区间下界（0 排除——零首仓无意义）
INITIAL_FRACTION_MAX = 1.0   # 闭区间上界（1.0=全部可用资金）
MAX_POSITION_LOSS_MIN = 0.0  # 开区间下界（0 排除；无上界）
# signal_sources 条目 type 白名单（signal-sources-structure-gate 卡
# V）：结构判据单源——validate_dna R-1b 成员判定与消息渲染同源引用；
# rule=规则条目（恒裸键，无 ref 要求），formula/model=插件源（须带
# ref）。插件子集 ("formula","model") 在 _entry_plugin_sources/
# has_plugin_sources/R-1c 三处以字面量存在（消费语义各自独立，本
# change 不重构——扩源类型需三处联动，记为已知债）
SIGNAL_SOURCE_TYPES = ("formula", "model", "rule")


def _entry_plugin_sources(dna: StrategyDNA) -> List[dict]:
    """role 解释为 entry 的插件源条目（进场判据的插件源面）。

    type∈{"formula","model"} 且 role 缺省或 =="entry" 的源解释为 entry
    ——缺省=entry 与全链既有契约一致（引擎组装器只组进场侧/翻译器拒
    绝插件出场/存量 11 行零 role 键零迁移）；role=="exit" 源不顶替
    entry（防「无 ENTRY 基因+仅 exit 源」形态下进场条件空集的静默零
    交易在结构层复刻——该形态按两源皆空拒绝）。
    """
    sources = dna.signal_sources
    return [
        s for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict)
        and s.get("type") in ("formula", "model")
        and s.get("role", "entry") == "entry"
    ]


def has_plugin_sources(dna: StrategyDNA) -> bool:
    """signal_sources 含插件源条目（type∈{formula,model}）——在场即真。

    执行链能力边界判据（recognize-plugin-source-dna T4）：模拟盘/实盘/
    REST 回测通道未接入插件信号组装，插件源在场即拒（含混合形态——
    插件源被静默忽略、结果只反映内置基因，比零交易更隐蔽；「在场即
    拒」防静默失真回潮）。与 validate_dna 的 signal_sources 结构判据
    正交（_entry_plugin_sources 为 entry 面提取器而非校验器——review
    Q-4 修正旧括号指代）：结构判据问「策略形态合法吗」，本判据问
    「这个执行环境支持吗」。
    """
    return any(
        isinstance(s, dict) and s.get("type") in ("formula", "model")
        for s in (dna.signal_sources or []))


@dataclass
class ValidationResult:
    """DNA 校验结果（is_valid=errors 为空；warnings 不影响放行）。"""
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_dna(dna: StrategyDNA) -> ValidationResult:
    """校验 StrategyDNA 合法性（拒绝面清单）。

    Checks:
    - 至少 1 个入场信号（trigger 或 guard）
    - 至少 1 个离场信号（trigger 或 guard）
    - sl_mode 在 SL_MODES 内（单源取值域）
    - 止损/止盈值域按 sl_mode 分叉：pct/trail 档 [SL_PCT_MIN, SL_PCT_MAX]
      （比例值）、atr 档 [SL_ATR_MIN, SL_ATR_MAX]（ATR 倍数）——量纲双关
    - 止盈 > 止损（设了止盈时；trail 档豁免——回撤比例与固定止盈
      不构成盈亏比；atr 档保留——倍数盈亏比有意义）
    - 仓位在 [0.10, 1.0]
    - 杠杆在 [1, MAX_LEVERAGE]（红线单源）
    - 新条件类型的结构完整性
    - cross_layer_logic 须 AND 或 OR（MTF 策略）
    - mixed 方向带 layers 但无 trend 层时 warning（不拒）
    """
    errors: List[str] = []
    warnings: List[str] = []

    # signal_sources 结构判据（signal-sources-structure-gate 卡 V）：
    # 第一判据块——entry 判据经 _entry_plugin_sources 消费
    # signal_sources 迭代，形制判据必须先行。None=合法退化（R-1d，
    # 存量语义）；顶层非 list 前置点名（防 str 迭代出字符/键的怪
    # 消息、防 int/bool 迭代 TypeError 在判据内炸穿）；逐条 elif
    # 语义（a 独占，b/c 互斥）——每条目至多一条结构判据消息。最小
    # 结构边界：ref 值形制（None/空串）不判（执行侧既有
    # RESEARCH_SIGNAL_SOURCE_INVALID 防线承接）；role/threshold/
    # operator/weight 不在校验范围
    ss = dna.signal_sources
    if ss is not None and not isinstance(ss, list):
        errors.append(
            f"signal_sources must be a list, got {type(ss).__name__}")
    elif isinstance(ss, list):
        for i, s in enumerate(ss):
            if not isinstance(s, dict):
                errors.append(
                    f"signal_sources[{i}] must be a dict, "
                    f"got {type(s).__name__}")
            elif s.get("type") not in SIGNAL_SOURCE_TYPES:
                errors.append(
                    f"signal_sources[{i}]: type must be one of "
                    f"{SIGNAL_SOURCE_TYPES}, got {s.get('type')!r}")
            elif s.get("type") in ("formula", "model") \
                    and "ref" not in s:
                errors.append(
                    f"signal_sources[{i}]: type {s.get('type')!r} "
                    f"requires 'ref'")

    # composition 结构判据（H4-a 件②，沿 signal_sources 判据同族形制：
    # None 合法退化、errors 收集不 raise、逐条至多一条消息、点名实际
    # 类型/值）。坏 mode 形态从「201 落库→回测期 422」前移为「创建/
    # 更新/进化种群初始化即 422」；求值期 RESEARCH_COMPOSITION_INVALID
    # 保留为已落库存量的兜底防线（纵深）。值域分工：threshold/weights
    # 各值/operator/role 不判（组装求值单点既有权威承接）。
    comp = dna.composition
    if comp is not None and not isinstance(comp, dict):
        errors.append(
            f"composition must be a dict, got {type(comp).__name__}")
    elif isinstance(comp, dict):
        mode = comp.get("mode", DEFAULT_COMPOSITION_MODE)  # 缺省与 signal_assembly 求值缺省一致（符号锚）
        if mode not in COMPOSITION_MODES:
            errors.append(
                f"composition: mode must be one of {COMPOSITION_MODES}, "
                f"got {mode!r}")
        elif mode == "weighted_vote" and "weights" in comp \
                and not isinstance(comp["weights"], dict):
            errors.append(
                f"composition.weights must be a dict, "
                f"got {type(comp['weights']).__name__}")

    # 非 MTF 策略：查顶层 signal_genes
    if not dna.layers:
        entry_signals = [
            g for g in dna.signal_genes
            if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
        ]
        # 进场判据双源（recognize-plugin-source-dna T2）：ENTRY 角色基因
        # 与 role 解释为 entry 的插件源任一在场即过；插件源不顶替出场
        # （exit 判据不变）。
        if not entry_signals and not _entry_plugin_sources(dna):
            errors.append("No entry signal defined (need at least one entry_trigger or entry_guard)")

        exit_signals = [
            g for g in dna.signal_genes
            if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)
        ]
        if not exit_signals:
            errors.append("No exit signal defined (need at least one exit_trigger or exit_guard)")

        # mixed 方向须至少一个 DIRECTION 基因
        if dna.risk_genes.direction == "mixed":
            direction_genes = [
                g for g in dna.signal_genes
                if g.role == SignalRole.DIRECTION
            ]
            if not direction_genes:
                errors.append(
                    "mixed direction requires at least one DIRECTION gene"
                )

    # MTF 策略：另查各 layer（结构面与顶层同规）
    if dna.layers:
        if len(dna.layers) > 3:
            errors.append(
                f"MTF strategy supports max 3 layers, got {len(dna.layers)}"
            )
        has_execution = False
        has_trend = False
        # 复制形态层判据基准（recognize-plugin-source-dna T2）：顶层
        # entry 面（ENTRY 角色基因或 role 解释为 entry 的插件源）——单层
        # 序化兼容层产物（层基因集与顶层完全一致——库存被误拒策略的
        # 实测形态）时层 entry 判据随顶层同判：顶层 entry 面在场→放行
        # （纯插件策略顶层插件源可顶替层 entry）；真分层维持强制（AND
        # 组合下层缺 entry=该层恒假=组合恒假，放行等于放行无意义策略；
        # OR 组合维持现状从严——无存量与生产者证据不放开）。
        top_entry_present = bool(
            [g for g in dna.signal_genes
             if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)]
            or _entry_plugin_sources(dna))
        for layer in dna.layers:
            layer_entry = [
                g for g in layer.signal_genes
                if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
            ]
            if not layer_entry:
                is_mirror_layer = (
                    len(dna.layers) == 1
                    and layer.signal_genes == dna.signal_genes)
                if not (is_mirror_layer and top_entry_present):
                    errors.append(f"Layer {layer.timeframe}: no entry signal defined")
            layer_exit = [
                g for g in layer.signal_genes
                if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)
            ]
            if not layer_exit:
                errors.append(f"Layer {layer.timeframe}: no exit signal defined")
            if layer.role not in (None, "trend", "execution", "structure", "zone"):
                errors.append(f"Layer {layer.timeframe}: invalid role '{layer.role}'")
            if layer.role in (None, "execution"):
                has_execution = True
            if layer.role == "trend":
                has_trend = True
        if not has_execution:
            errors.append("MTF strategy needs at least one execution layer")

        # mixed 方向须全部 layer 合计至少一个 DIRECTION 基因
        if dna.risk_genes.direction == "mixed":
            all_direction = [
                g for layer in dna.layers
                for g in layer.signal_genes
                if g.role == SignalRole.DIRECTION
            ]
            if not all_direction:
                errors.append(
                    "mixed direction requires at least one DIRECTION gene "
                    "across MTF layers"
                )

        # cross_layer_logic 值域校验
        if dna.cross_layer_logic not in ("AND", "OR"):
            errors.append(
                f"cross_layer_logic must be 'AND' or 'OR', got '{dna.cross_layer_logic}'"
            )

        # 提示面：mixed 方向但无 trend 层（方向信号可能无明确定义基准）
        if dna.risk_genes.direction == "mixed" and not has_trend:
            warnings.append(
                "mixed direction with layers but no trend layer: "
                "direction signal may not be well-defined"
            )

    # 顶层基因的条件结构校验
    for i, gene in enumerate(dna.signal_genes):
        cond_errors = _validate_condition_structure(gene.condition, i)
        errors.extend(cond_errors)

    # 各 layer 基因的条件结构校验（prefix 点名层与序）
    if dna.layers:
        for layer in dna.layers:
            for i, gene in enumerate(layer.signal_genes):
                cond_errors = _validate_condition_structure(
                    gene.condition, i, prefix=f"Layer {layer.timeframe} gene {i}"
                )
                errors.extend(cond_errors)

    # 基因级 timeframe 值域校验（fix-review B5 / security S-1）：手工
    # DNA 五入口落库前拦截（此前非法 tf 可落库，装载/执行时点才炸且
    # find_parquet 路径 ValueError 裸穿 500）。判据与翻译防线同源：
    # tf ∈ TF_SECONDS 且 >= 执行周期（低于执行周期无定义，与翻译面
    # assert_higher_tf >= 语义一致）；顶层与 layers 基因同面；errors
    # 收集形与 max_hold 同制（不 raise）
    from core.data.timeframes import TF_SECONDS
    exec_tf = dna.execution_genes.timeframe
    exec_tf_seconds = TF_SECONDS.get(exec_tf)
    _all_genes: List[tuple] = [
        (f"Gene {i}", g) for i, g in enumerate(dna.signal_genes)]
    for layer in (dna.layers or []):
        _all_genes.extend(
            (f"Layer {layer.timeframe} gene {i}", g)
            for i, g in enumerate(layer.signal_genes))
    for label, gene in _all_genes:
        gene_tf = getattr(gene, "timeframe", None)
        if gene_tf is None:
            continue
        if gene_tf not in TF_SECONDS:
            errors.append(
                f"{label}: timeframe must be one of "
                f"{sorted(TF_SECONDS)}, got '{gene_tf}'")
        elif exec_tf_seconds is not None \
                and TF_SECONDS[gene_tf] < exec_tf_seconds:
            errors.append(
                f"{label}: timeframe '{gene_tf}' must be >= execution "
                f"timeframe '{exec_tf}'")

    # 风险基因值域校验（sl_mode 感知——值域按模式分叉，DC-11/DC-12；
    # trail/atr 是合法 DNA 形态，本处只判值域，交易链兼容性归
    # validate_trading_compatibility 独立入口，严格分层）
    risk = dna.risk_genes

    if risk.sl_mode not in SL_MODES:
        errors.append(
            f"sl_mode must be one of {SL_MODES}, got '{risk.sl_mode}'"
        )

    # 止损/止盈值域按模式分档取域（同值异判：1.5 在 pct 拒、atr 过）
    if risk.sl_mode == "atr":
        lo, hi = SL_ATR_MIN, SL_ATR_MAX
        unit = "ATR multiples"
    else:
        lo, hi = SL_PCT_MIN, SL_PCT_MAX
        unit = "ratio"
    if not (lo <= risk.stop_loss <= hi):
        errors.append(
            f"Stop loss {risk.stop_loss} out of range [{lo}, {hi}] "
            f"({unit}, sl_mode={risk.sl_mode})"
        )
    if risk.take_profit is not None and not (lo <= risk.take_profit <= hi):
        errors.append(
            f"Take profit {risk.take_profit} out of range [{lo}, {hi}] "
            f"({unit}, sl_mode={risk.sl_mode})"
        )

    # 止盈>止损：trail 档豁免（回撤比例与固定止盈不构成盈亏比，A1）；
    # pct 档放行（fr_fd5e89897f8d——R:R<1 高胜率浅收割形制，exit_sweep
    # 网格支持；登记面 strategy_param_warnings 披露，与 hypothesis_service
    # 同步降级）；atr 档保留（倍数盈亏比有意义）
    if (
        risk.take_profit is not None
        and risk.sl_mode == "atr"
        and risk.take_profit <= risk.stop_loss
    ):
        errors.append(f"Take profit ({risk.take_profit}) must be greater than stop loss ({risk.stop_loss})")

    if not (0.10 <= risk.position_size <= 1.0):
        errors.append(f"Position size {risk.position_size} out of range [0.10, 1.0]")

    if not (1 <= risk.leverage <= MAX_LEVERAGE):
        errors.append(f"Leverage {risk.leverage} out of range [1, {MAX_LEVERAGE}]")

    # 分批数值三字段域校验（0.4.2 R3/DC-15 validator 格——手工 DNA 路径
    # 的防护面；登记路径经声明块校验前置拦截）。None=合法缺省态（执行
    # 按 0.5/0.1/1.0），不报错；max_exposure 上界取 DNA 实际 leverage
    # （与登记侧生效杠杆同语义——联动上界而非固定值）
    for frac_field in ("exit_fraction", "add_fraction"):
        frac_val = getattr(risk, frac_field)
        if frac_val is not None and not (0.0 < frac_val <= 1.0):
            errors.append(
                f"{frac_field} {frac_val} out of range (0, 1]")
    me_val = risk.max_exposure
    if me_val is not None and not (0.0 < me_val <= risk.leverage):
        errors.append(
            f"max_exposure {me_val} out of range (0, {risk.leverage}] "
            f"(bounded by risk_genes.leverage)")

    # 时间出场持有上界（add-max-hold-exit D-1）：None=合法缺省态（无
    # 时间离场——存量语义），不报错；登记路径经声明块校验前置拦截。
    # 类型防护（security S-2 修复）：与登记侧同形制——bool 穿透值域
    # （1<=True<=200 成立）静默化为 N=1、float 穿透取整、str 抛裸
    # TypeError，均在类型层拦截
    mh_val = risk.max_hold
    if mh_val is not None:
        if not isinstance(mh_val, int) or isinstance(mh_val, bool):
            errors.append(
                f"max_hold must be an integer (bars), got {type(mh_val).__name__}")
        elif not (MAX_HOLD_MIN <= mh_val <= MAX_HOLD_MAX):
            errors.append(
                f"max_hold {mh_val} out of range "
                f"[{MAX_HOLD_MIN}, {MAX_HOLD_MAX}] (bars)")

    # 固定时刻出场（add-session-structure-expression 任务 6）：UTC
    # 分钟时刻（None=缺省合法）；类型防护同 max_hold；0=0:00 合法
    te_val = risk.time_exit_utc  # dataclass 定义字段（恒存在，None=缺省）
    if te_val is not None:
        if not isinstance(te_val, int) or isinstance(te_val, bool):
            errors.append(
                f"time_exit_utc must be an integer (UTC minute), "
                f"got {type(te_val).__name__}")
        elif not (TIME_EXIT_UTC_MIN <= te_val <= TIME_EXIT_UTC_MAX):
            errors.append(
                f"time_exit_utc {te_val} out of range "
                f"[{TIME_EXIT_UTC_MIN}, {TIME_EXIT_UTC_MAX}] (UTC minutes)")
        # 周期组合校验（security F-3）：日线及以上每 bar 覆盖全天，
        # 时刻出场恒触发 ≡ max_hold=1 退化——与时刻出场意图背离，
        # 组合拒绝（周期分钟经 TF_SECONDS 单源；未知周期跳过——
        # 该形由基因级 tf 值域校验另行报错）
        if isinstance(te_val, int) and not isinstance(te_val, bool) \
                and exec_tf_seconds is not None \
                and exec_tf_seconds >= 1440 * 60:
            errors.append(
                f"time_exit_utc requires an intraday execution timeframe "
                f"(daily+ bars cover every UTC minute — time exit always "
                f"triggers, degenerate to max_hold=1), got '{exec_tf}'")

    # 动态定仓（complete-research-tooling 任务 4）：position_mode 枚举
    # {fixed, risk_budget}（None=缺省 fixed 合法）；risk_fraction (0,0.5]
    # （bool 排除——类型防护同族）；risk_budget 组合须 stop_loss>0
    # （止损距离是仓位公式的分母——无止损=风险预算不可定义）
    pm_val = risk.position_mode
    rf_val = risk.risk_fraction
    if pm_val is not None:
        if pm_val not in POSITION_MODES:
            errors.append(
                f"position_mode must be one of {list(POSITION_MODES)}, "
                f"got {pm_val!r}")
    if rf_val is not None:
        if isinstance(rf_val, bool) or not isinstance(rf_val, (int, float)):
            errors.append(
                f"risk_fraction must be a number in ({RISK_FRACTION_MIN}, "
                f"{RISK_FRACTION_MAX}], got {type(rf_val).__name__}")
        elif not (RISK_FRACTION_MIN < rf_val <= RISK_FRACTION_MAX):
            errors.append(
                f"risk_fraction {rf_val} out of range "
                f"({RISK_FRACTION_MIN}, {RISK_FRACTION_MAX}]")
    if pm_val == "risk_budget":
        if rf_val is None:
            errors.append(
                "position_mode='risk_budget' requires risk_fraction "
                "(entry size = equity × risk_fraction ÷ stop distance)")
        if not risk.stop_loss or risk.stop_loss <= 0:
            errors.append(
                "position_mode='risk_budget' requires stop_loss > 0 "
                "(stop distance is the size formula denominator — "
                "no stop = risk budget undefined)")

    # 仓位参数（add-position-sizing-params）：initial_fraction (0,1]
    # 首笔动用资金比例（替换 fixed 路径首笔定仓来源，加仓不受影响）；
    # max_position_loss 金额防线只有下界（>0，上界完全放开——考场
    # 无观点）；两键互斥校验见下（同笔首仓不可两个指挥）。
    # 类型防护沿 risk_fraction 形制（bool 排除）
    if_val = risk.initial_fraction
    mpl_val = risk.max_position_loss
    if if_val is not None:
        if isinstance(if_val, bool) or not isinstance(if_val, (int, float)):
            errors.append(
                f"initial_fraction must be a number in "
                f"({INITIAL_FRACTION_MIN}, {INITIAL_FRACTION_MAX}], "
                f"got {type(if_val).__name__}")
        elif not (INITIAL_FRACTION_MIN < if_val <= INITIAL_FRACTION_MAX):
            errors.append(
                f"initial_fraction {if_val} out of range "
                f"({INITIAL_FRACTION_MIN}, {INITIAL_FRACTION_MAX}]")
    if mpl_val is not None:
        if isinstance(mpl_val, bool) or not isinstance(mpl_val, (int, float)):
            errors.append(
                f"max_position_loss must be a positive number "
                f"(> {MAX_POSITION_LOSS_MIN}, no upper bound), "
                f"got {type(mpl_val).__name__}")
        else:
            # 有限性检查（SEC-01）：inf/超大整数不是金额——float 化
            # 溢出的 int 同非有限（延迟到物化层 float() 会崩）。
            # 「无上界=考场无观点」语义不受影响：finite 不是上界。
            try:
                mpl_finite = math.isfinite(float(mpl_val))
            except OverflowError:
                mpl_finite = False
            if not mpl_finite or not mpl_val > MAX_POSITION_LOSS_MIN:
                errors.append(
                    f"max_position_loss {mpl_val} must be a finite number "
                    f"> {MAX_POSITION_LOSS_MIN} (no upper bound)")
    # 互斥：首笔定仓两个来源不可同时声明——initial_fraction 替换
    # fixed 路径首仓、risk_budget 反推首仓，同笔首仓受两个指挥即拒绝
    if if_val is not None and pm_val == "risk_budget":
        errors.append(
            "initial_fraction and position_mode='risk_budget' are mutually "
            "exclusive (one first-entry sizing source only — fixed-path "
            "replacement vs risk-budget derivation)")

    if risk.direction not in ("long", "short", "mixed"):
        errors.append(f"Direction must be 'long', 'short', or 'mixed', got '{risk.direction}'")

    # MTF 控制参数值域校验
    valid_mtf_modes = (None, "direction", "confluence", "direction+confluence")
    if dna.mtf_mode not in valid_mtf_modes:
        errors.append(f"mtf_mode must be one of {valid_mtf_modes}, got '{dna.mtf_mode}'")

    if not (0.1 <= dna.confluence_threshold <= 0.9):
        errors.append(f"confluence_threshold {dna.confluence_threshold} out of range [0.1, 0.9]")

    if not (0.5 <= dna.proximity_mult <= 3.0):
        errors.append(f"proximity_mult {dna.proximity_mult} out of range [0.5, 3.0]")

    return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


# 交易链兼容性判别角色集（0.4.2 DC-6 判据二）：含任一即 live 链拒绝——
# live 链对分批行为无执行能力（add/reduce→None 静默不动作；回测与
# 纸盘新域已支持分批）
_SCALING_ROLES = frozenset({
    SignalRole.REDUCE_TRIGGER, SignalRole.REDUCE_GUARD,
    SignalRole.ADD_TRIGGER, SignalRole.ADD_GUARD,
    SignalRole.ADD_INHIBIT,
})


def validate_trading_compatibility(dna: StrategyDNA, link: str) -> List[str]:
    """live 链兼容性校验（0.4.2 R2/D3 扩展——独立入口，与 validate_dna
    严格分层：trail/分批是合法 DNA 形态，validate_dna 不因此报错，本
    函数只在策略进入 **live 链**装载时判链路能力——回放/模拟旧链已
    退役，回测引擎为能力全集基准不设闸门，纸盘新域支持分批与 trail
    均不经本闸门）。

    判据一（模式×链路静态能力矩阵）：
    - sl_mode="trail"：live 链拒（live 链无峰值回撤实现；回测与
      纸盘新域支持）。
    - sl_mode="atr"：live 链拒（live 链 SL/TP v1 仅 pct——
      _attach_sl_tp 按 pct 挂单，atr 倍数按 pct 挂单将产出
      sl=entry×(1−stop_loss) 的负价无效止损单）。
    - pct：放行。

    判据二（分批角色基因存在性——DNA 层静态判别，含 layers 展开）：
        signal_genes ∪ layers[*].signal_genes 任一 role ∈ 分批角色集 →
        live 链拒绝（拒绝 detail 指明基因与角色）。

    Args:
        dna: 待装载的策略 DNA。
        link: 交易链路标识（恒 "live"——由 live 插桩点传入，不猜测来源）。

    Returns:
        拒绝原因清单（空列表=兼容放行）。

    Raises:
        ValueError: link 非法（编程错误——live 插桩点是唯一调用方）。
    """
    reasons: List[str] = []
    if link != "live":
        raise ValueError(f"link 须为 live，got {link!r}")

    sl_mode = dna.risk_genes.sl_mode

    # 判据一：模式×链路
    if sl_mode == "trail":
        reasons.append(
            "sl_mode='trail'（追踪止损）live 链无峰值回撤实现"
            "（回测与纸盘新域支持），不支持装载")
    elif sl_mode == "atr":
        reasons.append(
            "atr 模式实盘链路暂不支持（SL/TP v1 仅 pct），可改用 pct "
            "或等待后续版本——危险形态：atr 倍数按 pct 挂单将产出 "
            "sl=entry×(1−stop_loss) 的负价无效止损单")

    # 判据二：分批角色（signal_genes 与全部 layers 展开并集）
    seen_roles = set()
    for gene in dna.signal_genes:
        seen_roles.add(gene.role)
    for layer in (dna.layers or []):
        for gene in layer.signal_genes:
            seen_roles.add(gene.role)
    hit_roles = sorted(
        (r for r in seen_roles if r in _SCALING_ROLES),
        key=lambda r: r.value)
    if hit_roles:
        role_names = "、".join(r.value for r in hit_roles)
        reasons.append(
            f"策略含分批仓位角色基因（{role_names}）——live 链对部分"
            "离场/条件加仓/抑制无执行能力（回测与纸盘新域支持），"
            "不支持装载")

    return reasons


def _validate_condition_structure(
    condition: dict, gene_idx: int, prefix: str = "",
) -> List[str]:
    """新条件类型的结构完整性校验（缺必需键即 error 点名条件类型与键）。

    cond_type 合法集校验（fix-review CARD-01）：合法集单源
    EVALUABLE_CONDITION_TYPES（executor 侧执行分派集）——validator
    前置拦截使装载面与执行侧 RESEARCH_NOT_EXPRESSIBLE 拒绝面一致化，
    存量库内非法 cond_type 策略装载从通过变拒绝。executor 顶层拖入
    numpy/pandas 重依赖，validator 为纯结构校验轻模块，函数内延迟
    import 保持装载链轻（executor.py 同形制先例：indicators/
    mtf_engine 延迟装载）。
    """
    # 延迟 import：EVALUABLE_CONDITION_TYPES 唯一权威在 executor
    # （合法集契约单源，S6b 双向相等断言锁两侧不漂移）
    from core.strategy.executor import EVALUABLE_CONDITION_TYPES

    errors: List[str] = []
    label = prefix if prefix else f"Gene {gene_idx}"
    cond_type = condition.get("type", "")

    if cond_type not in EVALUABLE_CONDITION_TYPES:
        errors.append(
            f"{label}: condition type must be one of "
            f"{sorted(EVALUABLE_CONDITION_TYPES)}, got '{cond_type}'")
    elif cond_type in ("cross_above_series", "cross_below_series"):
        if "target_indicator" not in condition:
            errors.append(f"{label}: {cond_type} requires 'target_indicator'")
    elif cond_type in ("lookback_any", "lookback_all", "within_bars_after"):
        if "window" not in condition:
            errors.append(f"{label}: {cond_type} requires 'window'")
        if "inner" not in condition:
            errors.append(f"{label}: {cond_type} requires 'inner' condition")
        else:
            # inner 深度校验（CARD-01 同族小面收口）：执行侧
            # _evaluate_inner_condition 对 inner 缺 type 委托主入口
            # condition["type"] 裸下标炸链、阈值四类缺 threshold 静默
            # 用 0（executor.py:204-205）——装载前结构拦截
            inner = condition["inner"]
            if not isinstance(inner, dict):
                errors.append(
                    f"{label}: {cond_type} 'inner' must be a dict, "
                    f"got {type(inner).__name__}")
            elif "type" not in inner:
                errors.append(
                    f"{label}: {cond_type} inner condition requires 'type'")
            else:
                inner_type = inner["type"]
                if inner_type not in EVALUABLE_CONDITION_TYPES:
                    errors.append(
                        f"{label}: {cond_type} inner condition type must be "
                        f"one of {sorted(EVALUABLE_CONDITION_TYPES)}, "
                        f"got '{inner_type}'")
                elif inner_type in ("lt", "gt", "le", "ge") \
                        and "threshold" not in inner:
                    errors.append(
                        f"{label}: {cond_type} inner condition "
                        f"({inner_type}) requires 'threshold'")
    elif cond_type == "touch_bounce":
        if "direction" not in condition:
            errors.append(f"{label}: touch_bounce requires 'direction'")
    elif cond_type == "role_reversal":
        if "role" not in condition:
            errors.append(f"{label}: role_reversal requires 'role'")
    elif cond_type == "wick_touch":
        if "direction" not in condition:
            errors.append(f"{label}: wick_touch requires 'direction'")

    return errors
