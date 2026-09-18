"""参照指标实现：独立内联（零 core.scoring 依赖），公式注记公开定义。

与被测 compute_metrics 的关系：同一数学契约的两条独立编码路径
（被测 metrics.py / 参照本文件），比对即交叉验证。
"""

from __future__ import annotations

import numpy as np

# timeframe → 年 bar 数（公开事实：加密市场 365 天连续交易）
BARS_PER_YEAR: dict[str, int] = {
    "15m": 365 * 96, "30m": 365 * 48, "1h": 365 * 24,
    "4h": 365 * 6, "1d": 365, "3d": 365 // 3,
}


def reference_metrics(
    equity: np.ndarray,
    trade_pnls: list[float] | np.ndarray,
    init_cash: float,
    timeframe: str = "4h",
) -> dict[str, float | int]:
    """Compute the five audited metrics on the final (leveraged+funded) curve.

    公式（与被测 metrics.py 行为契约一致，独立内联）：
    - total_return = eq[-1]/eq[0] - 1
    - annual_return = (1+tr)^(1/max(years,0.01)) - 1；years = n/bars_per_year
      [公开定义：几何年化；短窗 years 下限 0.01 防发散——行为锚点非数学理想]
    - max_drawdown = min((eq-cummax)/cummax)   [公开定义：峰谷回撤，∈[-1,0]]
    - sharpe：优先逐笔年化 (mean/std_ddof1)*sqrt(trades_per_year)，
      trade_returns = pnl/init_cash，trades_per_year = N/max(years,0.01)
      [注：被测口径为逐笔收益年化夏普变体，非逐 bar 夏普；锚 metrics.py]
      逐笔不足 2 笔时回退逐 bar（需 N>=5），再不足 → 0.0
    - win_rate = count(pnl>0)/N（严格大于 0）
    - 无交易 → 全零缺省（win_rate 语义=缺失，由比对器区分 None/0.0）
    """
    pnl_arr = np.asarray(trade_pnls, dtype=np.float64)
    total_trades = int(len(pnl_arr))
    n = len(equity)
    bars_per_year = BARS_PER_YEAR.get(timeframe, 365 * 6)

    if n < 2 or total_trades == 0:
        return {
            "total_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0,
            "win_rate": 0.0, "total_trades": 0, "annual_return": 0.0,
        }

    total_return = float(equity[-1] / equity[0]) - 1.0
    years = n / bars_per_year
    annual_return = (1.0 + total_return) ** (1.0 / max(years, 0.01)) - 1.0

    cummax = np.maximum.accumulate(equity)
    drawdown = (equity - cummax) / cummax
    max_drawdown = float(np.min(drawdown))
    if np.isnan(max_drawdown):
        max_drawdown = 0.0

    sharpe_ratio = 0.0
    trade_returns = pnl_arr / init_cash
    if len(trade_returns) > 1:
        std = float(np.std(trade_returns, ddof=1))
        if std > 0 and not np.isnan(std):
            trades_per_year = total_trades / max(years, 0.01)
            sharpe_ratio = float(np.mean(trade_returns) / std * np.sqrt(trades_per_year))
    elif total_trades >= 5:
        rets = np.diff(equity) / equity[:-1]
        std = float(np.std(rets, ddof=1))
        if len(rets) > 0 and std > 0 and not np.isnan(std):
            sharpe_ratio = float(np.mean(rets) / std * np.sqrt(bars_per_year))

    win_rate = float(np.count_nonzero(pnl_arr > 0) / total_trades)

    return {
        "total_return": total_return,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "annual_return": annual_return,
    }
