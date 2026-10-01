"""Binance K 线与衍生品数据的增量更新器（Parquet 落盘归本模块）。

检查本地 Parquet：无数据则按历史窗口全量拉取；有数据则只拉最新
时间戳之后的增量，合并去重落盘。唯一生产消费是 core/trading/runner
的模拟盘任务数据保新（scripts 手动补数为辅）；拉取能力来自
core/data/fetcher（本模块负责增量决策与写盘）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.fetcher import fetch_funding_history, fetch_klines, fetch_open_interest_history
from core.data.storage import save_parquet, load_parquet, get_latest_timestamp, merge_parquet
from core.data.validation import validate_known_interval, validate_symbol


def update_market_data(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    data_dir: Path | None = None,
    history_years: int = 2,
    api_key: str = "",
    api_secret: str = "",
) -> pd.DataFrame:
    """确保本地 Parquet 数据最新，按需增量拉取。

    Args:
        symbol: 交易对。
        interval: K 线周期（TF_SECONDS 键集内）。
        data_dir: Parquet 数据目录（默认 data/market）。
        history_years: 本地无数据时的全量回溯年数。
        api_key: Binance API key（fetcher 侧未使用，签名兼容保留）。
        api_secret: 同上。

    Returns:
        全量 DataFrame（本地存量 + 本次增量，合并去重后）。
        本地无数据且拉取为空时返回空帧。

    Raises:
        ValueError: symbol/interval 未过命名白名单。调用方
            core/trading/runner._fetch_and_update 的 except Exception
            会吞掉异常返回 None（闰年 2/29 静默失败当时即表现为
            无数据而非报错的同一链路）。
    """
    if data_dir is None:
        data_dir = Path("data/market")
    data_dir = Path(data_dir)

    # 命名白名单拒绝式校验（path 直拼写入路径，防路径穿越）
    validate_symbol(symbol)
    validate_known_interval(interval)

    path = data_dir / f"{symbol}_{interval}.parquet"

    latest_ts = get_latest_timestamp(path)

    if latest_ts is None:
        # 全量拉取（本地无数据，新 symbol 首拉）
        df = fetch_klines(
            symbol=symbol,
            interval=interval,
            start_str=f"{history_years} year ago UTC",
            api_key=api_key,
            api_secret=api_secret,
        )
        save_parquet(df, path)
        return df

    # 增量拉取：从本地最新时间戳起
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

    # 合并去重排序（merge_parquet 同逻辑单一实现）
    return merge_parquet(new_df, path)


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
    if data_dir is None:
        data_dir = Path("data/market")
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
        data_dir = Path("data/market")
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
