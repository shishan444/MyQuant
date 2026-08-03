"""实盘交易模块（独立于模拟交易 core/trading）。

仅接入币安 USDT-M futures **demo**（模拟资金，层3边界，排除真实资金/主网）。
设计要点（规划 v2·冻结）：
- **单向持仓 One-Way Mode**：账户级单向（positionSide=BOTH），方向由策略
  ``DNA.direction`` 自适应（long/short/mixed 三值全支持，复用 judgment 过滤）。
- **bar 轮询时序**：bar 闭合才决策+下单（与模拟交易同模型），信号 bar N 生成 →
  bar N+1 开盘下单，等价 shift(1)，杜绝未来函数。
- **仅共享纯计算层**：import executor(信号)/judgment(决策)/DNA·GARCH/types；
  状态管理/OMS/执行/pipeline 时序/API/前端/DB 全部独立新建。
- **模拟交易零改动零回归**：本模块与 core/trading 物理隔离。

触及 AI_CODING_GUIDE §1 红线（实盘执行），用户已确认层3 demo 边界。
"""

__all__: list[str] = []
