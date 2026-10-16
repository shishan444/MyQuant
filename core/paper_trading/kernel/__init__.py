"""执行内核（第 1 层·纯函数零 IO——「考场本体」，未来实盘可复用）。

五件分工（design §3）：
- types.py——内核词汇（Intent/Fill/ExecutionEvent/RiskParams+物化单点）
- intents.py——信号解释器（SignalSet 信号+闭合 bar → 动作意图）
- fills.py——成交方式接口 FillModel + 机械市价实现 MarketFill
- ledger.py——记账器（仓位/余额/权益/费用/清算/资金费）
- account.py——AccountSnapshot 只读快照
"""
