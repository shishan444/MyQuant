"""策略生命周期包（build-strategy-lifecycle）。

全系统唯一合法 strategy.status 变更通道的物理住址（v3 拓扑规则 4
授权：物理住 strategy 包，依赖单向 lifecycle→persistence）。常量
基座在 machine.py；公共出口 transit/compute_waterline 从 service
导入（调用方四类：研究链/纸盘/人工 REST 动作端点/后续 change 预留
接口——MCP 零写边）。
"""
from core.strategy.lifecycle.service import compute_waterline, transit

__all__ = ["compute_waterline", "transit"]

