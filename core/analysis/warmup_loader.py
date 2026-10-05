"""warmup 前扩装载公共件（fix-condition-stats-forward-return U1）。

职责：把「请求区间 + 所用指标」翻译成一次带 warmup 前缀的数据装载——
推导复用 registry_meta.derive_warmup（max(200, 指标周期×3)），装载起点
前移 warmup_bars × bar 时长，返回 (df, start_ts)：df 含 warmup 前缀
（原始 OHLCV+衍生品列，指标由调用方在含前缀数据上自行计算），
start_ts 为归一 UTC 的请求起点，调用方以 work.index >= start_ts
切回请求区间统计（warmup「只用不算」）。

时间频率映射 TF_MINUTES 自 TF_SECONDS（14 档权威表）派生——单一
事实源（CARD-04 收敛；validation_service 六档私有表已删，三消费点
改引本表）。历史：原自 validation_service._TF_MINUTES 提升并扩展
1m/5m 两档；3m/2h/6h/8h/12h/1w 此前走缺省兜底 240 分钟（1m/5m
场景过量前扩 48-240 倍，六档为同性质浪费修正）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import derive_warmup
from core.data.timeframes import TF_SECONDS

# 时间频率换算（bar → 分钟）：TF_SECONDS 派生（core/validation/mtf
# 同式），档位与秒表一致
TF_MINUTES = {tf: sec // 60 for tf, sec in TF_SECONDS.items()}


def to_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """时间参数归一（analysis 域单一实现，CARD-06 收敛点）：naive 按
    UTC 解释、aware 统一转 UTC——naive 补 UTC 后与 tz-aware 索引可比
    （date-only 输入不再崩溃的语义来源）。"""
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
    min_warmup_bars: int = 0,
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
        min_warmup_bars: warmup 下限叠加（derive_warmup 已含 200 floor，
            此处取 max(derive_warmup, min_warmup_bars)；缺省 0 行为不变）。
            消费者如 market_snapshot 需要前缀额外覆盖 90 日分位窗口。

    Returns:
        (df, start_ts)：df 含 warmup 前缀（compute_indicators=False），
        start_ts 为归一 UTC 的请求起点；调用方在含前缀数据上计算指标后
        以 work.index >= start_ts 切回统计段。

    Raises:
        ResearchError: RESEARCH_DATA_INSUFFICIENT（装载失败）/
            RESEARCH_WARMUP_INSUFFICIENT（数据起点前实际可得 bar 不足）。
    """
    from core.data.mtf_loader import load_and_prepare_df

    warmup_bars = max(derive_warmup(indicator_names), min_warmup_bars)
    tf_minutes = TF_MINUTES.get(timeframe, 240)
    warmup_delta = pd.Timedelta(minutes=tf_minutes * warmup_bars)
    start_ts = to_utc(pd.Timestamp(start))
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
        from core.analysis.registry_meta import WARMUP_RULE_TEXT
        raise ResearchError(
            "RESEARCH_WARMUP_INSUFFICIENT",
            f"warmup 不足：需要 {warmup_bars} bar，数据起点前实际可得 "
            f"{actual_warmup_bars} bar（数据起点 {df.index[0]}）",
            detail={"warmup_bars": warmup_bars,
                    "available_bars": actual_warmup_bars,
                    "data_start": df.index[0].isoformat(),
                    "rule": WARMUP_RULE_TEXT})

    return df, start_ts
