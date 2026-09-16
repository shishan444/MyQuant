"""统一 UTC ISO 时间戳（B4.1 双源消除：_now/_now_iso ×5 归一）。

db_ext legacy 链的 _now 随迁移工具链保留；其余（repository 层、双
runner、路由）一律引用本函数。
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """UTC 当前时间的 ISO 8601 字符串（与历史各 _now 实现逐字等价）。"""
    return datetime.now(timezone.utc).isoformat()
