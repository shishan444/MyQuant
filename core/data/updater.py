"""Incremental data updater for Binance K-line data.

Checks local Parquet files for existing data. If none exists, does a full
fetch. Otherwise, only fetches data after the latest local timestamp and
appends it, removing duplicates.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.fetcher import fetch_klines
from core.data.storage import save_parquet, load_parquet, get_latest_timestamp, merge_parquet


def update_market_data(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    data_dir: Path | None = None,
    history_years: int = 2,
    api_key: str = "",
    api_secret: str = "",
) -> pd.DataFrame:
    """Ensure local Parquet is up-to-date, fetching incrementally if needed.

    Args:
        symbol: Trading pair.
        interval: K-line interval.
        data_dir: Directory for Parquet files (default: data/market).
        history_years: Years of history to fetch if no local data.
        api_key: Binance API key.
        api_secret: Binance API secret.

    Returns:
        Complete DataFrame with all available data.
    """
    if data_dir is None:
        data_dir = Path("data/market")
    data_dir = Path(data_dir)

    path = data_dir / f"{symbol}_{interval}.parquet"

    latest_ts = get_latest_timestamp(path)

    if latest_ts is None:
        # Full fetch
        df = fetch_klines(
            symbol=symbol,
            interval=interval,
            start_str=f"{history_years} year ago UTC",
            api_key=api_key,
            api_secret=api_secret,
        )
        save_parquet(df, path)
        return df

    # Incremental fetch: from latest timestamp onward
    start_str = latest_ts.strftime("%Y-%m-%d %H:%M:%S")
    new_df = fetch_klines(
        symbol=symbol,
        interval=interval,
        start_str=start_str,
        api_key=api_key,
        api_secret=api_secret,
    )

    if new_df.empty:
        existing = load_parquet(path)
        return existing

    # Merge: concat, remove duplicates, sort
    existing = load_parquet(path)
    combined = pd.concat([existing, new_df])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()

    save_parquet(combined, path)
    return combined


# 衍生品 OI 数据的原始粒度（S3 metrics 归档原生 5m）与需落盘的周期
_OI_RAW_TIMEFRAME = "5m"
_OI_TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "1d", "3d"]

# pandas resample 频率串：与 K 线周期命名一致
_RESAMPLE_FREQ = {
    "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1D", "3d": "3D",
}


def update_funding_data(
    symbol: str = "BTCUSDT",
    data_dir: Path | None = None,
    history_start: str = "2024-01-01",
) -> pd.DataFrame:
    """增量更新资金费率 Parquet（{symbol}_funding.parquet，derivatives_merger 期待形制）。

    数据源为 S3 月度归档（见 fetch_funding_history）；已有数据按最新时间戳
    增量补齐，首跑按 history_start 全量拉取。
    """
    from core.data.fetcher import fetch_funding_history

    if data_dir is None:
        data_dir = Path("data/market")
    data_dir = Path(data_dir)
    path = data_dir / f"{symbol}_funding.parquet"

    latest_ts = get_latest_timestamp(path)
    start = history_start if latest_ts is None else (latest_ts - pd.Timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    new_df = fetch_funding_history(symbol=symbol, start=start)
    if new_df.empty:
        return load_parquet(path) if path.exists() else new_df

    merge_parquet(new_df, path)
    return load_parquet(path)


def update_open_interest_data(
    symbol: str = "BTCUSDT",
    data_dir: Path | None = None,
    history_start: str = "2024-01-01",
    timeframes: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """增量更新持仓量 Parquet（{symbol}_oi_{tf}.parquet，derivatives_merger 期待形制）。

    维护 5m 原生粒度主文件，再重采样（last）生成各周期文件。
    归档侧有约 T-5 延迟，增量终点即当前最新可得归档。
    """
    from core.data.fetcher import fetch_open_interest_history

    if data_dir is None:
        data_dir = Path("data/market")
    data_dir = Path(data_dir)
    if timeframes is None:
        timeframes = list(_OI_TIMEFRAMES)

    raw_path = data_dir / f"{symbol}_oi_{_OI_RAW_TIMEFRAME}.parquet"
    latest_ts = get_latest_timestamp(raw_path)
    start = history_start if latest_ts is None else (latest_ts - pd.Timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    new_df = fetch_open_interest_history(symbol=symbol, start=start)
    if not new_df.empty:
        merge_parquet(new_df, raw_path)

    if not raw_path.exists():
        return {}

    raw = load_parquet(raw_path)
    if raw.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        tf_df = raw[["open_interest"]].resample(_RESAMPLE_FREQ[tf]).last().dropna()
        tf_df.index.name = "timestamp"
        save_parquet(tf_df, data_dir / f"{symbol}_oi_{tf}.parquet")
        out[tf] = tf_df
    return out
