"""指标读数端点（add-query-indicator 2.1；0.4.1 R1 批量形制）。

POST 独立通道（数据检视器格局——params 嵌套 dict 走 GET 需 JSON 编码
传输，契约含糊，inspect_cases 创建时已否决同型方案）。零 DB 写入零
研究日志留痕（Q2 裁决——读数与元数据查询同级）。ResearchError 统一
research_error_to_http 透传；返回结构即服务层形制（indicator/params
生效值/range/warmup_bars/reductions；批量形制 {"groups":[…]}——数组
序=请求序）。研究档 POST 白名单登记在 mcpserver/api_client（不登记
则任何档位不可达）。

0.4.1 R1：可选 groups 与单组专属字段（indicator/params/reductions/
start/end）互斥——同传 422 指明互斥对；组数 1..8；整批原子含执行期
（任一组失败整批拒绝无部分结果，detail 以 groups[N] 指明组序）；载荷
分档单组 2048 / 批量 16384。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_data_dir
from api.routes.research.common import (
    require_known_timeframe,
    research_error_to_http,
)
from api.schemas import QueryIndicatorRequest
from core.analysis import indicator_query as iq
from core.analysis.hypothesis_service import ResearchError

router = APIRouter()

# 单组专属字段（与 groups 互斥；symbol/timeframe 公共必填保留）
_SINGLE_ONLY_KEYS = ("indicator", "params", "reductions", "start", "end")


@router.post("/query-indicator")
def query_indicator(
    req: QueryIndicatorRequest,
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """单指标/批量指标现算读数（检查④）：归约摘要直返（分档 2048/16384）。

    reductions 封闭枚举多选（latest/quantiles/regime_means/
    trend_slope，缺省前两者）；params 缺省拼注册表 default（echo 披露
    实际生效值）；装载双路径复用 0.2.8 契约（显式 start 走 warmup
    前扩，缺省全量直装 + effective_start 披露）。groups 形制：组成员
    与单组字段同形（各组区间独立）、组数 1..8、整批原子。
    """
    try:
        # P0 错误通道：非法周期前置 422——否则装载层裸 ValueError
        # 穿透为 500（except 只捕 ResearchError）
        require_known_timeframe(req.timeframe, "timeframe")
        if req.groups is not None:
            # 互斥：groups 存在时单组专属字段必须全缺省（同传歧义不猜）
            present = [k for k in _SINGLE_ONLY_KEYS
                       if getattr(req, k) is not None]
            if present:
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID",
                    f"groups 与单组专属字段互斥（同传: {sorted(present)}）"
                    "——批量形制下指标/参数/区间在各组内声明",
                    detail={"param": "groups",
                            "conflicting": sorted(present),
                            "legal_form": "groups ∥ indicator+params+"
                                          "reductions+start+end（任选一）"})
            return iq.query_indicator_groups(
                data_dir,
                symbol=req.symbol, timeframe=req.timeframe, groups=req.groups)
        if req.indicator is None:
            # indicator 转可选（0.4.1）后单组形制的缺键拒绝（服务层 422
            # ——形制与键组分派后的参数面校验同族）
            raise ResearchError(
                "RESEARCH_PARAM_INVALID", "params 缺必填键: indicator",
                detail={"param": "indicator",
                        "legal_form": "单组形制必填 indicator（或改用 "
                                      "groups 批量形制）"})
        return iq.query_indicator(
            data_dir,
            symbol=req.symbol, timeframe=req.timeframe,
            indicator=req.indicator, params=req.params,
            start=req.start, end=req.end, reductions=req.reductions)
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc
