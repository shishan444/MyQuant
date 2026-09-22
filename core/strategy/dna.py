"""Strategy DNA data structures for BTC/ETH quantitative trading strategy evolution.

This module defines the core genetic building blocks used to represent, serialize,
and evolve trading strategies within the evolutionary optimization framework.
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConditionType(Enum):
    """Comparison / relationship types used in signal conditions."""

    LT = "lt"                          # indicator < threshold
    GT = "gt"                          # indicator > threshold
    LE = "le"                          # indicator <= threshold
    GE = "ge"                          # indicator >= threshold
    CROSS_ABOVE = "cross_above"        # indicator crosses above threshold
    CROSS_BELOW = "cross_below"        # indicator crosses below threshold
    PRICE_ABOVE = "price_above"        # close price above indicator value
    PRICE_BELOW = "price_below"        # close price below indicator value
    # Phase 2: dynamic context conditions
    CROSS_ABOVE_SERIES = "cross_above_series"  # indicator A crosses above indicator B
    CROSS_BELOW_SERIES = "cross_below_series"  # indicator A crosses below indicator B
    LOOKBACK_ANY = "lookback_any"              # any bar in lookback window satisfies inner
    LOOKBACK_ALL = "lookback_all"              # all bars in lookback window satisfy inner
    # Phase 4: support/resistance conditions
    TOUCH_BOUNCE = "touch_bounce"      # price touches indicator line then bounces
    ROLE_REVERSAL = "role_reversal"    # indicator line switches support/resistance role
    WICK_TOUCH = "wick_touch"          # wick touches indicator line but close is other side


class SignalRole(Enum):
    """Role a signal gene plays within a strategy."""

    ENTRY_TRIGGER = "entry_trigger"   # directly triggers buy
    ENTRY_GUARD = "entry_guard"       # filters entry conditions
    EXIT_TRIGGER = "exit_trigger"     # directly triggers sell
    EXIT_GUARD = "exit_guard"         # filters exit conditions
    ADD_TRIGGER = "add_trigger"       # triggers position add
    ADD_GUARD = "add_guard"           # filters add conditions
    REDUCE_TRIGGER = "reduce_trigger" # triggers position reduce
    REDUCE_GUARD = "reduce_guard"     # filters reduce conditions
    DIRECTION = "direction"           # determines long/short direction for mixed strategies


def derive_role(timeframe: str) -> str:
    """Derive layer role from timeframe string.

    >= 1d -> 'structure'
    >= 1h -> 'zone'
    < 1h  -> 'execution'
    """
    tf = timeframe.strip().lower()
    # Extract numeric value and unit
    if tf.endswith("d"):
        return "structure"
    if tf.endswith("h"):
        hours = int(tf[:-1])
        return "structure" if hours >= 24 else "zone"
    return "execution"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SignalGene:
    """A single signal condition derived from a technical indicator.

    Attributes:
        indicator: Name of the indicator (e.g. "RSI", "EMA", "MACD").
        params: Indicator parameters (e.g. {"period": 14}).
        role: The role this signal plays in the strategy.
        field_name: For multi-output indicators, which output to use
                    (e.g. "histogram" for MACD). ``None`` for single-output indicators.
        condition: Structured condition dict, e.g. {"type": "lt", "threshold": 30}
                   or {"type": "price_above"} (no threshold needed).
    """

    indicator: str
    params: Dict[str, Union[int, float]]
    role: SignalRole
    field_name: Optional[str] = None
    condition: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "params": self.params,
            "role": self.role.value,
            "field": self.field_name,
            "condition": self.condition,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SignalGene":
        data = dict(data)  # shallow copy
        if isinstance(data.get("role"), str):
            data["role"] = SignalRole(data["role"])
        # Map JSON "field" to Python attribute "field_name"
        if "field" in data:
            data["field_name"] = data.pop("field")
        return cls(**{k: v for k, v in data.items() if k != "field"})


@dataclass
class LogicGenes:
    """Logical operators that combine signal genes for entry / exit decisions."""

    entry_logic: str = "AND"  # "AND" or "OR"
    exit_logic: str = "AND"   # "AND" or "OR"
    add_logic: str = "AND"    # "AND" or "OR"
    reduce_logic: str = "AND" # "AND" or "OR"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LogicGenes":
        return cls(**data)


@dataclass
class ExecutionGenes:
    """Execution-level genes: timeframe and trading symbol."""

    timeframe: str = "4h"        # "1h", "4h", "1d"
    symbol: str = "BTCUSDT"     # "BTCUSDT" or "ETHUSDT"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionGenes":
        return cls(**data)


@dataclass
class RiskGenes:
    """Risk management parameters."""

    stop_loss: float = 0.05               # pct mode: 0.05 (5%); atr mode: 2.0 (2xATR)
    take_profit: Optional[float] = None   # pct mode: 0.10 (10%); atr mode: 4.0 (4xATR)
    position_size: float = 0.3            # e.g. 0.3 (30%)
    leverage: int = 1                     # 1-10x
    direction: str = "long"               # "long" | "short"
    sl_mode: str = "pct"                  # "pct" | "atr"
    atr_period: int = 14                  # ATR lookback for sl_mode="atr"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RiskGenes":
        return cls(**data)


@dataclass
class TimeframeLayer:
    """A single timeframe layer within a multi-timeframe strategy.

    Each layer contains signal genes and logic genes evaluated
    independently on its own timeframe's data.

    role: "structure" (>=1d), "zone" (>=1h), or "execution" (<1h).
          Legacy "trend" is mapped to "structure" on deserialization.
          None defaults to "execution" for backward compatibility.
    """

    timeframe: str
    signal_genes: List[SignalGene] = field(default_factory=list)
    logic_genes: LogicGenes = field(default_factory=LogicGenes)
    role: Optional[str] = None  # "structure" | "zone" | "execution"

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "signal_genes": [sg.to_dict() for sg in self.signal_genes],
            "logic_genes": self.logic_genes.to_dict(),
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimeframeLayer":
        data = dict(data)
        if "signal_genes" in data and isinstance(data["signal_genes"], list):
            data["signal_genes"] = [
                SignalGene.from_dict(sg) if isinstance(sg, dict) else sg
                for sg in data["signal_genes"]
            ]
        if "logic_genes" in data and isinstance(data["logic_genes"], dict):
            data["logic_genes"] = LogicGenes.from_dict(data["logic_genes"])
        return cls(**data)


@dataclass
class StrategyDNA:
    """Complete genetic representation of a trading strategy.

    A StrategyDNA instance is the fundamental unit operated on by the
    evolutionary algorithm: selection, crossover, and mutation all work on
    this structure.

    Supports multi-timeframe (MTF) via ``layers``. For backward compatibility,
    ``signal_genes`` is available as a property proxying layers[0].
    """

    signal_genes: List[SignalGene] = field(default_factory=list)
    logic_genes: LogicGenes = field(default_factory=LogicGenes)
    execution_genes: ExecutionGenes = field(default_factory=ExecutionGenes)
    risk_genes: RiskGenes = field(default_factory=RiskGenes)
    strategy_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)
    mutation_ops: List[str] = field(default_factory=list)
    layers: Optional[List[TimeframeLayer]] = None
    cross_layer_logic: str = "AND"
    mtf_mode: Optional[str] = None  # "direction" | "confluence" | "direction+confluence" | None
    confluence_threshold: float = 0.3
    proximity_mult: float = 1.5
    # == v2 超集（add-ai-research-mcp 6.1）：缺省 None = 退化形态（零迁移）==
    # 信号源引用：[{type: rule|formula|model, ref, weight?}]（rule 源即 signal_genes）
    signal_sources: Optional[List[Dict[str, Any]]] = None
    # 组合决策层：{mode: weighted_vote|gate|logic, weights?, threshold?, op?}
    composition: Optional[Dict[str, Any]] = None
    _layers_explicit: bool = field(default=False, repr=False, init=False)

    def __post_init__(self):
        # Auto-detect explicit layers (passed via constructor, not auto-wrap)
        if self.layers is not None:
            object.__setattr__(self, "_layers_explicit", True)

    @property
    def is_mtf(self) -> bool:
        """Whether this DNA uses multi-timeframe layers."""
        return self.layers is not None and len(self.layers) >= 1 and self._layers_explicit

    @property
    def timeframes(self) -> List[str]:
        """List of timeframes used across all layers."""
        if self.layers:
            return [layer.timeframe for layer in self.layers]
        return [self.execution_genes.timeframe]

    @property
    def gene_signature(self) -> str:
        """Stable genotype signature string for similarity / dedup comparison.

        Encodes, for each signal gene: indicator + ALL params (sorted) +
        condition type + role, plus leverage/direction and (for MTF) the layer
        structure, so that different parameter values yield different signatures.
        This is an intrinsic property of the DNA (its genotype fingerprint),
        used by diversity measurement, strategy-library dedup, and the
        evolution exclude-set. Migrated verbatim from
        ``core.evolution.diversity._gene_signature``.
        """
        parts = []
        for gene in sorted(self.signal_genes, key=lambda g: g.role.value):
            param_parts = sorted(f"{k}={v}" for k, v in gene.params.items())
            param_summary = ",".join(param_parts)
            cond_type = gene.condition.get("type", "?") if gene.condition else "?"
            parts.append(f"{gene.indicator}({param_summary}):{cond_type}:{gene.role.value}")
        parts.append(f"lev:{self.risk_genes.leverage}")
        parts.append(f"dir:{self.risk_genes.direction}")
        # Include MTF layer structure for diversity (M3 fix)
        if self.layers:
            for layer in self.layers:
                layer_parts = [f"L:{layer.timeframe}:{layer.role or 'execution'}"]
                for g in sorted(layer.signal_genes, key=lambda x: x.role.value):
                    g_cond = g.condition.get("type", "?") if g.condition else "?"
                    layer_parts.append(f"{g.indicator}:{g_cond}")
                parts.append(",".join(layer_parts))
        # v2 插件信号源与组合层（fix-plugin-column-translation）：混合产物
        # 的 signal_genes 与纯 rule 策略可相同，签名若不含 signal_sources
        # 会把不同基因型误判同签名（translate 同签名分流、库去重、进化
        # exclude-set 全部失真）。退化形态（无插件源）签名逐位不变（零迁移）。
        plugin_sources = [
            s for s in (self.signal_sources or [])
            if isinstance(s, dict) and s.get("type") in ("formula", "model")
        ]
        if plugin_sources:
            parts.append("src:" + ";".join(sorted(
                f"{s.get('type')}:{s.get('ref')}:"
                f"{s.get('threshold', 0.0)}:{s.get('operator', '>')}"
                for s in plugin_sources)))
            if self.composition:
                comp = self.composition if isinstance(self.composition, dict) else {}
                parts.append("comp:" + json.dumps(
                    comp, sort_keys=True, ensure_ascii=False, default=str))
        return "|".join(parts)

    def _resolve_signal_genes(self) -> List[SignalGene]:
        """Resolve signal_genes from layers[0] if layers exist."""
        if self.layers and not self.signal_genes:
            return self.layers[0].signal_genes
        return self.signal_genes

    def to_dict(self) -> dict:
        result = {
            "strategy_id": self.strategy_id,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            "mutation_ops": self.mutation_ops,
            "signal_genes": [sg.to_dict() for sg in self.signal_genes],
            "logic_genes": self.logic_genes.to_dict(),
            "execution_genes": self.execution_genes.to_dict(),
            "risk_genes": self.risk_genes.to_dict(),
            "cross_layer_logic": self.cross_layer_logic,
            "mtf_mode": self.mtf_mode,
            "confluence_threshold": self.confluence_threshold,
            "proximity_mult": self.proximity_mult,
        }
        # v2 超集（非 None 才写——退化形态序列化逐位不变）
        if self.signal_sources is not None:
            result["signal_sources"] = self.signal_sources
        if self.composition is not None:
            result["composition"] = self.composition
        if self.layers:
            result["layers"] = [layer.to_dict() for layer in self.layers]
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyDNA":
        data = dict(data)  # shallow copy

        # Parse layers if present (MTF format)
        layers = None
        if "layers" in data and isinstance(data["layers"], list):
            layers = [
                TimeframeLayer.from_dict(l) if isinstance(l, dict) else l
                for l in data["layers"]
            ]
        data.pop("layers", None)

        # Parse cross_layer_logic
        cross_layer_logic = data.pop("cross_layer_logic", "AND")

        # Parse new MTF control fields with defaults
        mtf_mode = data.pop("mtf_mode", None)
        confluence_threshold = data.pop("confluence_threshold", 0.3)
        proximity_mult = data.pop("proximity_mult", 1.5)

        # Map legacy "trend" role -> "structure" in layers
        if layers:
            for layer in layers:
                if layer.role == "trend":
                    layer.role = "structure"

        if "signal_genes" in data and isinstance(data["signal_genes"], list):
            data["signal_genes"] = [
                SignalGene.from_dict(sg) if isinstance(sg, dict) else sg
                for sg in data["signal_genes"]
            ]

        if "logic_genes" in data and isinstance(data["logic_genes"], dict):
            data["logic_genes"] = LogicGenes.from_dict(data["logic_genes"])

        if "execution_genes" in data and isinstance(data["execution_genes"], dict):
            data["execution_genes"] = ExecutionGenes.from_dict(data["execution_genes"])

        if "risk_genes" in data and isinstance(data["risk_genes"], dict):
            data["risk_genes"] = RiskGenes.from_dict(data["risk_genes"])

        if not data.get("strategy_id"):
            data["strategy_id"] = str(uuid.uuid4())

        # v2 字段（add-ai-research-mcp 6.1）
        signal_sources = data.pop("signal_sources", None)
        composition = data.pop("composition", None)

        # 白名单过滤未知键：旧读者遇新键不再 TypeError（回退安全）；
        # 存量 DNA 无未知键，行为逐位不变
        _known = {
            "signal_genes", "logic_genes", "execution_genes", "risk_genes",
            "strategy_id", "generation", "parent_ids", "mutation_ops",
        }
        data = {k: v for k, v in data.items() if k in _known}

        instance = cls(**data)
        instance.signal_sources = signal_sources
        instance.composition = composition
        instance.layers = layers
        instance.cross_layer_logic = cross_layer_logic
        instance.mtf_mode = mtf_mode
        instance.confluence_threshold = confluence_threshold
        instance.proximity_mult = proximity_mult
        # Mark layers as explicit only if they came from the data (not auto-wrap)
        instance._layers_explicit = layers is not None

        # Auto-wrap: if no layers but has signal_genes, create a single layer
        if instance.layers is None and instance.signal_genes:
            instance.layers = [
                TimeframeLayer(
                    timeframe=instance.execution_genes.timeframe,
                    signal_genes=list(instance.signal_genes),
                    logic_genes=LogicGenes(
                        entry_logic=instance.logic_genes.entry_logic,
                        exit_logic=instance.logic_genes.exit_logic,
                    ),
                )
            ]

        return instance

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "StrategyDNA":
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# Strategy naming utility
# ---------------------------------------------------------------------------

INDICATOR_TYPE_MAP: Dict[str, str] = {
    "EMA": "趋势", "SMA": "趋势", "WMA": "趋势", "DEMA": "趋势", "TEMA": "趋势",
    "RSI": "动量", "MACD": "动量", "CCI": "动量", "Stochastic": "动量",
    "BB": "波动", "ATR": "波动", "KC": "波动",
    "ADX": "趋势", "Ichimoku": "趋势",
    "MFI": "量价", "OBV": "量价", "VWAP": "量价",
    "SAR": "趋势", "TRIX": "动量", "ROC": "动量",
}

_DIRECTION_LABEL = {"long": "做多", "short": "做空", "mixed": "混合"}


def generate_strategy_name(dna: "StrategyDNA") -> str:
    """Generate a human-readable strategy name from DNA.

    Format: "{indicator}{type} {direction} {timeframe}-{hash4}"
    Example: "EMA趋势 做多 4H-A3F2"
    Hash suffix distinguishes strategies that share the same entry trigger.
    """
    genes = dna.layers[0].signal_genes if dna.layers else dna.signal_genes
    trigger = next((g for g in genes if g.role == SignalRole.ENTRY_TRIGGER), None)
    indicator = trigger.indicator if trigger else (genes[0].indicator if genes else "MIX")
    type_label = INDICATOR_TYPE_MAP.get(indicator, "混合")
    dir_label = _DIRECTION_LABEL.get(dna.risk_genes.direction, "做多")
    tf = dna.execution_genes.timeframe.upper()

    # Build a short hash from all distinguishing fields for uniqueness
    sig_parts = []
    for g in dna.signal_genes:
        sig_parts.append(f"{g.indicator}:{g.role.value}:{sorted(g.params.items())}")
    sig_parts.append(f"logic:{dna.logic_genes.entry_logic}/{dna.logic_genes.exit_logic}")
    sig_parts.append(f"risk:{dna.risk_genes.stop_loss}/{dna.risk_genes.take_profit}/{dna.risk_genes.position_size}")
    # Include MTF layers to differentiate strategies with same base genes but different layer configs
    if dna.layers:
        for i, layer in enumerate(dna.layers):
            for g in layer.signal_genes:
                sig_parts.append(f"L{i}:{g.indicator}:{g.role.value}:{sorted(g.params.items())}")
    sig_str = "|".join(sig_parts)
    hash4 = hashlib.md5(sig_str.encode()).hexdigest()[:4].upper()

    return f"{indicator}{type_label} {dir_label} {tf}-{hash4}"
