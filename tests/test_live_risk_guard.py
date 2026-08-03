"""第5步 RiskGuard 单元测试（下单前拦截，实盘生命线）。"""
import pytest

from core.live_trading.risk_guard import RiskConfig, RiskGuard, RiskViolation
from core.live_trading.types import OrderAction


@pytest.fixture
def guard() -> RiskGuard:
    return RiskGuard(
        config=RiskConfig(
            max_notional_per_order=1000.0,
            min_order_interval_sec=5.0,
            daily_loss_limit=500.0,
            max_margin_usage=0.5,
        )
    )


class TestNotionalCap:
    def test_open_within_cap_passes(self, guard):
        guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000)  # 500 notional
        # 不抛即通过

    def test_open_over_cap_rejected(self, guard):
        with pytest.raises(RiskViolation, match="max_notional"):
            guard.check_order(OrderAction.OPEN_LONG, quantity=0.1, price=50000)  # 5000 > 1000

    def test_close_not_checked(self, guard):
        # 平仓不受名义额限制（平仓是降风险）
        guard.check_order(OrderAction.CLOSE_LONG, quantity=100, price=50000)  # 巨额平仓 OK


class TestOrderInterval:
    def test_too_soon_rejected(self, guard):
        guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000, now_ts=1000.0)
        guard.record_order(now_ts=1000.0)
        with pytest.raises(RiskViolation, match="min_order_interval"):
            guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000, now_ts=1002.0)

    def test_after_interval_passes(self, guard):
        guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000, now_ts=1000.0)
        guard.record_order(now_ts=1000.0)
        guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000, now_ts=1006.0)

    def test_first_order_no_interval(self, guard):
        # 首单无上次时间戳，不检查间隔
        guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000)


class TestMarginUsage:
    def test_over_margin_rejected(self, guard):
        # equity=1000, used=0, open notional 800 → 80% > 50%
        with pytest.raises(RiskViolation, match="max_margin_usage"):
            guard.check_order(
                OrderAction.OPEN_LONG, quantity=0.016, price=50000,
                equity=1000.0, used_margin=0.0,
            )

    def test_within_margin_passes(self, guard):
        guard.check_order(
            OrderAction.OPEN_LONG, quantity=0.005, price=50000,  # 250 notional
            equity=1000.0, used_margin=0.0,
        )


class TestDailyLossHalt:
    def test_halt_after_loss_limit(self, guard):
        guard.record_pnl(-500.0)  # 触及 -500 熔断
        assert guard.is_halted() is True
        with pytest.raises(RiskViolation, match="daily_loss_limit"):
            guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000)

    def test_not_halted_below_limit(self, guard):
        guard.record_pnl(-499.99)
        assert guard.is_halted() is False
        guard.check_order(OrderAction.OPEN_LONG, quantity=0.01, price=50000)

    def test_halt_blocks_even_close(self, guard):
        # 熔断是最高优先级，平仓也拦（程序应停）
        guard.record_pnl(-600.0)
        with pytest.raises(RiskViolation, match="daily_loss_limit"):
            guard.check_order(OrderAction.CLOSE_LONG, quantity=0.1, price=50000)


class TestDailyRollover:
    def test_pnl_accumulates_intraday(self, guard):
        guard.record_pnl(-100.0)
        guard.record_pnl(-50.0)
        assert guard.daily_pnl == -150.0

    def test_rollover_resets(self, guard, monkeypatch):
        guard.record_pnl(-300.0)
        assert guard.daily_pnl == -300.0
        # 模拟跨天
        monkeypatch.setattr(
            "core.live_trading.risk_guard._today_utc", lambda: "2099-01-01"
        )
        assert guard.daily_pnl == 0.0
        assert guard.is_halted() is False
