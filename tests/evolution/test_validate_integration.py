"""Integration tests for validate API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

pytestmark = [pytest.mark.integration]
from fastapi.testclient import TestClient

from api.app import create_app

# ── Fixtures ──

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"

@pytest.fixture
def client(db_path: Path, tmp_data_dir: Path):
    app = create_app(db_path=db_path, data_dir=tmp_data_dir)
    with TestClient(app) as c:
        yield c

# ── Validate API ──

class TestValidateAPI:
    """Tests for POST /api/validate endpoint."""

    def test_validate_with_no_data(self, client):
        """Should return empty result when no data files exist."""
        resp = client.post("/api/validate", json={
            "pair": "BTCUSDT",
            "timeframe": "4h",
            "start": "2024-01-01",
            "end": "2024-06-01",
            "when": [{"subject": "rsi_14", "action": "cross_above", "target": "70", "logic": "AND"}],
            "then": [{"subject": "close", "action": "drops_pct", "target": "2", "logic": "AND"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "match_rate" in data
        assert "total_count" in data
        assert data["total_count"] == 0

    def test_validate_accepts_base_timeframe(self, client):
        """Should accept base_timeframe field without error."""
        resp = client.post("/api/validate", json={
            "pair": "BTCUSDT",
            "timeframe": "15m",
            "base_timeframe": "15m",
            "start": "2024-01-01",
            "end": "2024-06-01",
            "when": [{"subject": "close", "action": "rises_pct", "target": "1", "logic": "AND"}],
            "then": [{"subject": "close", "action": "drops_pct", "target": "1", "logic": "AND"}],
        })
        assert resp.status_code == 200

    def test_validate_condition_with_timeframe(self, client):
        """Should accept timeframe field in conditions without error."""
        resp = client.post("/api/validate", json={
            "pair": "BTCUSDT",
            "timeframe": "15m",
            "base_timeframe": "15m",
            "start": "2024-01-01",
            "end": "2024-06-01",
            "when": [{"subject": "close", "action": "rises_pct", "target": "1", "logic": "AND", "timeframe": "4h"}],
            "then": [{"subject": "close", "action": "drops_pct", "target": "1", "logic": "AND"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "warnings" in data

    def test_validate_missing_required_fields(self, client):
        """Should return 422 for missing required fields."""
        resp = client.post("/api/validate", json={
            "pair": "BTCUSDT",
            # missing timeframe, start, end, when, then
        })
        assert resp.status_code == 422

    def test_validate_triggers_endpoint(self, client):
        """GET /api/validate/{task_id}/triggers should return paginated structure."""
        resp = client.get("/api/validate/test-task/triggers", params={"page": 1, "per_page": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "records" in data

    def test_validate_mtf_fallback_returns_warnings(self, client, tmp_data_dir):
        """Regression: MTF data loading failure should produce warnings, not crash.

        Before fix, warnings.append() was called before warnings was defined,
        causing NameError on MTF loading failure.
        """
        import pandas as pd
        # Create a minimal parquet file so data loading doesn't short-circuit
        df = pd.DataFrame({
            "open": [100.0, 101.0, 102.0],
            "high": [105.0, 106.0, 107.0],
            "low": [95.0, 96.0, 97.0],
            "close": [103.0, 104.0, 105.0],
            "volume": [1000.0, 1100.0, 1200.0],
        }, index=pd.date_range("2024-01-01", periods=3, freq="4h", tz="UTC"))
        df.index.name = "timestamp"
        df.to_parquet(tmp_data_dir / "BTCUSDT_4h.parquet")

        resp = client.post("/api/validate", json={
            "pair": "BTCUSDT",
            "timeframe": "4h",
            "base_timeframe": "4h",
            "start": "2024-01-01",
            "end": "2024-06-01",
            "when": [{"subject": "close", "action": "rises_pct", "target": "1", "logic": "AND", "timeframe": "1d"}],
            "then": [{"subject": "close", "action": "drops_pct", "target": "1", "logic": "AND"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        # Should return a valid result (not crash with NameError)
        assert "match_rate" in data
        # Should have a warning about MTF fallback (1d data doesn't exist)
        assert "warnings" in data
