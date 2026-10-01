"""衍生品数据合并：OI 与资金费率前向填充进 OHLCV。

职能：把可选的 open_interest / funding_rate 两列按 OHLCV 时间索引
对齐（reindex + ffill——衍生数据粒度与 K 线不同频，前向填充取
「最近一次已知值」），供下游指标计算。服务对象唯一：
mtf_loader._try_merge_derivatives → 高周期装载链（研究/回测/交易）。
纯内存变换不落盘；衍生数据缺失时优雅直通（不阻塞装载）。
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def merge_derivatives_into_ohlcv(
    ohlcv_df: pd.DataFrame,
    oi_df: pd.DataFrame | None = None,
    funding_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """把衍生品数据经前向填充合并进 OHLCV DataFrame。

    Args:
        ohlcv_df: OHLCV DataFrame（DatetimeIndex）。
        oi_df: 可选，含 open_interest 列的 DataFrame（DatetimeIndex）。
        funding_df: 可选，含 funding_rate 列的 DataFrame（DatetimeIndex）。

    Returns:
        有衍生数据时返回带衍生列（前向填充）的副本；两者皆缺/为空/
        缺目标列时该列不添加。oi_df 与 funding_df 均为 None 时原样
        返回同一对象（不拷贝）。衍生列在 OHLCV 早于首个衍生数据
        处为 NaN（无更早值可填充）。
    """
    if oi_df is None and funding_df is None:
        return ohlcv_df

    result = ohlcv_df.copy()

    if oi_df is not None and not oi_df.empty and "open_interest" in oi_df.columns:
        oi_series = oi_df["open_interest"]
        # 重索引到 OHLCV 频率并前向填充（取最近一次已知值）
        oi_aligned = oi_series.reindex(result.index, method="ffill")
        result["open_interest"] = oi_aligned
        logger.debug(
            "Merged OI data: %d/%d bars filled",
            oi_aligned.notna().sum(), len(result),
        )

    if funding_df is not None and not funding_df.empty and "funding_rate" in funding_df.columns:
        fr_series = funding_df["funding_rate"]
        fr_aligned = fr_series.reindex(result.index, method="ffill")
        result["funding_rate"] = fr_aligned
        logger.debug(
            "Merged Funding Rate data: %d/%d bars filled",
            fr_aligned.notna().sum(), len(result),
        )

    return result
