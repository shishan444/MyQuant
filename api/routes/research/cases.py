"""案例抽查端点（add-inspect-cases 2.1）。

POST 独立通道先例（数据检视器不走 run_analysis 模板族——selector
嵌套结构 body 直收，塞 GET query 需 JSON 编码传输、契约含糊易错）。
ResearchError 统一 research_error_to_http 透传；返回结构即服务层形制
（selector_echo/n_matched/sampling/cases...）。研究档 POST 白名单登记
在 mcpserver/api_client（不登记则任何档位不可达）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends

from api.deps import get_data_dir, get_db_path
from api.routes.research.common import research_error_to_http
from api.schemas import InspectCasesRequest
from core.analysis import case_inspection as ci
from core.analysis.hypothesis_service import ResearchError

router = APIRouter()


@router.post("/inspect-cases")
def inspect_cases(
    req: InspectCasesRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> Dict[str, Any]:
    """案例抽查：候选条件/点名时间戳的真实触发案例窗口（检查③）。

    selector 两形态互斥：condition（{subject, action, target} 三键，
    扫描候选同形制直喂；indicator 必填供 warmup 推导）或 timestamps
    （点名 ≤5，不传区间参数）。limit ∈ 1..5（缺省 3）、context_bars
    ∈ 1..20（缺省 8）；超限结构化 422 拒绝（不静默截断）。
    """
    try:
        return ci.inspect_cases(
            db_path, data_dir,
            symbol=req.symbol, timeframe=req.timeframe,
            selector=req.selector, limit=req.limit,
            context_bars=req.context_bars,
            start=req.start, end=req.end, indicator=req.indicator)
    except ResearchError as exc:
        raise research_error_to_http(exc) from exc
