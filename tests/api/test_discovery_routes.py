"""Discovery 路由特征网（修复期安全网，现状记录语义）。

覆盖 api/routes/discovery.py 三端点（patterns/similar/predict）：
- 无数据 symbol：统一 {"error": "No data available"} 形制
- 有数据：真跑决策树/KNN 引擎的小样本路径，记录响应契约键
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_ohlcv


@pytest.fixture
def seeded_client(api_client: TestClient) -> TestClient:
    """写入足量行情（引擎需要 > horizon 的数据）。"""
    make_ohlcv(80, "4h").to_parquet(
        api_client.app.state.data_dir / "BTCUSDT_4h.parquet",
    )
    return api_client


class TestPatterns:
    def test_no_data_returns_error_dict(self, api_client: TestClient) -> None:
        """无数据：200 + error 字段 + 空 rules（不 404）。"""
        response = api_client.post(
            "/api/discovery/patterns",
            json={"symbol": "NOSUCH", "timeframe": "4h"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] == "No data available"
        assert data["rules"] == []

    def test_patterns_with_data(self, seeded_client: TestClient) -> None:
        """有数据：返回规则列表与模型元信息键。"""
        response = seeded_client.post(
            "/api/discovery/patterns",
            json={"symbol": "BTCUSDT", "timeframe": "4h", "horizon": 6,
                  "max_depth": 3},
        )
        assert response.status_code == 200
        data = response.json()
        for key in ("rules", "accuracy", "cv_scores", "feature_importance",
                    "tree_depth", "n_samples"):
            assert key in data


class TestSimilar:
    def test_no_data_returns_error_dict(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/api/discovery/similar", json={"symbol": "NOSUCH"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["error"] == "No data available"
        assert data["cases"] == []

    def test_similar_with_data(self, seeded_client: TestClient) -> None:
        """有数据：返回最近邻案例（index/timestamp/close/distance 等键）。"""
        response = seeded_client.post(
            "/api/discovery/similar",
            json={"symbol": "BTCUSDT", "timeframe": "4h", "horizon": 6,
                  "n_neighbors": 5},
        )
        assert response.status_code == 200
        cases = response.json()["cases"]
        assert 0 < len(cases) <= 5
        first = cases[0]
        for key in ("index", "timestamp", "close_price", "future_return_pct",
                    "distance"):
            assert key in first


class TestPredict:
    def test_no_data_returns_error_dict(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/api/discovery/predict", json={"symbol": "NOSUCH"},
        )
        assert response.status_code == 200
        assert response.json()["error"] == "No data available"

    def test_predict_with_data(self, seeded_client: TestClient) -> None:
        """有数据：返回方向/收益统计/区间与置信度键。"""
        response = seeded_client.post(
            "/api/discovery/predict",
            json={"symbol": "BTCUSDT", "timeframe": "4h", "horizon": 6,
                  "n_neighbors": 5},
        )
        assert response.status_code == 200
        data = response.json()
        for key in ("predicted_direction", "avg_return", "median_return",
                    "positive_pct", "price_range_low", "price_range_high",
                    "confidence", "n_cases"):
            assert key in data
