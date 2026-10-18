"""L2-F resume 改动1: restart_crashed_tasks 崩溃任务重启契约。

审计发现 resume_evolution 是死代码(从未被调用 + status死锁 + 引擎API错配)。本契约实现
改动1(保守): 新增 restart_crashed_tasks, 启动时扫描崩溃未完成的任务置 pending, 让
_find_pending_task 重拾(崩溃即同配置重跑, 非逐个体续跑)。progress_json.restart_count 限次
防风暴。改动2(逐个体续跑)因 _eval_diagnostics 不序列化致 champion_tracker 行为漂移, 属
回测核心回归, 本阶段排除(记录为后续独立任务)。
"""
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from api.runner import restart_crashed_tasks, EvolutionRunner  # noqa: E402
from core.persistence.db_ext import init_db_ext  # noqa: E402
from core.persistence.db_ext import _connect  # noqa: E402


def _insert_task(db, task_id, status="stopped", stop_reason="crash_recovery",
                 champion_dna=None, progress_json=None):
    with _connect(db) as conn:
        conn.execute(
            "INSERT INTO evolution_task (task_id, status, target_score, score_template, "
            "initial_dna, symbol, timeframe, created_at, updated_at, stop_reason, "
            "champion_dna, progress_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, status, 1.0, "default", "{}", "BTCUSDT", "4h",
             "2024-01-01T00:00:00", "2024-01-01T00:00:00",
             stop_reason, champion_dna, progress_json),
        )
        conn.commit()


def _get_task(db, task_id):
    with _connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM evolution_task WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


class TestRestartCrashedTasks:
    """L2-F 改动1: 崩溃任务重启 + 限次防风暴。"""

    def test_eligible_crashed_task_restarted_to_pending(self, tmp_path: Path):
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "crash-1", stop_reason="crash_recovery", champion_dna=None)
        restarted = restart_crashed_tasks(db)
        assert restarted == 1
        task = _get_task(db, "crash-1")
        assert task["status"] == "pending"
        assert task["stop_reason"] is None

    def test_skips_completed_task_with_champion(self, tmp_path: Path):
        """已有 champion 的任务视为已完成, 不重启。"""
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "done-1", stop_reason="crash_recovery",
                     champion_dna='{"strategy_id":"x"}')
        assert restart_crashed_tasks(db) == 0
        assert _get_task(db, "done-1")["status"] == "stopped"

    def test_skips_user_stopped(self, tmp_path: Path):
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "user-1", stop_reason="user_stop")
        assert restart_crashed_tasks(db) == 0

    def test_skips_error_stop_reason(self, tmp_path: Path):
        """error 类不重启(可能确定性失败, 重启还会 error)。"""
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "err-1", stop_reason="error")
        assert restart_crashed_tasks(db) == 0

    def test_increments_restart_count(self, tmp_path: Path):
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "crash-2", progress_json=json.dumps({"restart_count": 0}))
        restart_crashed_tasks(db)
        progress = json.loads(_get_task(db, "crash-2")["progress_json"])
        assert progress["restart_count"] == 1

    def test_skips_at_max_restarts_prevents_storm(self, tmp_path: Path):
        """restart_count 达上限不再重启, 防确定性崩溃任务无限重启。"""
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "storm-1", progress_json=json.dumps({"restart_count": 3}))
        assert restart_crashed_tasks(db, max_restarts=3) == 0
        assert _get_task(db, "storm-1")["status"] == "stopped"

    def test_restarted_task_findable_by_pending_lookup(self, tmp_path: Path):
        """重启后 _find_pending_task 能拾取(闭环可续)。"""
        db = tmp_path / "test.db"
        init_db_ext(db)
        _insert_task(db, "crash-3", stop_reason="crash_recovery")
        restart_crashed_tasks(db)
        runner = EvolutionRunner(db_path=db, data_dir=tmp_path)
        pending = runner._find_pending_task()
        assert pending is not None
        assert pending["task_id"] == "crash-3"
