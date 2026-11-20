"""research 路由公共工具层（零兄弟依赖）。

消费面按符号：research_error_to_http——api/app.py 全局 exception
handler（唯一子包外消费方）、plugins.py 审计保留点（rejected 留档
后转换）、hypothesis_cards.py 本地转换（status 值域校验）；
require_known_timeframe——analysis/cases/hypotheses/indicator_query/
validation；encode/decode_cursor——hypotheses/hypothesis_cards/log。
价值：ResearchError -> HTTPException 转换单源维护；路由层纯转换
捕获已删，未捕获异常冒泡至应用层全局兜底复用同一转换出站。
职责边界：只收无路由状态的纯工具；不 import 任何兄弟模块——
本模块是子包内共享底座，向上依赖会形成循环。
"""
from __future__ import annotations

import math

from typing import Optional, Tuple

from fastapi import HTTPException

from core.analysis.errors import ResearchError


def research_error_to_http(exc: ResearchError) -> HTTPException:
    """ResearchError -> HTTPException（detail 含 code/message/detail 三段）。"""
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


def require_known_timeframe(timeframe: str, field: str) -> None:
    """timeframe 值域前置校验：非法即 ResearchError TIMEFRAME_UNKNOWN（422）。

    输入语义：timeframe 为待校验周期串；field 为请求字段路径（错误
    detail 披露定位用）。边界条件：合法=TF_SECONDS 键集成员（与读取
    路径 validate_known_interval 同口径）。错误语义：非法即拒，
    legal_range 附合法清单，形制照 hypothesis_service 既有先例——
    若不前置，装载层 validate_known_interval 抛裸 ValueError，路由层
    只捕 ResearchError，非法周期会裸穿 500。
    """
    from core.data.timeframes import TF_SECONDS

    if timeframe not in TF_SECONDS:
        raise ResearchError(
            "TIMEFRAME_UNKNOWN",
            f"未知周期: {timeframe!r}",
            detail={"field": field, "timeframe": timeframe,
                    "legal_range": sorted(TF_SECONDS)})


def require_task_cap_params(
    leverage: Optional[float] = None,
    margin_cap: Optional[float] = None,
) -> None:
    """任务级封顶两参值域校验（sandbox-and-report-supplement D1）。

    值域=需求硬约束进参数域封顶：leverage 整数 [1,3]（右闭两端皆
    合法）、margin_cap 实数 (0,0.60]（左开右闭——恰 0.60 合法、0
    与负值拒绝）；非整数 leverage 拒绝（引擎杠杆语义=整数倍）。未传
    （None）直通——③层不生效（合成序退化为既有两层）。schema 层
    取宽松 float 收窄在此单点（结构化 422 附 legal_range，非 pydantic
    默认错误形制——与 require_known_timeframe 同款先例）。
    """
    if leverage is not None:
        if (not float(leverage).is_integer()
                or not 1 <= float(leverage) <= 3):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"leverage 非法（整数 [1,3] 封顶——需求 3x 硬约束）: "
                f"{leverage!r}",
                detail={"field": "leverage", "value": leverage,
                        "legal_range": "[1,3]", "legal_form": "int"})
    if margin_cap is not None:
        if not 0.0 < float(margin_cap) <= 0.60:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"margin_cap 非法（(0,0.60] 封顶——需求 60% 硬约束）: "
                f"{margin_cap!r}",
                detail={"field": "margin_cap", "value": margin_cap,
                        "legal_range": "(0,0.60]"})


def require_halt_drawdown_pct(halt_drawdown_pct: Optional[float]) -> None:
    """熔断阈值值域校验（sandbox-and-report-supplement D3）。

    值域=(0,1) 开区间比率（0/1/负值/越界拒绝——0 无意义、1 恒不触发
    同为非法配置面）；未传（None）直通=不启用（三态之「未配置」）。
    """
    if halt_drawdown_pct is not None and not 0.0 < float(
            halt_drawdown_pct) < 1.0:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"halt_drawdown_pct 非法（(0,1) 开区间回撤比率）: "
            f"{halt_drawdown_pct!r}",
            detail={"field": "halt_drawdown_pct",
                    # value 非有限时 str 化（沿 require_finite_values
                    # 先例）：Starlette JSONResponse allow_nan=False，
                    # detail 携原始 nan/inf 序列化炸——结构化 422 变
                    # 空体 500 裸穿（C5 审查 CARD-06 验证目标 2 补口）
                    "value": (halt_drawdown_pct
                              if halt_drawdown_pct is None
                              or math.isfinite(halt_drawdown_pct)
                              else str(halt_drawdown_pct)),
                    "legal_range": "(0,1)"})


def encode_cursor(created_at: str, pk: str) -> str:
    """keyset 游标编码：(created_at, 主键) -> "created_at|pk"。

    两字段（ISO 时间串 / 研究域 hex id）均不含 "|"，拼接无歧义、
    客户端可原样回传 decode_cursor 续页。
    """
    return f"{created_at}|{pk}"


def decode_cursor(raw: Optional[str]) -> Optional[Tuple[str, str]]:
    """keyset 游标解码：None 直通；非 "created_at|pk" 双段形即 422。

    错误语义：RESEARCH_PARAM_INVALID 结构化拒绝（detail 披露期望
    形制）——坏游标静默回首页会伪装成「没有更多数据」，宁可显式拒。
    """
    if raw is None:
        return None
    parts = raw.split("|", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(status_code=422, detail={
            "code": "RESEARCH_PARAM_INVALID",
            "message": f"cursor 形制非法（期望 created_at|id 双段）: {raw!r}",
            "detail": {"field": "cursor",
                       "legal_form": "created_at|id"}})
    return parts[0], parts[1]
