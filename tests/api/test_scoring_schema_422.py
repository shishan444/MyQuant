"""评分值域收紧的 API 路由层 422 验证（CARD-08/09 integration 侧）。

test_schema_contract.py 已在 pydantic 直构层（unit）锚定非法值拒绝；
本文件经 FastAPI 真实路由装配（TestClient）验证同一收紧在 HTTP
边界生效：非法 score_template / objective → 422，请求体校验阶段
拦截，业务逻辑零执行（无需数据环境）。
"""
import pytest

pytestmark = [pytest.mark.integration]


class TestEvolutionTaskSchema422:
    def test_invalid_score_template_rejected_422(self, api_client):
        payload = {"symbol": "BTCUSDT", "timeframe": "4h",
                   "score_template": "bogus"}
        resp = api_client.post("/api/evolution/tasks", json=payload)
        assert resp.status_code == 422

    def test_invalid_objective_rejected_422(self, api_client):
        payload = {"symbol": "BTCUSDT", "timeframe": "4h",
                   "requirements": {"objective": "bogus"}}
        resp = api_client.post("/api/evolution/tasks", json=payload)
        assert resp.status_code == 422


class TestBacktestSchema422:
    def test_invalid_score_template_rejected_422(self, api_client):
        payload = {"dataset_id": "BTCUSDT_4h", "score_template": "nonexistent"}
        resp = api_client.post("/api/strategies/backtest", json=payload)
        assert resp.status_code == 422
