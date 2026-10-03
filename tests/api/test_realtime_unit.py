"""realtime（socket.io 服务端）特征网（修复期安全网，现状记录语义）。

覆盖 api/realtime.py：
- broadcast：无 loop 时安全 no-op；有 loop 时经 run_coroutine_threadsafe 调 sio.emit
- capture_loop：在运行中的事件循环内捕获主循环引用
- _join_by_query：connect 时按 query.task_id / session_id 入 room
- _cancel_stream：C→S cancel 事件转发 stream_control.request_cancel
- _emit_task_snapshot：连接即下发重连恢复快照（活跃任务才发）
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

from api import realtime


@pytest.fixture(autouse=True)
def _reset_loop_ref():
    """每个用例前后复位 _main_loop，避免跨用例状态泄漏。"""
    saved = realtime._main_loop
    realtime._main_loop = None
    yield
    realtime._main_loop = saved


class TestBroadcast:
    def test_broadcast_without_loop_is_noop(self) -> None:
        """socket.io 未就绪（无主循环引用）时 broadcast 安全 no-op。"""
        realtime.broadcast("/evolution", "room-1", "progress", {"x": 1})  # 不应抛异常

    def test_broadcast_with_loop_emits_to_room(self) -> None:
        """有主循环时 broadcast 在事件循环上调 sio.emit(namespace, room)。"""
        emitted = asyncio.Event()
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock(side_effect=lambda *a, **kw: emitted.set())

        async def _main() -> None:
            realtime.capture_loop()
            with patch.object(realtime, "sio", sio_mock):
                # 从另一个线程 broadcast（模拟 runner 线程跨线程 emit）
                t = threading.Thread(
                    target=realtime.broadcast,
                    args=("/evolution", "task-9", "generation_complete", {"best": 1.0}),
                )
                t.start()
                await asyncio.wait_for(emitted.wait(), timeout=5)
                t.join(timeout=5)

        asyncio.run(_main())
        sio_mock.emit.assert_awaited_once()
        args, kwargs = sio_mock.emit.await_args
        # emit(event, payload, namespace=..., room=...) 形制
        assert args[0] == "generation_complete"
        assert args[1] == {"best": 1.0}
        assert kwargs.get("namespace") == "/evolution"
        assert kwargs.get("room") == "task-9"


class TestJoinByQuery:
    def _run_with_sio_mock(self, environ: dict, namespace: str):
        sio_mock = MagicMock()
        realtime._join_by_query("sid-1", environ, namespace)
        return sio_mock

    def test_joins_room_by_task_id(self) -> None:
        """query 带 task_id 时按其入 room。"""
        with patch.object(realtime, "sio", MagicMock()) as sio_mock:
            realtime._join_by_query(
                "sid-1", {"QUERY_STRING": "task_id=task-42"}, "/evolution",
            )
        sio_mock.enter_room.assert_called_once_with(
            "sid-1", "task-42", namespace="/evolution",
        )

    def test_joins_room_by_session_id(self) -> None:
        """query 带 session_id 时按其入 room（流式批语义）。"""
        with patch.object(realtime, "sio", MagicMock()) as sio_mock:
            realtime._join_by_query(
                "sid-2", {"QUERY_STRING": "session_id=batch_abc"}, "/streams",
            )
        sio_mock.enter_room.assert_called_once_with(
            "sid-2", "batch_abc", namespace="/streams",
        )

    def test_task_id_takes_precedence(self) -> None:
        """同时带 task_id 与 session_id 时 task_id 优先（遍历顺序）。"""
        with patch.object(realtime, "sio", MagicMock()) as sio_mock:
            realtime._join_by_query(
                "sid-3",
                {"QUERY_STRING": "task_id=t1&session_id=s1"},
                "/evolution",
            )
        sio_mock.enter_room.assert_called_once_with(
            "sid-3", "t1", namespace="/evolution",
        )

    def test_no_query_no_room(self) -> None:
        """无 query 参数时不入任何 room。"""
        with patch.object(realtime, "sio", MagicMock()) as sio_mock:
            realtime._join_by_query("sid-4", {"QUERY_STRING": ""}, "/evolution")
            realtime._join_by_query("sid-4", {}, "/evolution")
        sio_mock.enter_room.assert_not_called()

    def test_empty_query_value_no_room(self) -> None:
        """query 值为空串时不入 room。"""
        with patch.object(realtime, "sio", MagicMock()) as sio_mock:
            realtime._join_by_query(
                "sid-5", {"QUERY_STRING": "task_id="}, "/evolution",
            )
        sio_mock.enter_room.assert_not_called()


class TestCancelStream:
    def test_cancel_forwards_session_id(self) -> None:
        """C→S cancel 事件携带 session_id 时转发协作取消。"""
        with patch("api.stream_control.request_cancel") as cancel_mock:
            asyncio.run(realtime._cancel_stream("sid", {"session_id": "batch_xyz"}))
        cancel_mock.assert_called_once_with("batch_xyz")

    def test_cancel_with_none_payload_forwards_none(self) -> None:
        """payload 为 None 时转发 None（request_cancel 侧 no-op）。"""
        with patch("api.stream_control.request_cancel") as cancel_mock:
            asyncio.run(realtime._cancel_stream("sid", None))
        cancel_mock.assert_called_once_with(None)


class TestBroadcastErrorContainment:
    def test_broadcast_on_closed_loop_does_not_propagate(self) -> None:
        """主循环已关闭时 broadcast 吞掉异常（不向调用方传播）。"""
        async def _capture_then_close() -> None:
            realtime.capture_loop()
        asyncio.run(_capture_then_close())  # loop 随 run 结束关闭
        # 已关闭 loop 上的调度失败被吞掉
        realtime.broadcast("/evolution", "r", "e", {"x": 1})


class TestConnectHandlers:
    """connect 薄壳：_join_by_query + _emit_task_snapshot 的组合转发。"""

    def test_connect_evolution_joins_and_snapshots(self, monkeypatch) -> None:
        monkeypatch.delenv("MYQUANT_DB_PATH", raising=False)
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._connect_evolution(
                "sid", {"QUERY_STRING": "task_id=t9"},  # type: ignore[arg-type]
            ))
        sio_mock.enter_room.assert_called_once_with(
            "sid", "t9", namespace="/evolution",
        )

    def test_connect_trading_joins_room(self, monkeypatch) -> None:
        monkeypatch.delenv("MYQUANT_DB_PATH", raising=False)
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._connect_trading(
                "sid", {"QUERY_STRING": "task_id=pt-9"},  # type: ignore[arg-type]
            ))
        sio_mock.enter_room.assert_called_once_with(
            "sid", "pt-9", namespace="/trading",
        )

    def test_connect_streams_joins_room(self) -> None:
        sio_mock = MagicMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._connect_streams(
                "sid", {"QUERY_STRING": "session_id=batch_1"},  # type: ignore[arg-type]
            ))
        sio_mock.enter_room.assert_called_once_with(
            "sid", "batch_1", namespace="/streams",
        )


class TestEmitTaskSnapshot:
    def _make_db_with_evolution_task(self, tmp_path: Path, status: str) -> Path:
        from core.persistence.db_ext import init_db_ext
        from core.persistence.db import save_task, update_task
        from tests.helpers.data_factory import make_dna
        db_path = tmp_path / "snap.db"
        init_db_ext(db_path)
        save_task(
            db_path, task_id="evo-1", target_score=80.0, template="explorer",
            symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna(),
        )
        update_task(db_path, "evo-1", status=status)
        return db_path

    def test_no_task_id_returns_early(self, monkeypatch) -> None:
        """连接未带 task_id 时不发快照。"""
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        monkeypatch.setenv("MYQUANT_DB_PATH", "/nonexistent/x.db")
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": ""}, "/evolution",
            ))
        sio_mock.emit.assert_not_awaited()

    def test_no_db_env_returns_early(self, monkeypatch, tmp_path: Path) -> None:
        """MYQUANT_DB_PATH 未设置（测试上下文无库）时跳过快照。"""
        monkeypatch.delenv("MYQUANT_DB_PATH", raising=False)
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=evo-1"}, "/evolution",
            ))
        sio_mock.emit.assert_not_awaited()

    def test_active_evolution_task_emits_snapshot(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """running 进化任务连接时下发 task_snapshot（B8.4 重连恢复）。"""
        db_path = self._make_db_with_evolution_task(tmp_path, "running")
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=evo-1"}, "/evolution",
            ))
        sio_mock.emit.assert_awaited_once()
        args, kwargs = sio_mock.emit.await_args
        event, payload = args[0], args[1]
        assert event == "task_snapshot"
        assert payload["task_id"] == "evo-1"
        assert payload["status"] == "running"
        assert payload["type"] == "task_snapshot"
        assert kwargs.get("namespace") == "/evolution"
        assert kwargs.get("room") == "evo-1"

    def test_finished_evolution_task_no_snapshot(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """已完成（非活跃态）任务连接时不发快照。"""
        db_path = self._make_db_with_evolution_task(tmp_path, "completed")
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=evo-1"}, "/evolution",
            ))
        sio_mock.emit.assert_not_awaited()

    def test_active_trading_task_emits_snapshot(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """running 模拟交易任务在 /trading namespace 下发快照。"""
        from tests.helpers.data_factory import make_dna
        from core.persistence.db_ext import (
            init_db_ext, save_paper_trading_task, update_paper_trading_task,
        )
        db_path = tmp_path / "snap2.db"
        init_db_ext(db_path)
        dna_json = make_dna().to_json()
        save_paper_trading_task(
            db_path, task_id="pt-1", dna_json=dna_json,
            symbol="BTCUSDT", timeframe="4h",
        )
        update_paper_trading_task(db_path, "pt-1", status="running")
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=pt-1"}, "/trading",
            ))
        sio_mock.emit.assert_awaited_once()
        event, payload = sio_mock.emit.await_args[0][0], sio_mock.emit.await_args[0][1]
        assert event == "task_snapshot"
        assert payload["task_id"] == "pt-1"
        assert payload["type"] == "task_snapshot"

    def test_unknown_task_no_snapshot(self, monkeypatch, tmp_path: Path) -> None:
        """task_id 在库中不存在时不发快照。"""
        db_path = self._make_db_with_evolution_task(tmp_path, "running")
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=missing"}, "/evolution",
            ))
        sio_mock.emit.assert_not_awaited()

    def test_unknown_trading_task_no_snapshot(self, monkeypatch, tmp_path: Path) -> None:
        """/trading namespace 下任务不存在时不发快照。"""
        db_path = self._make_db_with_evolution_task(tmp_path, "running")
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=missing-pt"}, "/trading",
            ))
        sio_mock.emit.assert_not_awaited()
