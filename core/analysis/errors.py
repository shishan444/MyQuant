"""研究域错误类型（轻量定义点——event-triggers 批次三 CARD-02）。

ResearchError 原定义在 hypothesis_service（随其顶层 import 链拖入
sqlalchemy/persistence 全链——import event_policy 等轻消费方被迫
负重加载）。本模块为其唯一权威定义：零第三方依赖、零域内反向
依赖，任何模块可安全模块级 import。

hypothesis_service re-export 同对象（既有全域 import 面零适配）；
新消费方（event_policy / param_guard / signal_assembly 等）直取
本模块断重链。
"""
from __future__ import annotations

import json
from typing import Any


class ResearchError(Exception):
    """研究域错误：code + http_status + message + detail（路由层转 HTTPException）。"""

    def __init__(self, code: str, message: str, *, status: int = 422, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


def load_stored_json(
    raw: str, *, source: str, detail: dict[str, Any] | None = None,
) -> Any:
    """库内持久化 JSON 解析（损坏即结构化拒绝，research-error-channel）。

    库内数据（验证记录 result / 假设 conditions 等）由本工程写入侧
    序列化，正常链路恒可解析；解析失败=持久化数据损坏（外部改写/
    截断/版本迁移遗留），按 422 结构化拒绝并点名来源与记录标识
    （RESEARCH_STORED_DATA_INVALID 单码），禁止裸 JSONDecodeError
    穿出炸 500。

    Args:
        raw: 待解析的库内 JSON 字符串（调用方自行守卫空值语义——
            空串/None 的合法缺省态不经过本函数）。
        source: 数据来源标识（表.字段形制，如 "validation_records.result"）。
        detail: 附加上下文（记录 id 等），与 parse_error 合并进 detail。

    Returns:
        json.loads 解析结果（dict/list/标量随存储形制）。

    Raises:
        ResearchError: RESEARCH_STORED_DATA_INVALID（422）。
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResearchError(
            "RESEARCH_STORED_DATA_INVALID",
            f"库内持久化数据损坏：{source} 的 JSON 无法解析",
            detail={**(detail or {}), "parse_error": str(exc)},
        ) from exc
