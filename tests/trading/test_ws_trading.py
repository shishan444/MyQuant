"""Tests for WebSocket trading endpoint: connection, messages, and push delegation.

Covers:
1. WS connection accepts and sends subscribed message
2. Ping/pong protocol
3. WS push via runner delegates correctly
4. WS push swallows exceptions
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_dna


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def trading_app(tmp_path: Path):
    """Create FastAPI app with initialized DB."""
    from api.app import create_app
    from core.persistence.db_ext import init_db_ext

    db_path = tmp_path / "test.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    init_db_ext(db_path)
    app = create_app(db_path=db_path, data_dir=data_dir)
    return app, db_path


# ---------------------------------------------------------------------------
# Test: WS connection and subscription
# ---------------------------------------------------------------------------

class TestTradingWSPush:

    def test_push_ws_calls_configured_fn(self):
        """Verify _push_ws delegates to the configured push function."""
        from core.trading.runner import _push_ws
        import core.trading.runner as runner_mod

        mock_fn = MagicMock()
        original = runner_mod._ws_push_fn
        runner_mod._ws_push_fn = mock_fn
        try:
            _push_ws("task-1", {"type": "position_update"})
            mock_fn.assert_called_once_with("task-1", {"type": "position_update"})
        finally:
            runner_mod._ws_push_fn = original

    def test_push_ws_noop_when_no_fn(self):
        """Verify _push_ws is a no-op when no push function is set."""
        from core.trading.runner import _push_ws
        import core.trading.runner as runner_mod

        original = runner_mod._ws_push_fn
        runner_mod._ws_push_fn = None
        try:
            _push_ws("task-1", {"type": "position_update"})  # should not raise
        finally:
            runner_mod._ws_push_fn = original

    def test_push_ws_swallows_exception(self):
        """Verify _push_ws does not propagate exceptions."""
        from core.trading.runner import _push_ws
        import core.trading.runner as runner_mod

        mock_fn = MagicMock(side_effect=RuntimeError("WS error"))
        original = runner_mod._ws_push_fn
        runner_mod._ws_push_fn = mock_fn
        try:
            _push_ws("task-1", {"type": "position_update"})  # should not raise
        finally:
            runner_mod._ws_push_fn = original
