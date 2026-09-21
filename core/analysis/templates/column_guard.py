"""模板列前置守卫（improve-mcp-agent-loop M3）。

列不可用 → ResearchError 结构化拒绝（错误即文档：附该模板实际可用列
清单与替代模板指引），替代裸 KeyError（经 MCP 坍缩为无信息 500）。
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from core.analysis.hypothesis_service import ResearchError


def require_column(df: pd.DataFrame, column: str, *, template: str) -> None:
    """列存在性检查：不可用即结构化拒绝（附可用列清单）。

    分析模板的 df 是装载后的原始 OHLCV+衍生品列——指标输出列
    （如 rsi_14）不在其中；指标条件应走 condition_stats（支持指标
    参数计算），指引写入 detail。
    """
    if column in df.columns:
        return
    raise ResearchError(
        "RESEARCH_COLUMN_NOT_AVAILABLE",
        f"模板 {template} 引用了不可用列: {column}",
        detail={
            "column": column,
            "available_columns": [str(c) for c in df.columns],
            "note": "指标条件（如 rsi_14）请用 condition_stats"
                    "（支持指标参数计算后引用）",
        },
    )


def require_indicator(indicator: str, registry: Dict[str, Any]) -> None:
    """指标注册表检查：未知指标即结构化拒绝（附注册表可用指标清单）。"""
    if indicator in registry:
        return
    raise ResearchError(
        "RESEARCH_INDICATOR_UNKNOWN",
        f"未知指标: {indicator}（不在指标注册表）",
        detail={"indicator": indicator,
                "available_indicators": sorted(registry.keys())},
    )
