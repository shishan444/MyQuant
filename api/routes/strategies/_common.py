"""Strategies 路由私有公共位（B5.1 拆分后 verify/batch/compare 共用）。"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe_error_message(exc: BaseException) -> str:
    """异常消息对外脱敏（结果条目 error 字段/流广播 message/mark_failed 共用）。

    已知类型映射友好消息并剥离文件系统绝对路径；未知异常回 generic
    文案，原文仅经 logger 保留（观测边界内可溯源）。
    """
    if isinstance(exc, FileNotFoundError):
        name = Path(exc.filename).name if exc.filename else ""
        return f"数据文件缺失: {name}" if name else "数据文件缺失"
    if isinstance(exc, PermissionError):
        return "数据文件无权限访问"
    logger.warning("unhandled error in strategies route: %r", exc, exc_info=exc)
    return "内部错误"
