"""Strategies 路由私有公共位（B5.1 拆分后 verify/batch/compare 共用）。

signal-sources-structure-gate（D-6）起兼载六入口（backtest 读库/内联、
batch、verify、compare、live 任务创建）共用的 DNA 拒绝表达
（_reject_invalid_or_plugin_dna）。判据单源在 core/strategy/validator
（validate_dna 结构/值域+has_plugin_sources 能力边界），本模块只承载
HTTP 拒绝表达（状态码/错误码/消息模板）；live 为跨目录消费方
（api/routes/live_trading.py 函数级 import——无环：本模块不 import
任何路由模块）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# 插件源能力边界消息的通道名映射（R-8 逐字节基准——六入口现状消息
# 的机械转写；channel 集与 _reject_invalid_or_plugin_dna 的校验集同源）
_PLUGIN_REJECT_MESSAGES = {
    "backtest": (
        "策略含插件信号源，REST 回测通道不支持"
        "（引擎该通道未接插件信号集，结果将为零"
        "交易）——请经研究验证链沙盒回测"),
    "verify": (
        "策略含插件信号源，REST 回测通道不支持"
        "（引擎该通道未接插件信号集，结果将为"
        "零交易）——请经研究验证链沙盒回测"),
    "batch": (
        "策略含插件信号源，REST 批量回测通道"
        "不支持（引擎该通道未接插件信号集，"
        "结果将为零交易）——请经研究验证链"
        "沙盒回测"),
    "compare": (
        "策略含插件信号源，REST 对比回测通道不支持"
        "（引擎该通道未接插件信号集，结果将为零"
        "交易）——请经研究验证链沙盒回测"),
    "live": (
        "执行环境不支持插件信号源（live 链未接入插件"
        "信号组装）——请经研究验证链沙盒回测"),
    # Q-3 评估入口族三道拒绝之一（dna-strong-typing-and-disclosure-
    # hygiene 件⑤）：进化任务创建入口——与 REST 回测通道同族能力边界
    "evolution": (
        "执行环境不支持插件信号源（进化评估链未接入插件"
        "信号组装，评估将失真）——请经研究验证链沙盒回测"),
}

# 读库态/直传态的结构拒绝消息（library_dna 两态——六处现状的机械转写）
_DNA_INVALID_MESSAGES = {
    True: "Library DNA invalid（库内 DNA 结构/值域非法）",
    False: "DNA 值域/结构校验未通过",
}


def _reject_invalid_or_plugin_dna(
    dna,
    *,
    library_dna: bool,
    channel: str,
) -> None:
    """六入口共用的 DNA 拒绝表达（两段判定顺序内化）。

    判据单源不动（R-7）：validate_dna（结构/值域）与
    has_plugin_sources（执行环境能力边界）的签名、语义、所在文件均
    不变，本 helper 只做调用与 HTTP 表达。命中即 raise，两段全过
    return None。

    Args:
        dna: 已解析的 StrategyDNA（解析/404 责任在调用方）。
        library_dna: True=读库装载（消息「Library DNA invalid…」）/
            False=请求直传（消息「DNA 值域/结构校验未通过」）。
        channel: 消费通道名（backtest/batch/verify/compare/live）
            ——插件源拒绝消息的通道名映射键。

    Raises:
        HTTPException: 422 DNA_VALIDATION_FAILED（detail 含 errors
            判据明细）或 422 PLUGIN_SOURCES_UNSUPPORTED（detail 无
            errors 键——两码键集不对称是既有契约基准的一部分）。
        ValueError: channel 不在五值集（编程错误——沿
            validate_trading_compatibility 的 link 校验先例）。
    """
    if channel not in _PLUGIN_REJECT_MESSAGES:
        raise ValueError(
            f"channel 须为 backtest/batch/verify/compare/live，got {channel!r}")

    # 第一段：结构/值域校验（先于能力边界——「策略坏」优先报出）
    from core.strategy.validator import validate_dna
    _vr = validate_dna(dna)
    if not _vr.is_valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "DNA_VALIDATION_FAILED",
                    "message": _DNA_INVALID_MESSAGES[library_dna],
                    "errors": _vr.errors})

    # 第二段：插件源能力边界（结构合法但执行环境不支持）
    from core.strategy.validator import has_plugin_sources
    if has_plugin_sources(dna):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PLUGIN_SOURCES_UNSUPPORTED",
                "message": _PLUGIN_REJECT_MESSAGES[channel]})


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


def _progress_payload(
    steps: List, step_index: int, group_key: str,
    dr, batch_results: list,
) -> Dict[str, Any]:
    """流式进度帧构造（verify/batch 两 processor 共用）。"""
    return {
        "current": step_index + 1,
        "total": len(steps),
        "group": group_key,
        "range_start": dr.start,
        "range_end": dr.end,
        "batch_results": batch_results,
    }
