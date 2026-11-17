"""并发回放编排（add-replay-backtest-mcp 任务 4.1）。

进程池并行（multiprocessing spawn——macOS 默认+防 fork 安全），每路
worker 内独立 ReplayRunner 实例（无共享状态天然隔离——P1 结构预留
兑现）；单策略不进池（调用方直调零开销）。默认并发 5 可配。
"""
from __future__ import annotations

from concurrent.futures import Executor, ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional

from core.replay.runner import ReplayResult, ReplayRunner

DEFAULT_REPLAY_WORKERS = 5


def _run_one(args) -> ReplayResult:
    """worker 入口：每路独立 ReplayRunner（进程内自建实例）。"""
    (data_dir, db_path, strategy_id, start, end, kwargs) = args
    return ReplayRunner(Path(data_dir)).run(
        Path(db_path), strategy_id, start, end, **kwargs)


def run_replay_batch(
    db_path: Path,
    strategy_ids: List[str],
    start: str,
    end: str,
    *,
    data_dir: Path,
    fee: float = 0.001,
    slippage: float = 0.0005,
    initial_cash: float = 100_000.0,
    leverage: int = 1,
    max_workers: int = DEFAULT_REPLAY_WORKERS,
    executor: Optional[Executor] = None,
) -> List[ReplayResult]:
    """并发组回放：多策略同区间批量——进程池并行，结果序=strategy_
    ids 序；executor 可注入（测试用 ThreadPoolExecutor 同接口跑轻量
    验证）；max_workers 上限取 min(配置, 策略数)。

    失败语义：单路 ReplayError 冒泡整批失败（fail-loud——并发组是
    原子语义单元，部分结果不返回；逐路容错归调用方按需包装）。
    """
    common = dict(fee=fee, slippage=slippage,
                  initial_cash=initial_cash, leverage=leverage)
    args_list = [
        (str(data_dir), str(db_path), sid, start, end, dict(common))
        for sid in strategy_ids]
    if executor is not None:
        futures = [executor.submit(_run_one, a) for a in args_list]
        return [f.result() for f in futures]
    workers = max(1, min(max_workers, len(strategy_ids)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run_one, args_list))
