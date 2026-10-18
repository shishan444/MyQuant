"""策略生命周期包（build-strategy-lifecycle）。

全系统唯一合法 strategy.status 变更通道的物理住址（v3 拓扑规则 4
授权：物理住 strategy 包，依赖单向 lifecycle→persistence）。常量
基座在 machine.py；公共出口 transit/compute_waterline 随组 2 落位。
"""
