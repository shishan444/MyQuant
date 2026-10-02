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


class TestValidateSceneNonFiniteParam422:
    """SEC-08 端到端：NaN/Infinity 字面量原始体（json.dumps 默认
    allow_nan 写出、服务端 json.loads 接受）→ 422 须为有限数。"""

    def test_nan_multiplier_returns_422(self, api_client):
        """原始体走 content=（httpx json 序列化禁 NaN，须手写字面量）。"""
        resp = api_client.post(
            "/api/validate/scene",
            content='{"symbol": "BTCUSDT", "timeframe": "4h", '
                    '"scene_type": "volume_spike", '
                    '"params": {"multiplier": NaN}}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "须为有限数" in body["detail"]["message"]

    def test_infinity_multiplier_returns_422(self, api_client):
        resp = api_client.post(
            "/api/validate/scene",
            content='{"symbol": "BTCUSDT", "timeframe": "4h", '
                    '"scene_type": "volume_spike", '
                    '"params": {"multiplier": Infinity}}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422
        assert "须为有限数" in resp.json()["detail"]["message"]


class TestValidateRulesWinRateNone:
    """CARD-32：零交易 win_rate 缺失语义（null）的 HTTP 序列化。

    修复前 RuleValidateResponse.win_rate: float 撞核心层 None
    （46339bc 缺失语义）→ pydantic 校验炸 500；修复后 Optional[float]，
    零交易两路径 200 + null，正常路径数值保持。
    """

    @staticmethod
    def _payload(**overrides):
        payload = {
            "pair": "TESTUSDT", "timeframe": "4h",
            "start": "2024-01-01", "end": "2025-01-01",
            "entry_conditions": [
                {"logic": "AND", "timeframe": "4h", "subject": "close",
                 "action": "lt", "target": "95"},
            ],
            "exit_conditions": [
                {"logic": "AND", "timeframe": "4h", "subject": "close",
                 "action": "gt", "target": "105"},
            ],
        }
        payload.update(overrides)
        return payload

    def test_missing_file_200_win_rate_null(self, api_client):
        """缺数据路径：200 + warnings + win_rate null（修复前 500）。"""
        resp = api_client.post("/api/validate/rules", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_trades"] == 0
        assert body["win_rate"] is None
        assert any("Data file not found" in w for w in body["warnings"])

    def test_no_paired_trades_200_win_rate_null(self, api_client):
        """有数据无配对路径：entry 永假 → 零交易 win_rate null。"""
        resp = api_client.post(
            "/api/validate/rules",
            json=self._payload(entry_conditions=[
                {"logic": "AND", "timeframe": "4h", "subject": "close",
                 "action": "lt", "target": "-1000000"},
            ]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_trades"] == 0
        assert body["win_rate"] is None
        assert body["trades"] == []

    def test_normal_trades_win_rate_numeric(self, api_client, tmp_data_dir):
        """正常交易路径：win_rate 数值（缺失语义不吞正常值）。"""
        import numpy as np
        import pandas as pd
        from core.data.storage import save_parquet
        n = 240
        dates = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        closes = 100.0 + 10.0 * np.sin(np.arange(n) * 2 * np.pi / 20)
        df = pd.DataFrame(
            {"open": closes, "high": closes + 1.0, "low": closes - 1.0,
             "close": closes, "volume": np.full(n, 1000.0)},
            index=dates,
        )
        df.index.name = "timestamp"
        save_parquet(df, tmp_data_dir / "TESTUSDT_4h.parquet")
        resp = api_client.post("/api/validate/rules", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_trades"] >= 1
        assert body["win_rate"] is not None
        assert isinstance(body["win_rate"], float)
        # 现状口径：核心层小数、路由层 ×100 转百分比域
        assert 0.0 <= body["win_rate"] <= 100.0
