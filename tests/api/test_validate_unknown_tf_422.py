"""CARD-03：/api/validate 与 /api/validate/rules 未知 tf 422（integration）。

核心层锚（tests/evolution/test_validation_feature_net.py 等）已直调断言
ResearchError；本文件经 FastAPI 真实路由装配（TestClient + lifespan）验证
HTTP 边界翻译：未知 timeframe（不在 TF_SECONDS 键集，如 "7h"）→ 422
结构化错误，code=RESEARCH_PARAM_INVALID。旧行为为 200 + 空结果静默。

注意：/api/validate/scene 端点对该错误的兜底属 CARD-05，此处不锚。
"""
import pytest

pytestmark = [pytest.mark.integration]


def _validate_payload(timeframe: str) -> dict:
    return {
        "pair": "TESTUSDT",
        "timeframe": timeframe,
        "start": "2024-01-01",
        "end": "2025-01-01",
        "when": [{"subject": "close", "action": "gt", "target": "0"}],
        "then": [{"subject": "close", "action": "rise", "target": "1"}],
    }


def _rules_payload(timeframe: str) -> dict:
    return {
        "pair": "TESTUSDT",
        "timeframe": timeframe,
        "start": "2024-01-01",
        "end": "2025-01-01",
        "entry_conditions": [
            {"logic": "AND", "timeframe": timeframe, "subject": "close",
             "action": "lt", "target": "95"},
        ],
        "exit_conditions": [
            {"logic": "AND", "timeframe": timeframe, "subject": "close",
             "action": "gt", "target": "105"},
        ],
    }


class TestValidateUnknownTf422:

    def test_unknown_tf_returns_422_structured(self, api_client):
        resp = api_client.post("/api/validate", json=_validate_payload("7h"))
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "timeframe 未知" in body["detail"]["message"]

    def test_known_tf_missing_file_still_200_empty(self, api_client):
        """CARD-03 保持锚：合法 tf 缺文件 → 200 空结果（非 422）。"""
        resp = api_client.post("/api/validate", json=_validate_payload("4h"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 0


class TestValidateRulesUnknownTf422:

    def test_unknown_tf_returns_422_structured(self, api_client):
        resp = api_client.post("/api/validate/rules", json=_rules_payload("7h"))
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "timeframe 未知" in body["detail"]["message"]
