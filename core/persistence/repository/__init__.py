"""repository 层（B3a 起）：表访问唯一入口（SQLAlchemy）。

域文件按 B3a-d 逐域迁入：dataset_meta（试点）→ strategy/backtest_result/
verify_session → paper_trading/live_trading → evolution_task。迁移期
db_ext 同名函数委托本层（签名不变、调用方零改动），B3d 旧轨退役。
"""
