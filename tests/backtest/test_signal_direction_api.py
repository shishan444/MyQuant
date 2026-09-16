"""Tests for backtest signal direction mapping in API responses.

Verifies that the backtest endpoint returns correct buy/sell signal types
matching the DNA direction:
- long: entry="buy", exit="sell"
- short: entry="sell", exit="buy"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.integration]

import pandas as pd
import numpy as np
from fastapi.testclient import TestClient

from MyQuant.api.app import create_app


# ── Fixtures ──
# db_path inherited from conftest.py

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with a synthetic parquet file."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    np.random.seed(42)
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)

    df = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    df.to_parquet(data_dir / "BTCUSDT_4h.parquet")
    return data_dir


@pytest.fixture
def client(db_path: Path, tmp_data_dir: Path):
    app = create_app(db_path=db_path, data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c


def _dna_dict(direction: str = "long") -> Dict[str, Any]:
    """Build a valid DNA dict with configurable direction."""
    return {
        "signal_genes": [
            {
                "indicator": "RSI",
                "params": {"period": 14},
                "role": "entry_trigger",
                "field": None,
                "condition": {"type": "lt", "threshold": 30},
            },
            {
                "indicator": "RSI",
                "params": {"period": 14},
                "role": "exit_trigger",
                "field": None,
                "condition": {"type": "gt", "threshold": 70},
            },
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {
            "stop_loss": 0.05,
            "take_profit": 0.10,
            "position_size": 0.5,
            "leverage": 1,
            "direction": direction,
        },
    }


# ── Tests ──

class TestLongDirectionSignals:
    """Long direction: entry should be "buy", exit should be "sell"."""

    def test_long_entry_is_buy(self, client: TestClient) -> None:
        """Long direction trades should have entry type "buy"."""
        payload = {
            "dna": _dna_dict(direction="long"),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        }
        resp = client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        signals = data.get("signals", [])

        if len(signals) >= 2:
            entry = signals[0]
            assert entry["type"] == "buy", (
                f"Long entry signal should be 'buy', got '{entry['type']}'"
            )

    def test_long_exit_is_sell(self, client: TestClient) -> None:
        """Long direction trades should have exit type "sell"."""
        payload = {
            "dna": _dna_dict(direction="long"),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        }
        resp = client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        signals = data.get("signals", [])

        if len(signals) >= 2:
            exit_signal = signals[1]
            assert exit_signal["type"] == "sell", (
                f"Long exit signal should be 'sell', got '{exit_signal['type']}'"
            )


class TestShortDirectionSignals:
    """Short direction: entry should be "sell", exit should be "buy"."""

    def test_short_entry_is_sell(self, client: TestClient) -> None:
        """Short direction trades should have entry type "sell"."""
        payload = {
            "dna": _dna_dict(direction="short"),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        }
        resp = client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        signals = data.get("signals", [])

        if len(signals) >= 2:
            entry = signals[0]
            assert entry["type"] == "sell", (
                f"Short entry signal should be 'sell', got '{entry['type']}'"
            )

    def test_short_exit_is_buy(self, client: TestClient) -> None:
        """Short direction trades should have exit type "buy"."""
        payload = {
            "dna": _dna_dict(direction="short"),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        }
        resp = client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        signals = data.get("signals", [])

        if len(signals) >= 2:
            exit_signal = signals[1]
            assert exit_signal["type"] == "buy", (
                f"Short exit signal should be 'buy', got '{exit_signal['type']}'"
            )


class TestSignalAlternation:
    """Signals should alternate: entry, exit, entry, exit..."""

    def test_long_signals_alternate(self, client: TestClient) -> None:
        """Long signals should alternate: buy, sell, buy, sell..."""
        payload = {
            "dna": _dna_dict(direction="long"),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        }
        resp = client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 200
        signals = resp.json().get("signals", [])

        for i in range(0, len(signals) - 1, 2):
            assert signals[i]["type"] == "buy", (
                f"Signal pair {i//2}: entry should be 'buy', got '{signals[i]['type']}'"
            )
            if i + 1 < len(signals):
                assert signals[i + 1]["type"] == "sell", (
                    f"Signal pair {i//2}: exit should be 'sell', got '{signals[i+1]['type']}'"
                )

    def test_short_signals_alternate(self, client: TestClient) -> None:
        """Short signals should alternate: sell, buy, sell, buy..."""
        payload = {
            "dna": _dna_dict(direction="short"),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
        }
        resp = client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 200
        signals = resp.json().get("signals", [])

        for i in range(0, len(signals) - 1, 2):
            assert signals[i]["type"] == "sell", (
                f"Signal pair {i//2}: entry should be 'sell', got '{signals[i]['type']}'"
            )
            if i + 1 < len(signals):
                assert signals[i + 1]["type"] == "buy", (
                    f"Signal pair {i//2}: exit should be 'buy', got '{signals[i+1]['type']}'"
                )
