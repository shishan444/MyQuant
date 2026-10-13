"""Indicator computation engine.

Provides _compute_indicator() and compute_all_indicators() for batch
pre-computation via pandas-ta.  Registry structures and indicator
definitions live in registry.py and are re-exported here for backward
compatibility.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import logging
import numpy as np
import pandas as pd
import pandas_ta as ta

# Re-export registry structures for backward compatibility
from core.features.registry import (
    ParamDef,
    IndicatorDef,
    INDICATOR_REGISTRY,
    get_interchangeable,
    resolve_indicator_column,
)


# ---------------------------------------------------------------------------
# Default parameter sets for pre-computation
# ---------------------------------------------------------------------------

_DEFAULT_PARAMS: Dict[str, List[Dict[str, Any]]] = {
    "EMA": [{"period": p} for p in [10, 20, 50, 100, 200]],
    "SMA": [{"period": p} for p in [10, 20, 50, 100, 200]],
    "WMA": [{"period": p} for p in [10, 20, 50]],
    "DEMA": [{"period": p} for p in [10, 20, 50]],
    "TEMA": [{"period": p} for p in [10, 20, 50]],
    "RSI": [{"period": p} for p in [7, 14, 21]],
    "MACD": [{"fast": 12, "slow": 26, "signal": 9}],
    "Stochastic": [{"k_period": 14, "d_period": 3}],
    "CCI": [{"period": 20}],
    "ROC": [{"period": 12}],
    "Williams %R": [{"period": 14}],
    "BB": [{"period": 20, "std": 2.0}],
    "ATR": [{"period": 14}],
    "Keltner": [{"ema_period": 20, "atr_period": 10, "multiplier": 2.0}],
    "Donchian": [{"period": 20}],
    "OBV": [{}],
    "CMF": [{"period": 20}],
    "MFI": [{"period": 14}],
    "ADX": [{"period": 14}],
    "PSAR": [{"step": 0.02, "max_step": 0.2}],
    # New indicators
    "RVOL": [{"period": 20}, {"period": 50}],
    "VROC": [{"period": 14}],
    "AD": [{}],
    "CVD": [{}],
    "VWMA": [{"period": 20}, {"period": 50}],
    "Aroon": [{"period": 25}],
    "CMO": [{"period": 14}],
    "TRIX": [{"period": 12}],
    # VolumeProfile is guard_only and expensive; compute on demand only
    # Pattern indicators (no params)
    "BearishEngulfing": [{}],
    "EveningStar": [{}],
    "ThreeBlackCrows": [{}],
    "ShootingStar": [{}],
    "ThreeWhiteSoldiers": [{}],
    "MorningStar": [{}],
    "BullishReversal": [{}],
    "BearishReversal": [{}],
    "BullishDivergence": [{}],
    "BearishDivergence": [{}],
    # Derivatives indicators
    "OI_ChangeRate": [{"period": 14}],
    "OI_ZScore": [{"period": 20}],
    "FundingZScore": [{"period": 30}],
    "OIPriceDivergence": [{"period": 14}],
    "FundingPressure": [{"period": 8}],
    # ML indicators (compute_mode="lazy", skipped by default)
    "FractalEntropy": [{"bins": 10, "lookback": 100}],
    "MultifactorOsc": [{"lookback": 20}],
}


# ---------------------------------------------------------------------------
# Computation engine
# ---------------------------------------------------------------------------

def _extract_outputs(
    result_df: pd.DataFrame, prefix_map: Dict[str, str]
) -> Dict[str, pd.Series]:
    """按列名前缀提取多输出指标的语义分量。

    指标库返回的列序是实现细节（随版本漂移），列名首段（如
    BBU_/BBM_/BBL_）才是稳定语义标识，因此取列按前缀匹配而非位置；
    预期分量缺失立即抛错，不静默取到错位列。

    Args:
        result_df: 指标库返回的多列 DataFrame。
        prefix_map: 语义分量名 -> 列名前缀，如 {"upper": "BBU_"}。

    Returns:
        语义分量名 -> 对应 Series 的映射。

    Raises:
        ValueError: 任一预期分量的前缀在 result_df 列中无匹配。
    """
    outputs: Dict[str, pd.Series] = {}
    for field, prefix in prefix_map.items():
        matched = [c for c in result_df.columns if str(c).startswith(prefix)]
        if not matched:
            raise ValueError(
                f"indicator output field '{field}' (prefix '{prefix}') "
                f"not found in columns: {list(result_df.columns)}"
            )
        outputs[field] = result_df[matched[0]]
    return outputs


def _compute_indicator(df: pd.DataFrame, name: str, params: Dict[str, Any]) -> Dict[str, pd.Series]:
    """Compute a single indicator and return new columns as a dict.

    Does NOT modify the input DataFrame. Returns only the newly computed
    columns keyed by column name.
    """
    new_cols: Dict[str, pd.Series] = {}

    if name == "EMA":
        period = int(params["period"])
        new_cols[f"ema_{period}"] = ta.ema(df["close"], length=period)
    elif name == "SMA":
        period = int(params["period"])
        new_cols[f"sma_{period}"] = ta.sma(df["close"], length=period)
    elif name == "RSI":
        period = int(params["period"])
        new_cols[f"rsi_{period}"] = ta.rsi(df["close"], length=period)
    elif name == "MACD":
        fast, slow, signal = int(params["fast"]), int(params["slow"]), int(params["signal"])
        macd_df = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
        if macd_df is not None:
            macd = _extract_outputs(
                macd_df, {"macd": "MACD_", "hist": "MACDh_", "signal": "MACDs_"}
            )
            new_cols[f"macd_{fast}_{slow}_{signal}"] = macd["macd"]
            new_cols[f"macd_signal_{fast}_{slow}_{signal}"] = macd["signal"]
            new_cols[f"macd_histogram_{fast}_{slow}_{signal}"] = macd["hist"]
    elif name == "BB":
        period, std = int(params["period"]), float(params["std"])
        std_str = str(std).replace(".0", "")  # align with executor.py naming
        # 吞参修复（fix-research-c1）：本版本 bbands 签名无 std 形参
        # （lower_std/upper_std），原 std=std 传入落入 **kwargs 被静默
        # 丢弃、恒用默认 2.0，造成「列名标 std 倍数、数值按 2 倍算」。
        # std=2 缺省路径与修复前逐元素一致（回归锚 A1 列值基础）。
        bb_df = ta.bbands(df["close"], length=period,
                          lower_std=std, upper_std=std)
        if bb_df is not None:
            bb = _extract_outputs(bb_df, {"upper": "BBU_", "middle": "BBM_", "lower": "BBL_"})
            new_cols[f"bb_upper_{period}_{std_str}"] = bb["upper"]
            new_cols[f"bb_middle_{period}_{std_str}"] = bb["middle"]
            new_cols[f"bb_lower_{period}_{std_str}"] = bb["lower"]
    elif name == "ATR":
        period = int(params["period"])
        new_cols[f"atr_{period}"] = ta.atr(df["high"], df["low"], df["close"], length=period)
    elif name == "ADX":
        period = int(params["period"])
        new_cols[f"adx_{period}"] = ta.adx(df["high"], df["low"], df["close"], length=period).iloc[:, 0]
    elif name == "WMA":
        period = int(params["period"])
        new_cols[f"wma_{period}"] = ta.wma(df["close"], length=period)
    elif name == "DEMA":
        period = int(params["period"])
        new_cols[f"dema_{period}"] = ta.dema(df["close"], length=period)
    elif name == "TEMA":
        period = int(params["period"])
        new_cols[f"tema_{period}"] = ta.tema(df["close"], length=period)
    elif name == "Stochastic":
        k_period = int(params["k_period"])
        d_period = int(params["d_period"])
        stoch_df = ta.stoch(df["high"], df["low"], df["close"],
                            k=k_period, d=d_period)
        if stoch_df is not None:
            new_cols[f"stoch_k_{k_period}_{d_period}"] = stoch_df.iloc[:, 0]
            new_cols[f"stoch_d_{k_period}_{d_period}"] = stoch_df.iloc[:, 1]
    elif name == "CCI":
        period = int(params["period"])
        new_cols[f"cci_{period}"] = ta.cci(df["high"], df["low"], df["close"], length=period)
    elif name == "ROC":
        period = int(params["period"])
        new_cols[f"roc_{period}"] = ta.roc(df["close"], length=period)
    elif name == "Williams %R":
        period = int(params["period"])
        new_cols[f"willr_{period}"] = ta.willr(df["high"], df["low"], df["close"], length=period)
    elif name == "Keltner":
        ema_p = int(params["ema_period"])
        atr_p = int(params["atr_period"])
        mult = float(params["multiplier"])
        kc_name = f"kc_{ema_p}_{atr_p}_{mult}"
        # 吞参修复（fix-research-c1）：本版本 ta.kc 无独立 ATR 周期概念
        # （atr_length 传入被吞，band 与 basis 共用 length 平滑
        # true_range），atr_period 从未生效。改自算：band 显式按 atr_p
        # 平滑——ta.atr 的 mamode="ema" 与 ema(true_range, atr_p) 精确
        # 等价（实测零差异）；缺省 rma 平滑是第二处语义偏移，显式
        # ema 必须保留。退化等值锚 KC-S3：atr_p==ema_p 且 mult=2 时
        # 与修复前 ta.kc 同参输出逐元素一致（KC 存量债务的唯一修复
        # 前对照点，基线见 tests/infra/test_indicator_params_effective.py）。
        basis = ta.ema(df["close"], length=ema_p)
        band = ta.atr(df["high"], df["low"], df["close"],
                      length=atr_p, mamode="ema")
        if basis is not None and band is not None:
            new_cols[f"{kc_name}_upper"] = basis + mult * band
            new_cols[f"{kc_name}_middle"] = basis
            new_cols[f"{kc_name}_lower"] = basis - mult * band
    elif name == "Donchian":
        period = int(params["period"])
        dc_df = ta.donchian(df["high"], df["low"], lower_length=period, upper_length=period)
        if dc_df is not None:
            dc = _extract_outputs(
                dc_df, {"lower": "DCL_", "middle": "DCM_", "upper": "DCU_"}
            )
            new_cols[f"dc_upper_{period}"] = dc["upper"]
            new_cols[f"dc_middle_{period}"] = dc["middle"]
            new_cols[f"dc_lower_{period}"] = dc["lower"]
    elif name == "OBV":
        new_cols["obv"] = ta.obv(df["close"], df["volume"])
    elif name == "CMF":
        period = int(params["period"])
        new_cols[f"cmf_{period}"] = ta.cmf(df["high"], df["low"], df["close"], df["volume"], length=period)
    elif name == "MFI":
        period = int(params["period"])
        new_cols[f"mfi_{period}"] = ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=period)
    elif name == "PSAR":
        step = float(params["step"])
        max_step = float(params["max_step"])
        psar_val = ta.psar(df["high"], df["low"], df["close"],
                           af=step, max_af=max_step)
        if psar_val is not None:
            new_cols["psar"] = psar_val.iloc[:, 0]
    # ── New indicators ──
    elif name == "RVOL":
        period = int(params["period"])
        vol_sma = ta.sma(df["volume"], length=period)
        new_cols[f"rvol_{period}"] = df["volume"] / vol_sma
    elif name == "VROC":
        period = int(params["period"])
        new_cols[f"vroc_{period}"] = ta.roc(df["volume"], length=period)
    elif name == "AD":
        new_cols["ad"] = ta.ad(df["high"], df["low"], df["close"], df["volume"])
    elif name == "CVD":
        hl_range = df["high"] - df["low"]
        safe_range = hl_range.replace(0, np.nan)
        cvd_raw = df["volume"] * (df["close"] - df["open"]) / safe_range
        new_cols["cvd"] = cvd_raw.fillna(0).cumsum()
    elif name == "VWMA":
        period = int(params["period"])
        new_cols[f"vwma_{period}"] = ta.vwma(df["close"], df["volume"], length=period)
    elif name == "Aroon":
        period = int(params["period"])
        aroon_df = ta.aroon(df["high"], df["low"], length=period)
        if aroon_df is not None:
            ar = _extract_outputs(
                aroon_df, {"down": "AROOND_", "up": "AROONU_", "osc": "AROONOSC_"}
            )
            new_cols[f"aroon_up_{period}"] = ar["up"]
            new_cols[f"aroon_down_{period}"] = ar["down"]
            new_cols[f"aroon_osc_{period}"] = ar["osc"]
    elif name == "CMO":
        period = int(params["period"])
        new_cols[f"cmo_{period}"] = ta.cmo(df["close"], length=period)
    elif name == "TRIX":
        period = int(params["period"])
        trix_df = ta.trix(df["close"], length=period)
        if trix_df is not None:
            new_cols[f"trix_{period}"] = trix_df.iloc[:, 0]
    elif name == "VolumeProfile":
        bins = int(params.get("bins", 50))
        lookback = int(params.get("lookback", 60))
        poc_col = f"vp_poc_{bins}_{lookback}"
        vah_col = f"vp_vah_{bins}_{lookback}"
        val_col = f"vp_val_{bins}_{lookback}"
        new_cols[poc_col] = _rolling_volume_profile(
            df["close"], df["volume"], bins=bins, lookback=lookback, which="poc",
        )
        new_cols[vah_col] = _rolling_volume_profile(
            df["close"], df["volume"], bins=bins, lookback=lookback, which="vah",
        )
        new_cols[val_col] = _rolling_volume_profile(
            df["close"], df["volume"], bins=bins, lookback=lookback, which="val",
        )

    # ── Pattern indicators ──
    elif name == "BearishEngulfing":
        from core.features.patterns.candlestick import detect_bearish_engulfing
        pat = detect_bearish_engulfing(df)
        new_cols["pattern_bearish_engulfing"] = pat["pattern_bearish_engulfing"]
    elif name == "EveningStar":
        from core.features.patterns.candlestick import detect_evening_star
        pat = detect_evening_star(df)
        new_cols["pattern_evening_star"] = pat["pattern_evening_star"]
    elif name == "ThreeBlackCrows":
        from core.features.patterns.candlestick import detect_three_black_crows
        pat = detect_three_black_crows(df)
        new_cols["pattern_3blackcrows"] = pat["pattern_3blackcrows"]
    elif name == "ShootingStar":
        from core.features.patterns.candlestick import detect_shooting_star
        pat = detect_shooting_star(df)
        new_cols["pattern_shooting_star"] = pat["pattern_shooting_star"]
    elif name == "ThreeWhiteSoldiers":
        from core.features.patterns.candlestick import detect_three_white_soldiers
        pat = detect_three_white_soldiers(df)
        new_cols["pattern_3whitesoldiers"] = pat["pattern_3whitesoldiers"]
    elif name == "MorningStar":
        from core.features.patterns.candlestick import detect_morning_star
        pat = detect_morning_star(df)
        new_cols["pattern_morning_star"] = pat["pattern_morning_star"]
    elif name == "BullishReversal":
        from core.features.patterns.candlestick import detect_bullish_reversal
        pat = detect_bullish_reversal(df)
        new_cols["pattern_bullish_reversal"] = pat["pattern_bullish_reversal"]
    elif name == "BearishReversal":
        from core.features.patterns.candlestick import detect_bearish_reversal
        pat = detect_bearish_reversal(df)
        new_cols["pattern_bearish_reversal"] = pat["pattern_bearish_reversal"]
    elif name == "BullishDivergence":
        from core.features.patterns.divergence import detect_bullish_divergence
        pat = detect_bullish_divergence(df)
        new_cols["pattern_bullish_divergence"] = pat["pattern_bullish_divergence"]
    elif name == "BearishDivergence":
        from core.features.patterns.divergence import detect_bearish_divergence
        pat = detect_bearish_divergence(df)
        new_cols["pattern_bearish_divergence"] = pat["pattern_bearish_divergence"]

    # ── Derivatives indicators ──
    elif name == "OI_ChangeRate":
        if "open_interest" in df.columns:
            period = int(params["period"])
            new_cols[f"oi_change_rate_{period}"] = df["open_interest"].pct_change(period)
    elif name == "OI_ZScore":
        if "open_interest" in df.columns:
            period = int(params["period"])
            oi = df["open_interest"]
            rolling_mean = oi.rolling(period, min_periods=1).mean()
            rolling_std = oi.rolling(period, min_periods=1).std()
            rolling_std = rolling_std.replace(0, np.nan)
            new_cols[f"oi_zscore_{period}"] = ((oi - rolling_mean) / rolling_std).fillna(0)
    elif name == "FundingZScore":
        if "funding_rate" in df.columns:
            period = int(params["period"])
            fr = df["funding_rate"]
            rolling_mean = fr.rolling(period, min_periods=1).mean()
            rolling_std = fr.rolling(period, min_periods=1).std()
            rolling_std = rolling_std.replace(0, np.nan)
            new_cols[f"funding_zscore_{period}"] = ((fr - rolling_mean) / rolling_std).fillna(0)
    elif name == "OIPriceDivergence":
        if "open_interest" in df.columns:
            period = int(params["period"])
            oi_rank = df["open_interest"].rolling(period, min_periods=2).apply(
                lambda x: pd.Series(x).rank().iloc[-1] / len(x), raw=False,
            )
            price_rank = df["close"].rolling(period, min_periods=2).apply(
                lambda x: pd.Series(x).rank().iloc[-1] / len(x), raw=False,
            )
            new_cols[f"oi_price_divergence_{period}"] = (oi_rank - price_rank).fillna(0)
    elif name == "FundingPressure":
        if "funding_rate" in df.columns:
            period = int(params["period"])
            new_cols[f"funding_pressure_{period}"] = df["funding_rate"].rolling(period, min_periods=1).sum()

    # ── ML indicators ──
    elif name == "FractalEntropy":
        from core.features.ml_indicators import compute_fractal_entropy
        bins = int(params["bins"])
        lookback = int(params["lookback"])
        new_cols[f"mfe_score_{bins}_{lookback}"] = compute_fractal_entropy(
            df["close"], bins=bins, lookback=lookback,
        )
    elif name == "MultifactorOsc":
        from core.features.ml_indicators import compute_multifactor_osc
        lookback = int(params["lookback"])
        result = compute_multifactor_osc(df, lookback=lookback)
        if result is not None:
            new_cols[f"mf_osc_{lookback}"] = result

    return new_cols


def _rolling_volume_profile(
    close: pd.Series,
    volume: pd.Series,
    bins: int = 50,
    lookback: int = 60,
    which: str = "poc",
) -> pd.Series:
    """Compute rolling volume profile and return POC/VAH/VAL series.

    Args:
        close: Close price series.
        volume: Volume series.
        bins: Number of price bins for histogram.
        lookback: Rolling window size.
        which: "poc" (point of control), "vah" (value area high), "val" (value area low).

    Returns:
        Series of the requested level values, same index as input.
    """
    close_vals = close.values
    volume_vals = volume.values
    n = len(close_vals)

    result = np.full(n, np.nan)

    for i in range(lookback - 1, n):
        window_close = close_vals[i - lookback + 1 : i + 1]
        window_vol = volume_vals[i - lookback + 1 : i + 1]

        if len(window_close) < 5:
            continue

        c_min = np.nanmin(window_close)
        c_max = np.nanmax(window_close)
        if c_max == c_min:
            result[i] = c_min
            continue

        # Build histogram
        edges = np.linspace(c_min, c_max, bins + 1)
        indices = np.digitize(window_close, edges) - 1
        indices = np.clip(indices, 0, bins - 1)
        vol_by_bin = np.zeros(bins)
        for j in range(len(indices)):
            vol_by_bin[indices[j]] += window_vol[j]

        total_vol = vol_by_bin.sum()
        if total_vol == 0:
            continue

        poc_idx = int(np.argmax(vol_by_bin))
        poc_price = (edges[poc_idx] + edges[poc_idx + 1]) / 2

        if which == "poc":
            result[i] = poc_price
        else:
            # Value area: bins containing 70% of volume around POC
            sorted_indices = np.argsort(vol_by_bin)[::-1]
            cumulative = 0.0
            va_bins = set()
            for idx in sorted_indices:
                va_bins.add(idx)
                cumulative += vol_by_bin[idx]
                if cumulative >= total_vol * 0.7:
                    break
            va_prices = [(edges[b], edges[b + 1]) for b in va_bins]
            if which == "vah":
                result[i] = max(h[1] for h in va_prices)
            elif which == "val":
                result[i] = min(h[0] for h in va_prices)

    return pd.Series(result, index=close.index)


def compute_all_indicators(
    df: pd.DataFrame,
    *,
    skip_lazy: bool = True,
    stop_check: Optional[Callable[[], None]] = None,
) -> pd.DataFrame:
    """Pre-compute all default indicators and append as columns.

    Args:
        df: OHLCV DataFrame with columns: open, high, low, close, volume
            and DatetimeIndex.
        skip_lazy: If True, skip indicators with compute_mode="lazy"
            (ML indicators, VolumeProfile). Default True for performance.
        stop_check: Optional callback that raises on stop request.
            Called before each indicator computation.

    Returns:
        Enhanced DataFrame with indicator columns appended.
    """
    result = df.copy()

    for name, param_sets in _DEFAULT_PARAMS.items():
        reg = INDICATOR_REGISTRY.get(name)
        if skip_lazy and reg and reg.compute_mode == "lazy":
            continue
        for params in param_sets:
            if stop_check is not None:
                stop_check()
            try:
                new_cols = _compute_indicator(result, name, params)
                for col, values in new_cols.items():
                    if col not in result.columns:
                        result[col] = values
            except (KeyError, ValueError, AttributeError, TypeError) as e:
                logger = logging.getLogger(__name__)
                logger.warning(
                    "跳过指标计算 %s（参数 %s）：%s", name, params, e
                )
                continue

    return result
