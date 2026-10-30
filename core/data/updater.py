"""Binance 衍生品数据（funding/OI）的增量更新器（Parquet 落盘归本模块）。

检查本地 Parquet：无数据则按历史窗口全量拉取；有数据则只拉最新
时间戳之后的增量，合并去重落盘。消费链：纸盘 feed 的 funding 节流
刷新+数据保鲜守护器 derivs 轮（每小时归档增量）；拉取能力来自
core/data/fetcher（本模块负责增量决策与写盘）。

K 线更新管道（update_market_data/fetch_klines）已随数据板块整改
退役（cleanup-data-segment R-09——vision 现货 K 线零调用死管道，
K 线实时面归 fapi 接入层）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.fetcher import fetch_funding_history, fetch_open_interest_history
from core.data.storage import save_parquet, load_parquet, get_latest_timestamp, merge_parquet
from core.data.validation import validate_known_interval, validate_symbol




# OI 原生粒度与重采样目标周期（5m 主文件 → 各周期 resample last）
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
    if data_dir is None:
        data_dir = Path("data/market_fapi")
    data_dir = Path(data_dir)
    validate_symbol(symbol)  # 命名白名单（path 直拼写入路径）
    path = data_dir / f"{symbol}_funding.parquet"

    latest_ts = get_latest_timestamp(path)
    start = history_start if latest_ts is None else (latest_ts - pd.Timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    new_df = fetch_funding_history(symbol=symbol, start=start)
    if new_df.empty:
        return load_parquet(path) if path.exists() else new_df

    return merge_parquet(new_df, path)


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
    if data_dir is None:
        data_dir = Path("data/market_fapi")
    data_dir = Path(data_dir)
    if timeframes is None:
        timeframes = list(_OI_TIMEFRAMES)

    # 命名白名单（symbol 与各 tf 均直拼写入路径）
    validate_symbol(symbol)
    for tf in timeframes:
        validate_known_interval(tf)

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
