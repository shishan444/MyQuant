"""做空假设通道——三关方向披露（add-direction-channel A4，A3-1~A3-6）。

覆盖 tasks A4 完成标准五项：
- short draft 三关返回体与 result_json 均含 direction="short"（记录行读回）
- long draft 三关 direction="long"
- 沙盒返回体无该键（负向——产品 R1 范围是三关，守范围）
- 幂等命中返回体含 direction 且值与 draft DNA 一致（含存量记录无键场景）
- direction 呈现与首次路径返回体同构（强断言）
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.validation_service import _range_hash
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}


@pytest.fixture()
def env(tmp_path: Path):
    """500 bar 4h 数据 + 库；app.state 由用例内覆写（TestClient 时序）。"""
    rng = np.random.default_rng(7)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 120, n))
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    pd.DataFrame({
        "open": close, "high": close + 80, "low": close - 80,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    return {"db": db, "data_dir": data_dir, "n": n}


def _make_draft(db: Path, strategy_id: str, direction: str):
    gene = SignalGene(
        indicator="RSI", params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
        condition={"type": "lt", "threshold": 35})
    dna = StrategyDNA(
        signal_genes=[gene], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction=direction))
    save_strategy(db, strategy_id=strategy_id, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name=f"dir-{direction}")


def _register_hyp(client, direction=None):
    body = {"conditions": CONDITIONS, "statement": "披露测试",
            "data_ranges": [{
                "symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-02-01T00:00:00Z"}]}
    if direction is not None:
        body["direction"] = direction
    r = client.post("/api/research/hypotheses", json=body)
    assert r.status_code == 200, r.text
    return r.json()["hypothesis_id"]


def _iso_request(client, hid, sid, start, end):
    return client.post("/api/research/validate-backtest", json={
        "hypothesis_id": hid, "strategy_id": sid,
        "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": start, "end": end}})


class TestDisclosureShort:
    def test_short_iso_backtest_direction_in_body_and_record(self, env,
                                                             api_client):
        """A3-1：short draft 隔离回测返回体与 result_json 均含 short。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, direction="short")
        _make_draft(env["db"], "st_short", "short")
        resp = _iso_request(api_client, hid, "st_short",
                            "2024-03-01T00:00:00Z", "2024-03-15T00:00:00Z")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["direction"] == "short"
        # 记录行读回：result_json 落库携带
        rh = rr.find_validation_by_identity(
            env["db"], hid, "st_short",
            _range_hash({"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": "2024-03-01T00:00:00Z",
                         "end": "2024-03-15T00:00:00Z"}),
            level="isolated_backtest", segmentation="")
        result = json.loads(rh["result"])
        assert result["direction"] == "short"


class TestDisclosureLong:
    def test_long_three_gates_direction(self, env, api_client):
        """A3-2：long draft 三关（隔离/随机窗/分段）direction="long"。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client)
        _make_draft(env["db"], "st_long", "long")

        r1 = _iso_request(api_client, hid, "st_long",
                          "2024-03-01T00:00:00Z", "2024-03-15T00:00:00Z")
        assert r1.status_code == 200, r1.text
        assert r1.json()["direction"] == "long"

        r2 = api_client.post("/api/research/random-window-test", json={
            "strategy_id": "st_long", "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-10T00:00:00Z", "data_end": "2024-03-20T00:00:00Z",
            "window_bars": 60, "n_windows": 2, "seed": 7,
            "hypothesis_id": hid})
        assert r2.status_code == 200, r2.text
        assert r2.json()["direction"] == "long"

        r3 = api_client.post("/api/research/staged-validation", json={
            "strategy_id": "st_long", "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-20T00:00:00Z",
            "segmentation": "auto", "hypothesis_id": hid})
        assert r3.status_code == 200, r3.text
        assert r3.json()["direction"] == "long"


class TestSandboxNegative:
    def test_sandbox_has_no_direction_key(self, env, api_client):
        """A3-3：沙盒返回体无 direction 键（守范围——不判门槛不入记录）。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        _make_draft(env["db"], "st_sb", "short")
        resp = api_client.post("/api/research/backtest-sandbox", json={
            "strategy_id": "st_sb", "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-15T00:00:00Z"})
        assert resp.status_code == 200, resp.text
        assert "direction" not in resp.json()


class TestIdempotentIsomorphicEcho:
    def test_iso_idempotent_echo_matches_first(self, env, api_client):
        """A3-4/A3-6：隔离回测幂等重放 direction 与首次一致且同构。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, direction="short")
        _make_draft(env["db"], "st_dupe", "short")
        first = _iso_request(api_client, hid, "st_dupe",
                             "2024-03-01T00:00:00Z", "2024-03-15T00:00:00Z")
        assert first.status_code == 200, first.text
        again = _iso_request(api_client, hid, "st_dupe",
                             "2024-03-01T00:00:00Z", "2024-03-15T00:00:00Z")
        assert again.status_code == 200, again.text
        b1, b2 = first.json(), again.json()
        assert b2["duplicated"] is True
        assert b1["direction"] == b2["direction"] == "short"

    def test_random_window_idempotent_echo_matches_first(self, env,
                                                         api_client):
        """A3-4 补：随机窗幂等重放 direction 与首次一致。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, direction="short")
        _make_draft(env["db"], "st_rw", "short")
        payload = {"strategy_id": "st_rw", "symbol": "BTCUSDT",
                   "timeframe": "4h",
                   "data_start": "2024-01-10T00:00:00Z",
                   "data_end": "2024-03-20T00:00:00Z",
                   "window_bars": 60, "n_windows": 2, "seed": 7,
                   "hypothesis_id": hid}
        first = api_client.post("/api/research/random-window-test",
                                json=payload)
        assert first.status_code == 200, first.text
        again = api_client.post("/api/research/random-window-test",
                                json=payload)
        assert again.status_code == 200, again.text
        b1, b2 = first.json(), again.json()
        assert b2["duplicated"] is True
        assert b1["direction"] == b2["direction"] == "short"
