"""分段验证路由测试（add-staged-validation 任务 3.2）。

覆盖：POST 200 结构（echo/segments[segment_verdict]/summary/verdict+
record_id/judged_ranges 六区）、422 拒绝族、挂假设留痕可查（ref_id
指向记录）、adhoc 无验证记录、拒绝零副作用。fixture 沿 staged 服务层
测试同形（make_two_regime_df + TEMA 策略）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.repository.strategy import save_strategy
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)
from tests.helpers.regime_factory import make_two_regime_df

SYMBOL = "BTCUSDT"


def _tema_dna() -> StrategyDNA:
    gene = SignalGene("TEMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                      "tema_20", {"type": "lt", "threshold": 1e9})
    return StrategyDNA(
        layers=[TimeframeLayer(timeframe="1d", signal_genes=[gene],
                               logic_genes=LogicGenes())],
        execution_genes=ExecutionGenes(timeframe="1d"),
        risk_genes=RiskGenes())


@pytest.fixture()
def s_env(tmp_path: Path):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_two_regime_df(n_trend=2200, n_range=1100, seed=7)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    save_strategy(db, strategy_id="st_tema", dna_json=_tema_dna().to_json(),
                  symbol=SYMBOL, timeframe="1d")
    return {"data_dir": data_dir, "db": db, "df": df,
            "start": df.index[700].isoformat(),
            "end": df.index[-1].isoformat()}


def _payload(s_env, **extra):
    p = {"strategy_id": "st_tema", "symbol": SYMBOL, "timeframe": "1d",
         "start": s_env["start"], "end": s_env["end"]}
    p.update(extra)
    return p


def _hyp(api_client, s_env) -> str:
    out = register_hypothesis(
        s_env["db"],
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}]},
        statement="api 分段验证测试",
        data_ranges=[{"symbol": SYMBOL, "timeframe": "1d",
                      "start": "2020-01-01T00:00:00Z",
                      "end": "2020-01-10T00:00:00Z"}])
    return out["hypothesis_id"]


class TestStagedValidationRoute:
    def test_post_200_structure(self, api_client, s_env):
        api_client.app.state.db_path = s_env["db"]
        api_client.app.state.data_dir = s_env["data_dir"]
        resp = api_client.post("/api/research/staged-validation",
                               json=_payload(s_env))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 六区：echo（strategy_id/request_range/segmentation）/ 声明
        # （warmup_bars）/ segments（label+segment_verdict）/ summary
        # （三键）/ verdict+record_id / judged_ranges
        assert body["strategy_id"] == "st_tema"
        assert body["segmentation"] == "auto"
        assert body["warmup_bars"] > 0
        assert len(body["segments"]) >= 2
        for seg in body["segments"]:
            assert "label" in seg and "segment_verdict" in seg
        assert set(body["summary"]) >= {"worst_segment", "spread", "all_pass"}
        # 0.2.11 三态（None 特例退役——verdict 恒三态之一）
        assert body["verdict"] in ("pass", "fail", "inconclusive")
        assert isinstance(body["judged_ranges"], list)

    def test_422_family(self, api_client, s_env):
        api_client.app.state.db_path = s_env["db"]
        api_client.app.state.data_dir = s_env["data_dir"]
        cases = [
            (_payload(s_env, strategy_id="nope"), "RESEARCH_STRATEGY_NOT_FOUND"),
            (_payload(s_env, segmentation="bogus"), "RESEARCH_PARAM_INVALID"),
            (_payload(s_env, segmentation="auto",
                      segments=[{"label": "x", "start": s_env["start"],
                                 "end": s_env["end"]}]),
             "RESEARCH_PARAM_INVALID"),
        ]
        for payload, code in cases:
            resp = api_client.post("/api/research/staged-validation",
                                   json=payload)
            assert resp.status_code in (404, 422), payload
            assert resp.json()["detail"]["code"] == code

    def test_attached_log_queryable(self, api_client, s_env):
        api_client.app.state.db_path = s_env["db"]
        api_client.app.state.data_dir = s_env["data_dir"]
        hyp = _hyp(api_client, s_env)
        resp = api_client.post("/api/research/staged-validation",
                               json=_payload(s_env, hypothesis_id=hyp))
        assert resp.status_code == 200
        body = resp.json()
        if body["record_id"]:
            recs = rr.list_validation_records(s_env["db"], hypothesis_id=hyp)
            assert any(r["record_id"] == body["record_id"]
                       and r["level"] == "staged_validation" for r in recs)

    def test_adhoc_no_validation_record(self, api_client, s_env):
        api_client.app.state.db_path = s_env["db"]
        api_client.app.state.data_dir = s_env["data_dir"]
        before = len(rr.list_research_log(s_env["db"]))
        resp = api_client.post("/api/research/staged-validation",
                               json=_payload(s_env))
        assert resp.status_code == 200
        body = resp.json()
        if body["verdict"] is not None:
            assert body["record_id"] is None  # adhoc 无凭据
        logs = rr.list_research_log(s_env["db"])
        assert len(logs) == before + 1
        assert logs[-1]["entry_type"] == "staged_validation"
        assert logs[-1]["hypothesis_id"] is None

    def test_rejection_no_side_effect(self, api_client, s_env):
        api_client.app.state.db_path = s_env["db"]
        api_client.app.state.data_dir = s_env["data_dir"]
        before = len(rr.list_research_log(s_env["db"]))
        resp = api_client.post("/api/research/staged-validation",
                               json=_payload(s_env, segmentation="bogus"))
        assert resp.status_code == 422
        assert len(rr.list_research_log(s_env["db"])) == before
