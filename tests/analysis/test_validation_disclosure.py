"""验证记录执行配置披露（research-execution-config-alignment 1.1，C1-A3）。

卡 A 双消费点的验证侧面：validation_service 五组装点（isolated
result_json / random result_json+返回体 / staged result_json+返回体）
与 translate 回执共用 _risk_genes_disclosure 单点——键集与值同源同值
（禁两处组装）；hypothesis_id 缺失（手工/进化产物 adhoc）→
default_filled=null 两态（is None 与 ==[] 类型级区分）。
幂等重放经 result_json 内嵌键自动同构。
"""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext

CONDITIONS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30}},
         "action": "gt", "target": 60}],
}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def env(tmp_path: Path):
    """500 bar 4h 数据 + 库；app.state 由用例内覆写（TestClient 时序）。"""
    rng = np.random.default_rng(11)
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
    return {"db": db, "data_dir": data_dir}


def _register_hyp(client, strategy_params=None):
    body = {"conditions": CONDITIONS, "statement": "披露测试",
            "data_ranges": _RANGES}
    if strategy_params is not None:
        body["strategy_params"] = strategy_params
    r = client.post("/api/research/hypotheses", json=body)
    assert r.status_code == 200, r.text
    return r.json()["hypothesis_id"]


def _record_result(db: Path, hypothesis_id: str, strategy_id: str,
                   level: str):
    """按三元（hypothesis/strategy/level）取记录行 result json。"""
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT result FROM validation_records "
            "WHERE hypothesis_id=? AND strategy_id=? AND level=?",
            (hypothesis_id, strategy_id, level)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"无 {level} 记录行"
    return json.loads(row[0])


class TestIsolatedSameSourceAsReceipt:
    """A3：隔离验证披露面与 translate 回执同源同值（双消费点断言）。"""

    def test_iso_disclosure_equals_translate_receipt(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        tr = api_client.post(f"/api/research/translate/{hid}", json={})
        assert tr.status_code == 200, tr.text
        receipt = tr.json()
        sid = receipt["strategy_id"]

        r = api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid, "strategy_id": sid,
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-03-01T00:00:00Z",
                              "end": "2024-03-15T00:00:00Z"}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"], "验证披露与回执不同源同值"
        assert "stop_loss" not in \
            body["risk_genes_disclosure"]["default_filled"]
        assert body["risk_genes_disclosure"]["values"]["stop_loss"] == 0.08
        # 记录行读回：result_json 落库同值
        result = _record_result(env["db"], hid, sid, "isolated_backtest")
        assert result["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"]

    def test_iso_idempotent_replay_isomorphic(self, env, api_client):
        """幂等重放：duplicated=True 且披露与首次逐键同构（result_json
        内嵌键经 _validation_payload 顶层化）。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        sid = api_client.post(f"/api/research/translate/{hid}",
                              json={}).json()["strategy_id"]
        payload = {"hypothesis_id": hid, "strategy_id": sid,
                   "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-03-01T00:00:00Z",
                                     "end": "2024-03-15T00:00:00Z"}}
        first = api_client.post("/api/research/validate-backtest",
                                json=payload)
        again = api_client.post("/api/research/validate-backtest",
                                json=payload)
        assert again.status_code == 200, again.text
        assert again.json()["duplicated"] is True
        assert again.json()["risk_genes_disclosure"] == \
            first.json()["risk_genes_disclosure"]


class TestAdhocNullDefaultFilled:
    """hypothesis_id 缺失两态：default_filled=null（is None 与 ==[]
    类型级区分——空列表冒充全部默认是可断言的语义错误）。"""

    def test_random_adhoc_null_default_filled(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        sid = api_client.post(f"/api/research/translate/{hid}",
                              json={}).json()["strategy_id"]
        r = api_client.post("/api/research/random-window-test", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-10T00:00:00Z",
            "data_end": "2024-03-20T00:00:00Z",
            "window_bars": 60, "n_windows": 2, "seed": 7})
        assert r.status_code == 200, r.text
        disc = r.json()["risk_genes_disclosure"]
        assert disc["default_filled"] is None, \
            "adhoc 无谱系 → null（非空列表）"
        assert disc["values"], "values 仍完整回显"
        # 记录行读回同值
        result = _record_result(env["db"], "adhoc", sid, "random_window")
        assert result["risk_genes_disclosure"]["default_filled"] is None

    def test_staged_adhoc_null_default_filled(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        sid = api_client.post(f"/api/research/translate/{hid}",
                              json={}).json()["strategy_id"]
        r = api_client.post("/api/research/staged-validation", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-20T00:00:00Z",
            "segmentation": "auto"})
        assert r.status_code == 200, r.text
        disc = r.json()["risk_genes_disclosure"]
        assert disc["default_filled"] is None
        assert disc["values"]["stop_loss"] == 0.08


class TestStagedWithHypothesis:
    """staged 挂假设：result_json 与返回体披露在场且同 translate 回执。"""

    def test_staged_disclosure_equals_receipt(self, env, api_client):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        hid = _register_hyp(api_client, strategy_params={"stop_loss": 0.08})
        tr = api_client.post(f"/api/research/translate/{hid}", json={})
        receipt = tr.json()
        sid = receipt["strategy_id"]
        r = api_client.post("/api/research/staged-validation", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-03-20T00:00:00Z",
            "segmentation": "auto", "hypothesis_id": hid})
        assert r.status_code == 200, r.text
        assert r.json()["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"]
        result = _record_result(env["db"], hid, sid, "staged_validation")
        assert result["risk_genes_disclosure"] == \
            receipt["risk_genes_disclosure"]
