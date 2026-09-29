"""案例抽查路由测试（add-inspect-cases 任务 3.2）。

覆盖：POST 200 结构四键与参数披露、422 场景族结构化码与 detail、
留痕条目可查（entry_type/ref_id 指纹/data_ranges）、拒绝路径零留痕。
fixture 照 regime_split api 先例（tmp parquet + init_db_ext +
app.state 注入）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_identity import canonical_condition_hash
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def cases_env(tmp_path: Path):
    """800 bar 1d 数据 + 独立 db。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=800, freq="1d", start="2023-06-01", seed=13)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _payload(**extra):
    p = {
        "symbol": SYMBOL, "timeframe": "1d",
        "selector": {"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}},
                                   "action": "lt", "target": 30.0}},
        "indicator": "RSI",
    }
    p.update(extra)
    return p


class TestInspectCasesRoute:
    def test_post_200_structure(self, api_client, cases_env):
        api_client.app.state.db_path = cases_env["db"]
        api_client.app.state.data_dir = cases_env["data_dir"]
        resp = api_client.post("/api/research/inspect-cases",
                               json=_payload(limit=3, context_bars=8))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("selector_echo", "n_matched", "sampling", "cases"):
            assert key in body
        assert body["sampling"] == "even"
        assert body["n_matched"] == 11  # 与单元测试独立推导同源
        assert len(body["cases"]) == 3
        assert body["limit"] == 3 and body["context_bars"] == 8

    def test_422_family(self, api_client, cases_env):
        api_client.app.state.db_path = cases_env["db"]
        api_client.app.state.data_dir = cases_env["data_dir"]
        df = cases_env["df"]
        cases = [
            (_payload(selector={"cond": {}}), "RESEARCH_PARAM_INVALID"),
            (_payload(limit=6), "RESEARCH_PARAM_INVALID"),
            (_payload(selector={"timestamps": [
                df.index[i].isoformat() for i in range(6)]}),
             "RESEARCH_PARAM_INVALID"),
            (_payload(indicator="NoSuchInd"), "RESEARCH_INDICATOR_UNKNOWN"),
        ]
        for payload, code in cases:
            resp = api_client.post("/api/research/inspect-cases", json=payload)
            assert resp.status_code == 422, payload
            detail = resp.json()["detail"]
            assert detail["code"] == code
            assert detail["message"]

    def test_audit_trail_queryable(self, api_client, cases_env):
        api_client.app.state.db_path = cases_env["db"]
        api_client.app.state.data_dir = cases_env["data_dir"]
        cond = {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 30.0}
        resp = api_client.post("/api/research/inspect-cases",
                               json=_payload(selector={"condition": cond}))
        assert resp.status_code == 200
        rows = [r for r in rr.list_research_log(cases_env["db"])
                if r["entry_type"] == "inspect_cases"]
        assert len(rows) == 1
        assert rows[0]["ref_id"] == canonical_condition_hash(cond, "1d")
        assert rows[0]["hypothesis_id"] is None
        ranges = json.loads(rows[0]["data_ranges"])
        assert ranges["symbol"] == SYMBOL
        assert "start" in ranges and "end" in ranges

    def test_rejection_no_side_effect(self, api_client, cases_env):
        api_client.app.state.db_path = cases_env["db"]
        api_client.app.state.data_dir = cases_env["data_dir"]
        resp = api_client.post("/api/research/inspect-cases",
                               json=_payload(limit=8))
        assert resp.status_code == 422
        assert all(r["entry_type"] != "inspect_cases"
                   for r in rr.list_research_log(cases_env["db"]))
