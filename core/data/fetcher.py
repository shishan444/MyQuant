"""Binance K-line data fetcher.

Uses Binance public data API (data-api.binance.vision) which is accessible
from restricted regions via proxy, unlike the standard API endpoints.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Default proxy for Binance API access (override via BINANCE_PROXY env var)
_DEFAULT_PROXY = os.environ.get("BINANCE_PROXY", "http://172.20.112.1:10809")

# Public data API - accessible from all regions
_BASE_URL = os.environ.get("BINANCE_DATA_API_URL", "https://data-api.binance.vision")

# Public S3 archive for historical derivatives data (funding / open interest),
# same "vision" family: reachable from restricted regions without a key.
_S3_ARCHIVE_URL = "https://data.binance.vision/data/futures/um"

# Maximum rows per request (Binance limit)
_PAGE_LIMIT = 1000

# Concurrent downloads for daily-metric archives (small files, static bucket)
_ARCHIVE_WORKERS = 8


def _years_ago_ms(n: int) -> int:
    """N 年前的毫秒时间戳；闰日 2/29 落入非闰年时回退 2/28。

    仅当当前日期为 2/29 且目标年份非闰年时 dt.replace 抛
    ValueError——年度起点取近似下界即可，回退不引入前视。
    """
    dt = datetime.now(timezone.utc)
    try:
        dt = dt.replace(year=dt.year - n)
    except ValueError:
        dt = dt.replace(year=dt.year - n, month=2, day=28)
    return int(dt.timestamp() * 1000)


def _parse_start_ts(start_str: str) -> int:
    """Convert a human-readable start string to milliseconds timestamp."""
    mapping = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
        "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
    }
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Handle "N year(s) ago UTC" pattern
    lower = start_str.lower().strip()
    for unit, label in [("year", "year"), ("years", "year")]:
        if f"{unit} ago" in lower:
            parts = lower.split()
            n = int(parts[0])
            return _years_ago_ms(n)

    # Handle "N day(s) ago" pattern
    for unit in ["day", "days"]:
        if f"{unit} ago" in lower:
            parts = lower.split()
            n = int(parts[0])
            return now_ms - n * 86400 * 1000

    # Try ISO format
    try:
        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        pass

    # Try direct millisecond timestamp
    try:
        return int(start_str)
    except (ValueError, TypeError):
        pass

    # Fallback: 2 years ago
    return _years_ago_ms(2)


def _build_session() -> requests.Session:
    """Create a requests session with proxy configured."""
    session = requests.Session()
    if _DEFAULT_PROXY:
        session.proxies = {
            "http": _DEFAULT_PROXY,
            "https": _DEFAULT_PROXY,
        }
    return session


def _direct_session() -> requests.Session:
    """直连 session：显式忽略环境代理。

    trust_env=False 阻断 requests 默认读取 HTTP(S)_PROXY/NO_PROXY
    环境变量（先例 mcpserver/api_client.py）；_build_session 的显式
    proxies 回退通道不受 trust_env 影响，两通道语义独立。
    """
    session = requests.Session()
    session.trust_env = False
    return session


# thread-local session 双通道：requests.Session 非线程安全（并发面为
# _download_archive_csv 的 8 workers 线程池），且直连/代理两通道配置
# 互斥（trust_env=False vs 显式 proxies）——每线程每通道各持一个
# session 复用连接池，跨线程不共享
_thread_local = threading.local()


def _session_for(kind: str) -> requests.Session:
    """取当前线程在指定通道（"direct"/"proxy"）复用的 Session。

    首次取用时经 _direct_session/_build_session 创建并缓存于线程
    local；同线程同通道后续复用同一实例（连接池热复用），线程结束
    随 local 回收。
    """
    sessions: dict = getattr(_thread_local, "sessions", None)
    if sessions is None:
        sessions = {}
        _thread_local.sessions = sessions
    session = sessions.get(kind)
    if session is None:
        session = _direct_session() if kind == "direct" else _build_session()
        sessions[kind] = session
    return session


def _http_get(url: str, params: dict | None = None, timeout: int = 30) -> requests.Response:
    """直连优先、代理回退的 GET。

    现实依据：vision/S3 系域名在受限地区可直连（这正是 vision 域名的设计目的）；
    代理仅在直连失败且 BINANCE_PROXY 有配置时作为回退通道。
    """
    try:
        return _session_for("direct").get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        if not _DEFAULT_PROXY:
            raise
        logger.debug("直连失败（%s），回退代理", exc)
        return _session_for("proxy").get(url, params=params, timeout=timeout)


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    start_str: str = "2 year ago UTC",
    end_str: str | None = None,
    api_key: str = "",
    api_secret: str = "",
) -> pd.DataFrame:
    """Fetch historical K-line data from Binance public data API.

    Args:
        symbol: Trading pair (e.g. "BTCUSDT").
        interval: K-line interval ("1m", "5m", "15m", "1h", "4h", "1d").
        start_str: Start time string.
        end_str: End time string (None = now).
        api_key: Unused, kept for API compatibility.
        api_secret: Unused, kept for API compatibility.

    Returns:
        DataFrame with columns: open, high, low, close, volume, trades.
        DatetimeIndex in UTC.
    """
    start_ms = _parse_start_ts(start_str)
    end_ms = (
        int(datetime.now(timezone.utc).timestamp() * 1000)
        if end_str is None
        else _parse_start_ts(end_str)
    )

    all_klines: list[list] = []

    # Paginate through the data
    current_start = start_ms
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": _PAGE_LIMIT,
        }
        resp = _http_get(f"{_BASE_URL}/api/v3/klines", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        all_klines.extend(data)

        # Move start to after the last candle
        last_open_time = data[-1][0]
        if last_open_time <= current_start:
            break
        current_start = last_open_time + 1

        # If we got fewer than limit, we've reached the end
        if len(data) < _PAGE_LIMIT:
            break

    if not all_klines:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trades"])

    df = pd.DataFrame(all_klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])

    # Keep only needed columns
    df = df[["timestamp", "open", "high", "low", "close", "volume", "trades"]]

    # Convert types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")

    # Remove duplicates
    df = df[~df.index.duplicated(keep="first")]

    return df


def _download_archive_csv(archive_dir: str, symbol: str, stem: str) -> pd.DataFrame | None:
    """从 data.binance.vision S3 归档下载单个 zip 并解析为 DataFrame。

    404（归档尚未生成/超出范围）或网络失败返回 None，由调用方跳过。
    """
    url = f"{_S3_ARCHIVE_URL}/{archive_dir}/{symbol}/{stem}.zip"
    try:
        resp = _http_get(url, timeout=30)
    except requests.RequestException as exc:
        logger.warning("归档下载失败 %s: %s", stem, exc)
        return None
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                return pd.read_csv(f)
    except (zipfile.BadZipFile, IndexError, pd.errors.ParserError) as exc:
        logger.warning("归档解析失败 %s: %s", stem, exc)
        return None


def _month_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """列出 [start, end] 覆盖的全部月份首日。"""
    months = []
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        months.append(cur)
        cur = (cur + pd.offsets.MonthBegin(1)).to_pydatetime()
        cur = pd.Timestamp(cur)
    return months


def _day_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """列出 [start, end] 覆盖的全部自然日零点。"""
    days = pd.date_range(start.normalize(), end.normalize(), freq="D", tz="UTC")
    return list(days)


def fetch_funding_history(
    symbol: str = "BTCUSDT",
    start: str = "2024-01-01",
    end: str | None = None,
) -> pd.DataFrame:
    """拉取资金费率历史（月度归档拼接）。

    数据源：data.binance.vision 月度 fundingRate 归档（2020-01 起，直连可达）。
    原计划经 fapi.binance.com/fapi/v1/fundingRate 拉取，实测该域名受地域
    限制且代理通道不可用，故改走 S3 归档（同数据域、历史更长）。

    Args:
        symbol: 合约交易对（如 "BTCUSDT"）。
        start: 起始日期（含），ISO 格式。
        end: 结束日期（不含），None = 当前时间。

    Returns:
        DataFrame，列 funding_rate，DatetimeIndex（UTC，8h 结算点）。
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")

    months = _month_starts(start_ts, end_ts)
    frames = []
    for m in months:
        stem = f"{symbol}-fundingRate-{m:%Y-%m}"
        df = _download_archive_csv("monthly/fundingRate", symbol, stem)
        if df is None or df.empty:
            logger.debug("跳过无数据月份 %s", stem)
            continue
        idx = pd.to_datetime(df["calc_time"], unit="ms", utc=True)
        frame = pd.DataFrame(
            {"funding_rate": df["last_funding_rate"].astype(float).to_numpy()},
            index=pd.DatetimeIndex(idx, name="timestamp"),
        )
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["funding_rate"])

    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out[(out.index >= start_ts) & (out.index < end_ts)]
    return out


def fetch_open_interest_history(
    symbol: str = "BTCUSDT",
    start: str = "2024-01-01",
    end: str | None = None,
) -> pd.DataFrame:
    """拉取持仓量（OI）历史（日度 metrics 归档拼接，5m 原生粒度）。

    数据源：data.binance.vision 日度 metrics 归档（sum_open_interest 列，
    2020-09 起；归档约有 T-5 延迟）。原计划经 fapi openInterestHist 端点
    拉取，该端点仅保留 30 天且地域受限，故改走 S3 归档。

    Args:
        symbol: 合约交易对（如 "BTCUSDT"）。
        start: 起始日期（含），ISO 格式。
        end: 结束日期（不含），None = 当前时间。

    Returns:
        DataFrame，列 open_interest，DatetimeIndex（UTC，5m 粒度）。
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")

    days = _day_starts(start_ts, end_ts)

    def _fetch_one(day: pd.Timestamp) -> pd.DataFrame | None:
        stem = f"{symbol}-metrics-{day:%Y-%m-%d}"
        df = _download_archive_csv("daily/metrics", symbol, stem)
        if df is None or df.empty:
            return None
        idx = pd.to_datetime(df["create_time"], utc=True, format="mixed")
        return pd.DataFrame(
            {"open_interest": df["sum_open_interest"].astype(float).to_numpy()},
            index=pd.DatetimeIndex(idx, name="timestamp"),
        )

    with ThreadPoolExecutor(max_workers=_ARCHIVE_WORKERS) as pool:
        results = list(pool.map(_fetch_one, days))

    frames = [f for f in results if f is not None and not f.empty]
    done = len(frames)
    logger.info("OI 归档下载完成：%d/%d 天有数据（%s）", done, len(days), symbol)

    if not frames:
        return pd.DataFrame(columns=["open_interest"])

    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out[(out.index >= start_ts) & (out.index < end_ts)]
    return out
