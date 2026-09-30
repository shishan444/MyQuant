"""asof_completed 高周期对齐权威模块（multi-timeframe-conditions B1）。

单一实现四链消费（研究链掩码投影 / Lab 链值投影 / 策略-进化链脉冲与
数值上下文投影）——纪律：任意基准 bar 可见的高周期值，只来自在其
之前已收盘的高周期 K 线（引擎强制、无关闭参数）。

数据索引=bar 开盘时刻：高周期 bar T 收盘于 T+tf，故值挂载点=开盘
索引 + tf 时长。缺口索引下按位置 shift(1) 会错位挂载（前一根的值
错挂到更晚的 bar 上），一律用 +tf 挂载推导——缺口上保守方向无前视。

warmup 尺度（N1 修正）：max(指标真实收敛×3, 20 bar) × TF_SECONDS。
与隔离回测 derive_warmup 的 200 floor 显式分离——200 floor 服务交易
统计稳定性，条件掩码求值只需指标数值收敛；机械沿用 200 被实测否决
（3d 轴 600 天 warmup 吃掉 63% 数据）。
"""
from __future__ import annotations

from typing import List

import pandas as pd

from core.data.timeframes import TF_SECONDS

#: 高周期轴 warmup 下限（工程参数 A-new；锚测试内可微调）
WARMUP_FLOOR_BARS = 20


def _tf_seconds(tf: str) -> int:
    """周期键 → 秒数（未知周期显式拒绝，附合法清单）。"""
    sec = TF_SECONDS.get(tf)
    if sec is None:
        raise ValueError(
            f"未知周期: {tf!r}（合法键集: {sorted(TF_SECONDS)}）")
    return sec


def assert_higher_tf(tf: str, base_tf: str) -> None:
    """登记侧与求值侧共用的方向判定：仅允许 >= 基准周期的高周期。

    Raises:
        ValueError: 周期不在 TF_SECONDS 键集，或低于基准周期
            （附降基准等价表达指引——低周期条件存在无歧义等价表达）。
    """
    tf_sec = _tf_seconds(tf)
    base_sec = _tf_seconds(base_tf)
    if tf_sec < base_sec:
        raise ValueError(
            f"周期 {tf} 低于基准周期 {base_tf}——低周期条件请降基准表达："
            f"基准周期降为 {tf}、其余条件挂高周期"
            f"（聚合歧义 any/all/末值不可自证，不引入低周期语义）")


def _completed_index(values: pd.Series, tf: str) -> pd.Series:
    """值挂载到收盘时刻（开盘 T → 收盘 T+tf）后的已完结序列。"""
    completed = values.copy()
    completed.index = completed.index + pd.Timedelta(seconds=_tf_seconds(tf))
    return completed.sort_index()


def project_completed(
    values: pd.Series, base_index: pd.Index, tf: str,
) -> pd.Series:
    """值投影：基准索引上可见的已完结高周期值。

    高周期 bar T 的值挂载到收盘时刻 T+tf，再对基准索引 backward
    对齐（取收盘时刻 <= 基准 bar 开盘时刻的最近值）。首段（首根
    高周期收盘之前）NaN；指标自然 NaN 不做值级填充（未收敛即不可见）。
    """
    completed = _completed_index(values, tf)
    return completed.reindex(base_index, method="ffill")


def project_completed_bool(
    mask: pd.Series, base_index: pd.Index, tf: str,
) -> pd.Series:
    """布尔投影（研究链与 Lab 链掩码求值唯一通道）。

    值投影同构 + 首段 False（无已收盘数据=不可见=条件不成立）。
    float 中转避免 object dtype 的 fillna 降维告警。
    """
    completed = _completed_index(mask.astype(float), tf)
    projected = completed.reindex(base_index, method="ffill")
    return projected.fillna(0.0).astype(bool)


def pulse_completed(
    signal: pd.Series, base_index: pd.Index, tf: str,
) -> pd.Series:
    """脉冲投影（策略链 execution 层）：穿越当根收盘落位到所在基准 bar。

    高周期轴 signal=True 的 bar T（收盘 T+tf）→ 基准索引上开盘时刻
    <= T+tf 的最后一个 bar 置 True（单 bar 脉冲，区别于掩码投影的
    状态窗）。收盘时刻早于基准首 bar 或晚于基准末 bar 的不落位
    （不可见；末 bar 窗口=+tf 时长，替换既有硬编码 1 天兜底）。
    """
    fires = signal.fillna(False).astype(bool)
    close_ts = fires.index + pd.Timedelta(seconds=_tf_seconds(tf))
    close_ts = close_ts[fires.to_numpy()]
    out = pd.Series(False, index=base_index)
    if len(close_ts) == 0:
        return out
    pos = base_index.searchsorted(close_ts, side="right") - 1
    valid = (pos >= 0) & (close_ts <= base_index[-1])
    out.iloc[pos[valid]] = True  # 重复落位同一基准 bar 自然幂等
    return out


def high_tf_warmup_span(indicator_refs: List[tuple], tf: str) -> pd.Timedelta:
    """高周期轴指标 warmup 前扩时长（N1 修正语义）。

    bars = max(各引用指标真实收敛需求 ×3, 20 bar)——收敛按**条件实际
    参数**粒度推导（indicator_convergence：RSI(14) 收敛 14 而非注册表
    max 50——机械沿用 max 会把 3d 轴 warmup 从 42 bar 虚增至 150 bar）；
    × TF_SECONDS 换算为时长。与隔离回测 derive_warmup 的 200 floor
    显式分离（依据见模块 docstring）。

    Args:
        indicator_refs: 条件实际引用的 (indicator, params) 清单；
            params 为 resolve 后全键形（缺键由收敛推导补 default）。
            空清单=floor 20 bar（无指标收敛需求）。

    Raises:
        ValueError: 引用不存在的指标。
    """
    # 函数内导入：registry_meta 属分析域元数据，core.data 模块级不反向依赖
    from core.analysis.registry_meta import indicator_convergence

    bars = WARMUP_FLOOR_BARS
    for name, params in indicator_refs:
        bars = max(bars, indicator_convergence(name, params or {}) * 3)
    return pd.Timedelta(seconds=bars * _tf_seconds(tf))
