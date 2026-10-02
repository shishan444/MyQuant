"""种群初始化与随机 StrategyDNA 生成。

服务对象：EvolutionEngine.evolve 的 init_population 调用（engine
的种群起点）、engine 变异/交叉失败兜底与 operators.mutate_add_layer
新层生成的 create_random_dna（换/造随机个体）、diversity 换血与
多样性维护的高频造新路径、tests/evolution 契约测试。职责边界：
代际循环前的种群构成——祖先/模板/画像引导/自由探索四路来源与
签名去重；不负责评估与选择（归 engine）。
"""
from __future__ import annotations

import random
import uuid
from typing import List, Optional

from core.strategy.dna import (
    SignalRole, SignalGene, LogicGenes, RiskGenes, ExecutionGenes,
    StrategyDNA, TimeframeLayer, derive_role,
)
from core.strategy.validator import validate_dna
from core.features.indicators import INDICATOR_REGISTRY
from core.features.indicator_profile import PROFILES
from core.evolution.operators import mutate_params, mutate_indicator, mutate_logic, mutate_risk, generate_random_condition


# ---------------------------------------------------------------------------
# 经典策略模板（交易研究 7 形态）
# ---------------------------------------------------------------------------

STRATEGY_TEMPLATES = [
    {   # trend_ema：EMA 趋势跟随 + MACD 确认
        "name": "trend_ema",
        "genes": [
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_trigger", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "EMA", "params": {"period": 50},
             "role": "entry_guard", "field": None,
             "condition": {"type": "price_above"}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "exit_trigger", "field": "histogram",
             "condition": {"type": "cross_below", "threshold": 0}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # momentum：RSI + MACD 动量交易
        "name": "momentum",
        "genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_guard", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}},
            {"indicator": "BB", "params": {"period": 20, "std": 2.0},
             "role": "exit_guard", "field": "upper",
             "condition": {"type": "price_above"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # mean_reversion：BB 下/上轨 + RSI 均值回归
        "name": "mean_reversion",
        "genes": [
            {"indicator": "BB", "params": {"period": 20, "std": 2.0},
             "role": "entry_trigger", "field": "lower",
             "condition": {"type": "price_below"}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "lt", "threshold": 35}},
            {"indicator": "BB", "params": {"period": 20, "std": 2.0},
             "role": "exit_trigger", "field": "upper",
             "condition": {"type": "price_above"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # trend_breakout：低 ADX 盘整 + MACD 突破
        "name": "trend_breakout",
        "genes": [
            {"indicator": "ADX", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "lt", "threshold": 25}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_trigger", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "exit_trigger", "field": "histogram",
             "condition": {"type": "cross_below", "threshold": 0}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt", "threshold": 0}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # dual_ma_cross：EMA(10) 上/下穿 EMA(20)
        "name": "dual_ma_cross",
        "genes": [
            {"indicator": "EMA", "params": {"period": 10},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "cross_above_series", "target_indicator": "EMA", "target_params": {"period": 20}}},
            {"indicator": "EMA", "params": {"period": 20},
             "role": "entry_guard", "field": None,
             "condition": {"type": "price_above"}},
            {"indicator": "EMA", "params": {"period": 10},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "cross_below_series", "target_indicator": "EMA", "target_params": {"period": 20}}},
            {"indicator": "EMA", "params": {"period": 20},
             "role": "exit_guard", "field": None,
             "condition": {"type": "price_below"}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # multi_tf_trend：EMA 趋势 + ADX 强度确认
        "name": "multi_tf_trend",
        "genes": [
            {"indicator": "EMA", "params": {"period": 50},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "price_above"}},
            {"indicator": "ADX", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "gt", "threshold": 25}},
            {"indicator": "EMA", "params": {"period": 50},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "price_below"}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt", "threshold": 0}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
    {   # volatility：低 ADX 盘整 + MACD 突破（波动率突破）
        "name": "volatility",
        "genes": [
            {"indicator": "ADX", "params": {"period": 14},
             "role": "entry_guard", "field": None,
             "condition": {"type": "lt", "threshold": 25}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "entry_trigger", "field": "histogram",
             "condition": {"type": "cross_above", "threshold": 0}},
            {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
             "role": "exit_trigger", "field": "histogram",
             "condition": {"type": "cross_below", "threshold": 0}},
            {"indicator": "ATR", "params": {"period": 14},
             "role": "exit_guard", "field": None,
             "condition": {"type": "gt", "threshold": 0}},
        ],
        "logic": {"entry_logic": "AND", "exit_logic": "OR"},
    },
]


# trend 类指标：用 price_above（True = close > 指标 = 看多）
_DIRECTION_TREND_INDICATORS = ["EMA", "SMA", "WMA", "DEMA", "TEMA", "VWMA"]

# band 类指标：在特定 field 上用 price_above
_DIRECTION_BAND_INDICATORS = ["BB", "Keltner", "Donchian"]

# momentum 类指标：用 gt 带方向性阈值（True = 看多）
# 每条配置：(指标名, field 名, 默认阈值, 阈值抖动区间)
_DIRECTION_MOMENTUM_CONFIGS = [
    ("RSI", None, 50, (30, 70)),
    ("MACD", "histogram", 0, (-5, 5)),
    ("CCI", None, 0, (-200, 200)),
    ("ROC", None, 0, (-10, 10)),
    ("CMO", None, 0, (-50, 50)),
    ("TRIX", None, 0, (-1, 1)),
    ("CMF", None, 0, (-0.5, 0.5)),
    ("MFI", None, 50, (30, 70)),
    ("Aroon", "aroon_osc", 0, (-50, 50)),
    ("MultifactorOsc", None, 0, (-1, 1)),
]


def _create_direction_gene() -> SignalGene:
    """以 trend/band/momentum 指标构造 DIRECTION 基因。

    三类统一「True = 看多」语义：
    - trend 类：price_above 条件（close > 指标 = 看多）
    - momentum 类：gt 条件（指标 > 阈值 = 看多）
    - band 类：在 field 上 price_above
    """
    kind = random.choices(
        ["trend", "band", "momentum"],
        weights=[5, 3, 4],
    )[0]

    if kind == "trend":
        return _create_trend_direction_gene()
    elif kind == "band":
        return _create_band_direction_gene()
    else:
        return _create_momentum_direction_gene()


def _create_trend_direction_gene() -> SignalGene:
    """trend 类指标 + price_above 的 DIRECTION 基因。"""
    indicator_name = random.choice(_DIRECTION_TREND_INDICATORS)
    reg = INDICATOR_REGISTRY.get(indicator_name)
    profile = PROFILES.get(indicator_name)

    params = _build_params(reg, profile)
    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=SignalRole.DIRECTION,
        field_name=None,
        condition={"type": "price_above"},
    )


def _create_band_direction_gene() -> SignalGene:
    """band 类指标 + price_above 的 DIRECTION 基因（多输出域随机取 field）。"""
    indicator_name = random.choice(_DIRECTION_BAND_INDICATORS)
    reg = INDICATOR_REGISTRY.get(indicator_name)
    profile = PROFILES.get(indicator_name)

    params = _build_params(reg, profile)

    field_name = None
    if reg and len(reg.output_fields) > 1:
        field_name = random.choice(reg.output_fields)

    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=SignalRole.DIRECTION,
        field_name=field_name,
        condition={"type": "price_above"},
    )


def _create_momentum_direction_gene() -> SignalGene:
    """momentum 类指标 + gt 阈值的 DIRECTION 基因。"""
    indicator_name, field_name, threshold, (lo, hi) = random.choice(
        _DIRECTION_MOMENTUM_CONFIGS
    )
    reg = INDICATOR_REGISTRY.get(indicator_name)
    profile = PROFILES.get(indicator_name)

    params = _build_params(reg, profile)
    # 阈值在默认值附近抖动（不固定用配置默认）
    jittered_threshold = round(random.uniform(lo, hi), 2)

    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=SignalRole.DIRECTION,
        field_name=field_name,
        condition={"type": "gt", "threshold": jittered_threshold},
    )


def _build_params(reg, profile) -> dict:
    """构造指标参数（画像引导优先，无画像时值域内随机）。"""
    if profile and profile.recommended_params and random.random() < profile.follow_probability:
        params = {}
        for pname, pdef in reg.params.items():
            if pname in profile.recommended_params and profile.recommended_params[pname]:
                raw = random.choice(profile.recommended_params[pname])
                params[pname] = int(raw) if pdef.type == "int" else float(raw)
            else:
                params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)
    else:
        params = {}
        for pname, pdef in (reg.params.items() if reg else []):
            val = random.uniform(pdef.min, pdef.max)
            params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
                else round(round(val / pdef.step) * pdef.step, 2)
    return params


def _dna_from_template(
    template: dict,
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: list | None = None,
) -> StrategyDNA:
    """从经典策略模板构造 StrategyDNA。"""
    actual_direction = direction
    signals = []
    for g in template["genes"]:
        signals.append(SignalGene(
            indicator=g["indicator"],
            params=dict(g["params"]),
            role=SignalRole(g["role"]),
            field_name=g.get("field"),
            condition=dict(g["condition"]),
        ))

    # mixed 策略自动补一个 DIRECTION 基因
    if actual_direction == "mixed":
        signals.append(_create_direction_gene())

    logic = template["logic"]

    # timeframe_pool 多周期时构造 MTF 层（执行层 + 至多 2 个其他周期层）
    mtf_layers = None
    if timeframe_pool and len(timeframe_pool) > 1:
        other_tfs = [tf for tf in timeframe_pool if tf != timeframe][:2]
        if other_tfs:
            mtf_layers = [TimeframeLayer(
                timeframe=timeframe,
                signal_genes=list(signals),
                logic_genes=LogicGenes(**logic),
            )]
            for tf in other_tfs:
                mtf_layers.append(create_random_mtf_layer(tf, symbol))

    dna = StrategyDNA(
        signal_genes=signals,
        logic_genes=LogicGenes(**logic),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=None, position_size=0.3,
            leverage=leverage, direction=actual_direction,
        ),
        layers=mtf_layers,
    )

    return dna


def _make_signal_profiled(role: SignalRole, pool: list) -> SignalGene:
    """画像可用时按画像引导构造信号基因（否则回退自由探索）。"""
    if not pool:
        pool[:] = [name for name in INDICATOR_REGISTRY.keys()
                    if not INDICATOR_REGISTRY[name].guard_only]
    indicator_name = random.choice(pool)
    reg = INDICATOR_REGISTRY[indicator_name]
    profile = PROFILES.get(indicator_name)

    if profile and random.random() < profile.follow_probability:
        # 跟随画像推荐用法
        params = {}
        for pname, candidates in profile.recommended_params.items():
            if candidates:
                raw = random.choice(candidates)
                pdef = reg.params.get(pname)
                params[pname] = int(raw) if pdef and pdef.type == "int" else float(raw)
            elif pname in reg.params:
                pdef = reg.params[pname]
                params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)

        # 画像无推荐参数但注册表有参数时，用注册表默认值
        if not profile.recommended_params and reg.params:
            for pname, pdef in reg.params.items():
                params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)

        cond_preset = random.choice(profile.recommended_conditions)
        condition = {"type": cond_preset.type}
        if cond_preset.thresholds:
            condition["threshold"] = random.choice(cond_preset.thresholds)

        field_name = cond_preset.target_field
        if field_name is None and len(reg.output_fields) > 1:
            field_name = random.choice(reg.output_fields)
    else:
        # 自由探索（原始逻辑：候选值或值域随机）
        params = {}
        for pname, pdef in reg.params.items():
            if pdef.candidates:
                params[pname] = int(random.choice(pdef.candidates)) if pdef.type == "int" \
                    else float(random.choice(pdef.candidates))
            else:
                val = random.uniform(pdef.min, pdef.max)
                params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
                    else round(round(val / pdef.step) * pdef.step, 2)

        condition = generate_random_condition(indicator_name, reg)
        field_name = None
        if len(reg.output_fields) > 1:
            field_name = random.choice(reg.output_fields)

    return SignalGene(
        indicator=indicator_name,
        params=params,
        role=role,
        field_name=field_name,
        condition=condition,
    )


def create_random_dna(
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    timeframe_pool: Optional[List[str]] = None,
    leverage: int = 1,
    direction: str = "long",
    indicator_pool: Optional[List[str]] = None,
    profiled: bool = True,
) -> StrategyDNA:
    """生成完全随机但合法的 StrategyDNA。

    Args:
        profiled: True 时用指标画像引导生成；False 时全随机信号
            （自由探索）。

    非法产出回退到 RSI 双基因保底形态（validate_dna 不通过时）。
    """
    # "mixed" 原样保留；多空决策由 DIRECTION 基因承载
    actual_direction = direction

    available_indicators = list(indicator_pool) if indicator_pool else list(INDICATOR_REGISTRY.keys())
    # 剔除进化中不可计算的指标：
    # - compute_mode="lazy" 的指标被 compute_all_indicators(skip_lazy=True) 跳过
    # - VWAP 在 _compute_indicator 无计算实现
    _NO_COMPUTE = {"VWAP", "FractalEntropy", "MultifactorOsc", "VolumeProfile"}
    available_indicators = [n for n in available_indicators if n not in _NO_COMPUTE]
    trigger_indicators = [
        name for name in available_indicators
        if name in INDICATOR_REGISTRY and not INDICATOR_REGISTRY[name].guard_only
    ]
    all_indicators = [name for name in available_indicators if name in INDICATOR_REGISTRY]

    def _make_signal(role: SignalRole, pool: list) -> SignalGene:
        if profiled:
            return _make_signal_profiled(role, pool)
        # 自由探索：全随机、无画像引导
        if not pool:
            pool[:] = [name for name in INDICATOR_REGISTRY.keys()
                        if not (role.value.endswith("guard") and INDICATOR_REGISTRY.get(name, type('', (), {})()).guard_only)]
        indicator_name = random.choice(pool)
        reg = INDICATOR_REGISTRY[indicator_name]
        params = _random_params(indicator_name)
        condition = _random_condition(indicator_name)
        field_name = None
        if len(reg.output_fields) > 1:
            field_name = random.choice(reg.output_fields)
        return SignalGene(
            indicator=indicator_name,
            params=params,
            role=role,
            field_name=field_name,
            condition=condition,
        )

    # 信号基因构成：至少 1 个 entry trigger + 1 个 exit trigger
    signals = []
    signals.append(_make_signal(SignalRole.ENTRY_TRIGGER, trigger_indicators))

    if random.random() < 0.5:
        signals.append(_make_signal(SignalRole.ENTRY_GUARD, all_indicators))

    signals.append(_make_signal(SignalRole.EXIT_TRIGGER, trigger_indicators))

    if random.random() < 0.4:
        signals.append(_make_signal(SignalRole.EXIT_GUARD, all_indicators))

    # mixed 策略自动补一个 DIRECTION 基因
    if actual_direction == "mixed":
        signals.append(_create_direction_gene())

    entry_logic = random.choice(["AND", "OR"])
    exit_logic = random.choice(["AND", "OR"])

    stop_loss = round(random.uniform(0.01, 0.15), 4)
    position_size = round(random.uniform(0.10, 0.60), 2)
    take_profit = None

    dna = StrategyDNA(
        signal_genes=signals,
        logic_genes=LogicGenes(entry_logic=entry_logic, exit_logic=exit_logic),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(stop_loss=stop_loss, take_profit=take_profit,
                             position_size=position_size, leverage=leverage,
                             direction=actual_direction),
    )

    # 校验失败回退保底形态（RSI 双基因，不重试）
    result = validate_dna(dna)
    if not result.is_valid:
        fallback_signals = [
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, None,
                       {"type": "gt", "threshold": 70}),
        ]
        if actual_direction == "mixed":
            fallback_signals.append(SignalGene(
                "EMA", {"period": 50}, SignalRole.DIRECTION, None,
                {"type": "price_above"},
            ))
        dna = StrategyDNA(
            signal_genes=fallback_signals,
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="OR"),
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3,
                                 leverage=leverage, direction=actual_direction),
            execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        )

    # MTF 层生成：执行周期层 + 其余周期各一层（至多 2 个）
    if timeframe_pool and len(timeframe_pool) > 1:
        other_tfs = [tf for tf in timeframe_pool if tf != timeframe][:2]
        if other_tfs:
            layers = [TimeframeLayer(
                timeframe=timeframe,
                signal_genes=list(dna.signal_genes),
                logic_genes=LogicGenes(
                    entry_logic=dna.logic_genes.entry_logic,
                    exit_logic=dna.logic_genes.exit_logic,
                ),
            )]
            for tf in other_tfs:
                layers.append(create_random_mtf_layer(tf, symbol))
            dna.layers = layers
            dna._layers_explicit = True
            dna.cross_layer_logic = random.choice(["AND", "OR"])

    return dna


def create_random_mtf_layer(
    timeframe: str,
    symbol: str = "BTCUSDT",
) -> TimeframeLayer:
    """为 MTF 策略生成附加周期层的单个随机 TimeframeLayer。

    timeframe 参数取执行周期以外的周期（周期位置为非执行位），
    但 role 字段按周期推导（60%）或标 "execution"（40%）。
    """
    trigger_indicators = [
        name for name, defn in INDICATOR_REGISTRY.items()
        if not defn.guard_only
    ]
    all_indicators = list(INDICATOR_REGISTRY.keys())

    signals = []
    ind = random.choice(trigger_indicators)
    signals.append(SignalGene(
        indicator=ind,
        params=_random_params(ind),
        role=SignalRole.ENTRY_TRIGGER,
        condition=_random_condition(ind),
    ))

    if random.random() < 0.5:
        ind = random.choice(all_indicators)
        signals.append(SignalGene(
            indicator=ind,
            params=_random_params(ind),
            role=SignalRole.ENTRY_GUARD,
            condition=_random_condition(ind),
        ))

    ind = random.choice(trigger_indicators)
    signals.append(SignalGene(
        indicator=ind,
        params=_random_params(ind),
        role=SignalRole.EXIT_TRIGGER,
        condition=_random_condition(ind),
    ))

    return TimeframeLayer(
        timeframe=timeframe,
        signal_genes=signals,
        logic_genes=LogicGenes(
            entry_logic=random.choice(["AND", "OR"]),
            exit_logic=random.choice(["AND", "OR"]),
        ),
        role=derive_role(timeframe) if random.random() < 0.6 else "execution",
    )


def _random_params(indicator_name: str) -> dict:
    """为指标生成值域内随机参数（步长对齐）。"""
    reg = INDICATOR_REGISTRY.get(indicator_name)
    if not reg:
        return {}
    params = {}
    for pname, pdef in reg.params.items():
        val = random.uniform(pdef.min, pdef.max)
        params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
            else round(round(val / pdef.step) * pdef.step, 2)
    return params


def _random_condition(indicator_name: str) -> dict:
    """为指标生成随机条件（委托 operators.generate_random_condition）。"""
    return generate_random_condition(indicator_name)


def init_population(
    size: int = 15,
    ancestor: Optional[StrategyDNA] = None,
    extra_ancestors: Optional[List[StrategyDNA]] = None,
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    leverage: int = 1,
    direction: str = "long",
    timeframe_pool: Optional[List[str]] = None,
    indicator_pool: Optional[List[str]] = None,
    exclude_signatures: Optional[set] = None,
) -> List[StrategyDNA]:
    """初始化种群（祖先保位 + 模板/画像/自由探索按 40/40/20 配比）。

    Args:
        size: 种群规模。
        ancestor: 起始祖先（首个个体，None 时用随机模板种子）。
        extra_ancestors: 多起点附加祖先（如 top 3）。
        timeframe: K 线周期。
        symbol: 交易对。
        leverage: 任务级杠杆约束。
        direction: 任务级方向约束。
        timeframe_pool: MTF 层生成的可用周期集。
        indicator_pool: 限定变异面的可用指标集。
        exclude_signatures: 排除的基因签名集（新种群避免与历史撞签）。
    """
    population = []

    # 首个个体是祖先本身
    if ancestor is not None:
        population.append(ancestor)
    elif STRATEGY_TEMPLATES:
        # 无祖先时以随机经典模板为种子
        template = random.choice(STRATEGY_TEMPLATES)
        population.append(_dna_from_template(template, timeframe, symbol, leverage, direction, timeframe_pool))
    else:
        population.append(create_random_dna(timeframe, symbol,
                                            leverage=leverage, direction=direction,
                                            timeframe_pool=timeframe_pool,
                                            indicator_pool=indicator_pool))

    # 多起点附加祖先（连续进化场景）
    if extra_ancestors:
        for a in extra_ancestors:
            if a not in population:
                population.append(a)

    # 其余个体按 40/40/20 配比生成
    remaining = size - len(population)
    n_template = max(1, int(remaining * 0.4))   # 40% 模板变异
    n_profiled = max(1, int(remaining * 0.4))   # 40% 画像引导随机
    n_random = remaining - n_template - n_profiled  # 20% 自由探索

    mutation_funcs = [mutate_params, mutate_indicator, mutate_logic, mutate_risk]

    # 模板变异：随机经典模板构造种子后做一次变异
    for _ in range(n_template):
        if len(population) < size:
            template = random.choice(STRATEGY_TEMPLATES)
            seed = _dna_from_template(template, timeframe, symbol, leverage, direction, timeframe_pool)
            mut_func = random.choice(mutation_funcs)
            try:
                child = mut_func(seed)
                result = validate_dna(child)
                if result.is_valid:
                    population.append(child)
                else:
                    population.append(seed)
            except Exception:
                population.append(seed)

    # 画像引导随机 DNA
    for _ in range(n_profiled):
        if len(population) < size:
            population.append(create_random_dna(
                timeframe, symbol,
                leverage=leverage, direction=direction,
                timeframe_pool=timeframe_pool,
                indicator_pool=indicator_pool,
            ))

    # 自由探索：无画像引导的随机 DNA
    for _ in range(n_random):
        if len(population) < size:
            population.append(create_random_dna(
                timeframe, symbol,
                leverage=leverage, direction=direction,
                timeframe_pool=timeframe_pool,
                indicator_pool=indicator_pool,
                profiled=False,
            ))

    # 签名去重：剔除命中 exclude_signatures 或种群内重复签名的个体
    if exclude_signatures:
        deduped = []
        seen = set()
        for ind in population:
            sig = ind.gene_signature
            if sig not in exclude_signatures and sig not in seen:
                deduped.append(ind)
                seen.add(sig)
        # 去重削员过多时以随机个体回填（限 3 倍尝试）
        attempts = 0
        while len(deduped) < size and attempts < size * 3:
            candidate = create_random_dna(
                timeframe, symbol,
                leverage=leverage, direction=direction,
                timeframe_pool=timeframe_pool,
                indicator_pool=indicator_pool,
            )
            sig = candidate.gene_signature
            if sig not in exclude_signatures and sig not in seen:
                deduped.append(candidate)
                seen.add(sig)
            attempts += 1
        population = deduped

    return population
