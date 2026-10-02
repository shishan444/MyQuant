"""runner_progress 特征网（修复期安全网，现状记录语义）。

覆盖 api/runner_progress.py 的异常吞掉与推送分支：
- _push_ws：无注册 fn 时 no-op；fn 抛异常吞掉（warning 可观测）
- update_heartbeat / update_phase / update_progress：repo 抛异常吞掉不传播；
  update_phase 失败后仍推 WS（进度通知与持久化解耦）
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit]

from api import runner_progress


@pytest.fixture(autouse=True)
def _reset_push_fn():
    saved = runner_progress._ws_push_fn
    runner_progress._ws_push_fn = None
    yield
    runner_progress._ws_push_fn = saved


class TestPushWs:
    def test_no_fn_registered_is_noop(self) -> None:
        runner_progress._push_ws("t1", {"type": "x"})  # 不应抛异常

    def test_fn_exception_swallowed(self, caplog) -> None:
        """注册的推送 fn 抛异常时吞掉并记 warning（B1 可观测语义）。"""
        def _boom(task_id: str, payload: dict) -> None:
            raise RuntimeError("push boom")

        runner_progress.set_ws_push_fn(_boom)
        with caplog.at_level("WARNING", logger="api.runner_progress"):
            runner_progress._push_ws("t2", {"type": "y"})  # 不应抛异常
        assert any("ws push failed" in r.message for r in caplog.records)

    def test_set_ws_push_fn_routes_payload(self) -> None:
        """set_ws_push_fn 注册后 _push_ws 透传 task_id 与 payload。"""
        seen: list = []
        runner_progress.set_ws_push_fn(lambda tid, payload: seen.append((tid, payload)))
        runner_progress._push_ws("t3", {"type": "phase_changed", "phase": "init"})
        assert seen == [("t3", {"type": "phase_changed", "phase": "init"})]


class TestDbUpdateResilience:
    def _boom_repo(*args, **kwargs):
        raise RuntimeError("repo down")

    def test_heartbeat_failure_swallowed(self, tmp_path: Path) -> None:
        with patch("core.persistence.repository.evolution.update_heartbeat",
                   side_effect=RuntimeError("db down")):
            runner_progress.update_heartbeat(tmp_path, "t1")  # 不应抛异常

    def test_phase_failure_still_pushes_ws(self, tmp_path: Path) -> None:
        """repo 写 phase 失败：异常吞掉，但 WS 推送照发（解耦现状）。"""
        pushed: list = []
        runner_progress.set_ws_push_fn(lambda tid, payload: pushed.append(payload))
        with patch("core.persistence.repository.evolution.update_phase",
                   side_effect=RuntimeError("db down")):
            runner_progress.update_phase(tmp_path, "t2", "evolution_running")
        assert pushed and pushed[0]["type"] == "phase_changed"
        assert pushed[0]["phase"] == "evolution_running"

    def test_progress_failure_swallowed(self, tmp_path: Path) -> None:
        with patch("core.persistence.repository.evolution.update_progress",
                   side_effect=RuntimeError("db down")):
            runner_progress.update_progress(tmp_path, "t3", {"gen": 1})  # 不应抛异常

    def test_phase_success_updates_db_and_pushes(
        self, tmp_path: Path,
    ) -> None:
        """repo 正常路径：phase 落库（真实 sqlite）+ WS 推送。"""
        from core.persistence.db_ext import init_db_ext
        from core.persistence.db import save_task
        from core.persistence.db import get_task
        from tests.helpers.data_factory import make_dna
        db_path = tmp_path / "p.db"
        init_db_ext(db_path)
        save_task(
            db_path, task_id="t4", target_score=80.0, template="explorer",
            symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna(),
        )
        pushed: list = []
        runner_progress.set_ws_push_fn(lambda tid, payload: pushed.append(payload))
        runner_progress.update_phase(db_path, "t4", "data_loading")
        assert get_task(db_path, "t4")["current_phase"] == "data_loading"
        assert pushed[0]["type"] == "phase_changed"

    def test_progress_success_persists_json(self, tmp_path: Path) -> None:
        """progress 结构化 JSON 落库（真实 sqlite）。"""
        import json as _json
        from core.persistence.db_ext import init_db_ext
        from core.persistence.db import get_task
        from core.persistence.db import save_task
        from tests.helpers.data_factory import make_dna
        db_path = tmp_path / "p2.db"
        init_db_ext(db_path)
        save_task(
            db_path, task_id="t5", target_score=80.0, template="explorer",
            symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna(),
        )
        runner_progress.update_progress(db_path, "t5", {"gen": 3, "best": 1.5})
        row = get_task(db_path, "t5")
        assert _json.loads(row["progress_json"]) == {"gen": 3, "best": 1.5}
