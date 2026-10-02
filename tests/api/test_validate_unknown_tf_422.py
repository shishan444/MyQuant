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


class TestValidateScene422:
    """CARD-05：/api/validate/scene 三类 422 与正常 200（ResearchError
    兜底接入 scene 路由；CARD-03 时的 500 裸穿中间态已收口）。"""

    def _payload(self, **overrides):
        payload = {
            "symbol": "TESTUSDT", "timeframe": "4h",
            "scene_type": "volume_spike",
        }
        payload.update(overrides)
        return payload

    def test_unknown_timeframe_returns_422(self, api_client):
        """CARD-03 遗留中间态收口锚：scene 端点未知 tf → 422（原 500）。"""
        resp = api_client.post(
            "/api/validate/scene", json=self._payload(timeframe="7h"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "timeframe 未知" in body["detail"]["message"]

    def test_bad_detector_param_returns_422(self, api_client):
        """坏参数需走到 detect：用 tmp_data_dir 自带的 BTCUSDT_4h 数据。"""
        resp = api_client.post(
            "/api/validate/scene",
            json=self._payload(symbol="BTCUSDT", params={"multiplier": "abc"}),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "multiplier 须为数值" in body["detail"]["message"]

    def test_empty_horizons_returns_422(self, api_client):
        resp = api_client.post(
            "/api/validate/scene", json=self._payload(horizons=[]),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "horizons 不能为空" in body["detail"]["message"]

    def test_known_tf_missing_file_still_200(self, api_client):
        """CARD-05 保持锚：合法请求缺数据文件 → 200 + No data found warning。"""
        resp = api_client.post("/api/validate/scene", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_triggers"] == 0
        assert any("No data found" in w for w in body["warnings"])
