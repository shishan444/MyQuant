"""指标计算引擎（批量预计算+单点现算双入口）。

职能：以 pandas-ta 为底座计算全部注册指标——compute_all_indicators
按默认参数集批量预计算（增强 df 全量铺列，回测/进化链消费），
_compute_indicator 按指定指标+参数单点现算（研究链五入口
indicator_query/case_inspection/plugin_service/warmup_loader/
validation_service 的裸装载与单独现算走此单点——依赖闭包
_ensure_dependencies 全入口共享）。职责边界：指标定义元数据（注册
表/参数域/输出列）权威在 registry.py，本模块 re-export 仅为向后
兼容面——定义变更走 registry 单源，不在此造第二权威。
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
# 批量预计算默认参数集（compute_all_indicators 铺列形制声明——每指标
# 多参数组全量预铺、消费端按列名取用；单点现算不走此表）
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
    # == structure (3)（add-session-structure-expression）==
    "SessionRange": [{"start_minute": 0, "end_minute": 420},
                     {"start_minute": 60, "end_minute": 480}],
    "Pivot": [{"left": 5, "right": 5}],
    "Supertrend": [{"atr_period": 10, "multiplier": 3.0}],
    # == structure (2)（add-box-capability）==：default 取 1h 冻结值
    # （15m 冻结值 240/96、480 经 candidates/显式传参走单点现算）
    "BoxRegime": [{"window": 120, "width_thr": 0.08, "min_len": 48}],
    "BoxChannel": [{"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
                    "buffer": 0.25}],
    # == information (3) / structure (2)（add-champion-indicators）==
    # default 取研究冻结值（demo gate/面板）；compute_mode=lazy 批量
    # 预铺跳过（BoxChannel 先例），条件链/研究链单点现算不受影响
    "ShannonEntropy": [{"window": 96, "rank_window": 500}],
    "PermutationEntropy": [{"window": 240, "pattern_len": 4}],
    "EfficiencyRatio": [{"window": 96, "rank_window": 500}],
    "CUSUM": [{"sd_window": 500, "drift": 0.5, "threshold": 5.0}],
    "GARCHFilter": [{"alpha": 0.08, "beta": 0.90, "init_window": 500}],
    # ML indicators (compute_mode="lazy", skipped by default)
    "FractalEntropy": [{"bins": 10, "lookback": 100}],
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


def _ensure_dependencies(df: pd.DataFrame, name: str, visited: tuple = ()) -> tuple:
    """depends_on 闭包装载（B4-P0 依赖显式化，design 卡 B 2.1）。

    计算宿主指标前按 registry depends_on 声明检查 df 缺失依赖列→
    递归 _compute_indicator 按该依赖默认参数现算注入**局部副本**
    （输入 df 不被修改）；幂等：依赖产出列族已在 df 时跳过重算
    （review2 CARD-02：registry output_fields 是前缀类别名（RSI→
    "rsi"），实际产出列带参数后缀（rsi_14）——按列族判定，裸名
    in 检查恒 False 恒重算的形制错位在此收口）；依赖计算失败→
    结构化拒绝点名依赖（「依赖指标 RSI 计算失败」）。研究链五入口
    （indicator_query/case_inspection/plugin_service/warmup_loader/
    validation_service 的裸装载+单独现算）与 compute_all_indicators
    批量路径全经此单点受益。

    visited（S-3 顺带）：装载归链（祖先指标序，不含本指标）——链上
    已见本指标即结构化拒绝循环依赖声明（替代千层递归撞限后错误
    归因指错对象）；缺省空集，外部调用零感知。

    Returns:
        (work_df, injected_cols)：work_df=补齐依赖列后的副本（依赖
        已在时=原 df 零拷贝）；injected_cols=本次注入的依赖列 dict
        （进 _compute_indicator 返回值——入口消费面 assign 全量
        并回，依赖列随宿主计算结果一并可见）。

    Raises:
        ResearchError: RESEARCH_INDICATOR_DEPENDENCY_FAILED（依赖
            计算失败——静默恒 0 路径消灭的失败可见面；循环依赖
            声明同码拒绝，detail 带完整链）。
    """
    from core.analysis.errors import ResearchError  # 局部导入——core.data 先例（模块级不反向依赖 core/analysis）

    if name in visited:
        chain = "→".join((*visited, name))
        raise ResearchError(
            "RESEARCH_INDICATOR_DEPENDENCY_FAILED",
            f"循环依赖声明: {chain}",
            detail={"indicator": name, "chain": [*visited, name]})
    reg = INDICATOR_REGISTRY.get(name)
    deps = list(getattr(reg, "depends_on", None) or [])
    if not deps:
        return df, {}
    missing = []
    for dep in deps:
        dep_reg = INDICATOR_REGISTRY.get(dep)
        if dep_reg is None:
            raise ResearchError(
                "RESEARCH_INDICATOR_DEPENDENCY_FAILED",
                f"指标 {name} 声明的依赖 {dep} 不在注册表",
                detail={"indicator": name, "dependency": dep})
        # 产出列族在场判定：output_field 裸名相等（无参数指标）或
        # f"_{参数后缀}" 前缀命中（参数化指标）——与 divergence
        # 消费端前缀取列语义自洽；嵌套前缀形（macd_signal_* 对
        # "signal"）不在场时保守判缺重算，行为无害
        if not all(
                any(c == f or c.startswith(f + "_") for c in df.columns)
                for f in dep_reg.output_fields):
            missing.append(dep)
    if not missing:
        return df, {}
    extra: Dict[str, pd.Series] = {}
    for dep in missing:
        dep_reg = INDICATOR_REGISTRY[dep]
        dep_params = {k: p.default for k, p in dep_reg.params.items()}
        try:
            extra.update(
                _compute_indicator(df, dep, dep_params,
                                   visited=(*visited, name)))
        except ResearchError:
            raise
        except Exception as exc:
            raise ResearchError(
                "RESEARCH_INDICATOR_DEPENDENCY_FAILED",
                f"依赖指标 {dep} 计算失败：{exc}",
                detail={"indicator": name, "dependency": dep}) from exc
    return df.assign(**extra), extra


def _compute_indicator(
        df: pd.DataFrame, name: str, params: Dict[str, Any],
        visited: tuple = ()) -> Dict[str, pd.Series]:
    """单点现算单个指标，返回新列 dict（列名 -> Series）。

    职责契约：输入 df 不被修改（依赖闭包注入走局部副本）；返回值
    仅含本次产出列——含依赖闭包注入的依赖列（入口消费面 assign
    全量并回 df）；未知指标名不在此报错（返回空 dict，白名单校验
    在登记/装载层各自权威面——本函数只管「算」，不管「该不该算」）。

    depends_on 闭包（B4-P0）：宿主计算前经 _ensure_dependencies 补齐
    缺失依赖列——注入的依赖列并入返回值（入口消费面全量并回 df；
    幂等：依赖列已在时零开销直返）。visited=装载归链（S-3：互递归
    传递，循环依赖结构化拒绝；缺省空集，外部调用零感知）。
    """
    df, _injected = _ensure_dependencies(df, name, visited)
    new_cols: Dict[str, pd.Series] = dict(_injected)

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
        # 自算替换（indicator-and-action-repair B3）：本版本 ta.cci 的
        # MAD 分母实现错误，值域落价格量级（实测 ±10^4，正确公式典型
        # ±200）——沿同文件 Keltner 吞参自算先例。标准 Lambert 公式：
        # 典型价对其 SMA 的偏离 ÷（0.015×平均绝对偏差）；MAD=窗口内
        # tp 对当前窗口均值的平均绝对偏差（rolling apply 单趟——嵌套
        # rolling 语义错且 warmup 翻倍）。列名/头部 NaN 语义不变。
        tp = (df["high"] + df["low"] + df["close"]) / 3
        mad = tp.rolling(period).apply(
            lambda w: np.abs(w - w.mean()).mean(), raw=True)
        new_cols[f"cci_{period}"] = (
            tp - tp.rolling(period).mean()) / (0.015 * mad)
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
        # 窗口不含当根（B5）：轨道=前 N 根极值（shift(1)——经典
        # Donchian 突破定义；本版本 ta.donchian 窗口含当根，致下轨
        # price_below/cross_below 数学恒假）。生成点唯一，全消费方
        # （executor 求值/scene/统计面）值语义一并归正（design 卡 C
        # 计算侧形制）；warmup 预算规则不变（周期×3 余量覆盖 +1）。
        new_cols[f"dc_upper_{period}"] = (
            df["high"].rolling(period).max().shift(1))
        new_cols[f"dc_lower_{period}"] = (
            df["low"].rolling(period).min().shift(1))
        new_cols[f"dc_middle_{period}"] = (
            new_cols[f"dc_upper_{period}"]
            + new_cols[f"dc_lower_{period}"]) / 2
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
    # == structure (3)（add-session-structure-expression 任务 3-5）==
    # 分支内逐键解包（AST 参数域对拍锚要求声明键在本分支被显式消费
    # ——委托实现整体收 params，此处解包即真实消费点）
    elif name == "SessionRange":
        from core.features.structure_indicators import compute_session_range
        start_m = int(params["start_minute"])
        end_m = int(params["end_minute"])
        new_cols.update(compute_session_range(
            df, name, {"start_minute": start_m, "end_minute": end_m}))
    elif name == "Pivot":
        from core.features.structure_indicators import compute_pivot
        left = int(params["left"])
        right = int(params["right"])
        new_cols.update(compute_pivot(
            df, name, {"left": left, "right": right}))
    elif name == "Supertrend":
        from core.features.structure_indicators import compute_supertrend
        atr_p = int(params["atr_period"])
        mult = float(params["multiplier"])
        new_cols.update(compute_supertrend(
            df, name, {"atr_period": atr_p, "multiplier": mult}))
    # == structure (2)（add-box-capability）==
    elif name == "BoxRegime":
        from core.features.structure_indicators import compute_box_regime
        window = int(params["window"])
        width_thr = float(params["width_thr"])
        min_len = int(params["min_len"])
        new_cols.update(compute_box_regime(
            df, name, {"window": window, "width_thr": width_thr,
                       "min_len": min_len}))
    elif name == "BoxChannel":
        from core.features.structure_indicators import compute_box_channel
        edge_window = int(params["edge_window"])
        q_low = float(params["q_low"])
        q_high = float(params["q_high"])
        buffer = float(params["buffer"])
        new_cols.update(compute_box_channel(
            df, name, {"edge_window": edge_window, "q_low": q_low,
                       "q_high": q_high, "buffer": buffer}))
    # == information (3) / structure (2)（add-champion-indicators）==
    # 分支内逐键解包（AST 参数域对拍锚要求——同 box 先例）
    elif name == "ShannonEntropy":
        from core.features.champion_indicators import compute_shannon_entropy
        window = int(params["window"])
        rank_window = int(params["rank_window"])
        new_cols.update(compute_shannon_entropy(
            df, name, {"window": window, "rank_window": rank_window}))
    elif name == "PermutationEntropy":
        from core.features.champion_indicators import (
            compute_permutation_entropy)
        window = int(params["window"])
        pattern_len = int(params["pattern_len"])
        new_cols.update(compute_permutation_entropy(
            df, name, {"window": window, "pattern_len": pattern_len}))
    elif name == "EfficiencyRatio":
        from core.features.champion_indicators import (
            compute_efficiency_ratio)
        window = int(params["window"])
        rank_window = int(params["rank_window"])
        new_cols.update(compute_efficiency_ratio(
            df, name, {"window": window, "rank_window": rank_window}))
    elif name == "CUSUM":
        from core.features.champion_indicators import compute_cusum
        sd_window = int(params["sd_window"])
        drift = float(params["drift"])
        threshold = float(params["threshold"])
        new_cols.update(compute_cusum(
            df, name, {"sd_window": sd_window, "drift": drift,
                       "threshold": threshold}))
    elif name == "GARCHFilter":
        from core.features.champion_indicators import compute_garch_filter
        alpha = float(params["alpha"])
        beta = float(params["beta"])
        init_window = int(params["init_window"])
        new_cols.update(compute_garch_filter(
            df, name, {"alpha": alpha, "beta": beta,
                       "init_window": init_window}))
    return new_cols


def _rolling_volume_profile(
    close: pd.Series,
    volume: pd.Series,
    bins: int = 50,
    lookback: int = 60,
    which: str = "poc",
) -> pd.Series:
    """滚动成交量分布：逐 bar 对前 lookback 根做价格分桶成交量直方
    图，返回指定分布位序列（成本支撑/阻力位语义）。

    按需计算（VolumeProfile 注册为 guard_only+compute_mode=lazy——
    批量预计算 skip_lazy 跳过，仅研究链显式单点现算触达：全量铺列
    对批量路径过贵，rolling 直方图是 O(n×lookback) 逐 bar 重算）。

    Args:
        close: 收盘价序列（分桶轴）。
        volume: 成交量序列（直方图权重）。
        bins: 价格分桶数。
        lookback: 滚动窗口根数。
        which: 取哪个分布位——"poc"（成交量最大桶价位，point of
            control）、"vah"（价值区上沿）、"val"（价值区下沿）。

    Returns:
        指定分布位的 Series，与输入同索引；窗口内样本不足处为 NaN。
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
    from core.analysis.errors import ResearchError  # 局部导入（同上）——except 面收录依赖失败（best-effort 跳过语义保持）

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
            except (KeyError, ValueError, AttributeError, TypeError,
                    ResearchError) as e:
                logger = logging.getLogger(__name__)
                logger.warning(
                    "跳过指标计算 %s（参数 %s）：%s", name, params, e
                )
                continue

    return result
