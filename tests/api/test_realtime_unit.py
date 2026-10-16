"""realtime（socket.io 服务端）特征网（修复期安全网，现状记录语义）。

覆盖 api/realtime.py：
- broadcast：无 loop 时安全 no-op；有 loop 时经 run_coroutine_threadsafe 调 sio.emit
- capture_loop：在运行中的事件循环内捕获主循环引用
- _join_by_query：connect 时按 query.task_id / session_id 入 room
- _cancel_stream：C→S cancel 事件转发 stream_control.request_cancel
- _emit_task_snapshot：连接即下发重连恢复快照（活跃任务才发）；
  /trading 分支读纸盘新表 pt_task（rebuild-paper-trading 5.2 新 schema）
- 纸盘七事件经 broadcast 桥接（design §7.2 契约键集+时间字段原样透传）
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

    def _make_db_with_paper_task(self, tmp_path: Path, status: str, *,
                                 with_account: bool = False,
                                 with_position: bool = False) -> Path:
        """新表构造（rebuild-paper-trading 5.2）：仓库直建 pt_task 行
        +状态迁移；with_account 时经 append_bar 写账户组（balance/
        unrealized_pnl/total_trades——append_bar 是账户组唯一写面）；
        with_position 时叠加持仓组核心字段（含 position_margin——P0
        权益修复联动的快照派生判据）。"""
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import paper

        db_path = tmp_path / "paper_snap.db"
        init_db_ext(db_path)
        paper.create_task(
            db_path, task_id="pt-1", strategy_id="st-1",
            dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        if status != "pending":
            paper.update_task(db_path, "pt-1", status="running",
                              started_at="2026-10-01T00:00:00+00:00")
        if with_account:
            # 账户组写入须在 running 态窗口（CARD-06 后 append_bar 的
            # task_state UPDATE 附 WHERE status='running'——终态后写
            # 被冻结），故先 running 写账户组再迁终态
            task_state: dict = {"balance": 100_000.0,
                                "unrealized_pnl": 50.0,
                                "total_trades": 3}
            if with_position:
                task_state.update({
                    "position_side": "long", "position_entry": 100.0,
                    "position_quantity": 1.0, "position_margin": 800.0,
                    "position_open_cost": 100.0,
                    "position_trail_peak": 105.0,
                    "position_entry_bar_seq": 5})
            paper.append_bar(
                db_path, "pt-1", bar_time="2026-10-01T00:00:00+00:00",
                fills=[],
                equity={"equity": 100_050.0, "balance": 100_000.0,
                        "unrealized_pnl": 50.0, "position_side": "long"},
                task_state=task_state)
        if status == "completed":
            # completed 前态仅 running（缺陷⑤守门拒 pending 直迁）——
            # 合法两跳构造（账户组已在 running 窗口落库）
            paper.update_task(db_path, "pt-1", status="completed",
                              stopped_at="2026-10-01T00:00:00+00:00")
        return db_path

    def test_active_paper_task_emits_new_schema_snapshot(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """running 纸盘任务在 /trading namespace 下发新 schema 快照
        （design §7.2）。空仓分支锚：持仓组 NULL → equity=balance+
        unrealized_pnl=100_050.0（margin 项缺省 0）。"""
        db_path = self._make_db_with_paper_task(
            tmp_path, "running", with_account=True)
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
        assert payload == {
            "type": "task_snapshot", "task_id": "pt-1", "status": "running",
            "equity": 100_050.0, "balance": 100_000.0, "total_trades": 3}

    def test_holding_paper_task_equity_includes_margin(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """持仓形态权益派生（P0 权益修复联动锚）：balance 开仓已扣
        margin——equity=balance+position_margin+unrealized_pnl（与
        AccountSnapshot.equity 逐仓口径同式），手算锚 100_000+800+50
        =100_850.0（修复前漏 margin 项会得 100_050.0）。"""
        db_path = self._make_db_with_paper_task(
            tmp_path, "running", with_account=True, with_position=True)
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=pt-1"}, "/trading",
            ))
        payload = sio_mock.emit.await_args[0][1]
        assert payload["status"] == "running"
        assert payload["balance"] == 100_000.0
        assert payload["equity"] == 100_850.0

    def test_pending_paper_task_snapshot_null_account(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """pending 任务（账户组 NULL）快照 equity/balance=None——未运行
        无账户事实，None 透传非 0 假值。"""
        db_path = self._make_db_with_paper_task(tmp_path, "pending")
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=pt-1"}, "/trading",
            ))
        payload = sio_mock.emit.await_args[0][1]
        assert payload["status"] == "pending"
        assert payload["equity"] is None
        assert payload["balance"] is None

    def test_terminal_paper_task_still_emits_snapshot(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """终态任务连接仍发快照（五态 status 自解释——重连端可感知
        任务已结束与最终统计）。"""
        db_path = self._make_db_with_paper_task(
            tmp_path, "completed", with_account=True)
        monkeypatch.setenv("MYQUANT_DB_PATH", str(db_path))
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock()
        with patch.object(realtime, "sio", sio_mock):
            asyncio.run(realtime._emit_task_snapshot(
                "sid", {"QUERY_STRING": "task_id=pt-1"}, "/trading",
            ))
        payload = sio_mock.emit.await_args[0][1]
        assert payload["status"] == "completed"
        assert payload["total_trades"] == 3

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


class TestPaperEventChannel:
    """纸盘七事件经 broadcast 桥接（rebuild-paper-trading 5.2）。

    payload 样本=执行器 push 形制（core/paper_trading/runtime/
    executor.py 各 _push 调用点+connect 快照）；键集断言锚 design
    §7.2 契约（样本缺字段或多字段必红）；时间字段字符串原样透传
    （B8.0 语义点④——broadcast 不做任何 Timestamp 转换）。
    """

    # design §7.2 七事件契约键集（源头定义，前端 contract.md+zod 对齐冻结）
    CONTRACT_KEYS = {
        "task_started": {"type", "task_id"},
        "task_update": {"type", "task_id", "status", "equity", "balance",
                        "unrealized_pnl", "bars_processed", "window_bars"},
        "fill_event": {"type", "task_id", "bar_time", "seq", "kind", "side",
                       "price", "quantity", "fee", "realized_pnl", "reason"},
        "task_completed": {"type", "task_id", "final_equity", "total_trades"},
        "task_stopped": {"type", "task_id", "stop_reason"},
        "task_error": {"type", "task_id", "error_detail"},
        "task_snapshot": {"type", "task_id", "status", "equity", "balance",
                          "total_trades"},
    }

    EVENTS = [
        {"type": "task_started", "task_id": "pt-1"},
        {"type": "task_update", "task_id": "pt-1", "status": "running",
         "equity": 100_050.0, "balance": 100_000.0, "unrealized_pnl": 50.0,
         "bars_processed": 12, "window_bars": 180},
        {"type": "fill_event", "task_id": "pt-1",
         "bar_time": "2026-10-01T04:00:00+00:00", "seq": 4, "kind": "open",
         "side": "long", "price": 100.0, "quantity": 1.0, "fee": 0.1,
         "realized_pnl": None, "reason": "signal"},
        {"type": "task_completed", "task_id": "pt-1",
         "final_equity": 101_000.0, "total_trades": 5},
        {"type": "task_stopped", "task_id": "pt-1", "stop_reason": "user_stop"},
        {"type": "task_error", "task_id": "pt-1", "error_detail": "no_data"},
        {"type": "task_snapshot", "task_id": "pt-1", "status": "running",
         "equity": 100_050.0, "balance": 100_000.0, "total_trades": 3},
    ]

    def test_event_samples_carry_contract_keys(self) -> None:
        """七事件样本键集 == design §7.2 契约键集（逐事件精确断言）。"""
        for payload in self.EVENTS:
            assert set(payload) == self.CONTRACT_KEYS[payload["type"]], \
                f"{payload['type']} 样本键集偏离契约"

    def test_seven_events_relayed_via_broadcast(self) -> None:
        """七事件经 broadcast 定向 emit：事件名=type、payload 原样
        （含 bar_time 时间字段字符串原样透传）、room=task_id。"""
        received: list = []
        sio_mock = MagicMock()
        sio_mock.emit = AsyncMock(
            side_effect=lambda *a, **kw: received.append((a, kw)))

        async def _main() -> None:
            realtime.capture_loop()
            with patch.object(realtime, "sio", sio_mock):
                for i, payload in enumerate(self.EVENTS):
                    # 执行器线程侧形制：跨线程 broadcast（run_coroutine_
                    # threadsafe 调度到主循环）
                    t = threading.Thread(
                        target=realtime.broadcast,
                        args=("/trading", payload["task_id"],
                              payload["type"], payload))
                    t.start()
                    t.join(timeout=5)
                    # 等主循环消化该次调度（emit 协程在 loop 上执行）
                    for _ in range(200):
                        if len(received) >= i + 1:
                            break
                        await asyncio.sleep(0.01)

        asyncio.run(_main())
        assert len(received) == 7
        for (args, kwargs), payload in zip(received, self.EVENTS):
            assert args[0] == payload["type"]
            assert args[1] is payload          # payload 原样引用透传
            assert kwargs.get("namespace") == "/trading"
            assert kwargs.get("room") == payload["task_id"]
        # 时间字段字符串原样（fill_event.bar_time 不被转换改形）
        fill_payload = received[2][0][1]
        assert fill_payload["bar_time"] == "2026-10-01T04:00:00+00:00"
        assert isinstance(fill_payload["bar_time"], str)


class TestConnectAuthRejection:
    """三命名空间 connect 的 auth 拒绝分支（闸门盲区消解——A2
    认证在 handler 层的接线；函数层 check_socket_auth 已由
    test_auth_middleware 覆盖）。"""

    def test_connect_rejected_without_token_when_key_set(
            self, monkeypatch):
        """API key 设置态下无 token connect：三命名空间均 False。"""
        import asyncio

        import api.realtime as realtime

        monkeypatch.setenv("MYQUANT_API_KEY", "secret-1")
        environ = {"QUERY_STRING": ""}  # 无 token
        for handler in (realtime._connect_evolution,
                        realtime._connect_trading,
                        realtime._connect_streams):
            assert asyncio.run(handler("sid-x", environ)) is False
