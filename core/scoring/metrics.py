"""权益曲线与逐笔交易数据的原始绩效指标提取。

职能与价值：从权益曲线（equity curve，逐 bar 组合价值序列）与逐笔
交易数据提取绩效指标字典（17 键），是绩效数值的单一权威实现——
指标口径（年化、回撤、爆仓截断、样本闸门）在此单源，评分与呈现
层只消费不重复计算。
服务对象：core/scoring/scorer.py（score_strategy 约束判定以本模块
输出为输入）与回测汇总链（结果呈现、研究循环指标面）。
职责边界：只做指标计算与数值域防御（样本不足闸门；爆仓三处截断
——total_return<=-1 时 annual_return=-1.0、max_drawdown 下界 -1.0、
calmar_ratio=0.0）；不做策略评分（scorer 职责）、不判爆仓清算
（单一权威在 backtest-liquidation-detection，不建第二权威）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# bars_per_year 全档映射（公共单一权威源）：数字币 7×24 市场的分钟→年换算。
# 回测引擎与 REST 批量回测同源引用，杜绝两套映射漂移（原 engine 缺
# 1m/5m/2h 档、batch 缺 3d 档，各自缺省档位为政）。
BARS_PER_YEAR: dict[str, int] = {
    "1m": 525600,   # 365*24*60
    "5m": 105120,   # 365*24*12
    "15m": 35040,   # 365*24*4
    "30m": 17520,   # 365*24*2
    "1h": 8760,     # 365*24
    "2h": 4380,     # 365*12
    "4h": 2190,     # 365*6
    "1d": 365,
    "3d": 365 // 3,  # 121
}
# 未知 timeframe 的缺省档（= 4h 档，既有两处缺省行为的并集）
DEFAULT_BARS_PER_YEAR: int = 2190


def _safe_mean(arr: "np.ndarray") -> "float | None":
    """样本不足出口闸门（0.2.11 D5）：n=0 → None（无答案，不产 NaN）。"""
    if arr is None or len(arr) == 0:
        return None
    return float(np.mean(arr))


def _safe_std(arr: "np.ndarray") -> "float | None":
    """样本不足出口闸门（0.2.11 D5）：n<2 → None（ddof=1 无定义）。

    单元素 np.std(ddof=1) 会发 degrees-of-freedom RuntimeWarning 后产
    NaN——闸门前置不产毒数据（981 条警告收敛的出口侧收口）。
    """
    if arr is None or len(arr) < 2:
        return None
    return float(np.std(arr, ddof=1))


def _annualize(total_return: float, years: float) -> float:
    """区间收益年化（模块内共用）：annual_return 与 market_annual_return
    两处同公式的单一权威。

    years>0 判空与 max 下限 0.01 是有意的双防御行为锚点——极短样本
    （years→0）防指数爆开，原样保留不合并。
    """
    return (1 + total_return) ** (1 / max(years, 0.01)) - 1 if years > 0 else 0.0


def compute_metrics(
    equity_curve: pd.Series,
    total_trades: int = 0,
    bars_per_year: int = 365 * 6,  # 4h 档年 bar 数（缺省）
    trade_win_rate: float | None = None,
    trade_returns: np.ndarray | None = None,
    benchmark_close: pd.Series | None = None,
) -> dict:
    """提取原始绩效指标（17 键 dict，键集见 return 语句）。

    职责契约：Sharpe 与 win_rate 优先用逐笔交易数据（trade_returns/
    trade_win_rate），不可得时回退 bar 级权益曲线口径；annual_return、
    max_drawdown、calmar 恒走权益曲线。各指标口径与防御语义见对应
    行内注释（样本闸门、门槛、截断下界）。

    Args:
        equity_curve: 逐 bar 组合价值序列。
        total_trades: 已平仓交易笔数。
        bars_per_year: 一年的 bar 数（缺省 6*365，即 4h 档）。
        trade_win_rate: 逐笔胜率（0-1），不可得传 None。
        trade_returns: 逐笔收益率数组（PnL/init_cash），不可得传 None。
        benchmark_close: 基准收盘价序列（无杠杆买入持有基线），须覆盖
            与 equity_curve 相同的时间范围。零费口径：基准不扣
            资金费/交易费（方向从严——基准更强、超额更难达成）。

    Returns:
        17 键指标 dict：annual_return、sharpe_ratio、max_drawdown、
        win_rate、calmar_ratio、total_trades、sortino_ratio、
        profit_factor、max_consecutive_losses、monthly_consistency、
        r_squared、total_bars、market_annual_return、alpha、
        backtest_years、benchmark_return（区间基准收益，None=无基准）、
        excess_return（区间超额=total_return-benchmark_return，
        None=无基准）。

    爆仓口径（fix-audit-correctness E-01）：total_return <= -1（权益末值
    不大于 0）时 annual_return 截断 -1.0、max_drawdown 下界 -1.0、
    calmar_ratio 置 0.0——全部有界实数，不产 nan/警告；正常曲线全部
    指标与该分支加入前逐值一致。
    """
    if len(equity_curve) < 2 or total_trades == 0:
        # 零交易：win_rate 为缺失语义（None），不参与评分维度
        return {
            "annual_return": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "win_rate": None,
            "calmar_ratio": 0.0,
            "total_trades": 0,
            "sortino_ratio": 0.0,
            "profit_factor": 0.0,
            "max_consecutive_losses": 0,
            "monthly_consistency": 0.0,
            "r_squared": 0.0,
            "total_bars": len(equity_curve),
            "market_annual_return": 0.0,
            "alpha": 0.0,
            "backtest_years": 0.0,
            "benchmark_return": None,
            "excess_return": None,
        }

    # 年化收益（权益曲线口径，标准形制）
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
    n_bars = len(equity_curve)
    years = n_bars / bars_per_year
    # 爆仓分支（fix-audit-correctness E-01）：total_return <= -1 即全损
    # （权益末值不大于 0），annual 截断 -1.0——_annualize 对爆仓形
    # (1+r)**x 的负底数分数幂产 nan/复数（numpy 标量语义链），
    # 截断优于哨兵；清算判定权威在 backtest-liquidation-detection，
    # 绩效层不建第二权威（不新增 liquidated 键）
    if total_return <= -1.0:
        annual_return = -1.0
    else:
        annual_return = _annualize(total_return, years)

    # 最大回撤（权益曲线口径，标准形制）
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    max_drawdown = float(drawdown.min())
    if np.isnan(max_drawdown):
        max_drawdown = 0.0
    # 爆仓口径（E-01）：回撤语义域 [-1, 0]——负权益深坑截断下界 -1.0，
    # 超界亏损的识别由回测清算判定承载，不在回撤指标重复表达
    if max_drawdown < -1.0:
        max_drawdown = -1.0

    # Sharpe ratio：优先逐笔口径，回退 bar 级
    sharpe_ratio = 0.0
    if trade_returns is not None and len(trade_returns) > 0:
        # 0.2.11 出口闸门：n<2 时 std=None（不产 NaN/不发警告）
        trade_mean = _safe_mean(trade_returns)
        trade_std = _safe_std(trade_returns)
        if trade_std is not None and trade_std > 0:
            trades_per_year = total_trades / max(years, 0.01)
            sharpe_ratio = (trade_mean / trade_std) * np.sqrt(trades_per_year)
    # 门槛 5 笔：交易数不足时不启用 bar 级 Sharpe 回退（回退口径需足够交易样本）
    elif total_trades >= 5:
        # 回退：bar 级 Sharpe（权益曲线收益序列，仅交易数达门槛时启用）
        returns = equity_curve.pct_change(fill_method=None).dropna()
        if len(returns) > 0:
            mean_ret = returns.mean()
            std_ret = returns.std()
            if std_ret > 0 and not np.isnan(std_ret):
                sharpe_ratio = (mean_ret / std_ret) * np.sqrt(bars_per_year)

    # 胜率：优先逐笔口径，回退 bar 级
    if trade_win_rate is not None:
        win_rate = trade_win_rate
    else:
        # 回退：bar 级（精度较低）；零收益样本同样归缺失
        returns = equity_curve.pct_change(fill_method=None).dropna()
        if len(returns) > 0:
            win_rate = float((returns > 0).sum() / len(returns))
        else:
            win_rate = None

    # Calmar ratio（年化收益/最大回撤）
    # 除零保护：回撤近零（<0.001）时 calmar 无意义，置 0 不除近零数；
    # 爆仓置 0.0（E-01）：全损时该比率无意义（-1/-1 数值上=1 会
    # 误导性呈现「好」比率），沿除零保护同哲学
    if total_return <= -1.0:
        calmar_ratio = 0.0
    elif abs(max_drawdown) > 0.001:
        calmar_ratio = abs(annual_return / max_drawdown)
    else:
        calmar_ratio = 0.0

    # Sortino ratio：同 Sharpe 形制，仅以下行波动（downside deviation）为分母
    sortino_ratio = 0.0
    if trade_returns is not None and len(trade_returns) > 1:
        downside = trade_returns[trade_returns < 0]
        if len(downside) > 0:
            # 0.2.11 出口闸门（生产真警告点）：多笔但仅 1 笔负收益时
            # downside 单元素 np.std(ddof=1) 发 dof<=0 警告——闸门前置
            downside_std = _safe_std(downside)
            if downside_std is not None and downside_std > 0:
                trades_per_year = total_trades / max(years, 0.01)
                sortino_ratio = (_safe_mean(trade_returns) / downside_std) * np.sqrt(trades_per_year)

    # Profit factor：毛利/毛损（盈利笔合计/亏损笔合计绝对值）
    profit_factor = 0.0
    if trade_returns is not None and len(trade_returns) > 0:
        gross_profit = float(np.sum(trade_returns[trade_returns > 0]))
        gross_loss = float(abs(np.sum(trade_returns[trade_returns < 0])))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = 10.0  # 全胜无亏损

    # 最大连亏笔数
    max_consecutive_losses = 0
    if trade_returns is not None and len(trade_returns) > 0:
        current_streak = 0
        for r in trade_returns:
            if r < 0:
                current_streak += 1
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                current_streak = 0

    # 月度一致性：盈利月份占比（0-1）
    monthly_consistency = 0.0
    # 最小样本 30 bar：月度 resample 需足够时间跨度，不足则一致性置 0
    if len(equity_curve) > 30:
        try:
            monthly_returns = equity_curve.resample('ME').last().pct_change(fill_method=None).dropna()
            if len(monthly_returns) > 0:
                monthly_consistency = float((monthly_returns > 0).sum() / len(monthly_returns))
        except Exception:
            pass

    # R-squared：权益曲线对线性增长的拟合度（1.0=完美线性增长）
    r_squared = 0.0
    # 最小样本 10 bar：样本过少时线性拟合残差无统计意义，置 0
    if len(equity_curve) > 10:
        x = np.arange(len(equity_curve))
        y = equity_curve.values
        # var 须与 cov 同为 ddof=1 配对（自由度约掉）——混用 ddof=0 会使
        # 斜率高估 n/(n-1) 倍、完美线性曲线的 r² 系统性低于 1
        slope = np.cov(x, y)[0, 1] / max(np.var(x, ddof=1), 1e-10)
        intercept = np.mean(y) - slope * np.mean(x)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        if ss_tot > 0:
            r_squared = float(max(0.0, 1.0 - ss_res / ss_tot))

    # Alpha：相对无杠杆买入持有基准的年化超额
    market_annual_return = 0.0
    alpha = 0.0
    benchmark_return = None
    excess_return = None
    if benchmark_close is not None and len(benchmark_close) >= 2:
        benchmark_return = float(
            benchmark_close.iloc[-1] / benchmark_close.iloc[0] - 1)
        market_annual_return = _annualize(benchmark_return, years)
        alpha = annual_return - market_annual_return
        # 区间口径超额（improve-backtest-benchmark-disclosure Decision 1）：
        # total_return - benchmark_return，短样本不做年化外推；与年化口径
        # alpha（annual_return - market_annual_return）并存，各表各义
        excess_return = float(total_return - benchmark_return)

    return {
        "annual_return": float(annual_return),
        "sharpe_ratio": float(sharpe_ratio),
        "max_drawdown": float(max_drawdown),
        "win_rate": None if win_rate is None else float(win_rate),
        "calmar_ratio": float(calmar_ratio),
        "total_trades": total_trades,
        "sortino_ratio": float(sortino_ratio),
        "profit_factor": float(profit_factor),
        "max_consecutive_losses": int(max_consecutive_losses),
        "monthly_consistency": float(monthly_consistency),
        "r_squared": float(r_squared),
        "total_bars": len(equity_curve),
        "market_annual_return": float(market_annual_return),
        "alpha": float(alpha),
        "backtest_years": float(years),
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
    }
