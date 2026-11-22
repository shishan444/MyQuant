"""T3：启动期回放残行清扫（fix-replay-gap-alignment 2.3，裁决②）。

origin=replay 且 status ∈ {running,pending} 的任务行逐行置 error
（error_detail 前缀 orphaned_by_restart 与人工清理 worker_crashed
区分成因）；成交/权益数据行全部保留；幂等（CAS 前态守门——二次
执行零命中）；已终态行与纸盘域行零触碰（域隔离双保险）。

形制随 startup_recover 既有测试域（隔离库直建行+直调函数——
tests/paper_trading/test_domain_isolation.py 同款装配简化版：本域
无需调度器装配，直调 sweep_orphaned_replay_tasks）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from core.replay import ReplayRunner
from core.replay.runner import sweep_orphaned_replay_tasks
from tests.helpers.data_factory import make_dna
from tests.replay._env import ReplayEnv


def _create_replay_row(db, *, status: str = "pending",
                        origin: str = "replay") -> str:
    """直建任务行并推到指定状态（测试通道——真实状态机迁移）。"""
    tid = paper.create_task(
        db, strategy_id="strat-sweep",
        dna_snapshot_json=make_dna().to_json(),
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified",
        initial_cash=10_000.0, leverage=1, window_bars=10_000,
        origin=origin)
    if status == "running":
        hit = paper.update_task(db, tid, status="running")
        assert hit == 1
    return tid


def _one_fill_bar(db, tid: str) -> None:
    """预置一行成交+权益数据（数据保留断言面——sweep 只动任务行）。"""
    paper.append_bar(
        db, tid, bar_time="2030-01-01T00:00:00+00:00",
        fills=[{"seq": 0, "kind": "open", "side": "long",
                "price": 100.0, "quantity": 1.0, "fee": 0.1,
                "realized_pnl": 0.0, "reason": "entry"}],
        equity={"equity": 10_000.0, "balance": 10_000.0,
                "unrealized_pnl": 0.0})


class TestT3aRunningResidualSwept:
    def test_running_swept_data_preserved_idempotent(self, tmp_path):
        db = tmp_path / "sweep.db"
        init_db_ext(db)
        tid = _create_replay_row(db, status="running")
        _one_fill_bar(db, tid)

        swept = sweep_orphaned_replay_tasks(db)

        assert swept == 1, "running 残行命中一条"
        row = paper.get_task(db, tid)
        assert row["status"] == "error"
        assert row["error_detail"].startswith("orphaned_by_restart"), (
            f"成因标记前缀（与人工清理 worker_crashed 区分）——"
            f"{row['error_detail']}")
        # 数据行保留（成交/权益零触碰）
        assert len(paper.list_fills(db, tid, limit=10)) == 1
        assert len(paper.list_equity_snapshots(db, tid, limit=10)) == 1
        # 幂等：二次调用零命中（CAS 前态守门——error 行不进扫描集）
        assert sweep_orphaned_replay_tasks(db) == 0
        assert paper.get_task(db, tid)["error_detail"].startswith(
            "orphaned_by_restart")


class TestT3bPendingResidualSwept:
    def test_pending_orphan_swept(self, tmp_path):
        """pending 孤儿同归清扫（同步驱动下启动瞬间现存 pending 必为
        无驱动者孤儿——零误杀根据）。"""
        db = tmp_path / "sweep.db"
        init_db_ext(db)
        tid = _create_replay_row(db, status="pending")
        _one_fill_bar(db, tid)

        swept = sweep_orphaned_replay_tasks(db)

        assert swept == 1
        row = paper.get_task(db, tid)
        assert row["status"] == "error"
        assert row["error_detail"].startswith("orphaned_by_restart")
        assert len(paper.list_fills(db, tid, limit=10)) == 1
        assert sweep_orphaned_replay_tasks(db) == 0


class TestT3cDomainIsolation:
    def test_paper_rows_and_terminal_rows_untouched(self, tmp_path):
        """域隔离不误伤：纸盘域 running/pending 行与回放终态行零触碰
        （终态前态门+域过滤双保险——三写者边界）。"""
        db = tmp_path / "sweep.db"
        init_db_ext(db)
        paper_running = _create_replay_row(db, status="running",
                                           origin="paper")
        paper_pending = _create_replay_row(db, origin="paper")
        replay_error = _create_replay_row(db)
        hit = paper.update_task(db, replay_error, status="error")
        assert hit == 1
        replay_completed = _create_replay_row(db)
        paper.update_task(db, replay_completed, status="running")
        hit = paper.update_task(db, replay_completed, status="completed")
        assert hit == 1
        before = {tid: paper.get_task(db, tid) for tid in (
            paper_running, paper_pending, replay_error, replay_completed)}

        swept = sweep_orphaned_replay_tasks(db)

        assert swept == 0, "纸盘域与终态行零命中"
        for tid, prev in before.items():
            after = paper.get_task(db, tid)
            for key, value in prev.items():
                assert after[key] == value, (
                    f"误伤行 {tid} 字段被改写: {key}")


class TestT3dZeroFalseKill:
    def test_completed_row_bitwise_unchanged(self, tmp_path):
        """零误杀回归锚：正常路径完整回放（建行→running→completed）
        后触发清扫——终态行字段逐位不变。"""
        env = ReplayEnv(tmp_path, n_bars=600)
        start, end = env.range(head_bars=200)
        res = ReplayRunner(env.data_dir).run(
            env.db, env.strategy_id, start, end, task_id="t3d")
        assert res.status == "completed"
        before = paper.get_task(env.db, "t3d")
        fills_before = paper.list_fills(env.db, "t3d", limit=1000)
        equity_before = paper.list_equity_snapshots(env.db, "t3d", limit=1000)

        swept = sweep_orphaned_replay_tasks(env.db)

        assert swept == 0, "completed 行不进清扫集"
        after = paper.get_task(env.db, "t3d")
        for key, value in before.items():
            assert after[key] == value, f"终态行字段被改写: {key}"
        assert paper.list_fills(env.db, "t3d", limit=1000) == fills_before
        assert paper.list_equity_snapshots(
            env.db, "t3d", limit=1000) == equity_before
