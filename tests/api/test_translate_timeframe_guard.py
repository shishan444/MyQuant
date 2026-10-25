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
        """payload 缺省（schema 默认 None=继承哨兵，R2 后——旧「默认
        4h」描述已过时）无守卫面——404 而非 422。"""
        resp = api_client.post("/api/research/translate/hyp_nonexistent")
        assert resp.status_code == 404, resp.text


class TestTranslateMalformedRangesRouteE2E:
    """阶段四⑤层级补全（fix-mtf-review-consistency）：CARD-24 畸形
    data_ranges 422 经真实路由端到端——服务层直调锚（tests/analysis/
    test_translate_timeframe_contract.py）之外，补 FastAPI 路由+
    ResearchError 转译通道的 integration 证据。"""

    def test_malformed_ranges_422_through_route(self, api_client, db_path):
        """畸形假设（data_ranges 首元素非 dict，直写库构造）→
        POST /api/research/translate → 422 RESEARCH_PARAM_INVALID
        detail 三键（param/data_ranges 回显/need_hint）。"""
        import json
        import sqlite3

        from core.analysis.executor import ensure_research_tables
        from core.persistence.db_ext import init_db_ext

        init_db_ext(db_path)
        ensure_research_tables(db_path)
        conds = json.dumps({
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "gt", "target": 65}]})
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
                " conditions_hash, statement, data_ranges, ranges_hash,"
                " status, created_at, updated_at)"
                " VALUES ('hyp_mal', ?, 'ch_mal', 's', ?, 'rh_mal',"
                " 'active', '2026-01-01', '2026-01-01')",
                (conds, json.dumps([["4h"]])))
        resp = api_client.post(
            "/api/research/translate/hyp_mal",
            json={"strategy_symbol": "BTCUSDT"})
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        d = body["detail"]["detail"]
        assert d["param"] == "strategy_timeframe"
        assert d["data_ranges"] == [["4h"]]
        assert "strategy_timeframe" in d["need_hint"]
