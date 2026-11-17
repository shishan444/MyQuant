"""路径拼接旁路收口特征网（CARD-05 修复锚定，修复期安全网）。

锚定 api 层五处直拼路径收口 core 后的行为（现状=修复后行为）：
- /api/data/ohlcv 与 /chart-indicators：非法 timeframe → 422（interval 未知，
  旧实现拼出不存在路径静默 404）；合法周期无数据 → 404 不变；别名命中
  （1h→60m 文件）
- /api/evolution/tasks：非法 timeframe → 400 "interval 未知"（状态码不变、
  detail 从「无K线数据」变为白名单拒绝）
- /api/strategies/backtest 与 /compare：非法 dataset_id（穿越/无下划线/
  形状不符）→ 422 Invalid dataset_id；合法形状无数据 → 404 不变
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna, make_ohlcv


def _inline_dna_dict() -> dict:
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger",
             "field": None, "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14}, "role": "exit_trigger",
             "field": None, "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10, "position_size": 0.3},
    }


class TestOhlcvTimeframeGuard:
    def test_unknown_timeframe_422(self, api_client: TestClient) -> None:
        """非法 timeframe → 422 白名单拒绝（旧实现静默 404）。"""
        response = api_client.get("/api/data/ohlcv/BTCUSDT/9x")
        assert response.status_code == 422
        assert "interval 未知" in response.json()["detail"]
        assert "9x" in response.json()["detail"]

    def test_known_timeframe_no_data_404(self, api_client: TestClient) -> None:
        """合法周期无数据 → 404 形制不变。"""
        response = api_client.get("/api/data/ohlcv/GHOSTUSDT/4h")
        assert response.status_code == 404

    def test_alias_hit_1h_to_60m_file(self, api_client: TestClient) -> None:
        """别名命中附带收益：请求 1h 命中 60m 命名文件。"""
        make_ohlcv(10, "1h").to_parquet(
            api_client.app.state.data_dir / "BTCUSDT_60m.parquet",
        )
        response = api_client.get("/api/data/ohlcv/BTCUSDT/1h")
        assert response.status_code == 200
        assert response.json()["dataset_id"] == "BTCUSDT_1h"
        assert len(response.json()["data"]) == 10


class TestChartIndicatorsTimeframeGuard:
    def test_unknown_timeframe_422(self, api_client: TestClient) -> None:
        response = api_client.get("/api/data/chart-indicators/BTCUSDT/bogus")
        assert response.status_code == 422
        assert "interval 未知" in response.json()["detail"]

