
import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
"""第7步 LiveTradingRunner 单元测试（parse/sync/run_iteration/孤儿单）。"""
from pathlib import Path

import pytest

from core.persistence.repository import live_trading as persistence  # B3c.1 退役重锚定
from core.live_trading.runner import (
    LiveTradingRunner,
    klines_to_df,
    parse_account_state,
    parse_position_from_exchange,
)
from core.live_trading.types import LiveAccountState


class TestParsePosition:
    def test_long(self):
        p = parse_position_from_exchange(
            "BTCUSDT",
            {"positionAmt": "0.1", "entryPrice": "50000", "unRealizedProfit": "100", "initialMargin": "500"},
        )
        assert p is not None and p.is_long and p.quantity == 0.1

    def test_short(self):
        p = parse_position_from_exchange(
            "BTCUSDT",
            {"positionAmt": "-0.1", "entryPrice": "50000", "unRealizedProfit": "-50", "initialMargin": "500"},
        )
        assert p is not None and p.is_short

    def test_flat_none(self):
        assert parse_position_from_exchange("BTCUSDT", {"positionAmt": "0"}) is None

    def test_no_raw(self):
        assert parse_position_from_exchange("BTCUSDT", None) is None


class TestParseAccount:
    def test_state_no_position(self):
        account = {"totalWalletBalance": "10000", "assets": [{"asset": "USDT", "availableBalance": "9500"}]}
        s = parse_account_state("BTCUSDT", account, None)
        assert s.balance == 9500 and s.equity == 10000
        assert s.position is None

    def test_with_position(self):
        account = {"totalWalletBalance": "10000", "assets": [{"asset": "USDT", "balance": "9500"}]}
        pos = {"positionAmt": "0.1", "entryPrice": "50000", "unRealizedProfit": "100", "initialMargin": "500"}
        s = parse_account_state("BTCUSDT", account, pos, allowed_direction="long")
        assert s.position is not None and s.position.is_long
        assert s.allowed_direction == "long"


class TestKlinesToDf:
    def test_basic(self):
        klines = [
            [1000, "50000", "50100", "49900", "50050", "1.0", 2000, "50050", 10, "0.5", "25025", "0"],
            [2000, "50050", "50200", "50000", "50150", "1.5", 3000, "75000", 12, "0.7", "35000", "0"],
        ]
        df = klines_to_df(klines)
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df["close"].iloc[-1] == 50150.0

    def test_empty(self):
        assert klines_to_df([]).empty


def _make_runner(tmp_path, executor_action=None, position_raw=None):
    class FakeExecutor:
        def __init__(self):
            self.state = LiveAccountState(balance=10000, equity=10000)

        def execute_closed_bar(self, sig_set, bar_idx, close_price):
            return executor_action, None

    class FakeRaw:
        def futures_account(self):
            return {"totalWalletBalance": "10000", "assets": [{"asset": "USDT", "availableBalance": "10000"}]}

        def futures_position_information(self, symbol=None):
            return [{"symbol": "BTCUSDT", **position_raw}] if position_raw else []

        def futures_get_open_orders(self, symbol=None):
            return [{"orderId": 1}]

        def futures_klines(self, **p):
            return []

    class FakeClient:
        raw = FakeRaw()

    db = tmp_path / "t.db"
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db)
    persistence.save_live_trading_task(db, "t1", "BTCUSDT", "1h", "{}")
    runner = LiveTradingRunner(
        executor=FakeExecutor(), client=FakeClient(),
        symbol="BTCUSDT", timeframe="1h", dna=object(),
        db_path=db, task_id="t1",
    )
    return runner, db


class TestRunIteration:
    def test_processes_new_bar_and_persists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_trading.runner.dna_to_signal_set", lambda dna, df: object())
        runner, db = _make_runner(tmp_path, executor_action=None)
        klines = [[1000, "50000", "50100", "49900", "50050", "1.0", 2000, "x", 1, "x", "x", "0"]]
        result = runner.run_iteration(klines)
        assert result is not None  # 新 bar 被处理（即使 action=None）
        # 持久化 + 心跳
        row = persistence.get_live_trading_task(db, "t1")
        assert row["heartbeat_at"] is not None

    def test_dedup_same_bar(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.live_trading.runner.dna_to_signal_set", lambda dna, df: object())
        runner, _ = _make_runner(tmp_path)
        klines = [[1000, "50000", "50100", "49900", "50050", "1.0", 2000, "x", 1, "x", "x", "0"]]
        assert runner.run_iteration(klines) is not None
        assert runner.run_iteration(klines) is None  # 同 bar 去重

    def test_empty_klines(self, tmp_path):
        runner, _ = _make_runner(tmp_path)
        assert runner.run_iteration([]) is None

    def test_sync_state_updates_position(self, tmp_path):
        # sync_state 从交易所拉持仓 → 更新 executor.state
        runner, _ = _make_runner(
            tmp_path, position_raw={"positionAmt": "0.2", "entryPrice": "50000", "unRealizedProfit": "50", "initialMargin": "1000"}
        )
        runner.sync_state()
        assert runner.executor.state.position is not None
        assert runner.executor.state.position.is_long
        assert runner.executor.state.position.quantity == 0.2


class TestRecoverOrphan:
    def test_returns_open_orders(self, tmp_path):
        runner, _ = _make_runner(tmp_path)
        assert runner.recover_orphan_orders() == [{"orderId": 1}]
