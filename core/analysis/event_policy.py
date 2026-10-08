"""事件策略权威变换（trigger-policy 能力）——掩码到事件流的单点实现。

服务对象：假设验证引擎触发循环（统计链）、假设预检频率外推、
event_study / condition_stats 分析模板、策略翻译的入场信号组装
（五链消费同一实现，多周期对齐 asof_completed 的单权威先例同构）。

职责边界：只做「合并掩码 → 事件位置序列」的下游变换与策略形制
校验；掩码生产归 condition_kernel（_mask_for 第七求值点），policy
是掩码下游变换、不感知条件形制与求值轴（timeframe 键的条件级
求值轴语义属另一层级）。两段校验纪律：validate_policy 仅做形制
校验（登记时点数据未加载）；bars 的数据长度上界在求值时点由调用
侧校验（与 forward_bars 同窗同位置）。

归一形约定：缺省与显式 {"mode": "none"} 均归一为 None——与无策略
等价（指纹输入不加 trigger_policy 键的 D4 语义在调用侧据此分支）；
有效策略形为 {"mode": "first_true"} 或 {"mode": "suppress_bars",
"bars": int}。该归一形即假设 conditions_json 顶层 trigger_policy 键
与策略 DNA entry_trigger_policy 字段的存储形。
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from core.analysis.errors import ResearchError   # CARD-02 断链：轻量定义点

#: 封闭枚举（AI 永不执行任意代码边界；扩充须经独立变更显式增补）
POLICY_MODES = ("none", "first_true", "suppress_bars")

_LEGAL_FORM = "对象形 {mode, bars?}: mode ∈ none|first_true|suppress_bars; suppress_bars 必带正整数 bars"


def validate_policy(policy: Any) -> Optional[dict]:
    """触发策略形制校验与归一（无数据依赖——D12 登记侧）。

    Args:
        policy: 原始声明。None=缺省；dict 形 {"mode": <枚举>,
            "bars": <正整数>}（bars 仅 suppress_bars 合法）。

    Returns:
        归一形：None（缺省/显式 none，等价无策略）或
        {"mode": "first_true"} / {"mode": "suppress_bars", "bars": int}。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（422）——非对象形、mode
            枚举外、多余键（防歧义形制——bars 挂在无参 mode 上同样
            拒绝）、suppress_bars 缺 bars 或 bars 非正整数（含 bool
            静默接受陷阱的既有守卫纪律）。
    """
    if policy is None:
        return None
    if not isinstance(policy, dict):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"trigger_policy 非法: {policy!r:.120}",
            detail={"param": "trigger_policy", "legal_form": _LEGAL_FORM})
    mode = policy.get("mode")
    if mode not in POLICY_MODES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"trigger_policy.mode 非法: {mode!r}",
            detail={"param": "trigger_policy.mode",
                    "legal_range": "|".join(POLICY_MODES),
                    "legal_form": _LEGAL_FORM})
    extra = set(policy) - {"mode", "bars"}
    if extra or ("bars" in policy and mode != "suppress_bars"):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"trigger_policy 携带多余键: {sorted(extra) or ['bars']}",
            detail={"param": "trigger_policy",
                    "legal_form": _LEGAL_FORM})
    if mode == "suppress_bars":
        bars = policy.get("bars")
        if isinstance(bars, bool) or not isinstance(bars, int) or bars < 1:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"trigger_policy.bars 非法: {bars!r}",
                detail={"param": "trigger_policy.bars",
                        "legal_range": "1..inf（正整数，bool 排除）",
                        "legal_form": _LEGAL_FORM})
        return {"mode": "suppress_bars", "bars": bars}
    if mode == "none":
        return None
    return {"mode": "first_true"}


def mask_to_events(mask: Any, policy: Optional[dict]) -> np.ndarray:
    """掩码 → 升序事件位置序列（确定性纯函数，无异常路径）。

    Args:
        mask: 布尔掩码序列（条件集 AND 合并掩码，任意可转 bool 序列）。
        policy: validate_policy 产出的归一形（None 视为 none 语义）。

    Returns:
        np.ndarray[int64] 升序事件位置。三模式语义：
        none=全部 True 位置（与现状直译逐字节等价）；first_true=每个
        极大连续 True 段首根；suppress_bars=自首个 True 起产生事件，
        与上一事件间隔差值不足 bars 的后续 True 抑制（跨段生效——
        间隔不足 bars 的相邻段合并为单事件，间隔恰为 bars 产生新事件，
        与「相邻事件间隔 ≥ bars」间隔不变量同源）。
    """
    mode = policy.get("mode", "none") if isinstance(policy, dict) else "none"
    arr = np.asarray(mask, dtype=bool)
    if mode != "suppress_bars":
        if mode == "first_true":
            starts = arr.copy()
            starts[1:] &= ~arr[:-1]
            return np.flatnonzero(starts).astype(np.int64)
        return np.flatnonzero(arr).astype(np.int64)
    bars = int(policy["bars"])  # 归一形必带（validate_policy 保证）
    events: list[int] = []
    last_event = -(bars + 1)  # 保证首个 True 无条件产生事件
    for pos in np.flatnonzero(arr):
        if pos - last_event >= bars:
            events.append(int(pos))
            last_event = int(pos)
    return np.asarray(events, dtype=np.int64)


def require_bars_within(
    policy: Optional[dict], data_len: int, param_name: str,
) -> None:
    """bars 数据长度上界求值侧校验（D12 两段校验纪律的求值侧半段）。

    五求值点（统计链四模板+引擎、策略链信号组装）的同构收敛单点
    （CARD-03）——消息文本与 detail 结构逐字复刻既有五处内联（param
    名经 ``param_name`` 参数化保真两链声明键：统计链 trigger_policy /
    策略链 entry_trigger_policy）。

    Args:
        policy: validate_policy 产出的归一形（None=无策略，直接过）。
        data_len: 求值数据长度（上界 len-1 的基数——含 warmup 前缀的
            全 df / 请求区间序列，与 forward_bars 同窗）。
        param_name: 错误锚定的参数名（不含 .bars 后缀——函数内拼）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID（422）——suppress_bars 的
            bars 超出 len(data)-1。
    """
    if (policy is not None
            and policy.get("mode") == "suppress_bars"
            and policy["bars"] > data_len - 1):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"{param_name}.bars 超求值数据长度上界: {policy['bars']}",
            detail={"param": f"{param_name}.bars",
                    "value": policy["bars"],
                    "legal_range": f"1..{data_len - 1}"})


def apply_policy_to_series(
    series: pd.Series, policy: Optional[dict],
    param_name: str = "trigger_policy",
) -> pd.Series:
    """掩码序列经策略变换并回投（变换+回投+预处理一体，CARD-03）。

    policy=None 原样返回 series（缺省零变化契约——不经 fillna 等任何
    预处理，与三既有回投点的「无策略原样」逐字节一致）；带策略路径
    内置 bars 上界校验（require_bars_within 同参数化）+ fillna(False)
    预处理 + 事件位置回投。消费者：executor 入场信号事件化薄壳、
    signal_assembly 组合后单次施加、分析模板统计段掩码回投。

    Args:
        series: 布尔掩码 Series（index 对齐消费方数据）。
        policy: validate_policy 产出的归一形。
        param_name: bars 校验错误锚定的参数名（两链键名）。

    Raises:
        ResearchError: bars 超上界（经 require_bars_within）。
    """
    if policy is None:
        return series
    require_bars_within(policy, len(series), param_name)
    # boolean 中转的 NaN→False 预处理：与原三处 fillna(False).astype(bool)
    # 逐字节同果，且避开 object dtype 上 fillna 的 downcast FutureWarning
    # （收敛单点后顺手稳化——语义显式三值→False→bool）
    mask = series.astype("boolean").fillna(False).astype(bool)
    event_pos = mask_to_events(mask.values, policy)
    out = pd.Series(False, index=series.index)
    out.iloc[event_pos] = True
    return out
