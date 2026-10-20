"""研究链读库 DNA 校验红锚·HTTP 层（signal-sources-structure-gate 0.3）。

契约出处（design 卡 R+A-4 两入口层之 HTTP 直调层）：四研究端点
（validate-backtest / staged-validation / backtest-sandbox /
random-window-test）消费库内策略 DNA 的读库装载单点挂全量
validate_dna——畸形 signal_sources → 422
{"code": "RESEARCH_DNA_INVALID", "message": 「库内策略 DNA 结构/
值域非法: {sid}」, "detail": {"strategy_id", "errors"}}；
全程无 500（现状：拼错型带绿灯跑完产出验证结果、非 dict 型裸
异常穿出、缺 ref 型静默失真——均红）。

全量锚（Q-1 裁决=挂全量校验）：非 signal_sources 的值域畸形
（RiskGenes(stop_loss=0.5)）同码拒绝——挂载是全量 validate_dna
而非仅 signal_sources 判据。

红锚语义：四端点×三类畸形+全量锚在卡 R 挂载前为红（200 带绿灯
结果/500/裸异常均以「非目标 422 载荷」表现为红）。isolated 端点
需 active hypothesis（precheck 前置）——经 API 登记真假设链。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient  # noqa: F401（fixture 类型标注用）

from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA

BAD_SOURCES = {
    "拼错type": [{"type": "fomula"}],
    "非dict条目": [{"type": "rule"}, "x"],
    "缺ref": [{"type": "formula", "threshold": 0.5}],
}


def _dna_with_sources(sources, *, risk: RiskGenes | None = None) -> StrategyDNA:
    """合法骨架（entry/exit 齐全）+指定 signal_sources/风险基因。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi", {"type": "cross_above", "threshold": 50.0}),
        ],
        signal_sources=sources,
        risk_genes=risk or RiskGenes(),
    )


def _seed(db, sid: str, dna: StrategyDNA) -> None:
    """直改库写畸形行（save_strategy 原语无校验——读库防线是被测面）。"""
    save_strategy(
        db, strategy_id=sid, dna_json=dna.to_json(),
        symbol="BTCUSDT", timeframe="4h", name=sid,
        gene_signature=f"sig-{sid}")


def _register_hypothesis(client: TestClient) -> str:
    """经 API 登记 active 假设（isolated 端点 precheck 前置依赖）。"""
    resp = client.post("/api/research/hypotheses", json={
        "conditions": {
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}],
        },
        "statement": "红锚假设（研究链 DNA 闸门）",
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": "2024-01-01T00:00:00Z",
                         "end": "2024-02-01T00:00:00Z"}],
    })
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["hypothesis_id"]


def _post_endpoint(client: TestClient, endpoint: str, sid: str,
                   hypothesis_id: str | None = None):
    """四端点同语义请求（strategy_id 消费面）。"""
    if endpoint == "validate-backtest":
        return client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hypothesis_id, "strategy_id": sid,
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-02-05T00:00:00Z",
                              "end": "2024-03-01T00:00:00Z"}})
    if endpoint == "staged-validation":
        return client.post("/api/research/staged-validation", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-02-05", "end": "2024-03-01"})
    if endpoint == "backtest-sandbox":
        return client.post("/api/research/backtest-sandbox", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-02-05", "end": "2024-03-01"})
    if endpoint == "random-window-test":
        return client.post("/api/research/random-window-test", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-02-05", "data_end": "2024-03-20"})
    raise ValueError(endpoint)


ENDPOINTS = ("validate-backtest", "staged-validation",
             "backtest-sandbox", "random-window-test")


@pytest.fixture(autouse=True)
def _seed_market(api_client):
    """覆写默认单 bar 数据为 500 根 4h bar（warmup 充足——拼错型现状
    「带绿灯跑完」的静默失真形态可复现+合法护栏用例可跑通；目标态
    装载点先于数据检查拒绝，数据量无关）。"""
    import numpy as np
    import pandas as pd

    data_dir = api_client.app.state.data_dir
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    rng = np.random.default_rng(5)
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    yield


class TestResearchEndpointsRejectMalformedDna:
    """四端点 × 三类畸形 → 422 RESEARCH_DNA_INVALID。"""

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    @pytest.mark.parametrize("label", BAD_SOURCES, ids=list(BAD_SOURCES))
    def test_endpoint_rejects_with_structured_code(
            self, api_client, endpoint, label):
        """畸形行经端点 → 422+code+message 点名 strategy_id+detail.errors
        含 signal_sources 定位（现状 200/500/裸异常——红）。"""
        db = api_client.app.state.db_path
        init_db_ext(db)
        sid = f"st-rg-{label}"
        _seed(db, sid, _dna_with_sources(BAD_SOURCES[label]))
        hypothesis_id = (
            _register_hypothesis(api_client)
            if endpoint == "validate-backtest" else None)

        resp = _post_endpoint(api_client, endpoint, sid, hypothesis_id)

        assert resp.status_code == 422, (
            f"[{endpoint}/{label}] {resp.status_code} {resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_DNA_INVALID", (
            f"[{endpoint}/{label}] {body}")
        assert body["message"] == f"库内策略 DNA 结构/值域非法: {sid}", (
            f"[{endpoint}/{label}] {body}")
        errors = body["detail"]["errors"]
        assert any("signal_sources" in e for e in errors), (
            f"[{endpoint}/{label}] {errors}")
        assert body["detail"]["strategy_id"] == sid

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_full_validation_anchor_non_sources_value_error(
            self, api_client, endpoint):
        """全量锚（Q-1=挂全量 validate_dna）：signal_sources 之外的
        值域畸形（stop_loss=0.5 越域）→ 同码同形制拒绝——证明挂载是
        全量校验而非仅 signal_sources 判据子集。"""
        db = api_client.app.state.db_path
        init_db_ext(db)
        sid = "st-rg-value-error"
        _seed(db, sid, _dna_with_sources(
            None, risk=RiskGenes(stop_loss=0.5)))  # pct 域外
        hypothesis_id = (
            _register_hypothesis(api_client)
            if endpoint == "validate-backtest" else None)

        resp = _post_endpoint(api_client, endpoint, sid, hypothesis_id)

        assert resp.status_code == 422, (
            f"[{endpoint}] {resp.status_code} {resp.text[:300]}")
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_DNA_INVALID", f"[{endpoint}] {body}"
        assert any("Stop loss" in e for e in body["detail"]["errors"]), (
            f"[{endpoint}] {body['detail']}")

    def test_legal_dna_still_evaluates(self, api_client):
        """合法 DNA 求值不变（绿态护栏，代表端点）：合法混合形态行经
        sandbox 端点 → 正常 200 产出 metrics（挂载前后行为不变面）。
        沙盒对纯插件源另有加严门/组装链要求，此处用纯 rule 源+合法
        骨架（挂载不改变合法策略的求值路径）。"""
        db = api_client.app.state.db_path
        init_db_ext(db)
        _seed(db, "st-rg-legal", _dna_with_sources([{"type": "rule"}]))

        resp = api_client.post("/api/research/backtest-sandbox", json={
            "strategy_id": "st-rg-legal", "symbol": "BTCUSDT",
            "timeframe": "4h",
            "start": "2024-02-05", "end": "2024-02-25"})

        assert resp.status_code == 200, resp.text
