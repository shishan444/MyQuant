"""指标读数路由测试（add-query-indicator 任务 3.2）。

覆盖：POST 200 结构四区（echo/声明/披露/归约）、422 拒绝族结构化码、
零留痕契约（Q2 裁决——调用前后 research_log 计数不变，与 inspect_cases
的留痕对链形成行为对照）。fixture 照 inspect_cases api 先例
（tmp parquet + init_db_ext + app.state 注入）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def q_env(tmp_path: Path):
    """800 bar 1d 数据 + 独立 db（零留痕断言需可查 log 基线）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=800, freq="1d", start="2023-06-01", seed=13)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _payload(**extra):
    p = {"symbol": SYMBOL, "timeframe": "1d", "indicator": "RSI"}
    p.update(extra)
    return p


class TestQueryIndicatorRoute:
    def test_post_200_structure(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        resp = api_client.post("/api/research/query-indicator",
                               json=_payload(params={"period": 38}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 四区：echo（indicator+params 生效值）/ 声明（warmup_bars）/
        # 披露（range 三键）/ 归约（列名键 → 所选归约）
        assert body["indicator"] == "RSI"
        assert body["params"] == {"period": 38}
        assert body["warmup_bars"] == 200
        assert set(body["range"]) == {"effective_start", "effective_end",
                                      "bars"}
        assert set(body["reductions"]["rsi_38"]) == {"latest", "quantiles"}
        assert body["reductions"]["rsi_38"]["quantiles"]["p50"] > 0

    def test_422_family(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        cases = [
            (_payload(reductions=["nope"]), "RESEARCH_PARAM_INVALID"),
            (_payload(params={"period": 99}), "RESEARCH_PARAM_INVALID"),
            (_payload(params={"period": 14, "bogus": 1}),
             "RESEARCH_PARAM_INVALID"),
            (_payload(indicator="NoSuchInd"), "RESEARCH_INDICATOR_UNKNOWN"),
        ]
        for payload, code in cases:
            resp = api_client.post("/api/research/query-indicator",
                                   json=payload)
            assert resp.status_code == 422, payload
            detail = resp.json()["detail"]
            assert detail["code"] == code
            assert detail["message"]

    def test_zero_audit_trail(self, api_client, q_env):
        """Q2 契约：读数零留痕——成功调用前后 log 计数不变。"""
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        before = len(rr.list_research_log(q_env["db"]))
        for _ in range(3):  # 重复调用亦不产生累计留痕
            resp = api_client.post("/api/research/query-indicator",
                                   json=_payload())
            assert resp.status_code == 200
        after = len(rr.list_research_log(q_env["db"]))
        assert before == after

    def test_rejection_no_side_effect(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        before = len(rr.list_research_log(q_env["db"]))
        resp = api_client.post("/api/research/query-indicator",
                               json=_payload(params={"period": 99}))
        assert resp.status_code == 422
        assert len(rr.list_research_log(q_env["db"])) == before


class TestNonResearchErrorPassthrough:
    """CARD-03 修复资产：except 收窄为 ResearchError——服务层非
    ResearchError 异常原样穿透（不再坍缩为 research_error_to_http 内
    AttributeError 500，原始异常类型与栈可观测）。"""

    def _bind_env(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]

    def test_value_error_passthrough(self, api_client, q_env, monkeypatch):
        from core.analysis import indicator_query as iq

        def _boom(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(iq, "query_indicator", _boom)
        self._bind_env(api_client, q_env)
        with pytest.raises(ValueError, match="boom"):
            api_client.post("/api/research/query-indicator", json=_payload())

    def test_research_error_channel_unchanged(self, api_client, q_env, monkeypatch):
        """对照组：ResearchError 仍走结构化 422（收窄未放跑正主）。"""
        from core.analysis import indicator_query as iq
        from core.analysis.hypothesis_service import ResearchError

        def _re(*args, **kwargs):
            raise ResearchError("RESEARCH_PARAM_INVALID", "测试注入")

        monkeypatch.setattr(iq, "query_indicator", _re)
        self._bind_env(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_payload())
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"
