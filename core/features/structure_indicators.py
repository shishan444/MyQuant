"""结构类指标实现（add-session-structure-expression 任务 3/4/5）。

三个时段/结构指标的计算实现——SessionRange（会话区间三列）/Pivot
（确认延迟枢轴）/Supertrend（趋势跟踪带）。自 indicators.py 拆出
（800 行红线）；分派仍单点（indicators._compute_indicator elif 链）。

防前视纪律（三指标共同）：全部输出在 bar t 只使用截至 t-1 已收盘
数据（shift(1) 前置或等价的确认延迟）——当前 bar 的 high/low/close
不进 t 时刻的输出。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def compute_session_range(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """SessionRange：UTC 分钟时段的区间三列（防前视 t-1 累积）。

    四不变量（防前视——t 时刻输出只含截至 t-1 的会话内已收盘 bar）：
    ①session_high[t] = 截至上一根 bar 的会话内最高 high；
    ②session_low[t] = 同口径最低 low；
    ③session_vwap[t] = Σ(typ×vol)/Σvol 同口径累积（typ=HLC 均值）；
    ④会话首根 bar（t-1 不在会话内或无会话数据）输出 NaN（不虚构
    会话外前值——跨会话不延续，每会话独立归零）。

    会话成员判定：bar 开盘时刻的 UTC 分钟 ∈ [start_minute, end_minute)
    （左闭右开——end_minute 时刻的 bar 归下一段）。

    会话周期段（B1 修复——窗口结束值固定至日终）：段起点=进入会话，
    段终点=UTC 日界。段内会话外行 ffill 延续会话终值（spec「窗口
    结束值固定至日终」）；跨段不延续——次日窗口开始前 NaN（保住
    UTC 日界重置不变量；按会话周期段而非日历日分组，start_minute>0
    时当日 0:00 至窗口开始前不承接昨日终值）。跨午夜时段（防御
    分支）同被日界切断归零——未启用形制不为此精化。
    """
    start_m = int(params["start_minute"])
    end_m = int(params["end_minute"])
    minute_of_day = pd.Series(
        df.index.hour * 60 + df.index.minute, index=df.index)
    if start_m < end_m:
        in_session = (minute_of_day >= start_m) & (minute_of_day < end_m)
    else:
        # 跨午夜时段（注册表钩子现约束 start<end；防御性支持跨日形）
        in_session = (minute_of_day >= start_m) | (minute_of_day < end_m)

    # t-1 累积：shift(1) 后在会话成员内分组累积（会话切换自然归零——
    # 非会话段累积为 0 状态，重入会话从零重启）
    prev_in = in_session.shift(1, fill_value=False)
    typ = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typ * df["volume"]
    # 会话内的连续段编号（供组内累积）：以 prev_in 的会话段为单位
    seg = (prev_in & ~prev_in.shift(1, fill_value=False)).cumsum()
    valid = prev_in.astype(bool)

    # 会话周期段编号（B1——供段内 ffill）：进入会话或 UTC 日界均开
    # 新段（日界切断跨段延续）
    enter_session = in_session & ~in_session.shift(1, fill_value=False)
    day = pd.Series(df.index.normalize(), index=df.index)
    cycle = (enter_session | day.ne(day.shift())).cumsum()

    def _session_cummax(series: pd.Series) -> pd.Series:
        # 输入先 shift(1)（t-1 累积——当前 bar 不进自身输出）
        base = series.shift(1).where(valid)
        # 段内 cummax：seg 相同且 valid 的行组内累积
        inner = base.groupby(seg[base.notna()]).cummax() \
            if base.notna().any() else base
        # 会话周期段内 ffill：会话外行延续会话终值至日终
        return inner.groupby(cycle).ffill()

    session_high = _session_cummax(df["high"])
    session_low = _session_cummax(-df["low"]) * -1.0

    cum_pv = pv.shift(1).where(valid)
    cum_vol = df["volume"].shift(1).where(valid)
    seg_valid = seg[valid]
    sum_pv = cum_pv.groupby(seg_valid).cumsum()
    sum_vol = cum_vol.groupby(seg_valid).cumsum()
    # vwap 同口径段内 ffill（会话外延续 Σ/Σ 终值）
    session_vwap = (
        sum_pv / sum_vol.replace(0.0, np.nan)).groupby(cycle).ffill()

    return {
        f"session_high_{start_m}_{end_m}": session_high,
        f"session_low_{start_m}_{end_m}": session_low,
        f"session_vwap_{start_m}_{end_m}": session_vwap,
    }


def compute_pivot(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """Pivot：确认延迟枢轴（前 left 后 right 严格极值）。

    pivot_high[j]：high[j] 严格大于前 left 根与后 right 根的 high；
    确认时刻 = j+right（第 right 根后续 bar 收盘后才可断言）——输出
    落位在 j+right（t-1 视角：t 时刻可见的最新确认值）。pivot_low
    对称。确认前（无任何已确认枢轴）NaN；之后 ffill 前值（最近一个
    已确认枢轴水平延续）。
    """
    left = int(params["left"])
    right = int(params["right"])
    high, low = df["high"], df["low"]

    # 严格极值：窗口内比较用 shift 网格（前 left × 后 right 逐位严格大于）
    is_ph = pd.Series(True, index=df.index)
    is_pl = pd.Series(True, index=df.index)
    for k in range(1, left + 1):
        is_ph &= high > high.shift(k)
        is_pl &= low < low.shift(k)
    for k in range(1, right + 1):
        is_ph &= high > high.shift(-k)
        is_pl &= low < low.shift(-k)

    def _confirmed_level(extreme: pd.Series, hit: pd.Series) -> pd.Series:
        # 枢轴值在确认时刻（j+right）落位：hit.shift(-right) 把 j 的
        # 值移到 j+right 行——该行收盘后 j 已可断言（防前视等价：
        # t 行的值由 t-right 行的极值判定产生）。fill_value 保 bool
        # dtype（无 fillna——object 退化会使 where 行为异常）
        # 落位到确认时刻 j+right：极值与掩码同 shift(+right)——
        # 行 k=j+right 处值=extreme[j]、掩码=hit[j]（j 行收盘后须再
        # 等 right 根才可断言，防前视）
        shifted_hit = hit.shift(right, fill_value=False).astype(bool)
        shifted_extreme = extreme.shift(right)
        confirmed = shifted_extreme.where(shifted_hit)
        return confirmed.ffill()

    return {
        f"pivot_high_{left}_{right}": _confirmed_level(high, is_ph),
        f"pivot_low_{left}_{right}": _confirmed_level(low, is_pl),
    }


def compute_box_regime(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """BoxRegime：箱体识别件（极值宽族——结构存在性判定）。

    公式（demo r9_d1_ruler.py 同构）：
    width = (hi_max − lo_min) / close_mean——三分量均取 rolling(window)
    含当根（hi_max=high.rolling(window).max()、lo_min=low.rolling
    (window).min()、close_mean=close.rolling(window).mean()）；
    box_state = width ≤ width_thr（含等号——恰达门槛按箱体态处理；
    窗口不满的 NaN 期=False）；box_width=该 width 数值列（与 state
    自洽）；box_established=box_state 连续 min_len 根为真的成熟态
    （rolling(min_len).min()——头部未满窗按 0.0 未成熟处理，是语义
    正确而非数据缺失，min_len 不进 warmup 白名单的依据）。

    防前视论证：三列均为 rolling 后顾窗（窗口含当根但不含未来根）
    ——bar t 的输出只依赖 [t−window+1, t] 的 high/low/close；修改
    t+1 及之后的数据不改变 t 及之前的输出（截断不变量）。与
    BoxChannel 的分界：本件是结构存在性列（rolling 含当根——描述
    「截至当根的行情结构」，当根行情合法参与）；BoxChannel 的沿/
    破坏线是价格比较基准列（shift(1) 前一根取值——供当根收盘比较，
    防自引用）。两族口径不可互换（识别件 vs 沿通道件——demo 两族
    冻结事实）。

    布尔条件语义：box_state/box_established 为 True=1.0/False=0.0
    的数值列（pattern 先例同族）——条件引用推荐 eq 1/eq 0/gt 0.5；
    「box_state gt 1」恒 False（值域 {0.0, 1.0}）勿踩。

    timeframe 感知默认（registry 形制适配）：default 取 1h 冻结值
    (120, 48)，candidates 另收录 15m 冻结值 (240, 96)——15m 轴 demo
    冻结值须显式传参（demo r9_d1 冻结：1h=(120,48)/15m=(240,96)）。
    """
    window = int(params["window"])
    width_thr = float(params["width_thr"])
    min_len = int(params["min_len"])

    hi = df["high"].rolling(window).max()
    lo = df["low"].rolling(window).min()
    mid = df["close"].rolling(window).mean()
    width = (hi - lo) / mid  # 分母=滚动均值（非当根价——与盘点口径同源）
    # 布尔列数值化（True=1.0/False=0.0——NaN 期 False，条件链数值比较）
    box_state = (width <= width_thr).fillna(False).astype(float)
    # 成熟态：连续 min_len 根 state 真（头部未满窗=0.0 未成熟）
    box_established = box_state.rolling(min_len).min().fillna(0.0)

    return {
        f"boxreg_{window}_{width_thr}_{min_len}_box_state": box_state,
        f"boxreg_{window}_{width_thr}_{min_len}_box_width": width,
        f"boxreg_{window}_{width_thr}_{min_len}_box_established":
            box_established,
    }


def compute_box_channel(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """BoxChannel：箱体沿通道件（分位沿族——交易判定基准）。

    公式（demo r9_d3_box.py 同构）：
    upper = close.rolling(edge_window).quantile(q_high).shift(1)；
    lower = close.rolling(edge_window).quantile(q_low).shift(1)；
    channel_width = (upper − lower) / close.shift(1)；
    break_high = upper + buffer × (upper − lower)；
    break_low = lower − buffer × (upper − lower)（逐 bar 滚动破坏线，
    MACD histogram 派生列形制——break_low 与 demo 入场冻结线
    sl_line=q10−buffer×(q90−q10) 数学等价）。

    防前视（防当根自引用）：upper/lower 取 shift(1) 前一根取值
    （rolling 后顾窗＋shift(1) 双保险）——当根收盘只与截至 t−1 的
    分位沿比较；修改当根 t 的收盘不改变 t 根的 upper/lower/channel_
    width/break 列值（防自引用不变量）。与 BoxRegime 的分界：本件
    五列均为价格比较基准列（shift(1)）；BoxRegime 三列为结构存在性
    列（rolling 含当根）——两族口径不可互换。

    分位族单口径：q_low/q_high 作用于同一 close.rolling(edge_window)
    分位（demo q10/q90 冻结对）；q_low ≥ q_high 经注册表 cross_params
    结构化拒绝（通道倒挂无意义——SessionRange 先例）。

    channel_width 分母为 close.shift(1)（有意收紧）：与 demo 箱体
    资格宽（box_ok）的当根 close 分母是显式差异面——本列作价格比较
    基准列统一 shift(1) 口径；资格宽阈值（如 0.06）不进指标参数，
    由策略条件 channel_width lt 阈值承载。break 线是逐 bar 滚动值；
    「入场时点冻结」归策略执行语义（消费侧在入场时点取 e−1 根列值
    冻结常数线——ruler_compare 对照三分类先例，禁逐 bar 消费滚动
    列值做分类判定）。

    timeframe 感知默认：default 取 1h 冻结值 240，candidates 另收录
    15m 冻结值 480——15m 轴 demo 冻结值须显式传参（demo r9_d3 冻结）。
    """
    edge_window = int(params["edge_window"])
    q_low = float(params["q_low"])
    q_high = float(params["q_high"])
    buffer = float(params["buffer"])

    upper = (df["close"].rolling(edge_window)
             .quantile(q_high).shift(1))
    lower = (df["close"].rolling(edge_window)
             .quantile(q_low).shift(1))
    # shift(1) 分母（有意收紧——基准列口径，见 docstring 差异面标注）
    channel_width = (upper - lower) / df["close"].shift(1)
    span = upper - lower
    break_high = upper + buffer * span
    break_low = lower - buffer * span

    return {
        f"boxch_{edge_window}_{q_low}_{q_high}_{buffer}_upper": upper,
        f"boxch_{edge_window}_{q_low}_{q_high}_{buffer}_lower": lower,
        f"boxch_{edge_window}_{q_low}_{q_high}_{buffer}_channel_width":
            channel_width,
        f"boxch_{edge_window}_{q_low}_{q_high}_{buffer}_break_high":
            break_high,
        f"boxch_{edge_window}_{q_low}_{q_high}_{buffer}_break_low":
            break_low,
    }


def compute_supertrend(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """Supertrend：标准趋势跟踪带（ATR 通道 + 趋势状态递推）。

    标准公式（P7 文献锚——Olivier Seban 形制的通用实现）：
    基线 = HL2（(high+low)/2）；上轨 = 基线 + multiplier×ATR(atr_period)；
    下轨 = 基线 − multiplier×ATR。最终带值递推：
    最终下轨[t] = max(下轨[t], 最终下轨[t-1]) 当 close[t-1] > 最终下轨[t-1]
                否则 下轨[t]（趋势延续时带上移不下移）；
    最终上轨对称（趋势延续时带下移不上移）。
    趋势 dir[t]=+1（多头，close 在最终下轨上方）/ −1；翻转条件：
    close[t] 上穿最终上轨 → 转多；下穿最终下轨 → 转空。
    supertrend 列 = 多头期取最终下轨值、空头期取最终上轨值。
    supertrend_dir = ±1.0 二值（翻转 = 该列 cross 0）。

    防前视：递推用 shift(1) 前值——t 时刻带值由 t-1 状态与 t 行情
    （收盘价判定翻转）产生；ATR 为 Wilder 平滑（pandas ta 同族）。
    """
    atr_period = int(params["atr_period"])
    multiplier = float(params["multiplier"])

    high, low, close = df["high"], df["low"], df["close"]
    hl2 = (high + low) / 2.0

    # Wilder ATR（与 pandas_ta.ta.atr 同族：TR 的 RMA 平滑）
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / atr_period, min_periods=atr_period,
                 adjust=False).mean()

    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    n = len(df)
    final_upper = np.array(upper_basic, dtype=float, copy=True)
    final_lower = np.array(lower_basic, dtype=float, copy=True)
    close_arr = close.to_numpy(dtype=float)
    ub_arr = upper_basic.to_numpy(dtype=float)
    lb_arr = lower_basic.to_numpy(dtype=float)

    for t in range(1, n):
        # 最终下轨：趋势延续时带上移不下移（前 close 在带上方）
        if close_arr[t - 1] > final_lower[t - 1]:
            final_lower[t] = max(lb_arr[t], final_lower[t - 1])
        else:
            final_lower[t] = lb_arr[t]
        # 最终上轨对称（前 close 在带下方时带下移不上移）
        if close_arr[t - 1] < final_upper[t - 1]:
            final_upper[t] = min(ub_arr[t], final_upper[t - 1])
        else:
            final_upper[t] = ub_arr[t]

    final_upper_s = pd.Series(final_upper, index=df.index)
    final_lower_s = pd.Series(final_lower, index=df.index)

    # 趋势状态递推（shift(1) 防前视）：t-1 多头且 close[t] ≥ 最终下轨[t]
    # → 延续多；close[t] > 最终上轨[t] → 转多；close[t] < 最终下轨[t]
    # → 转空；其余延续 t-1 状态
    direction = np.zeros(n)
    supertrend = np.full(n, np.nan)
    for t in range(n):
        if t == 0 or np.isnan(final_lower[t]) or np.isnan(final_upper[t]):
            direction[t] = 0.0 if t == 0 else direction[t - 1]
            supertrend[t] = np.nan
            continue
        prev_dir = direction[t - 1] if t > 0 else 0.0
        if prev_dir >= 0:
            if close_arr[t] < final_lower[t]:
                direction[t] = -1.0
            else:
                direction[t] = 1.0 if prev_dir > 0 or \
                    close_arr[t] > final_upper[t] else 0.0
        else:
            if close_arr[t] > final_upper[t]:
                direction[t] = 1.0
            else:
                direction[t] = -1.0
        supertrend[t] = (final_lower[t] if direction[t] > 0
                         else final_upper[t] if direction[t] < 0 else np.nan)

    return {
        f"supertrend_{atr_period}_{multiplier}":
            pd.Series(supertrend, index=df.index),
        f"supertrend_dir_{atr_period}_{multiplier}":
            pd.Series(direction, index=df.index),
    }
