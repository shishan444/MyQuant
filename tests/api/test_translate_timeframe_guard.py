"""translate 端点 strategy_timeframe 前置值域校验测试（fix-review B6 /
security S-2）。

非法 tf 此前两路 500 冒出（find_parquet 装载路径裸 ValueError）；现
前置 require_known_timeframe 转 422 TIMEFRAME_UNKNOWN（analysis.py
:799 同款先例；校验先于假设查询——无需构造真实假设）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


class TestTranslateTimeframeGuard:
    def test_unknown_timeframe_422(self, api_client):
        """非法 strategy_timeframe → 422 TIMEFRAME_UNKNOWN（detail 附
        legal_range），非 500。"""
        resp = api_client.post(
            "/api/research/translate/hyp_any",
            json={"strategy_symbol": "BTCUSDT",
                  "strategy_timeframe": "10h"})
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "TIMEFRAME_UNKNOWN"
        assert "strategy_timeframe" in body["detail"]["detail"]["field"]
        assert "legal_range" in body["detail"]["detail"]

    def test_known_timeframe_passes_guard(self, api_client):
        """合法 tf 通过守卫面（后续 404=假设不存在——证明守卫不拦
        合法值且校验序正确）。"""
        resp = api_client.post(
            "/api/research/translate/hyp_nonexistent",
            json={"strategy_symbol": "BTCUSDT",
                  "strategy_timeframe": "4h"})
        assert resp.status_code == 404, resp.text

    def test_default_payload_no_guard_face(self, api_client):
        """payload 缺省（schema 默认 4h）无守卫面——404 而非 422。"""
        resp = api_client.post("/api/research/translate/hyp_nonexistent")
        assert resp.status_code == 404, resp.text
