"""limit/page 参数上限特征网（CARD-09 修复锚定，修复期安全网）。

锚定各路由文件 Query 约束后的行为（现状=修复后行为）：
- 列表类上限 500、ohlcv 类上限 50000；超限 → 422
- 下界：limit/page ge=1（0 与负值 → 422）、offset ge=0（负值 → 422）
- 恰等上限放行（le 含等号）；默认值路径不变
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


class TestUpperBound422:
    """超上限 → 422（校验先于业务路径）。"""

    @pytest.mark.parametrize(("url", "param", "value"), [
        pytest.param("/api/data/datasets", "limit", 501, id="datasets 列表 501"),
        pytest.param("/api/data/datasets/ds-x/preview", "limit", 501, id="preview 501"),
        pytest.param("/api/data/datasets/ds-x/ohlcv", "limit", 50001, id="dataset ohlcv 50001"),
        pytest.param("/api/data/ohlcv/BTCUSDT/4h", "limit", 50001, id="by-symbol ohlcv 50001"),
        pytest.param("/api/data/chart-indicators/BTCUSDT/4h", "limit", 50001, id="chart-indicators 50001"),
        pytest.param("/api/evolution/strategies", "limit", 501, id="evolution strategies 501"),
        pytest.param("/api/evolution/tasks/tx/discovered-strategies", "limit", 501, id="discovered 501"),
        pytest.param("/api/strategies", "limit", 501, id="strategies 501"),
        pytest.param("/api/strategies/verify/history", "limit", 501, id="verify history 501"),
        pytest.param("/api/strategies/verify/sessions", "limit", 501, id="verify sessions 501"),
    ])
    def test_over_limit_422(self, api_client: TestClient, url, param, value) -> None:
        response = api_client.get(url, params={param: value})
        assert response.status_code == 422, f"{url}?{param}={value}"

    @pytest.mark.parametrize(("url", "params"), [
        pytest.param("/api/evolution/tasks", {"page": 0}, id="page=0"),
        pytest.param("/api/evolution/tasks", {"page_size": 501}, id="page_size=501"),
        pytest.param("/api/strategies", {"offset": -5}, id="strategies offset=-5"),
        pytest.param("/api/data/datasets", {"limit": 0}, id="limit=0 下界拒绝"),
        pytest.param("/api/data/datasets", {"limit": -1}, id="limit=-1"),
    ])
    def test_lower_bound_422(self, api_client: TestClient, url, params) -> None:
        response = api_client.get(url, params=params)
        assert response.status_code == 422, f"{url}?{params}"


class TestBoundaryAndDefaultPass:
    """恰等上限与默认值放行（le 含等号）。"""

    def test_limit_at_500_passes(self, api_client: TestClient) -> None:
        response = api_client.get("/api/data/datasets", params={"limit": 500})
        assert response.status_code == 200

    def test_limit_at_1_lower_bound_passes(self, api_client: TestClient) -> None:
        response = api_client.get("/api/data/datasets", params={"limit": 1})
        assert response.status_code == 200

    def test_ohlcv_limit_at_50000_passes_validation(
        self, api_client: TestClient,
    ) -> None:
        """ohlcv 类恰 50000：过校验（有数据 200）。"""
        response = api_client.get("/api/data/ohlcv/BTCUSDT/4h", params={"limit": 50000})
        assert response.status_code == 200

    def test_page_and_page_size_bounds_pass(self, api_client: TestClient) -> None:
        response = api_client.get(
            "/api/evolution/tasks", params={"page": 1, "page_size": 500},
        )
        assert response.status_code == 200

    def test_offset_zero_passes(self, api_client: TestClient) -> None:
        response = api_client.get("/api/strategies", params={"offset": 0})
        assert response.status_code == 200

    def test_defaults_unchanged(self, api_client: TestClient) -> None:
        """默认参数路径不受约束影响。"""
        assert api_client.get("/api/data/datasets").status_code == 200
        assert api_client.get("/api/evolution/tasks").status_code == 200
        assert api_client.get("/api/strategies").status_code == 200
        assert api_client.get("/api/strategies/verify/history").status_code == 200
        assert api_client.get("/api/strategies/verify/sessions").status_code == 200
