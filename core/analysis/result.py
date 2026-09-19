"""结构化分析结果统一构造器（add-ai-research-mcp 2.6）。

三要素强制（spec ai-research-tools）：任何分析结果 MUST 含
样本量 / 置信 / 数据区间——缺任一不可构造（TypeError/ValueError 拒绝），
杜绝"看起来可信但不知道基于多少证据"的输出。

置信信息（confidence）：结构化 dict。统计类模板至少含
``method``（方法名）与 ``interval``（区间，list[float]）或
``level``（显著性水平）。具体方法由各模板决定（设计开放点：
p 值/自助法在首个模板实施时定——当前用正态近似区间，大样本下
与自助法收敛，工程成本最低）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# 小样本拒绝线：低于此样本量的统计结果不可构造（不可信）
MIN_SAMPLES_DEFAULT = 30


@dataclass
class AnalysisResult:
    """带三要素的分析结果（to_dict 后即 API/落日志的统一载荷）。"""

    template: str
    sample_size: int
    confidence: Dict[str, Any]
    data_ranges: List[Dict[str, str]]
    payload: Dict[str, Any] = field(default_factory=dict)
    hypothesis_id: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template": self.template,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "data_ranges": self.data_ranges,
            "payload": self.payload,
            "hypothesis_id": self.hypothesis_id,
        }


def build_result(
    template: str,
    *,
    sample_size: int,
    confidence: Dict[str, Any] | None,
    data_ranges: List[Dict[str, str]] | None,
    payload: Dict[str, Any] | None = None,
    min_samples: int = MIN_SAMPLES_DEFAULT,
    hypothesis_id: str | None = None,
) -> AnalysisResult:
    """构造三要素齐备的分析结果；任一要素缺失或不可信即拒绝。

    Raises:
        ValueError: sample_size 非正/低于下限；confidence 缺关键字段；
            data_ranges 为空或条目非半开 [start, end) 形。
    """
    if not template:
        raise ValueError("template 必填")
    if not isinstance(sample_size, int) or sample_size < min_samples:
        raise ValueError(
            f"样本量 {sample_size} 低于下限 {min_samples}（小样本拒绝——结果不可信）")
    if not confidence or "method" not in confidence:
        raise ValueError("confidence 必填且含 method（置信信息缺失）")
    if not ("interval" in confidence or "level" in confidence or "p_value" in confidence):
        raise ValueError("confidence 须含 interval / level / p_value 之一")
    if not data_ranges:
        raise ValueError("data_ranges 必填（数据区间登记缺失）")
    for r in data_ranges:
        if not isinstance(r, dict) or "start" not in r or "end" not in r:
            raise ValueError(f"data_ranges 条目须为半开区间形 {{start, end}}: {r!r}")

    return AnalysisResult(
        template=template,
        sample_size=sample_size,
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload or {},
        hypothesis_id=hypothesis_id,
    )
