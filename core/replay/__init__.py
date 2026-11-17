"""回放引擎包（core/replay）——逐根回放驱动外壳。

唯一公共入口 ReplayRunner.run（回放驱动编排）；ReplayResult 结果汇
总与 ReplayError 装载失败。执行核心=PaperTaskExecutor+kernel（原址
单一事实源，本包零执行逻辑复制——同构性 spec 承诺）。import 方向
契约：replay→paper_trading 允许、paper_trading→replay 禁止、
replay 对 core/backtest（VBT）零依赖（守卫 tests/replay/
test_no_backtest_import 双书写形制扫描）。
"""
from core.replay.feeder import ReplayFeeder
from core.replay.pool import DEFAULT_REPLAY_WORKERS, run_replay_batch
from core.replay.runner import (
    ReplayError,
    ReplayResult,
    ReplayRunner,
)

__all__ = [
    "ReplayFeeder", "ReplayError", "ReplayResult", "ReplayRunner",
    "run_replay_batch", "DEFAULT_REPLAY_WORKERS"]
