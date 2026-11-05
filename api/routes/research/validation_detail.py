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

import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db_path
from core.analysis.errors import ResearchError, load_stored_json
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

    # TD-02 点位 3/4：params/result 走结构化通道；损坏 422 点名
    # 表.字段与行级定位（不裸穿 500）——端点出口统一转 HTTPException
    # （get_validation_executions 内点位 1/2 的 ResearchError 同链捕获）
    try:
        params: Dict[str, Any] = (
            load_stored_json(row["params"],
                             source="validation_records.params",
                             detail={"record_id": record_id})
            if row["params"] else {})
        result_json: Dict[str, Any] = (
            load_stored_json(row["result"],
                             source="validation_records.result",
                             detail={"record_id": record_id})
            if row["result"] else {})
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
            # 信号留痕三布尔（add-vr-signal-trace 任务 5.2——0/1/NULL→
            # true/false/null 直译；NULL=产生于能力上线前合法降级态，代际
            # 陈述由前端承载。detail_available 语义不动〔明细行存在性，
            # 与三布尔不同维度不混写——R-025〕）
            "signal_trace": (
                None if row.get("signal_trace") is None
                else bool(row["signal_trace"])),
            "trade_semantics": (
                None if row.get("trade_semantics") is None
                else bool(row["trade_semantics"])),
            "stop_price": (
                None if row.get("stop_price") is None
                else bool(row["stop_price"])),
            # MTF 门控拦截计数（记录级顶层键提取——null 透传：无门控面
            # 与 0=门控全过严格区分）
            "mtf_gated_bars": result_json.get("mtf_gated_bars"),
        }

        executions = rr.get_validation_executions(db_path, record_id)
        if not executions:
            return {**base, "detail_available": False, "executions": []}

        # 份级 trail_series（result_json 份 metrics 提取——isolated=顶层
        # metrics 直嵌/random=windows[i].metrics/staged=segments[i].metrics，
        # exec_index 与份序对齐〔B4 惯例〕；非 trail 档 null）
        per_exec_metrics: list = []
        if row["level"] == "isolated_backtest":
            per_exec_metrics = [result_json.get("metrics") or {}]
        elif row["level"] == "random_window":
            per_exec_metrics = [
                w.get("metrics") or {} for w in result_json.get("windows", [])]
        elif row["level"] == "staged_validation":
            per_exec_metrics = [
                s.get("metrics") or {} for s in result_json.get("segments", [])]
        trace_by_ref: Dict[str, list] = {}
        for tr in rr.get_vr_signal_trace_rows(db_path, record_id):
            trace_by_ref.setdefault(tr["exec_ref"], []).append(tr)

        for ex in executions:
            idx = int(ex.get("exec_index", 0))
            metrics = (
                per_exec_metrics[idx]
                if idx < len(per_exec_metrics) else {})
            # trail_series 份 metrics 提取（存档早于 trail 能力的记录缺键
            # →null 如实；非 trail 档 null）
            ex["trail_series"] = metrics.get("trail_series")
            # trace_rows：该份留痕行（seq 升序已由读函数保证；无行=[]——
            # 判读协议：三布尔 true+全份空=W0 缺陷带 record_id；三布尔
            # null+空=合法存量降级）
            ex["trace_rows"] = trace_by_ref.get(ex["exec_ref"], [])

        return {**base, "detail_available": True, "executions": executions}
    except ResearchError as exc:
        from api.routes.research.common import research_error_to_http
        raise research_error_to_http(exc) from exc
