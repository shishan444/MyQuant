"""市场模型层（B7.1：清算/费用/滑点/资金费率的显式市场假设单源）。

此前散在 backtest engine 的市场假设集中于此：维持保证金率分层（币安
USDT-M 口径）、清算阈值函数。费用/滑点/资金费率的引擎实现经 B7.0
裁决已合理（文档化锚点），随用例迁入。
"""
from core.market_model.liquidation import (
    BinanceMmrTiers,
    maintenance_margin_rate,
    maintenance_threshold,
    tier_arrays,
)

__all__ = [
    "BinanceMmrTiers", "maintenance_margin_rate",
    "maintenance_threshold", "tier_arrays",
]
