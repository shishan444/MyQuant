"""并发回放池测试（add-replay-backtest-mcp 任务 4.1——验收锚③）。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.repository import paper
from core.replay import run_replay_batch
from tests.replay._env import ReplayEnv


def _make_envs_with_strategies(tmp_path: Path, n: int) -> ReplayEnv:
    """一库多策略（同库并发验证互不污染的实体面）。"""
    env = ReplayEnv(tmp_path, n_bars=500)
    from core.persistence.repository.strategy import save_strategy
    from tests.helpers.data_factory import make_dna

    env.strategy_ids = [env.strategy_id]
    for i in range(1, n):
        sid = f"st_replay_pool_{i}"
        save_strategy(env.db, strategy_id=sid,
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      status="draft")  # draft 免去重
        env.strategy_ids.append(sid)
    return env


class TestReplayBatch:
    """并发组：结果序保持+落库互不污染（锚③）。"""

    def test_batch_results_ordered_and_isolated(self, tmp_path):
        env = _make_envs_with_strategies(tmp_path, n=5)
        start, end = env.range(head_bars=200)
        # ThreadPoolExecutor 注入（接口同构轻量验证——进程池路径同
        # _run_one 单元逻辑；CI 环境进程池开销与稳定性归后续 e2e）
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = run_replay_batch(
                env.db, env.strategy_ids, start, end,
                data_dir=env.data_dir, executor=ex)
        assert [r.strategy_id for r in results] == env.strategy_ids, \
            "结果序=strategy_ids 序"
        task_ids = {r.task_id for r in results}
        assert len(task_ids) == 5, "每路独立 task_id"
        # 落库互不污染：五路 fills 按各自 task_id 可取且互不串
        for res in results:
            task = paper.get_task(env.db, res.task_id)
            assert task["strategy_id"] == res.strategy_id
        # 五路 bars_processed 一致（同区间同数据）
        assert len({r.bars_processed for r in results}) == 1

    def test_single_worker_sequential(self, tmp_path):
        """max_workers=1 退化串行（min 钳制语义）。"""
        env = _make_envs_with_strategies(tmp_path, n=3)
        start, end = env.range(head_bars=100)
        with ThreadPoolExecutor(max_workers=1) as ex:
            results = run_replay_batch(
                env.db, env.strategy_ids, start, end,
                data_dir=env.data_dir, executor=ex)
        assert len(results) == 3
