"""验证明细读取端点（add-validation-detail B2）。

明细整份返回不分页（单记录明细为终端消费——K 线复核区组装，无
部分取用例）。两态判定序沿 validation_report 先例（2026-10-14 Q1
裁决形制）：

- ① record_id 在 validation_records 无行 → 404 RESEARCH_RECORD_
  NOT_FOUND（资源不存在）；
- ② 记录存在而明细行不存在（历史 289 条或 B4 生效前记录）→ 200
  空明细骨架（detail_available=false + executions=[] + 执行参数
  三键 null，其余元数据正常返回）——明细是记录的附属数据，附属
  缺失=空态语义；前端以 detail_available 判别「产生于明细落盘
  之前，不可复核交易位置」（可复核性以明细行存在性判定，非时间
  点推断；不回填不重跑——重跑所得非原始执行）。

纯 GET 幂等：明细行 append-only，重复调用同响应。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db_path
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/validation-records/{record_id}/detail")
def validation_record_detail(
    record_id: str,
    db_path: Path = Depends(get_db_path),
) -> Dict[str, Any]:
    """验证明细整份读取：两态判定序（404 资源不存在 / 200 空骨架）。"""
    row = rr.get_validation_record_by_id(db_path, record_id)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "code": "RESEARCH_RECORD_NOT_FOUND",
            "message": f"验证记录不存在: {record_id}",
            "detail": None})

    params: Dict[str, Any] = (
        json.loads(row["params"]) if row["params"] else {})
    # 执行参数三键容缺：历史记录（params 无三键）null 如实缺失——
    # 前端标注未知而非显示默认值
    base = {
        "record_id": record_id,
        "hypothesis_id": row["hypothesis_id"],
        "strategy_id": row["strategy_id"],
        "level": row["level"],
        "verdict": row["verdict"],
        "created_at": row["created_at"],
        "init_cash": params.get("init_cash"),
        "fee": params.get("fee"),
        "slippage": params.get("slippage"),
    }

    executions = rr.get_validation_executions(db_path, record_id)
    if not executions:
        return {**base, "detail_available": False, "executions": []}
    return {**base, "detail_available": True, "executions": executions}
