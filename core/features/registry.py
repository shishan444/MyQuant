"""指标注册表：指标元数据的数据结构与 45 指标定义（全链单源权威）。

职能：定义 ParamDef（参数域）、IndicatorDef（指标定义）、
INDICATOR_REGISTRY（45 指标注册表）与 get_interchangeable（同类
互换集查询）。
价值与服务对象：全工程指标元数据唯一权威——19 个生产文件消费
（analysis 域 13 / strategy 2 / evolution 1 / features 1 /
validation 1 / api 1），支撑翻译侧 supported_conditions 合法集、
列名解析 resolve_indicator_column 全链单源、变异侧 ParamDef.clamp
值域收敛、同类互换 get_interchangeable 候选集。
边界：只承载元数据与命名解析，不做指标计算（计算归
indicators.py）；自 indicators.py 拆出以控制单文件 800 行上限。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable


# ---------------------------------------------------------------------------
# Registry data structures
# ---------------------------------------------------------------------------

@dataclass
class ParamDef:
    """参数定义（min/max/default/step 值域——变异算子的参数域权威）。"""
    type: str          # "int" | "float"
    min: float
    max: float
    default: float
    step: float
    candidates: Optional[List[float]] = None

    def clamp(self, value: float) -> float:
        """钳制 value 到 [min, max] 并取整到 step 边界（变异产物的
        参数域收敛——越界值拉回合法域，而非拒绝整个个体）。"""
        import math
        value = max(self.min, min(self.max, value))
        return round(value / self.step) * self.step


@dataclass
class IndicatorDef:
    """单个指标的注册表定义（类别/参数域/输出列/合法条件集）。"""
    category: str
    params: Dict[str, ParamDef]
    output_fields: List[str]
    supported_conditions: List[str]
    guard_only: bool = False
    naming: str = "default"       # column naming mode
    compute_mode: str = "eager"   # "eager" | "lazy"
    # 跨参数约束钩子（add-session-structure-expression 任务 1）：
    # 参数各自域校验通过后的第二道联合校验（如 SessionRange 的
    # start_minute<end_minute）；签名 (params) -> Optional[str]，
    # 返回非 None 即拒绝消息（None=通过）。无钩子指标零行为变化。
    cross_params: Optional[Callable[[Dict[str, float]], Optional[str]]] = None
    # 分钟语义预热（任务 2）：以自然分钟计的收敛需求（如 SessionRange
    # 的 1440=完整一天），derive_warmup_full 按 base_tf 折算为 bar 并入
    # 主 max；None=不参与该维（floor 200 既有语义不虚增）。
    warmup_minutes: Optional[int] = None


# ---------------------------------------------------------------------------
# Indicator Registry (45 indicators, 6 categories)
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
    """返回同类别全部其他指标（变异侧同类互换候选集；类别相同视为
    语义可互换。未注册名返回空列表=无互换面）。"""
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
    """把指标名+参数解析为预期 DataFrame 列名（列命名约定权威正向
    解析点）。

    消费现状（实然披露）：executor 列解析全链经本函数
    （_get_indicator_column 单点转发）；indicators.py 计算侧产列
    按同一命名约定自拼列名（与各 naming 分支为镜像实现，约定变更
    须双侧同步）；column_ref.py 的逆文法解码与之对偶——同一命名
    约定现存三份须同步的实现（正向解析/产列自拼/逆向解码）。

    Args:
        name: 注册表内指标名（如 "EMA"、"BB"）。
        params: 参数字典（如 {"period": 20}）。
        field_name: 多输出指标的字段名（如布林带 "upper"）；缺省取
            各 naming 分支的缺省字段。
        naming: 列名模式（IndicatorDef.naming 值）；空串=按
            INDICATOR_REGISTRY 自动解析。

    Returns:
        预期列名字符串（未注册指标在 default 模式下回退
        name.lower() 前缀形）。
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

    # 未知 naming 模式兜底：回退裸指标名小写形（新 naming 分支遗漏时
    # 列名仍确定——与 default 分支的未注册回退同形）
    return name.lower()
