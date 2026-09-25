"""研究域测试确定性 OHLCV 构造器（improve-research-test-blueprint 1.1）。

单一来源：方向已知的合成数据（定向漂移趋势 / 均值回归震荡 / 趋势+周期
回调复合 / 纯随机游走零假设基线）。五个函数的数学与随机数消耗顺序
逐字保真自原三处本地实现（tests/analysis/test_market_snapshot.py 的
make_regime_ohlcv / make_two_regime_df；tests/analysis/templates/
test_screen_patterns.py 的 _random_walk / _trending_with_dips）——
收口改造的等价性标准是既有断言零改动、同 seed 同输出。

新测试需要结构可控的合成数据（尖刺/高原、可切段）时从本模块组合或
扩展，不在测试文件内平行实现。

Usage:
    from tests.helpers.regime_factory import (
        make_trend_ohlcv, make_range_ohlcv, make_two_regime_df,
        make_random_walk_ohlcv, make_directional_composite,
    )
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_BASE = 40000.0  # 与 test_market_snapshot.BASE 对齐的默认中枢价


def make_trend_ohlcv(
    n: int,
    *,
    seed: int,
    start: str = "2024-01-01",
    freq: str = "1D",
    base: float = _BASE,
) -> pd.DataFrame:
    """确定性趋势段 OHLCV：复利定向漂移 +0.6%/bar + 小噪声（ADX 收敛 >25）。

    复利结构自低位定向爬升，远离震荡中枢；OHLC 乘法扰动、volume 整数
    均匀分布——形制对齐衍生品 merger 消费的五列 OHLCV。
    """
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="timestamp")
    rng = np.random.default_rng(seed)
    closes = np.empty(n)
    c = base * 0.5
    for i in range(n):
        c *= 1.0 + 0.006 + rng.normal(0, 0.001)
        closes[i] = c
    wiggle = np.abs(rng.normal(0, 0.002, n))
    return pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.001, n)),
        "high": closes * (1 + wiggle),
        "low": closes * (1 - wiggle),
        "close": closes,
        "volume": rng.integers(800, 1200, n).astype(float),
    }, index=idx)


def make_range_ohlcv(
    n: int,
    *,
    seed: int,
    start: str = "2024-01-01",
    freq: str = "1D",
    base: float = _BASE,
) -> pd.DataFrame:
    """确定性震荡段 OHLCV：向 base 中枢均值回归（力度 0.15）+ 振荡噪声。

    价格被持续拉回中枢，ADX 回落 <20——与 make_trend_ohlcv 构成可判
    行情类型的两极数据。
    """
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="timestamp")
    rng = np.random.default_rng(seed)
    closes = np.empty(n)
    c = base * 0.5
    for i in range(n):
        c += (base - c) * 0.15
        c *= 1.0 + rng.normal(0, 0.004)
        closes[i] = c
    wiggle = np.abs(rng.normal(0, 0.002, n))
    return pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.001, n)),
        "high": closes * (1 + wiggle),
        "low": closes * (1 - wiggle),
        "close": closes,
        "volume": rng.integers(800, 1200, n).astype(float),
    }, index=idx)


def make_two_regime_df(n_trend: int = 330, n_range: int = 260,
                       seed: int = 11, base: float = _BASE) -> pd.DataFrame:
    """单数据集两段：前 n_trend 根趋势、后 n_range 根震荡（1D 频率）。

    供「同数据集两个 as_of 时点判出不同行情类型」的区分验收。
    """
    n = n_trend + n_range
    idx = pd.date_range("2024-01-01", periods=n, freq="1D",
                        tz="UTC", name="timestamp")
    rng = np.random.default_rng(seed)
    closes = np.empty(n)
    c = base * 0.5  # 趋势段自低位定向爬升，远离震荡中枢
    for i in range(n):
        if i < n_trend:
            c *= 1.0 + 0.006 + rng.normal(0, 0.001)
        else:
            c += (base - c) * 0.15
            c *= 1.0 + rng.normal(0, 0.004)
        closes[i] = c
    wiggle = np.abs(rng.normal(0, 0.002, n))
    return pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.001, n)),
        "high": closes * (1 + wiggle),
        "low": closes * (1 - wiggle),
        "close": closes,
        "volume": rng.integers(800, 1200, n).astype(float),
    }, index=idx)


def make_random_walk_ohlcv(seed: int, n: int = 600) -> pd.DataFrame:
    """纯随机游走 OHLCV（1h 频率）：无信号零假设基线。

    累加高斯步进，无漂移无回归——FDR 受控类测试用它证明「纯运气数据
    上假规律命中被校正挤掉」。
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    spread = np.abs(rng.normal(1, 0.3, n)) + 0.01
    return pd.DataFrame({
        "open": close, "high": close + spread, "low": close - spread,
        "close": close, "volume": pd.Series(rng.random(n) * 1000 + 100, index=idx),
    }, index=idx)


def make_directional_composite(seed: int = 7, n: int = 900) -> pd.DataFrame:
    """平趋势 + 深周期回调复合 OHLCV（1h 频率）：方向符号锚定数据。

    谷底反弹使「指标低位」类条件有真信号、峰顶回落使「指标高位」类
    条件有负信号——供扫描/统计模板的方向语义断言（gt 全正、lt 全负）。
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    t = np.arange(n)
    trend = 100 + t * 0.03          # 平趋势：12 bar 贡献 +0.36
    dip = -12 * np.sin(t / 40 * np.pi)  # 深回调：谷后 12 bar 反弹 ≈ +11
    noise = rng.normal(0, 0.5, n)
    close = pd.Series(trend + dip + noise, index=idx)
    spread = np.abs(rng.normal(1, 0.25, n)) + 0.01
    return pd.DataFrame({
        "open": close, "high": close + spread, "low": close - spread,
        "close": close, "volume": pd.Series(rng.random(n) * 1000 + 100, index=idx),
    }, index=idx)
