"""指标读数端点（add-query-indicator 2.1）。

POST 独立通道（数据检视器格局——params 嵌套 dict 走 GET 需 JSON 编码
传输，契约含糊，inspect_cases 创建时已否决同型方案）。零 DB 写入零
研究日志留痕（Q2 裁决——读数与元数据查询同级）。ResearchError 统一
research_error_to_http 透传；返回结构即服务层形制（indicator/params
生效值/range/warmup_bars/reductions）。研究档 POST 白名单登记在
mcpserver/api_client（不登记则任何档位不可达）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_data_dir
from api.routes.research.common import research_error_to_http
from api.schemas import QueryIndicatorRequest
from core.analysis import indicator_query as iq

router = APIRouter()


@router.post("/query-indicator")
def query_indicator(
    req: QueryIndicatorRequest,
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """单指标任意参数现算读数（检查④）：归约摘要直返（≤2048 字节）。

    reductions 封闭枚举多选（latest/quantiles/regime_means/
    trend_slope，缺省前两者）；params 缺省拼注册表 default（echo 披露
    实际生效值）；装载双路径复用 0.2.8 契约（显式 start 走 warmup
    前扩，缺省全量直装 + effective_start 披露）。
    """
    try:
        return iq.query_indicator(
            data_dir,
            symbol=req.symbol, timeframe=req.timeframe,
            indicator=req.indicator, params=req.params,
            start=req.start, end=req.end, reductions=req.reductions)
    except Exception as exc:
        raise research_error_to_http(exc) from exc
