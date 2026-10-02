"""stream_control 特征网（修复期安全网，现状记录语义）。

覆盖 api/stream_control.py（B8.2/B8.0 语义点① 协作取消注册表）：
- register 后 request_cancel 触发回调
- 未注册 / 已注销 / 空 session_id 时 no-op
- 回调抛异常不向调用方传播（吞掉 + warning 日志）
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from api import stream_control


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空注册表，避免跨用例泄漏。"""
    stream_control._cancel_callbacks.clear()
    yield
    stream_control._cancel_callbacks.clear()


class TestRegisterAndCancel:
    def test_registered_callback_is_invoked(self) -> None:
        """注册后 request_cancel 调用对应回调。"""
        called = []
        stream_control.register("sess-1", lambda: called.append(True))
        stream_control.request_cancel("sess-1")
        assert called == [True]

    def test_request_cancel_unregistered_is_noop(self) -> None:
        """未注册的 session（任务未在跑/已完成）取消为 no-op，不抛异常。"""
        stream_control.request_cancel("no-such-session")  # 不应抛异常

    def test_request_cancel_none_or_empty_is_noop(self) -> None:
        """None / 空串 session_id 直接 no-op。"""
        stream_control.request_cancel(None)
        stream_control.request_cancel("")

    def test_unregister_stops_cancellation(self) -> None:
        """注销后（任务完成/失败）再取消不再触发回调。"""
        called = []
        stream_control.register("sess-2", lambda: called.append(True))
        stream_control.unregister("sess-2")
        stream_control.request_cancel("sess-2")
        assert called == []

    def test_unregister_unknown_session_is_noop(self) -> None:
        """注销不存在的 session_id 不抛异常（pop 默认值语义）。"""
        stream_control.unregister("never-registered")

    def test_register_overwrites_previous_callback(self) -> None:
        """同 session_id 重复注册时后注册的回调生效。"""
        calls: list[str] = []
        stream_control.register("sess-3", lambda: calls.append("first"))
        stream_control.register("sess-3", lambda: calls.append("second"))
        stream_control.request_cancel("sess-3")
        assert calls == ["second"]


class TestCancelErrorContainment:
    def test_callback_exception_does_not_propagate(self, caplog) -> None:
        """回调自身抛异常时 request_cancel 不传播（吞掉并记 warning）。"""
        def _boom() -> None:
            raise RuntimeError("cancel hook failed")

        stream_control.register("sess-err", _boom)
        # 不应抛出 RuntimeError
        stream_control.request_cancel("sess-err")

    def test_callback_exception_logged_as_warning(self, caplog) -> None:
        """异常被吞掉时有 warning 日志可观测（B1 系语义：静默失败必须可见）。"""
        def _boom() -> None:
            raise RuntimeError("cancel hook failed")

        stream_control.register("sess-err2", _boom)
        with caplog.at_level("WARNING", logger="api.stream_control"):
            stream_control.request_cancel("sess-err2")
        assert any("stream cancel failed" in r.message for r in caplog.records)
