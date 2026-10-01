"""非法 timeframe 错误通道守卫测试（CARD-04 修复资产）。

八研究入口的 timeframe 值域前置校验（common.require_known_timeframe）：
非法周期（不在 TF_SECONDS 键集）→ 422 + TIMEFRAME_UNKNOWN（detail 带
field 定位与 legal_range 清单），不再裸穿 500。拒绝路径零持久副作用；
值域校验先于 symbol 可达性探测（同请求双错时 field 定位到 timeframe）。
"""

import pytest

pytestmark = [pytest.mark.integration]

CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}
_RANGE = {"symbol": "BTCUSDT", "timeframe": "13x",
          "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}

# (端点, 最小 payload, 期望 detail.field)
BAD_TF_CASES = [
    pytest.param(
        "/api/research/hypotheses",
        {"conditions": CONDITIONS, "statement": "守卫", "data_ranges": [_RANGE]},
        "data_ranges[0].timeframe", id="登记假设"),
    pytest.param(
        "/api/research/analysis",
        {"template": "condition_stats", "params": {}, "data_range": _RANGE},
        "data_range.timeframe", id="分析模板"),
    pytest.param(
        "/api/research/inspect-cases",
        {"symbol": "BTCUSDT", "timeframe": "13x",
         "selector": {"condition": {"subject": "close", "action": "gt", "target": 1}}},
        "timeframe", id="案例抽查"),
    pytest.param(
        "/api/research/query-indicator",
        {"symbol": "BTCUSDT", "timeframe": "13x", "indicator": "RSI"},
        "timeframe", id="指标读数"),
    pytest.param(
        "/api/research/validate-backtest",
        {"hypothesis_id": "hyp_x", "strategy_id": "st_x", "request_range": _RANGE},
        "request_range.timeframe", id="隔离回测"),
    pytest.param(
        "/api/research/staged-validation",
        {"strategy_id": "st_x", "symbol": "BTCUSDT", "timeframe": "13x",
         "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
        "timeframe", id="分段验证"),
    pytest.param(
        "/api/research/backtest-sandbox",
        {"strategy_id": "st_x", "symbol": "BTCUSDT", "timeframe": "13x",
         "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"},
        "timeframe", id="沙盒回测"),
    pytest.param(
        "/api/research/random-window-test",
        {"strategy_id": "st_x", "symbol": "BTCUSDT", "timeframe": "13x",
         "data_start": "2024-01-01T00:00:00Z", "data_end": "2024-02-01T00:00:00Z"},
        "timeframe", id="随机窗口"),
]


class TestUnknownTimeframeRejected:
    @pytest.mark.parametrize(("endpoint", "payload", "field"), BAD_TF_CASES)
    def test_illegal_timeframe_returns_structured_422(
            self, api_client, endpoint, payload, field):
        resp = api_client.post(endpoint, json=payload)
        assert resp.status_code == 422, f"{endpoint} 应 422 而非 {resp.status_code}"
        detail = resp.json()["detail"]
        assert detail["code"] == "TIMEFRAME_UNKNOWN"
        assert detail["detail"]["field"] == field, "field 须定位到具体请求字段"
        assert "13x" in detail["detail"]["timeframe"]
        assert "4h" in detail["detail"]["legal_range"], "legal_range 附合法清单"

    def test_rejection_leaves_no_persistent_side_effect(self, api_client):
        """非法周期登记：假设与日志零落库（拒绝路径零持久副作用）。"""
        resp = api_client.post("/api/research/hypotheses", json={
            "conditions": CONDITIONS, "statement": "守卫",
            "data_ranges": [_RANGE]})
        assert resp.status_code == 422

        hyps = api_client.get("/api/research/hypotheses").json()
        assert hyps["items"] == []
        logs = api_client.get("/api/research/log").json()
        assert logs["items"] == []

    def test_timeframe_validated_before_symbol_reachability(self, api_client):
        """同请求双错（非法周期 + 不可达 symbol）：timeframe 值域校验先报。"""
        resp = api_client.post("/api/research/hypotheses", json={
            "conditions": CONDITIONS, "statement": "守卫",
            "data_ranges": [{**_RANGE, "symbol": "NOPEUSDT"}]})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "TIMEFRAME_UNKNOWN", \
            "值域校验应先于 symbol 可达性探测"

    def test_legal_timeframe_unaffected(self, api_client):
        """合法周期通道不受守卫影响（登记照常 200）。"""
        resp = api_client.post("/api/research/hypotheses", json={
            "conditions": CONDITIONS, "statement": "守卫",
            "data_ranges": [{**_RANGE, "timeframe": "4h"}]})
        assert resp.status_code == 200, resp.text
