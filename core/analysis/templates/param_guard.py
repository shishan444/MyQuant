"""模板入参守卫（clear-analysis-tds TD-2 + trigger-policy TD-A）——数值、
枚举、时间窗形与时间形参数的类型/值域前置校验唯一权威。

八个模板函数入口的公共守卫（Q1 收敛：四既有内联校验迁移+四存量
模板补齐）；screen_patterns 例外——自有校验体系（top_n/min_samples/
horizons 422 附 legal_range），主 spec 有界语义锚定，不消费本守卫。
守卫链：bool 排除（bool 是 int 子类的静默接受陷阱）→ 非 int →
lo/hi 越界 → 422 RESEARCH_PARAM_INVALID 附 legal_range（错误即文档）；
枚举型字符串封闭值域（枚举外拒——消灭静默落入兜底分支的结论污染，
如 distribution_compare 的 transform 非法串被当 raw 语义）。

TD-A 扩展（trigger-policy 卡6）：结构形/时间形守卫——
_require_time_window（distribution_compare 的 group_a/group_b 时间窗
对象）与 _require_iso_datetime（pattern_mining 的 as_of 时间串），
修正该两类参数现状 TypeError / KeyError / 裸 ValueError 落 500 或
无结构化码 failed 的通道错置。可解析性判定用 pandas（pd.Timestamp）；
未带时区 UTC 归一属调用侧既有规约（to_utc 单源），守卫不产生解析
产物；值可解析但窗区间非法/超数据范围走模板既有语义不拦截。

错误 message/detail 形制逐字节复刻 condition_stats.py 既有内联校验
（D5 硬约束：四模板迁移后既有断言零改写的前提）。边界语义：lo/hi 为
None 表示该侧无界（阈值类 min_samples/min_events 上界 None——阈值
语义由调用方自担；0=禁用小样本检查的既有合法语义故 lo=0）；窗口/
前向类上界 len(df)-1 由调用方现取（df 在模板函数内）。

拒绝通道两通道形态（与 condition_stats 先例一致）：快路径同步 422；
行数超阈值走 202 的重组路径落 failed 终态（reason 前缀
[RESEARCH_PARAM_INVALID]——轮询方可解析错误码）；恒异步模板
（pattern_mining）的校验拒绝即走该通道。

消费者：templates 包内八模板函数入口（screen_patterns 例外，包内
共享，不反向依赖路由层）。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd

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


def _is_parseable_datetime(value: Any) -> bool:
    """值为可解析时间串（字符串形 + pd.Timestamp 可解析且非 NaT）。

    非字符串形（None→NaT、bool/int→epoch 语义）一律不可——契约
    「值为时间串」要求字符串形，杜绝 bool 静默接受陷阱的同族入口；
    空串等 pandas 静默解析为 NaT 的形（不抛异常）同样不可——NaT
    非有效时间，下游比较会全 False 掩盖传参错误。
    """
    if not isinstance(value, str):
        return False
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return False
    return not pd.isna(ts)


_TIME_WINDOW_FORM = '{"start","end"} 时间窗对象（半开区间），值为可解析时间串'


def _require_time_window(value: Any, *, name: str) -> None:
    """时间窗对象守卫（TD-A）：非对象形 / 缺 start-end 键 / 多余键 /
    值非可解析时间串 → 422 附合法形。

    修正的通道错置：现状三类非法形分别以 TypeError / KeyError 落 500。
    值可解析但窗区间非法（如 start ≥ end）走模板既有区间语义不拦截。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（422）——detail 附
            legal_form（{"start","end"} 时间窗形说明）。
    """
    if (not isinstance(value, dict)
            or set(value) != {"start", "end"}
            or not _is_parseable_datetime(value["start"])
            or not _is_parseable_datetime(value["end"])):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{name} 非法: {value!r}",
            detail={"param": name,
                    "legal_form": _TIME_WINDOW_FORM})


def _require_iso_datetime(value: Any, *, name: str) -> None:
    """时间形参数守卫（TD-A）：不可解析时间串 → 422 附合法形。

    值可解析但超数据范围走模板既有数据范围语义不拦截（守卫只管
    可解析性）。守卫对象仅 pattern_mining 的 as_of（恒异步模板——
    校验拒绝经调用侧通道落 failed 终态前缀，event_study 签名无
    此参数）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（422）——detail 附
            legal_form（ISO 时间串形说明）。
    """
    if not _is_parseable_datetime(value):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{name} 非法: {value!r}",
            detail={"param": name,
                    "legal_form": "ISO 8601 时间串（未带时区按 UTC 解释）"})
