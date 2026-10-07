"""模板入参守卫（clear-analysis-tds TD-2）——数值与枚举参数的类型/值域
前置校验唯一权威。

九个分析模板函数入口的公共守卫（Q1 全量收敛：四既有内联校验迁移+
六存量模板补齐）：bool 排除（bool 是 int 子类的静默接受陷阱）→ 非 int →
lo/hi 越界 → 422 RESEARCH_PARAM_INVALID 附 legal_range（错误即文档）；
枚举型字符串封闭值域（枚举外拒——消灭静默落入兜底分支的结论污染，
如 distribution_compare 的 transform 非法串被当 raw 语义）。

错误 message/detail 形制逐字节复刻 condition_stats.py 既有内联校验
（D5 硬约束：四模板迁移后既有断言零改写的前提）。边界语义：lo/hi 为
None 表示该侧无界（阈值类 min_samples/min_events 上界 None——阈值
语义由调用方自担；0=禁用小样本检查的既有合法语义故 lo=0）；窗口/
前向类上界 len(df)-1 由调用方现取（df 在模板函数内）。

拒绝通道两通道形态（与 condition_stats 先例一致）：快路径同步 422；
行数超阈值走 202 的重组路径落 failed 终态（reason 前缀
[RESEARCH_PARAM_INVALID]——轮询方可解析错误码）；恒异步模板
（pattern_mining）的校验拒绝即走该通道。

消费者：templates 包内九模板函数入口（包内共享，不反向依赖路由层）。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from core.analysis.hypothesis_service import ResearchError


def _require_int_range(
    value: Any, *, name: str,
    lo: Optional[int] = None, hi: Optional[int] = None,
) -> None:
    """数值参数守卫：bool 排除 → 非 int → lo/hi 越界 → 422 附 legal_range。

    Args:
        value: 待校验参数值（原始形，未清洗）。
        name: 参数名（错误 message/detail 与 legal_range 的锚）。
        lo: 下界（None=该侧无界；0 为有效下界——阈值类禁用语义）。
        hi: 上界（None=该侧无界；窗口/前向类由调用方传 len(df)-1）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（422）——detail 附
            legal_range（None 侧显示 inf）。
    """
    if (isinstance(value, bool) or not isinstance(value, int)
            or (lo is not None and value < lo)
            or (hi is not None and value > hi)):
        lo_s = lo if lo is not None else "-inf"
        hi_s = hi if hi is not None else "inf"
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{name} 非法: {value!r}",
            detail={"param": name,
                    "legal_range": f"{lo_s}..{hi_s}"})


def _require_enum(
    value: Any, *, name: str, choices: Iterable[str],
) -> None:
    """枚举参数守卫：枚举外取值 → 422 附合法清单（封闭值域）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（422）——legal_range 为
            排序后的枚举清单（| 分隔）。
    """
    allowed = tuple(choices)
    if value not in allowed:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{name} 非法: {value!r}",
            detail={"param": name,
                    "legal_range": "|".join(sorted(allowed))})
