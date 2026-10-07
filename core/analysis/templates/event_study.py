"""事件研究模板（add-ai-research-mcp 2.4）。

语义：给定事件触发条件，把每个事件后的 [0, window] bar 价格路径
对齐到事件时刻（相对事件 bar 收盘归一），统计路径分布（均值/分位）
与终点收益分布——回答"这类事件之后典型怎么走、最坏最好差多少"。

窗口对齐：事件 bar 为 t0，路径元素 (close[t0+k] - close[t0]) /
close[t0]，k = 1..window（事件当 bar 不计，避免把事件本身算进路径）。
尾部不完整路径的事件丢弃（不 pad）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import AnalysisResult, build_result
from core.analysis.templates.column_guard import require_column
from core.analysis.templates.param_guard import _require_int_range


def event_study(
    df: pd.DataFrame,
    *,
    condition: Dict[str, Any],
    window: int = 12,
    data_ranges: list[dict[str, str]],
    min_events: int = 30,
    hypothesis_id: str | None = None,
    mtf: "MtfEvalContext | None" = None,
    trigger_policy: Dict[str, Any] | None = None,
) -> AnalysisResult:
    """事件窗对齐的路径统计。

    Args:
        condition: {subject, action, target}（假设引擎同源形制；MAY
            携带 timeframe 键——高周期条件经掩码投影求值）。0.2.14
            改道 condition_kernel._mask_for（第七求值点收口）——额外
            动作键全量透传生效、结构化 subject 支持获得（现算路径）。
        window: 事件后观察窗（bar 数）。

    Raises:
        ResearchError: RESEARCH_PARAM_INVALID——事件数不足（小样本
            拒绝，卡10 收编，原 ValueError 落 500 误导 Agent 盲重试）。
    """
    # TD-2 入参守卫（clear-analysis-tds）：窗口类 1..len(df)-1；阈值类
    # 0..无上界（0=禁用事件数检查的既有合法语义）
    _require_int_range(window, name="window", lo=1, hi=len(df) - 1)
    _require_int_range(min_events, name="min_events", lo=0)

    # trigger-policy 卡4（5.1）：形制归一 + bars 数据长度上界求值侧
    # 校验（数据已加载——D12 与 forward_bars 同窗同位置）；缺省/显式
    # none 归一 None=逐 bar 现状口径（存量回归锚）
    from core.analysis.event_policy import validate_policy
    policy_norm = validate_policy(trigger_policy)
    if (policy_norm is not None
            and policy_norm.get("mode") == "suppress_bars"
            and policy_norm["bars"] > len(df) - 1):
        from core.analysis.hypothesis_service import ResearchError as _RE
        raise _RE(
            "RESEARCH_PARAM_INVALID",
            f"trigger_policy.bars 超求值数据长度上界: {policy_norm['bars']}",
            detail={"param": "trigger_policy.bars",
                    "value": policy_norm["bars"],
                    "legal_range": f"1..{len(df) - 1}"})

    from core.analysis.column_ref import ColumnRefError, resolve_column_ref
    from core.analysis.condition_kernel import MtfEvalContext, _mask_for

    # 列前置检查：subject 不可用先结构化拒绝（否则 mask 全 False 会以
    # 误导性的"事件数不足"小样本拒绝收场）。结构化 subject 经 resolve
    # 校验即放行（_mask_for 两段式现算）；字符串 subject 保持原始列检查。
    subject = condition.get("subject", "")
    if isinstance(subject, dict):
        try:
            resolve_column_ref(subject)
        except ColumnRefError as exc:
            raise ResearchError(
                "RESEARCH_COLUMN_NOT_AVAILABLE",
                f"condition.subject 引用非法: {exc.message}",
                detail={"param": "condition.subject",
                        "code": exc.code, **exc.detail}) from exc
    else:
        require_column(df, str(subject), template="event_study")

    mask = _mask_for(df, condition, mtf=mtf)
    mask = mask.fillna(False).astype(bool)
    event_idx = np.where(mask.values)[0]

    # trigger-policy 卡4：带策略时事件位置取权威变换产出（上面掩码
    # 直译语句保留——无策略路径逐字节不变；n_events/min_events 口径
    # 随之切事件数）
    if policy_norm is not None:
        from core.analysis.event_policy import mask_to_events
        event_idx = mask_to_events(mask.values, policy_norm)

    # 丢弃尾部不完整窗口的事件
    event_idx = event_idx[event_idx + window < len(df)]

    if len(event_idx) < min_events:
        # 卡10 收编（distribution_compare 先例形制）：小样本属业务
        # 拒绝 422 非内部错误
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"小样本拒绝: 事件数 {len(event_idx)} < {min_events}",
            detail={"n_events": int(len(event_idx)),
                    "min_events": min_events,
                    "hint": "扩大事件区间或调低 min_events"})

    close = df["close"].values
    # 路径矩阵：行=事件，列=k=1..window
    paths = np.empty((len(event_idx), window))
    for row, t0 in enumerate(event_idx):
        base = close[t0]
        paths[row] = (close[t0 + 1: t0 + 1 + window] - base) / base

    mean_path = paths.mean(axis=0)
    q05 = np.quantile(paths, 0.05, axis=0)
    q25 = np.quantile(paths, 0.25, axis=0)
    q50 = np.quantile(paths, 0.50, axis=0)
    q75 = np.quantile(paths, 0.75, axis=0)
    q95 = np.quantile(paths, 0.95, axis=0)

    terminal = paths[:, -1]
    # 终点收益的 t 检验（H0: 事件后终点收益均值为 0）。可检性 None
    # 语义（先例=condition_kernel._p_value_for）：n<2 无自由度（std/
    # t 检验产 NaN）、零方差（std(ddof=1)≤0）时 p_value 置 None、
    # n<2 的 std 同置——NaN 进 payload 会在响应序列化 allow_nan=False
    # 裸崩（S2 响应侧破口）；统计层防线与通道级 sanitize 切割
    n_term = len(terminal)
    std = float(terminal.std(ddof=1)) if n_term >= 2 else None
    if n_term >= 2 and std > 0:
        from scipy import stats as sps
        _, p_value = sps.ttest_1samp(terminal, 0.0)
        p_value = float(p_value)
    else:
        p_value = None

    confidence = {
        "method": "one_sample_t",
        "p_value": p_value,
        "n_events": int(n_term),
        "alpha": 0.05,
    }
    payload = {
        "condition": dict(condition),
        "window": window,
        "n_events": int(len(event_idx)),
        "path": {
            "k": list(range(1, window + 1)),
            "mean": mean_path.round(6).tolist(),
            "q05": q05.round(6).tolist(),
            "q25": q25.round(6).tolist(),
            "median": q50.round(6).tolist(),
            "q75": q75.round(6).tolist(),
            "q95": q95.round(6).tolist(),
        },
        "terminal_return": {
            "mean": float(terminal.mean()),
            "std": std,
            "min": float(terminal.min()),
            "max": float(terminal.max()),
            "positive_rate": float((terminal > 0).mean()),
        },
        "first_event_time": str(df.index[event_idx[0]]),
        "last_event_time": str(df.index[event_idx[-1]]),
    }
    if policy_norm is not None:
        payload["trigger_policy"] = policy_norm   # 报告声明生效策略

    return build_result(
        "event_study",
        sample_size=len(event_idx),
        confidence=confidence,
        data_ranges=data_ranges,
        payload=payload,
        min_samples=min_events,
        hypothesis_id=hypothesis_id,
    )
