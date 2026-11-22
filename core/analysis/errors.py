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
import math
from typing import Any


class ResearchError(Exception):
    """研究域错误：code + status + message + detail。

    未捕获冒泡至应用层全局 handler（api/app.py），经
    research_error_to_http 单源转 HTTPException 出站。
    """

    def __init__(self, code: str, message: str, *, status: int = 422, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def __reduce__(self):
        """跨进程可序列化（fix-replay-gap-alignment 2.2）。

        status/detail 为 keyword-only 参——3-元组 state 形制：先按
        位置参重建实例（__init__ 重跑，code/message 恢复、args=
        (message,) 不变），再经 pickle 默认 state 恢复（obj.__dict__
        .update）覆写 status/detail。进程内 raise/except/str 零变化
        （__reduce__ 仅影响 pickle 路径——回放进程池 worker 回传）。"""
        return (ResearchError, (self.code, self.message),
                {"status": self.status, "detail": self.detail})


def load_stored_json(
    raw: str, *, source: str,
    detail: dict[str, Any] | None = None,
    code: str = "RESEARCH_STORED_DATA_INVALID",
) -> Any:
    """库内持久化 JSON 解析（损坏即结构化拒绝，research-error-channel）。

    库内数据（验证记录 result / 假设 conditions 等）由本工程写入侧
    序列化，正常链路恒可解析；解析失败=持久化数据损坏（外部改写/
    截断/版本迁移遗留），按 422 结构化拒绝并点名来源与记录标识，
    禁止裸 JSONDecodeError 穿出炸 500。

    异常类型不分域（复用 ResearchError 四元组）；错误码按数据所有者
    域分派：研究域缺省 RESEARCH_STORED_DATA_INVALID；纸盘域消费方
    （pt_signal/pt_report/pt_task 等点位）经 code 形参传
    PAPER_STORED_DATA_INVALID。

    Args:
        raw: 待解析的库内 JSON 字符串（调用方自行守卫空值语义——
            空串/None 的合法缺省态不经过本函数）。
        source: 数据来源标识（表.字段形制，如 "validation_records.result"）。
        detail: 附加上下文（记录 id 等），与 parse_error 合并进 detail。
        code: 错误码（缺省研究域码；纸盘域传 PAPER_STORED_DATA_INVALID）。

    Returns:
        json.loads 解析结果（dict/list/标量随存储形制）。

    Raises:
        ResearchError: code 形参指定的错误码（缺省
            RESEARCH_STORED_DATA_INVALID，422）。
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResearchError(
            code,
            f"库内持久化数据损坏：{source} 的 JSON 无法解析",
            detail={**(detail or {}), "parse_error": str(exc)},
        ) from exc


def require_finite_values(
    obj: Any, *, source: str,
    detail: dict[str, Any] | None = None,
    code: str = "RESEARCH_RESULT_NON_FINITE",
) -> None:
    """落库前数值有限性守卫：递归校验待序列化对象内全部 float 有限。

    与 load_stored_json 成对（读侧解析守卫/写侧序列化守卫）：标准
    json.dumps 默认 allow_nan=True——inf/nan 输出 Infinity/NaN 落库
    即非法 JSON 字面量（Python 读端宽容解析回 inf，前端标准
    JSON.parse 抛 SyntaxError，一行坏数据炸整页记录列表——
    fix-replay-list-channel CARD-03/D-2）。引擎浮点溢出在此结构化
    拒绝不落坏行（整体报错，指标不照常返回）。

    Args:
        obj: 待 json.dumps 的对象（dict/list/float/其他标量递归遍历；
            float 之外类型直通——int 任意大、str/None/bool 无非有限
            形态）。
        source: 落库目标标识（表.字段形制，与 load_stored_json 同款）。
        detail: 附加上下文（record_id 等），与首坏值路径合并进 detail。
        code: 错误码（缺省研究域结果码）。

    Raises:
        ResearchError: 422——detail 点名首个非有限值路径与字符串值形。
    """
    stack = [("", obj)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, float):
            if not math.isfinite(node):
                raise ResearchError(
                    code,
                    f"回测结果含非有限数值，拒绝落库：{source} 的 "
                    f"{path or '<root>'} = {node!r}",
                    detail={**(detail or {}), "field": path or "<root>",
                            "value": str(node)})
        elif isinstance(node, dict):
            stack.extend((f"{path}.{k}" if path else str(k), v)
                         for k, v in node.items())
        elif isinstance(node, (list, tuple)):
            stack.extend((f"{path}[{i}]" if path else f"[{i}]", v)
                         for i, v in enumerate(node))
