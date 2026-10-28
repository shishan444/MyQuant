"""fapi USDT-M 永续行情接入层（binance SDK 全工程唯一触点）。

包结构：stream.py（WS 组合流+协议接口）/ rest.py（REST 兜底）/
injection.py（测试替身——仅测试依赖）。依赖方向硬约束：binance
SDK 子树（binance.um_futures / binance.websocket）全工程 import 点
仅 stream.py 与 rest.py；数据作业与执行器永不 import SDK。
"""
from core.data.fapi.stream import (
    FAPI_SYMBOLS,
    FAPI_TIMEFRAMES,
    FIXED_SUBSCRIBE_KEYS,
    FIXED_SUBSCRIBE_STREAMS,
    BinanceFapiStream,
    ClosedBarEvent,
    ConnEvent,
    FapiStreamProtocol,
    RECONNECT_BACKOFF_CAP_SECONDS,
    RECONNECT_BACKOFF_INITIAL_SECONDS,
    SILENT_WINDOW_BASE_SECONDS,
    is_valid_bar_values,
    silent_window_seconds,
    subscribe_stream_name,
)

__all__ = [
    "FAPI_SYMBOLS",
    "FAPI_TIMEFRAMES",
    "FIXED_SUBSCRIBE_KEYS",
    "FIXED_SUBSCRIBE_STREAMS",
    "BinanceFapiStream",
    "ClosedBarEvent",
    "ConnEvent",
    "FapiStreamProtocol",
    "RECONNECT_BACKOFF_CAP_SECONDS",
    "RECONNECT_BACKOFF_INITIAL_SECONDS",
    "SILENT_WINDOW_BASE_SECONDS",
    "is_valid_bar_values",
    "silent_window_seconds",
    "subscribe_stream_name",
]
