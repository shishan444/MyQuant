"""清算口径（币安 USDT-M 分层维持保证金率，B7.0 口径卡·架构约束修订）。

架构约束发现：engine 为「无杠杆 vbt 跑 + 事后逐交易块放大」——订单层
的权益是无杠杆曲线，与杠杆名义不匹配，故「基准改仓位名义」不能直接
落地（订单层判不出杠杆级亏损）。可行精确化=维持 batch1 的等效换算
形态（无杠杆曲线上的杠杆等效清算线），把固定 0.9 近似替换为分层
精确值 (1 − mmr)：

    threshold = init_cash × (1 − (1 − mmr(notional)) / L)
    其中 notional ≈ init_cash × L（等效名义，用于选档）

mmr 取币安 USDT-M 分层档位（0.4%–10%），(1−mmr)∈[0.90, 0.996]——
0.9 是最低档近似，新口径随名义规模更宽松（更接近真实合约）。
逐 bar 判定与事后检测共同引用本唯一实现（单一权威源）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Tier:
    notional_max: float  # 该档位的名义上限（USD，含）
    mmr: float           # 维持保证金率


# 币安 USDT-M 永续（BTCUSDT 口径，2026 公开档位；单实现纪律：唯一字面量源）
BinanceMmrTiers: tuple[_Tier, ...] = (
    _Tier(notional_max=50_000, mmr=0.004),
    _Tier(notional_max=250_000, mmr=0.005),
    _Tier(notional_max=1_000_000, mmr=0.010),
    _Tier(notional_max=5_000_000, mmr=0.025),
    _Tier(notional_max=20_000_000, mmr=0.050),
    _Tier(notional_max=float("inf"), mmr=0.100),
)


def maintenance_margin_rate(position_notional: float) -> float:
    """按仓位名义价值选择档位 mmr（超界取最高档）。"""
    for tier in BinanceMmrTiers:
        if position_notional <= tier.notional_max:
            return tier.mmr
    return BinanceMmrTiers[-1].mmr


def maintenance_threshold(init_cash: float, leverage: float) -> float:
    """无杠杆 vbt 曲线上的杠杆等效清算线（分层精确化，B7.1）。

    threshold = init_cash × (1 − (1 − mmr(init_cash×L)) / L)
    纯算术函数——逐 bar 判定与事后检测共同引用。
    """
    mmr = maintenance_margin_rate(init_cash * leverage)
    return init_cash * (1.0 - (1.0 - mmr) / leverage)


def tier_arrays() -> tuple:
    """档位表的 numpy 平铺（@njit 订单回调内联选档用）。"""
    import numpy as np
    maxs = np.array([t.notional_max for t in BinanceMmrTiers], dtype=np.float64)
    mmrs = np.array([t.mmr for t in BinanceMmrTiers], dtype=np.float64)
    return maxs, mmrs
