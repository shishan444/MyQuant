"""warmup 前扩装载公共件（fix-condition-stats-forward-return U1）。

职责：把「请求区间 + 所用指标」翻译成一次带 warmup 前缀的数据装载——
推导复用 registry_meta.derive_warmup（max(200, 指标周期×3)），装载起点
前移 warmup_bars × bar 时长，返回 (df, start_ts)：df 含 warmup 前缀
（原始 OHLCV+衍生品列，指标由调用方在含前缀数据上自行计算），
start_ts 为归一 UTC 的请求起点，调用方以 work.index >= start_ts
切回请求区间统计（warmup「只用不算」）。

时间频率映射自 validation_service._TF_MINUTES 提升为公共常量并扩展
1m/5m 两档——分析模板可作用于 1m/5m 数据集，原映射缺省兜底 240 分钟
会把 1m 的 200-bar warmup 前扩成约 33 天。缺省兜底 240（4h）与既有
行为一致。validation_service 的迁移为独立后续任务（推导权威已共享）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import derive_warmup

# 时间频率换算（bar → 分钟）：validation_service._TF_MINUTES 全量 + 1m/5m
TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "3d": 4320,
}


def _to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """时间参数入口归一：naive 按 UTC 解释（语义对齐
    validation_service._to_utc，不引入第三种 tz 语义）。"""
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def load_with_warmup(
    data_dir: Path,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    indicator_names: list[str],
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """[start-warmup, end] 前扩装载：返回含 warmup 前缀的 df 与请求起点。

    Args:
        data_dir: parquet 数据目录。
        symbol: 交易对（如 BTCUSDT）。
        timeframe: bar 周期（TF_MINUTES 键；未知周期按 240 分钟兜底）。
        start: 请求区间起点（naive 按 UTC 解释）。
        end: 请求区间终点（沿用 load_and_prepare_df 的闭端过滤）。
        indicator_names: 本次实际用到的指标名（derive_warmup 输入，
            INDICATOR_REGISTRY 键）。

    Returns:
        (df, start_ts)：df 含 warmup 前缀（compute_indicators=False），
        start_ts 为归一 UTC 的请求起点；调用方在含前缀数据上计算指标后
        以 work.index >= start_ts 切回统计段。

    Raises:
        ResearchError: RESEARCH_DATA_INSUFFICIENT（装载失败）/
            RESEARCH_WARMUP_INSUFFICIENT（数据起点前实际可得 bar 不足）。
    """
    from core.data.mtf_loader import load_and_prepare_df

    warmup_bars = derive_warmup(indicator_names)
    tf_minutes = TF_MINUTES.get(timeframe, 240)
    warmup_delta = pd.Timedelta(minutes=tf_minutes * warmup_bars)
    start_ts = _to_utc(pd.Timestamp(start))
    load_start = (start_ts - warmup_delta).isoformat()

    df = load_and_prepare_df(
        data_dir, symbol, timeframe, data_start=load_start, data_end=end,
        compute_indicators=False,
    )
    if df is None:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"区间数据不足: {symbol} {timeframe} [{load_start}, {end})")

    # warmup 可得性：装载后起点必须真正早于 start（warmup 段有数据）
    actual_warmup_bars = int((df.index < start_ts).sum())
    if actual_warmup_bars < warmup_bars:
        raise ResearchError(
            "RESEARCH_WARMUP_INSUFFICIENT",
            f"warmup 不足：需要 {warmup_bars} bar，数据起点前实际可得 "
            f"{actual_warmup_bars} bar（数据起点 {df.index[0]}）")

    return df, start_ts
