"""统一时间周期秒表（B4.1 双源消除：_BAR_SECONDS 与 _TF_DURATION_MINUTES 归一）。

并集（两旧表的键全保留）；trading 旧表缺的周期仅影响 `.get` 兜底路径
（默认 86400 不变），行为等价。
"""
from __future__ import annotations

TF_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}
