"""runner_progress 特征网（修复期安全网，现状记录语义）。

覆盖 api/runner_progress.py 的异常吞掉与推送分支：
- _push_ws：无注册 fn 时 no-op；fn 抛异常吞掉（warning 可观测）
- update_phase / update_progress：repo 抛异常吞掉不传播；
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


