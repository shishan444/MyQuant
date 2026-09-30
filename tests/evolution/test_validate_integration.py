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
            "when": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "cross_above", "target": "70", "logic": "AND"}],
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
        """0.2.14 语义翻转：条件带 timeframe 键且该周期无数据——显式
        结构化拒绝（RESEARCH_SYMBOL_NOT_FOUND），不再静默降级按基准
        周期求值（timeframe 键静默忽略为既有缺陷，本变更收口）。

        原 NameError 回归语境（warnings 未定义）由拒绝路径前置覆盖。
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
        assert resp.status_code == 422
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_SYMBOL_NOT_FOUND"
        assert body["detail"]["timeframe"] == "1d"


class TestValidateMtfEffective:
    """0.2.14 Lab 链 timeframe 生效锚（双来源披露：前视消除+键从静默
    忽略到真生效）+ 形成期窗口差分锚。"""

    def _setup(self, tmp_data_dir):
        import pandas as pd
        from tests.helpers.data_factory import make_ohlcv
        base = make_ohlcv(n=800, freq="15min", start="2024-01-01", seed=11)
        high = make_ohlcv(n=60, freq="4h", start="2024-01-01", seed=11)
        base.to_parquet(tmp_data_dir / "BTCUSDT_15m.parquet")
        high.to_parquet(tmp_data_dir / "BTCUSDT_4h.parquet")
        return base, high

    def _post(self, client, when, end):
        return client.post("/api/validate", json={
            "pair": "BTCUSDT", "timeframe": "15m", "base_timeframe": "15m",
            "start": "2024-01-01", "end": end,
            "when": when,
            "then": [{"subject": "close", "action": "gt", "target": "0"}],
        })

    def test_timeframe_key_now_effective(self, client, tmp_data_dir):
        """带 timeframe 4h ≠ 忽略键按 15m 求值（旧静默忽略缺陷修复的
        生效锚）：阈值取 15m close 最大值之上——无键恒无触发、带 4h
        键有触发（4h close 分布不同）。"""
        import numpy as np
        base, high = self._setup(tmp_data_dir)
        x = float(np.max(base["close"])) * 2.0      # 15m 恒 False
        cond_plain = {"subject": "close", "action": "gt",
                      "target": str(x), "logic": "AND"}
        cond_tf = {**cond_plain, "timeframe": "4h"}
        # 4h 阈值取 4h 最小 close 之下（对照分离：带键必命中）
        r_plain = self._post(client, [cond_plain], "2024-01-30")
        assert r_plain.status_code == 200
        assert r_plain.json()["total_count"] == 0
        cond_tf2 = {"subject": "close", "action": "gt",
                    "target": str(float(np.min(high["close"])) - 1),
                    "logic": "AND", "timeframe": "4h"}
        r_tf = self._post(client, [cond_tf2], "2024-01-30")
        assert r_tf.status_code == 200
        assert r_tf.json()["total_count"] > 0       # 4h 键真实生效

    def test_forming_period_differential(self, client, tmp_data_dir):
        """形成期窗口差分锚：改 end 时刻所处 4h 形成期 bar 的值，
        触发集不变（该 bar 对 end 前的 15m bar 不可见）。"""
        import numpy as np
        import pandas as pd
        base, high = self._setup(tmp_data_dir)
        end_ts = pd.Timestamp("2024-01-08 02:00", tz="UTC")
        end = end_ts.isoformat()
        cond = {"subject": "close", "action": "gt",
                "target": str(float(np.median(high["close"]))),
                "logic": "AND", "timeframe": "4h"}
        r1 = self._post(client, [cond], end)
        assert r1.status_code == 200
        # 改 end 所处 4h 形成期 bar（开盘 00:00、收盘 04:00 > end）
        forming = high[high.index <= end_ts].index[-1]
        assert forming + pd.Timedelta(hours=4) > end_ts   # 确系形成中
        high2 = high.copy()
        high2.loc[forming, "close"] = float(np.max(high["close"])) * 100
        high2.to_parquet(tmp_data_dir / "BTCUSDT_4h.parquet")
        r2 = self._post(client, [cond], end)
        assert r2.status_code == 200
        assert r1.json()["total_count"] == r2.json()["total_count"]
        t1 = [t["time"] for t in r1.json()["triggers"]]
        t2 = [t["time"] for t in r2.json()["triggers"]]
        assert t1 == t2
