"""Indicator usage profiles: recommended parameters, roles, and conditions.

Based on research from freqtrade, backtrader, vectorbt, pandas-ta, and Investopedia.
Each profile defines the consensus usage for an indicator, guiding evolution
while preserving 30% random exploration capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ConditionPreset:
    """Recommended condition for an indicator."""
    type: str                             # Condition type: "lt", "gt", "cross_above" etc.
    thresholds: List[float] = field(default_factory=list)  # Recommended threshold values
    target_field: Optional[str] = None    # Field name for multi-output indicators


@dataclass
class IndicatorProfile:
    """Recommended usage profile for a single indicator."""
    recommended_roles: List[str]                # Preferred roles
    recommended_params: Dict[str, List[float]]  # Preferred parameter values
    recommended_conditions: List[ConditionPreset]
    follow_probability: float = 0.70            # Probability of following recommendations


# ---------------------------------------------------------------------------
# Condition presets (reusable)
# ---------------------------------------------------------------------------
_LT_RSI_OVERSOLD = ConditionPreset("lt", [25, 30, 35])
_GT_RSI_OVERBOUGHT = ConditionPreset("gt", [65, 70, 75])
_CROSS_ABOVE_HIST = ConditionPreset("cross_above", [0], target_field="histogram")
_CROSS_BELOW_HIST = ConditionPreset("cross_below", [0], target_field="histogram")
_PRICE_ABOVE = ConditionPreset("price_above", [])
_PRICE_BELOW = ConditionPreset("price_below", [])
_CROSS_ABOVE = ConditionPreset("cross_above", [])
_CROSS_BELOW = ConditionPreset("cross_below", [])
_CROSS_ABOVE_SERIES = ConditionPreset("cross_above_series", [])
_CROSS_BELOW_SERIES = ConditionPreset("cross_below_series", [])
_LOOKBACK_ANY = ConditionPreset("lookback_any", [])
_LOOKBACK_ALL = ConditionPreset("lookback_all", [])
_GT = ConditionPreset("gt", [])
_LT = ConditionPreset("lt", [])
_EQ_1 = ConditionPreset("eq", [1])

# ---------------------------------------------------------------------------
# Indicator profiles (40 indicators)
# ---------------------------------------------------------------------------

PROFILES: Dict[str, IndicatorProfile] = {
    # == trend (5) ==
    "EMA": IndicatorProfile(
        recommended_roles=["entry_guard", "exit_guard"],
        recommended_params={"period": [10, 20, 50, 100, 200]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW, _CROSS_ABOVE, _CROSS_BELOW, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.70,
    ),
    "SMA": IndicatorProfile(
        recommended_roles=["entry_guard", "exit_guard"],
        recommended_params={"period": [10, 20, 50, 200]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW, _CROSS_ABOVE, _CROSS_BELOW, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.70,
    ),
    "WMA": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [10, 20, 50]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW, _CROSS_ABOVE, _CROSS_BELOW, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.60,
    ),
    "DEMA": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [20, 50]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW, _CROSS_ABOVE, _CROSS_BELOW, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.60,
    ),
    "TEMA": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [10, 20]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW, _CROSS_ABOVE, _CROSS_BELOW, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.60,
    ),
    # == momentum (9) ==
    "RSI": IndicatorProfile(
        recommended_roles=["entry_trigger", "exit_trigger"],
        recommended_params={"period": [7, 14, 21]},
        recommended_conditions=[_LT_RSI_OVERSOLD, _GT_RSI_OVERBOUGHT],
        follow_probability=0.70,
    ),
    "MACD": IndicatorProfile(
        recommended_roles=["entry_trigger", "exit_trigger"],
        recommended_params={"fast": [12], "slow": [26], "signal": [9]},
        recommended_conditions=[_CROSS_ABOVE_HIST, _CROSS_BELOW_HIST],
        follow_probability=0.70,
    ),
    "Stochastic": IndicatorProfile(
        recommended_roles=["entry_trigger", "exit_trigger"],
        recommended_params={"k_period": [14], "d_period": [3]},
        recommended_conditions=[
            ConditionPreset("lt", [20]),
            ConditionPreset("gt", [80]),
            ConditionPreset("cross_above", [20], target_field="k"),
            ConditionPreset("cross_below", [80], target_field="k"),
        ],
        follow_probability=0.70,
    ),
    "CCI": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [20]},
        recommended_conditions=[
            ConditionPreset("lt", [-100]),
            ConditionPreset("gt", [100]),
        ],
        follow_probability=0.60,
    ),
    "ROC": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [12]},
        recommended_conditions=[_CROSS_ABOVE, _CROSS_BELOW, _GT, _LT],
        follow_probability=0.60,
    ),
    "Williams %R": IndicatorProfile(
        recommended_roles=["entry_trigger", "exit_trigger"],
        recommended_params={"period": [14]},
        recommended_conditions=[
            ConditionPreset("lt", [-80]),
            ConditionPreset("gt", [-20]),
        ],
        follow_probability=0.70,
    ),
    "Aroon": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={"period": [25]},
        recommended_conditions=[_CROSS_ABOVE, _CROSS_BELOW, _GT, _LT, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.60,
    ),
    "CMO": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [14]},
        recommended_conditions=[
            ConditionPreset("gt", [50]),
            ConditionPreset("lt", [-50]),
        ],
        follow_probability=0.55,
    ),
    "TRIX": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={"period": [12]},
        recommended_conditions=[_CROSS_ABOVE, _CROSS_BELOW, _GT, _LT],
        follow_probability=0.55,
    ),

    # == volatility (4) ==
    "BB": IndicatorProfile(
        recommended_roles=["entry_guard", "exit_guard"],
        recommended_params={"period": [20], "std": [2.0]},
        recommended_conditions=[
            ConditionPreset("price_below", [], target_field="lower"),
            ConditionPreset("price_above", [], target_field="upper"),
            _LOOKBACK_ANY, _LOOKBACK_ALL,
        ],
        follow_probability=0.70,
    ),
    "ATR": IndicatorProfile(
        recommended_roles=["exit_guard"],
        recommended_params={"period": [14]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.80,
    ),
    "Keltner": IndicatorProfile(
        recommended_roles=["entry_guard", "exit_guard"],
        recommended_params={"ema_period": [20], "atr_period": [10], "multiplier": [2.0]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW],
        follow_probability=0.60,
    ),
    "Donchian": IndicatorProfile(
        recommended_roles=["entry_trigger", "exit_trigger"],
        recommended_params={"period": [20]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW],
        follow_probability=0.65,
    ),

    # == volume (8) ==
    "OBV": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={},
        recommended_conditions=[_GT, _LT, _CROSS_ABOVE, _CROSS_BELOW],
        follow_probability=0.55,
    ),
    "CMF": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [20]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.55,
    ),
    "MFI": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={"period": [14]},
        recommended_conditions=[
            ConditionPreset("lt", [20]),
            ConditionPreset("gt", [80]),
        ],
        follow_probability=0.65,
    ),
    "RVOL": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [20]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.55,
    ),
    "VROC": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [14]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.50,
    ),
    "AD": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={},
        recommended_conditions=[_GT, _LT, _CROSS_ABOVE, _CROSS_BELOW],
        follow_probability=0.55,
    ),
    "CVD": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={},
        recommended_conditions=[_GT, _LT, _CROSS_ABOVE, _CROSS_BELOW],
        follow_probability=0.50,
    ),
    "VWMA": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [20, 50]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW, _CROSS_ABOVE, _CROSS_BELOW, _CROSS_ABOVE_SERIES, _CROSS_BELOW_SERIES],
        follow_probability=0.55,
    ),

    # == trend_strength (2) ==
    "ADX": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [14]},
        recommended_conditions=[
            ConditionPreset("gt", [20, 25, 30]),
        ],
        follow_probability=0.80,
    ),
    "PSAR": IndicatorProfile(
        recommended_roles=["exit_trigger"],
        recommended_params={"step": [0.02], "max_step": [0.2]},
        recommended_conditions=[_PRICE_ABOVE, _PRICE_BELOW],
        follow_probability=0.80,
    ),

    # == pattern (10) ==
    "BearishEngulfing": IndicatorProfile(
        recommended_roles=["exit_trigger", "entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "EveningStar": IndicatorProfile(
        recommended_roles=["exit_trigger", "entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "ThreeBlackCrows": IndicatorProfile(
        recommended_roles=["exit_trigger", "entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "ShootingStar": IndicatorProfile(
        recommended_roles=["exit_trigger", "entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "ThreeWhiteSoldiers": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "MorningStar": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "BullishReversal": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "BearishReversal": IndicatorProfile(
        recommended_roles=["exit_trigger", "entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "BullishDivergence": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),
    "BearishDivergence": IndicatorProfile(
        recommended_roles=["exit_trigger", "entry_trigger"],
        recommended_params={},
        recommended_conditions=[_EQ_1],
        follow_probability=0.50,
    ),

    # == derivatives (5) ==
    "OI_ChangeRate": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [14]},
        recommended_conditions=[_CROSS_ABOVE, _CROSS_BELOW, _GT, _LT],
        follow_probability=0.50,
    ),
    "OI_ZScore": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [20]},
        recommended_conditions=[
            ConditionPreset("gt", [2.0]),
            ConditionPreset("lt", [-2.0]),
        ],
        follow_probability=0.50,
    ),
    "FundingZScore": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [30]},
        recommended_conditions=[
            ConditionPreset("gt", [2.0]),
            ConditionPreset("lt", [-2.0]),
        ],
        follow_probability=0.50,
    ),
    "OIPriceDivergence": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [14]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.50,
    ),
    "FundingPressure": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"period": [8]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.50,
    ),

    # == ml (1) ==
    "FractalEntropy": IndicatorProfile(
        recommended_roles=["entry_guard"],
        recommended_params={"bins": [10], "lookback": [100]},
        recommended_conditions=[
            ConditionPreset("gt", [0.8]),
            ConditionPreset("lt", [0.4]),
        ],
        follow_probability=0.45,
    ),

    # == structure (3)（add-session-structure-expression——进化画像双源
    # 完备性：注册表成员全覆盖锚要求）==
    "SessionRange": IndicatorProfile(
        recommended_roles=["entry_guard", "entry_trigger"],
        recommended_params={"start_minute": [0, 60], "end_minute": [420, 480]},
        recommended_conditions=[_GT, _LT],
        follow_probability=0.40,
    ),
    "Pivot": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={"left": [5], "right": [5]},
        recommended_conditions=[
            ConditionPreset("cross_above", [None]),
            ConditionPreset("gt", [None]),
        ],
        follow_probability=0.40,
    ),
    "Supertrend": IndicatorProfile(
        recommended_roles=["entry_trigger"],
        recommended_params={"atr_period": [10], "multiplier": [3.0]},
        recommended_conditions=[
            ConditionPreset("cross_above", [None]),
            ConditionPreset("cross_below", [None]),
        ],
        follow_probability=0.45,
    ),
    # == structure (1) ==
    "VolumeProfile": IndicatorProfile(
        recommended_roles=["entry_guard", "exit_guard"],
        recommended_params={"bins": [50], "lookback": [60]},
        recommended_conditions=[
            ConditionPreset("touch_bounce", []),
            ConditionPreset("role_reversal", []),
        ],
        follow_probability=0.60,
    ),
}
