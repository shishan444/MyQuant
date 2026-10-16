"""纸盘交易域（rebuild-paper-trading 变更，新域三层半架构）。

纸盘交易=用实时行情和虚拟资金前向运行策略的验证功能。本域是旧
core/trading 的完全重构替代（考场无观点：执行内核忠实执行策略声明，
不加任何私设门槛；只出证据不做裁决）：

- kernel/——执行内核（纯函数零 IO：意图/成交/记账/清算）
- runtime/——运行编排（调度/行情共享/执行器/恢复；组 3 交付）
- reporting.py——验证报告器（组 4 交付）

新表仓库落 core/persistence/repository/paper.py（沿工程仓库集中惯例，
不进本域）。
"""
