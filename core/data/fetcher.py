"""Binance 衍生品归档数据拉取（funding/OI——S3 月档/日档）。

数据源为 data.binance.vision S3 公共归档——受限地区可达、无需
API key，这是与标准 API 端点的关键差异。消费链：updater 衍生品
增量更新 → 纸盘 feed funding 节流刷新+守护器 derivs 轮；本模块
不落盘（写 Parquet 归 updater/storage）。

K 线拉取管道（fetch_klines/_parse_start_ts/_years_ago_ms/_BASE_URL/
_PAGE_LIMIT）已随数据板块整改退役（cleanup-data-segment R-09——
现货端点零调用死管道，K 线实时面归 fapi 接入层）。
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

from core.data.validation import validate_symbol

logger = logging.getLogger(__name__)

# Binance 访问代理默认值（BINANCE_PROXY 环境变量可覆写；硬编码内网
# 地址现状见技术债务登记 C4——已裁决保留）
_DEFAULT_PROXY = os.environ.get("BINANCE_PROXY", "http://172.20.112.1:10809")

# 衍生品历史（funding / open interest）S3 公共归档，同为 vision 域名族：
# 受限地区可达、无需 key
_S3_ARCHIVE_URL = "https://data.binance.vision/data/futures/um"

# 日度 metrics 归档并发下载数（文件小、桶静态，8 并发安全）
_ARCHIVE_WORKERS = 8

# 归档下载体积上限：压缩包 50MB / 解压后 500MB（实际归档为 KB~百 KB 级，
# 余量三个数量级——纯防解压炸弹，不约束正常文件）
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 500 * 1024 * 1024




def _build_session() -> requests.Session:
    """代理回退通道 session：显式配置 proxies（BINANCE_PROXY 有效时）。"""
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




def _download_archive_csv(archive_dir: str, symbol: str, stem: str) -> pd.DataFrame | None:
    """从 data.binance.vision S3 归档下载单个 zip 并解析为 DataFrame。

    404（归档尚未生成/超出范围）、网络失败或体积超限返回 None，由调用方跳过。
    symbol 直拼出站 URL，入口经命名白名单拒绝（与写入路径同契约点，
    防 URL 路径段注入）。
    """
    validate_symbol(symbol)
    url = f"{_S3_ARCHIVE_URL}/{archive_dir}/{symbol}/{stem}.zip"
    try:
        resp = _http_get(url, timeout=30)
    except requests.RequestException as exc:
        logger.warning("归档下载失败 %s: %s", stem, exc)
        return None
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    # 体积关：Content-Length 声明超限即拒（不解压——requests 非流式
    # 已下载完整 body，声明关不省流量）；实际大小兜底（无声明/声明
    # 失实）——日度/月度归档实为 KB~百 KB 级，上限余量三个数量级，
    # 纯防解压炸弹不伤正常文件
    declared = resp.headers.get("Content-Length")
    if declared is not None and int(declared) > _MAX_ARCHIVE_BYTES:
        logger.warning("归档体积超限 %s: 声明 %s 字节（上限 %d）",
                       stem, declared, _MAX_ARCHIVE_BYTES)
        return None
    if len(resp.content) > _MAX_ARCHIVE_BYTES:
        logger.warning("归档体积超限 %s: 实际 %d 字节（上限 %d）",
                       stem, len(resp.content), _MAX_ARCHIVE_BYTES)
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            info = zf.getinfo(csv_name)
            if info.file_size > _MAX_EXTRACTED_BYTES:
                logger.warning("归档解压体积超限 %s: %d 字节（上限 %d）",
                               stem, info.file_size, _MAX_EXTRACTED_BYTES)
                return None
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
        cur = cur + pd.offsets.MonthBegin(1)
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
