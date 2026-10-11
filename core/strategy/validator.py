"""策略 DNA 合法性校验（回测前置闸门）。

职能：validate_dna 对 StrategyDNA 做结构完整性与值域合法性检查，
产出 ValidationResult（errors 拒绝面 / warnings 提示面）。

价值与服务对象：策略 DNA 进引擎前的唯一合法性闸门——消费方为
进化链 population（种群初始化）与 operators（变异子代校验），
非法产出回退保底以此为据；SL_PCT_MIN/MAX 与 SL_ATR_MIN/MAX 同时是
exit_sweep 网格校验的引用式值域（扫描通道各档单点权威，改域单点
生效），MAX_LEVERAGE 为杠杆红线单源（api/schemas 三处 Field 同源
引用——D4 家族）。

职责边界：只判合法性与结构完整，不判策略优劣（表现评估归回测
引擎与评估链）；不修改 DNA。
"""

from __future__ import annotations

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
# 杠杆红线单源（D4/DC-5）：api/schemas.py 三处 Field(le=…)、validator
# risk 域、登记声明块校验五处同源引用——改值代价=一处常量+边界用例
MAX_LEVERAGE = 10


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

    # 非 MTF 策略：查顶层 signal_genes
    if not dna.layers:
        entry_signals = [
            g for g in dna.signal_genes
            if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
        ]
        if not entry_signals:
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
        for layer in dna.layers:
            layer_entry = [
                g for g in layer.signal_genes
                if g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
            ]
            if not layer_entry:
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
    # atr 档保留（倍数盈亏比有意义）
    if (
        risk.take_profit is not None
        and risk.sl_mode != "trail"
        and risk.take_profit <= risk.stop_loss
    ):
        errors.append(f"Take profit ({risk.take_profit}) must be greater than stop loss ({risk.stop_loss})")

    if not (0.10 <= risk.position_size <= 1.0):
        errors.append(f"Position size {risk.position_size} out of range [0.10, 1.0]")

    if not (1 <= risk.leverage <= MAX_LEVERAGE):
        errors.append(f"Leverage {risk.leverage} out of range [1, {MAX_LEVERAGE}]")

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


def _validate_condition_structure(
    condition: dict, gene_idx: int, prefix: str = "",
) -> List[str]:
    """新条件类型的结构完整性校验（缺必需键即 error 点名条件类型与键）。"""
    errors: List[str] = []
    label = prefix if prefix else f"Gene {gene_idx}"
    cond_type = condition.get("type", "")

    if cond_type in ("cross_above_series", "cross_below_series"):
        if "target_indicator" not in condition:
            errors.append(f"{label}: {cond_type} requires 'target_indicator'")
    elif cond_type in ("lookback_any", "lookback_all"):
        if "window" not in condition:
            errors.append(f"{label}: {cond_type} requires 'window'")
        if "inner" not in condition:
            errors.append(f"{label}: {cond_type} requires 'inner' condition")
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
