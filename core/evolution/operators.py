"""进化算子库：变异 + 交叉。

服务对象：EvolutionEngine 的 mutation_pool 与 crossover 调用
（engine 按停滞档位配权调用）、population.init_population 的模板
变异组（直调四变异算子与 generate_random_condition）、
tests/evolution 契约测试直调。
职责边界：单代内的基因扰动与重组——产出新 StrategyDNA（新
strategy_id/parent_ids/mutation_ops 溯源），不负责选择与种群
结构（归 engine/population）。

变异类型：
1. 参数变异：指标参数在值域内有界扰动
2. 指标替换：换同类可互换指标
3. 逻辑变异：AND/OR 翻转、增删守卫条件
4. 风险变异：stop_loss/take_profit/position_size 扰动
"""
from __future__ import annotations

import copy
import itertools
import random
import uuid
from typing import Optional

from core.strategy.dna import (
    SignalRole, SignalGene, StrategyDNA, derive_role,
)
from core.strategy.validator import validate_dna
from core.features.indicators import INDICATOR_REGISTRY, get_interchangeable
from core.features.registry import IndicatorDef
from core.features.indicator_profile import PROFILES


def _new_id() -> str:
    return str(uuid.uuid4())


def _pick_signal_pool(data: dict) -> dict:
    """选一个信号基因池做变异：基础 signal_genes 或随机 MTF 层。

    返回带基因列表可变引用的 dict：
      {"genes": <list>}
    基础层取 data["signal_genes"]；MTF 层取
    data["layers"][i]["signal_genes"]。
    """
    layers = data.get("layers")
    if not layers:
        return {"genes": data["signal_genes"]}

    # 基础层与其余各层等概率抽取（均匀 n_targets 选一）
    n_targets = 1 + len(layers)  # 基础 + 每层
    choice = random.randint(0, n_targets - 1)
    if choice == 0:
        return {"genes": data["signal_genes"]}
    else:
        layer_idx = choice - 1
        return {"genes": layers[layer_idx]["signal_genes"]}


def generate_random_condition(
    indicator_name: str,
    reg: IndicatorDef | None = None,
    df_columns: list | None = None,
    use_profile: bool = True,
) -> dict:
    """为指定指标生成随机条件 dict。

    use_profile 为真且画像存在时，按 follow_probability 概率跟随
    画像推荐条件；否则从 supported_conditions 自由探索。

    Args:
        indicator_name: 指标名。
        reg: 注册表 IndicatorDef（None 时自取）。
        df_columns: 可用 DataFrame 列（当前未使用）。
        use_profile: 是否采用指标画像推荐。
    """
    if reg is None:
        reg = INDICATOR_REGISTRY.get(indicator_name)
    if reg is None or not reg.supported_conditions:
        return {"type": "gt", "threshold": 0.0}

    # 条件类型：画像推荐（use_profile 时）或自由探索
    cond_type = None
    cond_preset = None
    if use_profile:
        profile = PROFILES.get(indicator_name)
        if profile and profile.recommended_conditions and random.random() < profile.follow_probability:
            cond_preset = random.choice(profile.recommended_conditions)
            cond_type = cond_preset.type
    if cond_type is None:
        cond_type = random.choice(reg.supported_conditions)

    condition: dict = {"type": cond_type}

    # 类型专属字段填充：画像路径与自由探索路径走同一填充逻辑——
    # 此前画像路径只带 type/threshold/target_field 提前返回，复杂类型
    # 缺必需字段（lookback_any 缺 window/inner、cross_above_series 缺
    # target_indicator），产出非法 DNA。
    if cond_preset and cond_preset.thresholds and cond_type in ("eq", "lt", "gt", "le", "ge"):
        condition["threshold"] = random.choice(cond_preset.thresholds)
    elif cond_type == "eq":
        # 形态类指标：值等于 1 视为形态成立
        condition["threshold"] = 1
    elif cond_type in ("lt", "gt", "le", "ge"):
        if indicator_name == "RSI":
            condition["threshold"] = random.choice([25, 30, 35, 40, 60, 65, 70, 75])
        else:
            condition["threshold"] = round(random.uniform(-1, 1), 2)
    elif cond_type in ("cross_above_series", "cross_below_series"):
        # 目标指标取自同类可互换集或 trend 类
        target = _pick_series_target(indicator_name, reg)
        condition["target_indicator"] = target
        target_reg = INDICATOR_REGISTRY.get(target)
        if target_reg and target_reg.params:
            condition["target_params"] = {
                k: int(v.default) if v.type == "int" else float(v.default)
                for k, v in target_reg.params.items()
            }
        else:
            condition["target_params"] = {}
    elif cond_type in ("lookback_any", "lookback_all"):
        window = random.choice([3, 5, 8, 10])
        # 内层条件取简单形态
        inner_types = ["price_above", "price_below", "gt", "lt"]
        inner_type = random.choice(inner_types)
        inner: dict = {"type": inner_type}
        if inner_type in ("lt", "gt"):
            inner["threshold"] = round(random.uniform(-1, 1), 2)
            if inner_type == "gt":
                inner["threshold"] = random.choice([0.0, 0.5, 1.0])
            else:
                inner["threshold"] = random.choice([0.0, -0.5, -1.0])
        condition["window"] = window
        condition["inner"] = inner
    elif cond_type == "within_bars_after":
        # R4 时序动作构造分支（fix-mtf-execution-consistency 同步点⑧）：
        # 词表收录面（:91 随机选择）自动获得，构造面须显式分支——缺此
        # 分支时 1/17 概率产出裸 {"type": ...}（缺 window/inner）→求值
        # KeyError→batch 降级全 False→个体适应度静默失真（收录与构造
        # 两面独立）。形制沿 lookback 先例：window 取既有窗池、inner
        # 取简单形态（嵌套形 {"type", "threshold"}——登记面平铺键经
        # 翻译分支转换，两域键形不得混植）。
        window = random.choice([3, 5, 8, 10])
        inner_types = ["price_above", "price_below", "gt", "lt"]
        inner_type = random.choice(inner_types)
        inner: dict = {"type": inner_type}
        if inner_type in ("lt", "gt"):
            if inner_type == "gt":
                inner["threshold"] = random.choice([0.0, 0.5, 1.0])
            else:
                inner["threshold"] = random.choice([0.0, -0.5, -1.0])
        condition["window"] = window
        condition["inner"] = inner
    elif cond_type == "touch_bounce":
        condition["direction"] = random.choice(["support", "resistance"])
        condition["proximity_pct"] = random.choice([0.005, 0.01, 0.02])
        condition["bounce_pct"] = random.choice([0.003, 0.005, 0.01])
    elif cond_type == "role_reversal":
        condition["role"] = random.choice(["support", "resistance"])
        condition["lookback"] = random.choice([5, 10, 15, 20])
    elif cond_type == "wick_touch":
        condition["direction"] = random.choice(["above", "below"])
        condition["proximity_pct"] = random.choice([0.005, 0.01, 0.02])

    # 画像预设的 target_field 覆盖（如 BB 的 upper/middle/lower）
    if cond_preset and cond_preset.target_field:
        condition["target_field"] = cond_preset.target_field

    return condition


def _pick_series_target(indicator_name: str, reg: IndicatorDef) -> str:
    """为 cross_above/below_series 选合适的目标指标。"""
    # 同类可互换指标优先
    same_cat = get_interchangeable(indicator_name)
    # trend 类指标作常用目标补充
    trend_indicators = [name for name, d in INDICATOR_REGISTRY.items()
                        if d.category == "trend" and name != indicator_name]
    candidates = same_cat + trend_indicators
    # 剔除 guard_only（守卫专用不可作触发目标）
    candidates = [c for c in candidates if not INDICATOR_REGISTRY.get(c, IndicatorDef("", {}, [], [], guard_only=True)).guard_only]
    if not candidates:
        return "EMA"
    return random.choice(candidates)


# ---------------------------------------------------------------------------
# MTF（多周期）变异算子
# ---------------------------------------------------------------------------

# 候选周期标准层级（层的增删改的默认候选集）
_STANDARD_TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d", "3d"]


def mutate_add_layer(dna: StrategyDNA, candidate_timeframes: list | None = None) -> StrategyDNA:
    """插入一个新的 timeframe 层（信号随机生成）。

    从候选集排除已占用周期后随机取一个（无邻接偏好）。
    """
    if candidate_timeframes is None:
        candidate_timeframes = _STANDARD_TIMEFRAMES

    # 硬上限：最多 3 层
    if dna.layers and len(dna.layers) >= 3:
        return dna

    existing = set(dna.timeframes)
    candidates = [tf for tf in candidate_timeframes if tf not in existing]
    if not candidates:
        return dna

    new_tf = random.choice(candidates)

    # 新层信号以随机 DNA 生成
    from core.evolution.population import create_random_dna
    seed_dna = create_random_dna(timeframe=new_tf, symbol=dna.execution_genes.symbol)

    new_layer_data = {
        "timeframe": new_tf,
        "signal_genes": [sg.to_dict() for sg in seed_dna.signal_genes],
        "logic_genes": seed_dna.logic_genes.to_dict(),
        "role": derive_role(new_tf),
    }

    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["add_layer"]
    data["generation"] = dna.generation + 1

    if "layers" not in data or not data["layers"]:
        # 首次加层：既有信号包装为基础层
        data["layers"] = [{
            "timeframe": dna.execution_genes.timeframe,
            "signal_genes": data["signal_genes"],
            "logic_genes": data["logic_genes"],
        }]

    data["layers"].append(new_layer_data)
    return StrategyDNA.from_dict(data)


def mutate_remove_layer(dna: StrategyDNA) -> StrategyDNA:
    """移除一个非执行周期层（至少保留 1 层）。"""
    if not dna.is_mtf:
        return dna

    data = dna.to_dict()
    layers = data.get("layers", [])
    if len(layers) <= 1:
        return dna

    exec_tf = data["execution_genes"]["timeframe"]
    removable = [i for i, l in enumerate(layers) if l["timeframe"] != exec_tf]
    if not removable:
        return dna

    idx = random.choice(removable)
    layers.pop(idx)

    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["remove_layer"]
    data["generation"] = dna.generation + 1
    data["layers"] = layers
    return StrategyDNA.from_dict(data)


def mutate_layer_timeframe(dna: StrategyDNA, candidate_timeframes: list | None = None) -> StrategyDNA:
    """改换一个随机非执行层的周期。"""
    if candidate_timeframes is None:
        candidate_timeframes = _STANDARD_TIMEFRAMES

    if not dna.is_mtf:
        return dna

    data = dna.to_dict()
    layers = data.get("layers", [])
    exec_tf = data["execution_genes"]["timeframe"]
    changeable = [i for i, l in enumerate(layers) if l["timeframe"] != exec_tf]
    if not changeable:
        return dna

    idx = random.choice(changeable)
    current_tf = layers[idx]["timeframe"]
    # 排除当前周期、执行周期与兄弟层已占用周期（保持层周期不重）
    sibling_tfs = {layers[j]["timeframe"] for j in range(len(layers)) if j != idx}
    alternatives = [tf for tf in candidate_timeframes
                    if tf != current_tf and tf != exec_tf and tf not in sibling_tfs]
    if not alternatives:
        return dna

    layers[idx]["timeframe"] = random.choice(alternatives)

    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["layer_timeframe"]
    data["generation"] = dna.generation + 1
    data["layers"] = layers
    return StrategyDNA.from_dict(data)


def mutate_cross_logic(dna: StrategyDNA) -> StrategyDNA:
    """翻转跨层逻辑（AND <-> OR）。"""
    if not dna.is_mtf:
        return dna

    data = dna.to_dict()
    current = data.get("cross_layer_logic", "AND")
    data["cross_layer_logic"] = "OR" if current == "AND" else "AND"
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["cross_logic"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def mutate_mtf_mode(dna: StrategyDNA) -> StrategyDNA:
    """轮换 MTF mode 选项。

    带 entry_trigger_policy 的个体 no-op（trigger-policy D10/6.3 MTF
    划界）：policy 与 MTF 层间组合语义无定义——不产生该形态子代
    （引擎侧另有防御拒绝兜底）。
    """
    if not dna.is_mtf:
        return dna
    if dna.entry_trigger_policy is not None:
        return dna

    modes = [None, "direction", "confluence", "direction+confluence"]
    current_idx = modes.index(dna.mtf_mode) if dna.mtf_mode in modes else 0
    new_idx = (current_idx + random.randint(1, len(modes) - 1)) % len(modes)

    data = dna.to_dict()
    data["mtf_mode"] = modes[new_idx]
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["mtf_mode"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def mutate_confluence_threshold(dna: StrategyDNA) -> StrategyDNA:
    """confluence_threshold 在 [0.1, 0.9] 内多项式变异。"""
    data = dna.to_dict()
    current = data.get("confluence_threshold", 0.3)
    new_val = _polynomial_mutation(current, 0.1, 0.9, eta=20.0)
    data["confluence_threshold"] = round(new_val, 3)
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["confluence_threshold"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def mutate_proximity_mult(dna: StrategyDNA) -> StrategyDNA:
    """proximity_mult 在 [0.5, 3.0] 内多项式变异。"""
    data = dna.to_dict()
    current = data.get("proximity_mult", 1.5)
    new_val = _polynomial_mutation(current, 0.5, 3.0, eta=20.0)
    data["proximity_mult"] = round(new_val, 3)
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["proximity_mult"]
    data["generation"] = dna.generation + 1
    return StrategyDNA.from_dict(data)


def _polynomial_mutation(
    value: float, lower: float, upper: float, eta: float = 20.0,
) -> float:
    """多项式有界变异（Deb & Goyal, 1996）。

    高概率在当前值附近小幅扰动、低概率大幅跳变。eta 控制分布
    形状：
    - 高 eta（如 20）：小步变异（exploitation）
    - 低 eta（如 5）：大步变异（exploration）

    Args:
        value: 当前参数值。
        lower: 下界。
        upper: 上界。
        eta: 分布指数（越高越集中）。

    Returns:
        变异后的值（限 [lower, upper]）。
    """
    delta = random.random()
    if delta < 0.5:
        xy = 1.0 - (value - lower) / max(upper - lower, 1e-10)
        val = 2.0 * delta + (1.0 - 2.0 * delta) * (xy ** (eta + 1.0))
        deltaq = val ** (1.0 / (eta + 1.0)) - 1.0
    else:
        xy = 1.0 - (upper - value) / max(upper - lower, 1e-10)
        val = 2.0 * (1.0 - delta) + 2.0 * (delta - 0.5) * (xy ** (eta + 1.0))
        deltaq = 1.0 - val ** (1.0 / (eta + 1.0))
    return max(lower, min(upper, value + deltaq * (upper - lower)))


def mutate_params(dna: StrategyDNA) -> StrategyDNA:
    """对随机信号基因的随机参数做变异（基础层或 MTF 层）。"""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["param_mutation"]
    data["generation"] = dna.generation + 1

    # 变异目标基因池：基础层或随机层
    gene_pool = _pick_signal_pool(data)

    if not gene_pool["genes"]:
        return StrategyDNA.from_dict(data)

    # 随机取一个带 params 的信号基因
    candidates = [i for i, sg in enumerate(gene_pool["genes"])
                  if sg.get("params")]
    if not candidates:
        return StrategyDNA.from_dict(data)

    idx = random.choice(candidates)
    gene = gene_pool["genes"][idx]
    indicator = gene["indicator"]
    params = dict(gene["params"])

    if indicator in INDICATOR_REGISTRY:
        reg = INDICATOR_REGISTRY[indicator]
        param_name = random.choice(list(reg.params.keys()))
        pdef = reg.params[param_name]

        # 1. 画像推荐参数（最高优先）
        profile = PROFILES.get(indicator)
        if profile and param_name in profile.recommended_params:
            rec_vals = profile.recommended_params[param_name]
            if rec_vals and random.random() < profile.follow_probability:
                new_val = random.choice(rec_vals)
                params[param_name] = int(new_val) if pdef.type == "int" else round(float(new_val), 4)
                gene["params"] = params
                return StrategyDNA.from_dict(data)

        # 2. 注册表候选值（回退）
        if pdef.candidates and random.random() < 0.5:
            candidates_filtered = [c for c in pdef.candidates
                                   if pdef.min <= c <= pdef.max]
            if candidates_filtered:
                new_val = random.choice(candidates_filtered)
                params[param_name] = int(new_val) if pdef.type == "int" else round(float(new_val), 4)
                gene["params"] = params
                return StrategyDNA.from_dict(data)

        # 3. 多项式有界变异——比均匀随机步长的探索/利用平衡更好
        current = params.get(param_name, pdef.default)
        new_val = _polynomial_mutation(
            current, pdef.min, pdef.max, eta=20.0,
        )
        new_val = pdef.clamp(round(new_val / pdef.step) * pdef.step)
        params[param_name] = int(new_val) if pdef.type == "int" else round(float(new_val), 4)
        gene["params"] = params

    return StrategyDNA.from_dict(data)


def mutate_indicator(dna: StrategyDNA) -> StrategyDNA:
    """以同类可互换指标替换一个随机指标（基础层或 MTF 层）。"""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["indicator_replacement"]
    data["generation"] = dna.generation + 1

    gene_pool = _pick_signal_pool(data)

    if not gene_pool["genes"]:
        return StrategyDNA.from_dict(data)

    idx = random.randint(0, len(gene_pool["genes"]) - 1)
    gene = gene_pool["genes"][idx]
    current = gene["indicator"]

    alternatives = get_interchangeable(current)
    if not alternatives:
        return StrategyDNA.from_dict(data)

    role = gene.get("role", "entry_trigger")
    is_trigger = role.endswith("_trigger")
    if is_trigger:
        alternatives = [a for a in alternatives
                        if not INDICATOR_REGISTRY[a].guard_only]

    if not alternatives:
        return StrategyDNA.from_dict(data)

    new_indicator = random.choice(alternatives)
    reg = INDICATOR_REGISTRY[new_indicator]

    # 参数优先取画像推荐
    profile = PROFILES.get(new_indicator)
    if profile and profile.recommended_params and random.random() < profile.follow_probability:
        new_params = {}
        for pname, pdef in reg.params.items():
            if pname in profile.recommended_params and profile.recommended_params[pname]:
                raw = random.choice(profile.recommended_params[pname])
                new_params[pname] = int(raw) if pdef.type == "int" else float(raw)
            else:
                new_params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)
    else:
        new_params = {k: int(v.default) if v.type == "int" else float(v.default)
                      for k, v in reg.params.items()}

    condition = generate_random_condition(new_indicator, reg)

    gene["indicator"] = new_indicator
    gene["params"] = new_params
    gene["field"] = None if len(reg.output_fields) == 1 else random.choice(reg.output_fields)
    gene["condition"] = condition

    return StrategyDNA.from_dict(data)


def mutate_logic(dna: StrategyDNA) -> StrategyDNA:
    """翻转 AND/OR 逻辑（基础层或随机 MTF 层）。"""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["logic_mutation"]
    data["generation"] = dna.generation + 1

    action = random.choice(["flip_entry", "flip_exit", "flip_both"])

    # 逻辑基因目标：基础层或随机层
    targets = [("base", data["logic_genes"])]
    for i, layer in enumerate(data.get("layers", [])):
        targets.append((f"layer_{i}", layer["logic_genes"]))

    _, target_logic = random.choice(targets)

    if action == "flip_entry" or action == "flip_both":
        current = target_logic["entry_logic"]
        target_logic["entry_logic"] = "OR" if current == "AND" else "AND"
    if action == "flip_exit" or action == "flip_both":
        current = target_logic["exit_logic"]
        target_logic["exit_logic"] = "OR" if current == "AND" else "AND"

    return StrategyDNA.from_dict(data)


def mutate_add_signal(dna: StrategyDNA) -> StrategyDNA:
    """向策略追加一个随机守卫或 DIRECTION 信号基因（基础层或 MTF 层）。"""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["add_signal"]
    data["generation"] = dna.generation + 1

    gene_pool = _pick_signal_pool(data)

    if not gene_pool["genes"]:
        return StrategyDNA.from_dict(data)

    # 盘点既有角色
    has_entry_guard = any(sg.get("role") == "entry_guard" for sg in gene_pool["genes"])
    has_exit_guard = any(sg.get("role") == "exit_guard" for sg in gene_pool["genes"])
    has_direction = any(sg.get("role") == "direction" for sg in gene_pool["genes"])

    # mixed 策略缺 DIRECTION 基因时有一定概率补上
    is_mixed = data.get("risk_genes", {}).get("direction") == "mixed"
    if is_mixed and not has_direction and random.random() < 0.2:
        from core.evolution.population import _create_direction_gene
        gene = _create_direction_gene()
        new_gene = {
            "indicator": gene.indicator,
            "params": gene.params,
            "role": "direction",
            "field": gene.field_name,
            "condition": gene.condition,
        }
        gene_pool["genes"].append(new_gene)
        return StrategyDNA.from_dict(data)

    # 决定追加哪种守卫（缺哪个优先补哪个）
    if not has_entry_guard and (has_exit_guard or random.random() < 0.5):
        role = "entry_guard"
    elif not has_exit_guard:
        role = "exit_guard"
    else:
        role = random.choice(["entry_guard", "exit_guard"])

    # 指标选择（守卫角色全指标可用）
    all_indicators = [name for name in INDICATOR_REGISTRY.keys()
                      if not INDICATOR_REGISTRY[name].guard_only or role.endswith("_guard")]
    if not all_indicators:
        return StrategyDNA.from_dict(data)

    indicator_name = random.choice(all_indicators)
    reg = INDICATOR_REGISTRY[indicator_name]

    # 参数优先取画像推荐
    profile = PROFILES.get(indicator_name)
    if profile and profile.recommended_params and random.random() < profile.follow_probability:
        new_params = {}
        for pname, pdef in reg.params.items():
            if pname in profile.recommended_params and profile.recommended_params[pname]:
                raw = random.choice(profile.recommended_params[pname])
                new_params[pname] = int(raw) if pdef.type == "int" else float(raw)
            else:
                new_params[pname] = int(pdef.default) if pdef.type == "int" else float(pdef.default)
    else:
        new_params = {}
        for pname, pdef in reg.params.items():
            val = random.uniform(pdef.min, pdef.max)
            new_params[pname] = int(round(val / pdef.step) * pdef.step) if pdef.type == "int" \
                else round(round(val / pdef.step) * pdef.step, 2)

    condition = generate_random_condition(indicator_name, reg)
    field_name = None if len(reg.output_fields) <= 1 else random.choice(reg.output_fields)

    new_gene = {
        "indicator": indicator_name,
        "params": new_params,
        "role": role,
        "field": field_name,
        "condition": condition,
    }

    gene_pool["genes"].append(new_gene)
    return StrategyDNA.from_dict(data)


def mutate_remove_signal(dna: StrategyDNA) -> StrategyDNA:
    """移除一个随机守卫信号基因（基础层或 MTF 层，触发器不移除）。"""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["remove_signal"]
    data["generation"] = dna.generation + 1

    gene_pool = _pick_signal_pool(data)

    # 只移除守卫，绝不动触发器（触发器是策略骨架）
    guard_indices = [i for i, sg in enumerate(gene_pool["genes"])
                     if sg.get("role", "").endswith("_guard")]
    if not guard_indices:
        return StrategyDNA.from_dict(data)

    idx = random.choice(guard_indices)
    gene_pool["genes"].pop(idx)
    return StrategyDNA.from_dict(data)


def mutate_risk(dna: StrategyDNA) -> StrategyDNA:
    """风险参数在值域内扰动。"""
    data = dna.to_dict()
    data["strategy_id"] = _new_id()
    data["parent_ids"] = [dna.strategy_id]
    data["mutation_ops"] = list(dna.mutation_ops) + ["risk_mutation"]
    data["generation"] = dna.generation + 1

    risk = data["risk_genes"]

    # stop_loss 变异：值域 [0.005, 0.20]、步长 0.005
    if random.random() < 0.5:
        current = risk["stop_loss"]
        delta = random.choice([-1, 1]) * 0.005 * random.randint(1, 5)
        risk["stop_loss"] = round(max(0.005, min(0.20, current + delta)), 4)

    # position_size 变异：值域 [0.10, 1.0]、步长 0.05
    if random.random() < 0.5:
        current = risk["position_size"]
        delta = random.choice([-1, 1]) * 0.05 * random.randint(1, 3)
        risk["position_size"] = round(max(0.10, min(1.0, current + delta)), 2)

    # take_profit 变异：None 时按 stop_loss 倍数初始化，否则步长扰动
    if random.random() < 0.3:
        if risk["take_profit"] is None:
            risk["take_profit"] = round(risk["stop_loss"] * random.uniform(1.5, 3.0), 4)
        else:
            delta = random.choice([-1, 1]) * 0.01 * random.randint(1, 3)
            risk["take_profit"] = round(
                max(risk["stop_loss"] + 0.005, risk["take_profit"] + delta), 4
            )

    # direction 不参与变异：评估前由任务级 direction 强制覆盖
    # （engine/evaluate_population 三处），变异产物必然被抹除

    data["risk_genes"] = risk
    return StrategyDNA.from_dict(data)


def crossover(parent_a: StrategyDNA, parent_b: StrategyDNA) -> StrategyDNA:
    """双亲基因重组产子代。

    保持 MTF 层结构：对应层的信号基因做交叉。双亲须有相同层结构
    （相同的周期数——层多的一方多余层直接拷贝）。
    """
    child_signals = []
    a_genes = parent_a.signal_genes
    b_genes = parent_b.signal_genes

    # 混合方式：entry 取一方、exit 取另一方
    entry_a = [g for g in a_genes if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)]
    exit_b = [g for g in b_genes if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)]

    child_signals.extend(entry_a if entry_a else [a_genes[0]] if a_genes else [])
    child_signals.extend(exit_b if exit_b else [b_genes[-1]] if b_genes else [])

    # DIRECTION 基因：任一亲代有则必保留一个
    direction_a = [g for g in a_genes if g.role == SignalRole.DIRECTION]
    direction_b = [g for g in b_genes if g.role == SignalRole.DIRECTION]
    direction_candidates = direction_a or direction_b
    if direction_candidates:
        child_signals.append(random.choice(direction_candidates))

    if not child_signals:
        child_signals = list(a_genes)

    # 逻辑与风险基因各自随机取一方
    logic = random.choice([parent_a.logic_genes, parent_b.logic_genes])
    risk = random.choice([parent_a.risk_genes, parent_b.risk_genes])

    # --- MTF 层交叉（保持结构） ---
    child_layers = None
    child_cross_logic = "AND"

    a_has_layers = parent_a.layers is not None and len(parent_a.layers) > 0
    b_has_layers = parent_b.layers is not None and len(parent_b.layers) > 0

    if a_has_layers and b_has_layers:
        # 双亲都有层：对应层交叉
        from core.strategy.dna import TimeframeLayer, LogicGenes
        child_layers = []
        for la, lb in itertools.zip_longest(parent_a.layers, parent_b.layers):
            # 层数不等时多出的一侧整层拷贝
            if lb is None:
                # 亲代 a 多出的层，直接拷贝
                child_layers.append(TimeframeLayer(
                    timeframe=la.timeframe,
                    signal_genes=list(la.signal_genes),
                    logic_genes=la.logic_genes,
                    role=la.role,
                ))
                continue
            if la is None:
                # 亲代 b 多出的层，直接拷贝
                child_layers.append(TimeframeLayer(
                    timeframe=lb.timeframe,
                    signal_genes=list(lb.signal_genes),
                    logic_genes=lb.logic_genes,
                    role=lb.role,
                ))
                continue

            # 同位层：entry 取一方、exit 取另一方
            layer_entry_a = [g for g in la.signal_genes
                             if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)]
            layer_exit_b = [g for g in lb.signal_genes
                            if g.role in (SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD)]
            layer_signals = []
            layer_signals.extend(layer_entry_a if layer_entry_a else
                                 [la.signal_genes[0]] if la.signal_genes else [])
            layer_signals.extend(layer_exit_b if layer_exit_b else
                                 [lb.signal_genes[-1]] if lb.signal_genes else [])
            # DIRECTION 基因：任一亲代有则必保留一个
            layer_dir_a = [g for g in la.signal_genes if g.role == SignalRole.DIRECTION]
            layer_dir_b = [g for g in lb.signal_genes if g.role == SignalRole.DIRECTION]
            layer_dir_candidates = layer_dir_a or layer_dir_b
            if layer_dir_candidates:
                layer_signals.append(random.choice(layer_dir_candidates))
            if not layer_signals:
                layer_signals = list(la.signal_genes)
            layer_logic = random.choice([la.logic_genes, lb.logic_genes])
            # 层角色随机取一方
            layer_role = random.choice([la.role, lb.role])
            child_layers.append(TimeframeLayer(
                timeframe=la.timeframe,
                signal_genes=layer_signals,
                logic_genes=layer_logic,
                role=layer_role,
            ))
        child_cross_logic = random.choice(
            [parent_a.cross_layer_logic, parent_b.cross_layer_logic]
        )
    elif a_has_layers:
        child_layers = list(parent_a.layers)
        child_cross_logic = parent_a.cross_layer_logic
    elif b_has_layers:
        child_layers = list(parent_b.layers)
        child_cross_logic = parent_b.cross_layer_logic

    # v2 字段同亲代原子继承（H2，dna-strong-typing-and-disclosure-
    # hygiene 件⑤）：signal_sources 与 composition 必须整对取自同一
    # 亲代——composition.weights 键集按插件源 ref 列名索引、mode/op/
    # threshold 描述的正是该源集合的组合方式，跨亲代拼合必产键集与
    # 源集错位的无意义基因型（禁两键独立 choice）。退化形态：两亲代
    # 均无→(None, None) 逐位不变；单亲代有→choice 在「有值/None」
    # 间取其一（沿元字段先例的探索语义，不特判）
    v2_pair = random.choice([
        (parent_a.signal_sources, parent_a.composition),
        (parent_b.signal_sources, parent_b.composition),
    ])

    child = StrategyDNA(
        signal_genes=child_signals,
        logic_genes=logic,
        risk_genes=risk,
        execution_genes=parent_a.execution_genes,
        parent_ids=[parent_a.strategy_id, parent_b.strategy_id],
        mutation_ops=["crossover"],
        generation=max(parent_a.generation, parent_b.generation) + 1,
        layers=child_layers,
        cross_layer_logic=child_cross_logic,
        mtf_mode=random.choice([parent_a.mtf_mode, parent_b.mtf_mode]),
        confluence_threshold=random.choice([parent_a.confluence_threshold, parent_b.confluence_threshold]),
        proximity_mult=random.choice([parent_a.proximity_mult, parent_b.proximity_mult]),
        # 入场触发策略继承（trigger-policy D11 / Q5 裁决）：沿本函数
        # 六处元字段 random.choice 先例——子代是全新基因型，触发声明
        # 取其一亲代
        entry_trigger_policy=random.choice([
            parent_a.entry_trigger_policy,
            parent_b.entry_trigger_policy]),
        signal_sources=v2_pair[0],
        composition=v2_pair[1],
    )
    return child


def mutate_composition(dna: StrategyDNA) -> StrategyDNA:
    """组合基因变异（add-ai-research-mcp 6.3）。

    作用面：signal_sources 权重 / composition 阈值与逻辑。无组合基因
    （退化形态）时 no-op 返回原 dna——纯退化种群行为零改变。
    子代合法性：变异仅在合法值域内扰动（权重 clip 到 [0,2]、阈值
    [0,1]、logic op ∈ {AND, OR}、mode 不变）。
    """
    data = dna.to_dict()
    sources = data.get("signal_sources")
    comp = data.get("composition")
    if not sources and not comp:
        return dna

    choice = random.random()
    if comp and choice < 0.5:
        # 阈值扰动
        if comp.get("mode") == "logic":
            comp["op"] = "OR" if comp.get("op", "AND") == "AND" else "AND"
        else:
            t = float(comp.get("threshold", 0.5))
            comp["threshold"] = round(min(1.0, max(0.0, t + random.uniform(-0.1, 0.1))), 4)
    else:
        # 权重扰动（有 sources 时）
        for s in sources or []:
            if random.random() < 0.5:
                w = float(s.get("weight", 1.0))
                s["weight"] = round(min(2.0, max(0.0, w + random.uniform(-0.3, 0.3))), 4)

    if sources:
        data["signal_sources"] = sources
    if comp:
        data["composition"] = comp
    return StrategyDNA.from_dict(data)
