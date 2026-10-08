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

from typing import Any


class ResearchError(Exception):
    """研究域错误：code + http_status + message + detail（路由层转 HTTPException）。"""

    def __init__(self, code: str, message: str, *, status: int = 422, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail
