"""指标元数据与信号列查询端点（add-ai-research-mcp 1.3 出口）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from api.deps import get_db_path
from core.analysis.registry_meta import available_signal_columns, indicator_metadata

router = APIRouter()


@router.get("/indicators")
def indicators() -> List[Dict[str, Any]]:
    """内置指标元数据（45 项，含 lookback——warmup 推导依据）。"""
    return indicator_metadata()


@router.get("/signals")
def signals(db_path: Path = Depends(get_db_path)) -> List[Dict[str, Any]]:
    """可用信号列：内置 ∪ 插件，运行时合并（条目标注来源）。"""
    return available_signal_columns(db_path)
