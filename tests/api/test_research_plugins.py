"""插件机制端到端测试（add-ai-research-mcp 5.2/5.4/5.5）。"""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.plugin_service import enrich_with_plugin_columns
from core.analysis.registry_meta import available_signal_columns
from core.persistence.repository import research as rr


@pytest.fixture()
def plugin_env(tmp_path: Path):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    n = 700
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    rng = np.random.default_rng(4)
    close = 60000 + np.cumsum(rng.normal(0, 120, n))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, n),
                  "trades": 40}, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    return {"data_dir": data_dir}


def _wire(api_client, env):
    db = _ensure_research_tables(api_client)
    api_client.app.state.db_path = db
    api_client.app.state.data_dir = env["data_dir"]
    return db


def _ensure_research_tables(api_client):
    """研究表自愈（api_client 夹具库 init_db_ext 只建 legacy 表）。"""
    db = Path(api_client.app.state.db_path)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    return db


class TestFormulaPlugin:
    def test_register_and_signal_appears(self, api_client, plugin_env):
        """注册 → 可用信号列即时出现（标注来源 formula）。"""
        _wire(api_client, plugin_env)
        resp = api_client.post("/api/research/plugins/formula", json={
            "name": "oi_mom", "expression": "zscore(close, 20)"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["duplicated"] is False and body["version"] == 1

        cols = api_client.get("/api/research/signals").json()
        hit = [c for c in cols if c["column_key"] == body["column"]]
        assert hit and hit[0]["source"] == "formula"

    def test_duplicate_returns_existing(self, api_client, plugin_env):
        _wire(api_client, plugin_env)
        r1 = api_client.post("/api/research/plugins/formula", json={
            "name": "dup_test", "expression": "diff(close, 3)"}).json()
        r2 = api_client.post("/api/research/plugins/formula", json={
            "name": "dup_test", "expression": "diff(close, 3)"}).json()
        assert r2["duplicated"] is True and r2["version"] == r1["version"]

    def test_same_name_new_expression_versions_up(self, api_client, plugin_env):
        """同名不同表达式 → 版本递增（5.5）。"""
        _wire(api_client, plugin_env)
        r1 = api_client.post("/api/research/plugins/formula", json={
            "name": "ver_test", "expression": "zscore(close, 10)"}).json()
        r2 = api_client.post("/api/research/plugins/formula", json={
            "name": "ver_test", "expression": "zscore(close, 30)"}).json()
        assert r2["version"] == r1["version"] + 1
        assert r2["duplicated"] is False
        # 历史版本可查
        audit = api_client.get("/api/research/plugins/formula",
                               params={"name": "ver_test"}).json()
        assert audit["total"] == 2
        assert {i["version"] for i in audit["items"]} == {1, 2}

    def test_lookahead_formula_rejected(self, api_client, plugin_env):
        """前视公式被拒（静态校验第三关）+ rejected 留档。"""
        _wire(api_client, plugin_env)
        resp = api_client.post("/api/research/plugins/formula", json={
            "name": "cheat", "expression": "shift(close, -5)"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_FORMULA_INVALID"
        # rejected 只留记录不注册
        cols = api_client.get("/api/research/signals").json()
        assert not any("cheat" in c["column_key"] for c in cols)

    def test_enrich_evaluates_plugin_column(self, api_client, plugin_env):
        """enrich_with_plugin_columns：公式列在 df 上可求值（分析可引用）。"""
        db = _wire(api_client, plugin_env)
        api_client.post("/api/research/plugins/formula", json={
            "name": "enr", "expression": "zscore(close, 20)"})
        cols = available_signal_columns(db)
        target = next(c["column_key"] for c in cols if c["source"] == "formula")

        df = pd.read_parquet(plugin_env["data_dir"] / "BTCUSDT_4h.parquet")
        out = enrich_with_plugin_columns(db, df, {target})
        assert target in out.columns and out[target].notna().sum() > 500


class TestModelPlugin:
    def test_async_train_and_register(self, api_client, plugin_env):
        """模型配置 → 202 → 轮询 succeeded → signal_registry 注册（5.4）。"""
        _wire(api_client, plugin_env)
        resp = api_client.post("/api/research/plugins/model", json={
            "name": "mtest", "features": ["close"], "template": "rolling_logistic",
            "params": {"window": 0, "step": 50},
            "target": {"type": "return_sign", "horizon": 2},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-01T00:00:00Z", "data_end": "2024-03-01T00:00:00Z"})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "pending" and "task_id" in body

        # 轮询到终态
        deadline = time.time() + 60
        final = None
        while time.time() < deadline:
            t = api_client.get(f"/api/research/analysis/tasks/{body['task_id']}").json()
            if t["status"] in ("succeeded", "failed"):
                final = t
                break
            time.sleep(0.3)
        assert final is not None and final["status"] == "succeeded", final

        # 插件 valid + 注册列出现 + OOS 摘要
        rows = api_client.get("/api/research/plugins/model",
                              params={"name": "mtest"}).json()["items"]
        assert rows and rows[0]["status"] == "valid"
        assert rows[0]["oos_summary"] and "oos_accuracy" in rows[0]["oos_summary"]
        cols = api_client.get("/api/research/signals").json()
        assert any(c["column_key"] == rows[0]["signal_column"] for c in cols)

    def test_duplicate_config_returns_existing(self, api_client, plugin_env):
        """同配置幂等：二次提交命中既有 valid 插件。"""
        _wire(api_client, plugin_env)
        payload = {
            "name": "mdup", "features": ["close"], "template": "rolling_linear",
            "params": {"window": 0, "step": 60},
            "target": {"type": "return", "horizon": 2},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-01T00:00:00Z", "data_end": "2024-03-01T00:00:00Z"}
        r1 = api_client.post("/api/research/plugins/model", json=payload)
        assert r1.status_code == 202
        # 等第一次训练完成
        deadline = time.time() + 60
        while time.time() < deadline:
            st = api_client.get(f"/api/research/plugins/model",
                                params={"name": "mdup"}).json()["items"]
            if st and st[0]["status"] in ("valid", "rejected"):
                break
            time.sleep(0.3)
        r2 = api_client.post("/api/research/plugins/model", json=payload)
        assert r2.status_code == 200
        assert r2.json()["duplicated"] is True

    def test_bad_feature_rejected_sync(self, api_client, plugin_env):
        _wire(api_client, plugin_env)
        resp = api_client.post("/api/research/plugins/model", json={
            "name": "mbad", "features": ["nope_col"],
            "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-01T00:00:00Z", "data_end": "2024-02-01T00:00:00Z"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_CONDITION_INVALID"
