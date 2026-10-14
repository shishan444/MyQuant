"""Indicator registry: data structures and indicator definitions.

Defines ParamDef, IndicatorDef, INDICATOR_REGISTRY, and get_interchangeable().
Extracted from indicators.py to keep file sizes within the 800-line limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Registry data structures
# ---------------------------------------------------------------------------

@dataclass
class ParamDef:
    """Parameter definition with range constraints for mutation."""
    type: str          # "int" | "float"
    min: float
    max: float
    default: float
    step: float
    candidates: Optional[List[float]] = None

    def clamp(self, value: float) -> float:
        """Clamp value to valid range, rounded to step boundary."""
        import math
        value = max(self.min, min(self.max, value))
        return round(value / self.step) * self.step


@dataclass
class IndicatorDef:
    """Definition of a single indicator in the registry."""
    category: str
    params: Dict[str, ParamDef]
    output_fields: List[str]
    supported_conditions: List[str]
    guard_only: bool = False
    naming: str = "default"       # column naming mode
    compute_mode: str = "eager"   # "eager" | "lazy"


# ---------------------------------------------------------------------------
# Indicator Registry (30 indicators, 6 categories)
# ---------------------------------------------------------------------------

INDICATOR_REGISTRY: Dict[str, IndicatorDef] = {
    # == trend (5) ==
    "EMA": IndicatorDef(
        category="trend",
        params={"period": ParamDef("int", 5, 200, 50, 5, candidates=[7, 15, 20, 30, 60, 90])},
        output_fields=["ema"],
        supported_conditions=["price_above", "price_below", "cross_above", "cross_below",
                              "cross_above_series", "cross_below_series"],
    ),
    "SMA": IndicatorDef(
        category="trend",
        params={"period": ParamDef("int", 5, 200, 50, 5)},
        output_fields=["sma"],
        supported_conditions=["price_above", "price_below", "cross_above", "cross_below",
                              "cross_above_series", "cross_below_series"],
    ),
    "WMA": IndicatorDef(
        category="trend",
        params={"period": ParamDef("int", 5, 200, 50, 5)},
        output_fields=["wma"],
        supported_conditions=["price_above", "price_below", "cross_above", "cross_below",
                              "cross_above_series", "cross_below_series"],
    ),
    "DEMA": IndicatorDef(
        category="trend",
        params={"period": ParamDef("int", 5, 200, 50, 5)},
        output_fields=["dema"],
        supported_conditions=["price_above", "price_below", "cross_above", "cross_below",
                              "cross_above_series", "cross_below_series"],
    ),
    "TEMA": IndicatorDef(
        category="trend",
        params={"period": ParamDef("int", 5, 200, 50, 5)},
        output_fields=["tema"],
        supported_conditions=["price_above", "price_below", "cross_above", "cross_below",
                              "cross_above_series", "cross_below_series"],
    ),
    # == momentum (9) ==
    "RSI": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 2, 50, 14, 2)},
        output_fields=["rsi"],
        supported_conditions=["lt", "gt", "le", "ge"],
    ),
    "MACD": IndicatorDef(
        category="momentum",
        params={
            "fast": ParamDef("int", 5, 30, 12, 2),
            "slow": ParamDef("int", 20, 60, 26, 2),
            "signal": ParamDef("int", 5, 20, 9, 1),
        },
        output_fields=["macd", "signal", "histogram"],
        supported_conditions=["cross_above", "cross_below", "gt", "lt"],
        naming="macd",
    ),
    "Stochastic": IndicatorDef(
        category="momentum",
        params={
            "k_period": ParamDef("int", 5, 50, 14, 2),
            "d_period": ParamDef("int", 3, 20, 3, 1),
        },
        output_fields=["k", "d"],
        supported_conditions=["lt", "gt", "cross_above", "cross_below"],
        naming="stoch",
    ),
    "CCI": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 5, 50, 20, 2)},
        output_fields=["cci"],
        supported_conditions=["lt", "gt"],
    ),
    "ROC": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 2, 50, 12, 2)},
        output_fields=["roc"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),
    "Williams %R": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 2, 50, 14, 2)},
        output_fields=["willr"],
        supported_conditions=["lt", "gt"],
    ),
    "Aroon": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 10, 50, 25, 5)},
        output_fields=["aroon_up", "aroon_down", "aroon_osc"],
        supported_conditions=["cross_above", "cross_below", "gt", "lt",
                              "cross_above_series", "cross_below_series"],
        naming="aroon",
    ),
    "CMO": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 2, 50, 14, 2)},
        output_fields=["cmo"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),
    "TRIX": IndicatorDef(
        category="momentum",
        params={"period": ParamDef("int", 5, 50, 12, 2)},
        output_fields=["trix"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),

    # == volatility (4) ==
    "BB": IndicatorDef(
        category="volatility",
        params={
            "period": ParamDef("int", 5, 50, 20, 2),
            "std": ParamDef("float", 1.0, 3.0, 2.0, 0.5),
        },
        output_fields=["upper", "middle", "lower"],
        supported_conditions=["price_above", "price_below", "lookback_any", "lookback_all",
                              "touch_bounce", "role_reversal", "wick_touch"],
        naming="bb",
    ),
    "ATR": IndicatorDef(
        category="volatility",
        params={"period": ParamDef("int", 5, 50, 14, 2)},
        output_fields=["atr"],
        supported_conditions=["gt", "lt"],
        guard_only=True,
    ),
    "Keltner": IndicatorDef(
        category="volatility",
        params={
            "ema_period": ParamDef("int", 5, 50, 20, 2),
            "atr_period": ParamDef("int", 5, 50, 10, 2),
            "multiplier": ParamDef("float", 1.0, 3.0, 2.0, 0.5),
        },
        output_fields=["upper", "middle", "lower"],
        supported_conditions=["price_above", "price_below",
                              "touch_bounce", "role_reversal", "wick_touch"],
        naming="kc",
    ),
    "Donchian": IndicatorDef(
        category="volatility",
        params={"period": ParamDef("int", 5, 50, 20, 2)},
        output_fields=["upper", "middle", "lower"],
        supported_conditions=["price_above", "price_below",
                              "touch_bounce", "role_reversal", "wick_touch"],
        naming="dc",
    ),

    # == volume (8) ==
    "OBV": IndicatorDef(
        category="volume",
        params={},
        output_fields=["obv"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),
    "CMF": IndicatorDef(
        category="volume",
        params={"period": ParamDef("int", 5, 30, 20, 2)},
        output_fields=["cmf"],
        supported_conditions=["gt", "lt"],
    ),
    "MFI": IndicatorDef(
        category="volume",
        params={"period": ParamDef("int", 5, 30, 14, 2)},
        output_fields=["mfi"],
        supported_conditions=["lt", "gt"],
    ),
    "RVOL": IndicatorDef(
        category="volume",
        params={"period": ParamDef("int", 5, 100, 20, 5)},
        output_fields=["rvol"],
        supported_conditions=["gt", "lt", "lookback_any", "lookback_all"],
    ),
    "VROC": IndicatorDef(
        category="volume",
        params={"period": ParamDef("int", 5, 50, 14, 5)},
        output_fields=["vroc"],
        supported_conditions=["gt", "lt", "lookback_any", "lookback_all"],
    ),
    "AD": IndicatorDef(
        category="volume",
        params={},
        output_fields=["ad"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),
    "CVD": IndicatorDef(
        category="volume",
        params={},
        output_fields=["cvd"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),
    "VWMA": IndicatorDef(
        category="volume",
        params={"period": ParamDef("int", 5, 200, 20, 5)},
        output_fields=["vwma"],
        supported_conditions=["price_above", "price_below", "cross_above", "cross_below",
                              "cross_above_series", "cross_below_series"],
    ),

    # == trend_strength (2) ==
    "ADX": IndicatorDef(
        category="trend_strength",
        params={"period": ParamDef("int", 7, 50, 14, 2)},
        output_fields=["adx"],
        supported_conditions=["gt", "lt"],
        guard_only=True,
    ),
    "PSAR": IndicatorDef(
        category="trend_strength",
        params={
            "step": ParamDef("float", 0.01, 0.05, 0.02, 0.01),
            "max_step": ParamDef("float", 0.1, 0.3, 0.2, 0.05),
        },
        output_fields=["psar"],
        supported_conditions=["price_above", "price_below"],
        guard_only=True,
        naming="psar",
    ),

    # == pattern (10) ==
    "BearishEngulfing": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_bearish_engulfing"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "EveningStar": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_evening_star"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "ThreeBlackCrows": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_3blackcrows"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "ShootingStar": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_shooting_star"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "ThreeWhiteSoldiers": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_3whitesoldiers"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "MorningStar": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_morning_star"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "BullishReversal": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_bullish_reversal"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "BearishReversal": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_bearish_reversal"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "BullishDivergence": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_bullish_divergence"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),
    "BearishDivergence": IndicatorDef(
        category="pattern",
        params={},
        output_fields=["pattern_bearish_divergence"],
        supported_conditions=["eq"],
        guard_only=False,
        naming="pattern",
    ),

    # == structure (1) ==
    "VolumeProfile": IndicatorDef(
        category="structure",
        params={
            "bins": ParamDef("int", 20, 100, 50, 10),
            "lookback": ParamDef("int", 20, 200, 60, 20),
        },
        output_fields=["vp_poc", "vp_vah", "vp_val"],
        supported_conditions=["touch_bounce", "role_reversal"],
        guard_only=True,
        naming="vp",
        compute_mode="lazy",
    ),

    # == derivatives (5) ==
    "OI_ChangeRate": IndicatorDef(
        category="derivatives",
        params={"period": ParamDef("int", 5, 50, 14, 2)},
        output_fields=["oi_change_rate"],
        supported_conditions=["gt", "lt", "cross_above", "cross_below"],
    ),
    "OI_ZScore": IndicatorDef(
        category="derivatives",
        params={"period": ParamDef("int", 5, 50, 20, 2)},
        output_fields=["oi_zscore"],
        supported_conditions=["gt", "lt"],
    ),
    "FundingZScore": IndicatorDef(
        category="derivatives",
        params={"period": ParamDef("int", 10, 60, 30, 5)},
        output_fields=["funding_zscore"],
        supported_conditions=["gt", "lt"],
    ),
    "OIPriceDivergence": IndicatorDef(
        category="derivatives",
        params={"period": ParamDef("int", 5, 50, 14, 2)},
        output_fields=["oi_price_divergence"],
        supported_conditions=["gt", "lt"],
    ),
    "FundingPressure": IndicatorDef(
        category="derivatives",
        params={"period": ParamDef("int", 4, 30, 8, 2)},
        output_fields=["funding_pressure"],
        supported_conditions=["gt", "lt"],
    ),

    # == ml (1) ==
    "FractalEntropy": IndicatorDef(
        category="ml",
        params={
            "bins": ParamDef("int", 5, 30, 10, 5),
            "lookback": ParamDef("int", 30, 200, 100, 10),
        },
        output_fields=["mfe_score"],
        supported_conditions=["gt", "lt"],
        naming="mfe",
        compute_mode="lazy",
    ),
}


def get_interchangeable(indicator_name: str) -> List[str]:
    """Return indicators in the same category (excludes self)."""
    if indicator_name not in INDICATOR_REGISTRY:
        return []
    category = INDICATOR_REGISTRY[indicator_name].category
    return [
        name for name, defn in INDICATOR_REGISTRY.items()
        if defn.category == category and name != indicator_name
    ]


# ---------------------------------------------------------------------------
# Column name resolution — single source of truth
# ---------------------------------------------------------------------------

# Pattern indicator name -> fixed column name mapping
_PATTERN_COLUMNS: Dict[str, str] = {
    "BearishEngulfing": "pattern_bearish_engulfing",
    "EveningStar": "pattern_evening_star",
    "ThreeBlackCrows": "pattern_3blackcrows",
    "ShootingStar": "pattern_shooting_star",
    "ThreeWhiteSoldiers": "pattern_3whitesoldiers",
    "MorningStar": "pattern_morning_star",
    "BullishReversal": "pattern_bullish_reversal",
    "BearishReversal": "pattern_bearish_reversal",
    "BullishDivergence": "pattern_bullish_divergence",
    "BearishDivergence": "pattern_bearish_divergence",
}


def resolve_indicator_column(
    name: str,
    params: dict,
    field_name: str = "",
    naming: str = "",
) -> str:
    """Resolve indicator name + params to the expected DataFrame column name.

    Single source of truth for column naming conventions.  All consumers
    (executor.py, indicators.py) should call this
    instead of duplicating if/elif chains.

    Args:
        name: Indicator name from registry (e.g. "EMA", "BB").
        params: Parameter dict (e.g. {"period": 20}).
        field_name: Optional field for multi-output indicators (e.g. "upper").
        naming: Naming mode from IndicatorDef.naming. If empty, auto-resolved
            from INDICATOR_REGISTRY.

    Returns:
        Expected column name string.
    """
    # Auto-resolve naming from registry if not explicitly provided
    if not naming:
        reg = INDICATOR_REGISTRY.get(name)
        naming = reg.naming if reg else "default"

    if naming == "default":
        reg = INDICATOR_REGISTRY.get(name)
        prefix = reg.output_fields[0] if reg else name.lower()
        period = params.get("period")
        if period is not None:
            return f"{prefix}_{int(period)}"
        return prefix

    if naming == "bb":
        period = int(params["period"])
        std = float(params["std"])
        std_str = str(std).replace(".0", "")
        field = field_name or "upper"
        return f"bb_{field}_{period}_{std_str}"

    if naming == "macd":
        fast, slow, sig = int(params["fast"]), int(params["slow"]), int(params["signal"])
        field = field_name or "histogram"
        if field == "macd":
            return f"macd_{fast}_{slow}_{sig}"
        if field == "signal":
            return f"macd_signal_{fast}_{slow}_{sig}"
        return f"macd_histogram_{fast}_{slow}_{sig}"

    if naming == "stoch":
        k_period = int(params["k_period"])
        d_period = int(params["d_period"])
        field = field_name or "k"
        prefix = "stoch_k" if field == "k" else "stoch_d"
        return f"{prefix}_{k_period}_{d_period}"

    if naming == "kc":
        ema_p = int(params["ema_period"])
        atr_p = int(params["atr_period"])
        mult = float(params["multiplier"])
        field = field_name or "upper"
        return f"kc_{ema_p}_{atr_p}_{mult}_{field}"

    if naming == "dc":
        period = int(params["period"])
        field = field_name or "upper"
        return f"dc_{field}_{period}"

    if naming == "vp":
        bins = int(params.get("bins", 50))
        lookback = int(params.get("lookback", 60))
        field = field_name or "vp_poc"
        return f"{field}_{bins}_{lookback}"

    if naming == "pattern":
        return _PATTERN_COLUMNS.get(name, f"pattern_{name.lower()}")

    if naming == "psar":
        return "psar"

    if naming == "aroon":
        period = int(params["period"])
        field = field_name or "aroon_up"
        return f"{field}_{period}"

    if naming == "mfe":
        bins = int(params["bins"])
        lookback = int(params["lookback"])
        return f"mfe_score_{bins}_{lookback}"

    # Unknown naming mode fallback
    return name.lower()
