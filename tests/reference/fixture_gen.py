"""种子纯函数合成数据生成器（五场景）+ 数据自检。

同种子同数据（default_rng 纯函数链）；assert_valid_ohlcv 在数据进链路前
把关 OHLC 不变式——坏数据不进链路（spec: 合成数据可信输入）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SCENARIOS = ("normal", "short_window", "trend", "volatile", "liquidation_edge")


def gen_ohlcv(scenario: str = "normal", seed: int = 42, n: int | None = None,
              base_price: float = 40000.0, freq: str = "4h",
              start: str = "2024-01-01") -> pd.DataFrame:
    """Generate scenario OHLCV deterministically from (scenario, seed).

    场景设计（数值语义目标，非形状目标）：
    - normal:            随机游走（默认 500 bar），一般会有数十笔信号交易
    - short_window:      极短窗（30 bar），触发 years 下限保护与缺省语义
    - trend:             单边上涨行情（确定性漂移 + 小噪声）
    - volatile:          剧烈波动（大 sigma + 跳跃）
    - liquidation_edge:  先跌后稳，构造高杠杆下权益穿 maintenance 的边界
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
    if scenario == "short_window":
        n = n or 30
    else:
        n = n or 500

    rng = np.random.default_rng(seed)
    if scenario == "normal":
        returns = rng.standard_normal(n) * 0.01 + 0.0001
    elif scenario == "short_window":
        returns = rng.standard_normal(n) * 0.01 + 0.0002
    elif scenario == "trend":
        returns = rng.standard_normal(n) * 0.003 + 0.006
    elif scenario == "volatile":
        jumps = (rng.random(n) > 0.9) * rng.standard_normal(n) * 0.08
        returns = rng.standard_normal(n) * 0.03 + jumps
    else:  # liquidation_edge
        returns = rng.standard_normal(n) * 0.004 - 0.004

    close = base_price * np.cumprod(1 + returns)
    opn = close * (1 + rng.standard_normal(n) * 0.002)
    # wick 从 body 边界向外延伸，结构性保证 low<=min(open,close)<=max<=high
    body_min = np.minimum(opn, close)
    body_max = np.maximum(opn, close)
    high = body_max * (1 + np.abs(rng.standard_normal(n)) * 0.006)
    low = body_min * (1 - np.abs(rng.standard_normal(n)) * 0.006)
    volume = rng.integers(100, 10000, size=n).astype(float)

    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="timestamp")
    df = pd.DataFrame(
        {"open": opn, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    assert_valid_ohlcv(df)
    return df


def assert_valid_ohlcv(df: pd.DataFrame) -> None:
    """OHLC 不变式自检：不满足即 ValueError 指明违反项（坏数据不进链路）。"""
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            raise ValueError(f"invariant broken: missing column {col!r}")
    if df.isna().any().any():
        raise ValueError("invariant broken: NaN present")
    body_min = np.minimum(df["open"], df["close"])
    body_max = np.maximum(df["open"], df["close"])
    bad_low = df["low"] > body_min
    if bad_low.any():
        i = int(np.argmax(bad_low.values))
        raise ValueError(f"invariant broken: low > min(open,close) at {df.index[i]}")
    bad_high = df["high"] < body_max
    if bad_high.any():
        i = int(np.argmax(bad_high.values))
        raise ValueError(f"invariant broken: high < max(open,close) at {df.index[i]}")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError("invariant broken: timestamp index not strictly monotonic")
    if (df <= 0).any().any():
        raise ValueError("invariant broken: non-positive price/volume")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("invariant broken: index is not DatetimeIndex")
