"""第2步 BinanceDemoClient 单元测试（mock binance.Client，不连实盘）。"""
import time

import pytest

from core.live_trading import credentials as cred_mod
from core.live_trading.binance_client import (
    CredentialMissingError,
    MODE_ONEWAY,
    BinanceDemoClient,
    SymbolPrecision,
    _round_to_step,
    extract_precision,
)


class FakeFuturesClient:
    """模拟 binance.Client 的 futures 方法（测试隔离用）。"""

    def __init__(self, dual_side: bool = True, server_offset_ms: int = 0) -> None:
        self._dual_side = dual_side
        self._server_offset_ms = server_offset_ms
        self.change_calls: list[dict] = []

    def futures_time(self):
        return {"serverTime": int(time.time() * 1000) + self._server_offset_ms}

    def futures_get_position_mode(self):
        return {"dualSidePosition": self._dual_side}

    def futures_change_position_mode(self, **params):
        self.change_calls.append(params)
        self._dual_side = params["dualSidePosition"]
        return {}

    def futures_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }

    def futures_account(self):
        return {"assets": [{"asset": "USDT", "balance": "10000.0"}]}


class TestRoundToStep:
    def test_basic_round(self):
        assert _round_to_step(123.456, 0.1) == 123.5

    def test_zero_step_passthrough(self):
        assert _round_to_step(123.456, 0) == 123.456

    def test_rounds_down(self):
        assert _round_to_step(123.42, 0.1) == 123.4


class TestExtractPrecision:
    def test_btcusdt(self):
        info = FakeFuturesClient().futures_exchange_info()
        p = extract_precision(info, "BTCUSDT")
        assert p.tick_size == 0.10
        assert p.step_size == 0.001

    def test_unknown_symbol_raises(self):
        with pytest.raises(ValueError):
            extract_precision({"symbols": []}, "XYZUSDT")

    def test_round_helpers(self):
        p = SymbolPrecision(tick_size=0.1, step_size=0.001)
        assert p.round_price(123.456) == 123.5
        assert p.round_qty(1.23456) == 1.235


class TestTimeSync:
    def test_offset_computed_and_synced(self):
        c = BinanceDemoClient(client=FakeFuturesClient(server_offset_ms=500))
        offset = c.sync_time()
        assert 400 <= offset <= 600
        assert c.time_synced is True

    def test_large_offset_unsynced(self):
        c = BinanceDemoClient(client=FakeFuturesClient(server_offset_ms=5000))
        c.sync_time()
        assert c.time_synced is False


class TestPrecisionCache:
    def test_cached_returns_same(self):
        c = BinanceDemoClient(client=FakeFuturesClient())
        p1 = c.load_precision("BTCUSDT")
        p2 = c.load_precision("BTCUSDT")
        assert p1 is p2


class TestPositionMode:
    def test_already_one_way_no_change(self):
        fc = FakeFuturesClient(dual_side=False)
        c = BinanceDemoClient(client=fc)
        assert c.ensure_one_way_mode() is False
        assert fc.change_calls == []  # 不切换

    def test_switch_from_hedge(self):
        fc = FakeFuturesClient(dual_side=True)
        c = BinanceDemoClient(client=fc)
        assert c.ensure_one_way_mode() is False
        assert len(fc.change_calls) == 1
        assert fc.change_calls[0]["dualSidePosition"] is MODE_ONEWAY

    def test_get_mode_hedge(self):
        c = BinanceDemoClient(client=FakeFuturesClient(dual_side=True))
        assert c.get_position_mode() is True


class TestCredentialLoad:
    def test_missing_creds_raises(self, monkeypatch):
        # 无注入 client + 无凭证 → 报错（不连实盘）
        monkeypatch.setattr(cred_mod, "get_password", lambda *a, **k: None)
        with pytest.raises(CredentialMissingError):
            BinanceDemoClient()
