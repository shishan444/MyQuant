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

    def test_rejected_record_failure_does_not_mask_rejection(
            self, api_client, plugin_env, monkeypatch):
        """留档失败不影响拒绝语义（闸门终验消解：防御分支补测）。

        调用图：路由 except ResearchError 分支内 try
        ps.rejected_formula_record——校验失败与留档异常同现才可达，
        monkeypatch 注入留档异常构造双错同现。
        """
        from core.analysis import plugin_service as ps

        _wire(api_client, plugin_env)

        def _boom(*args, **kwargs):
            raise RuntimeError("留档通道故障")
        monkeypatch.setattr(ps, "rejected_formula_record", _boom)

        resp = api_client.post("/api/research/plugins/formula", json={
            "name": "cheat2", "expression": "hack()"})
        assert resp.status_code == 422, "留档失败不得掩盖拒绝语义"
        assert resp.json()["detail"]["code"] == "RESEARCH_FORMULA_INVALID"

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


class TestModelTimeoutChain:
    """2.4 模型超时链：failed(timeout) → plugin 行 rejected 联动落库。"""

    def test_timeout_marks_plugin_rejected(self, api_client, plugin_env, monkeypatch):
        """极小 timeout 使训练超时 → 任务 failed(timeout) + plugin rejected。"""
        db = _wire(api_client, plugin_env)
        # 边界替身：注入慢 fit（被测的是超时→rejected 联动链，非训练本身；
        # 替身不配合 stop_check → 走守护超时标记路径）
        import core.signals.model as model_mod
        import time as _t

        def _slow_fit(df, cfg):
            _t.sleep(1.5)
            raise RuntimeError("不应到达")

        monkeypatch.setattr(model_mod, "fit_predict_walk_forward", _slow_fit)
        import core.analysis.plugin_service as ps_mod
        monkeypatch.setattr(ps_mod, "MODEL_TRAIN_TIMEOUT_S", 0.3)
        from core.analysis.executor import init_executor
        import core.analysis.executor as exec_mod
        old = exec_mod._EXECUTOR
        exec_mod._EXECUTOR = None
        init_executor(db, default_timeout_s=0.3)
        try:
            from core.analysis.plugin_service import register_model_plugin
            out = register_model_plugin(
                db, plugin_env["data_dir"],
                name="slow_model", features=["close"], template="rolling_logistic",
                params={"window": 0, "step": 20},
                target={"type": "return_sign", "horizon": 2},
                symbol="BTCUSDT", timeframe="4h",
                data_start="2024-01-01T00:00:00Z", data_end="2024-03-01T00:00:00Z")
            assert out["status"] == "pending"
            deadline = time.time() + 30
            t = None
            while time.time() < deadline:
                t = rr.get_analysis_task(db, out["task_id"])
                if t["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.2)
            assert t is not None and t["status"] == "failed", t
            assert t["reason"] == "timeout", f"应为超时终态: {t.get('reason')}"
            # plugin 行 rejected 联动：守护先标任务，_train 的 _fail 在其
            # sleep 结束后执行——轮询 plugin 行到终态（不静默丢失）
            from sqlalchemy import select
            from core.persistence.orm.models import ModelPlugin
            from core.persistence.repository.dataset_meta import _engine_for
            row = None
            deadline2 = time.time() + 5
            while time.time() < deadline2:
                with _engine_for(db).connect() as conn:
                    row = conn.execute(select(ModelPlugin).where(
                        ModelPlugin.plugin_id == out["plugin_id"])).mappings().first()
                if row is not None and row["status"] in ("valid", "rejected"):
                    break
                time.sleep(0.2)
            assert row is not None and row["status"] == "rejected", dict(row) if row else None
            assert row["reject_reason"], "拒绝原因须落库"
        finally:
            if exec_mod._EXECUTOR is not None:
                exec_mod._EXECUTOR.shutdown()
            exec_mod._EXECUTOR = old


class TestPluginStacking:
    """2.6 插件叠层：fx_b 引用 fx_a 的两轮求值（数值级断言）。"""

    def test_stacked_formula_evaluates_numerically(self, api_client, plugin_env):
        db = _wire(api_client, plugin_env)
        a = api_client.post("/api/research/plugins/formula",
                            json={"name": "base", "expression": "zscore(close, 20)"})
        b = api_client.post("/api/research/plugins/formula",
                            json={"name": "derived", "expression": "diff(fx_base, 3)"})
        assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)

        df = pd.read_parquet(plugin_env["data_dir"] / "BTCUSDT_4h.parquet")
        out = enrich_with_plugin_columns(db, df, {"fx_derived"})
        assert "fx_base" in out.columns and "fx_derived" in out.columns
        # 数值级：fx_derived = fx_base - shift(fx_base, 3) 逐点一致
        base_series = out["fx_base"]
        expected = base_series - base_series.shift(3)
        mask = expected.notna()
        assert (out.loc[mask, "fx_derived"] - expected[mask]).abs().max() < 1e-9


class TestRegisterExistingPluginSignal:
    """2.7 register_existing_plugin_signal 直测：双表原子。"""

    def test_register_and_log_atomic(self, tmp_path):
        import subprocess as _sp
        db = tmp_path / "p.db"
        _sp.run(["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}",
                 "upgrade", "head"], capture_output=True, check=True)
        from core.persistence.repository.dataset_meta import _engine_for
        from sqlalchemy import insert
        from core.persistence.orm.models import ModelPlugin

        with _engine_for(db).begin() as conn:
            conn.execute(insert(ModelPlugin).values(
                plugin_id="mpl_x", name="m", version=1,
                config="{}", signal_column="model_m", status="valid",
                strategy_visible=1, created_at="2026-09-19T00:00:00"))

        rr.register_existing_plugin_signal(
            db, source="model", plugin_id="mpl_x", name="m", version=1,
            column_name="model_m", extra={"oos": {"acc": 0.6}})
        entries = rr.list_signal_registry_entries(db)
        assert [e["column_name"] for e in entries] == ["model_m"]
        assert any(e["entry_type"] == "plugin" for e in rr.list_research_log(db))

        # 原子性：注册表主键冲突 → 日志回滚
        with pytest.raises(Exception):
            rr.register_existing_plugin_signal(
                db, source="model", plugin_id="mpl_x", name="m", version=1,
                column_name="model_m")
        # 仍是注册前的一条日志（冲突写入被整体回滚）
        plugin_logs = [e for e in rr.list_research_log(db)
                       if e["entry_type"] == "plugin"]
        assert len(plugin_logs) == 1


class TestSeedDivergenceAndUpdateIdempotence:
    """2.8 异种子断言 + 数据更新幂等。"""

    def test_different_seed_different_windows(self, api_client, plugin_env, monkeypatch):
        db = _wire(api_client, plugin_env)
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)
        from core.persistence.db_ext import save_strategy
        save_strategy(db, strategy_id="st_rw", dna_json=StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                     "rsi_14", {"type": "lt", "threshold": 35})],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes()).to_json(), symbol="BTCUSDT", timeframe="4h")
        payload = {
            "strategy_id": "st_rw", "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-03-01T00:00:00Z", "data_end": "2024-03-25T00:00:00Z",
            "window_bars": 10, "n_windows": 3,
        }
        r7 = api_client.post("/api/research/random-window-test",
                             json={**payload, "seed": 7}).json()
        r8 = api_client.post("/api/research/random-window-test",
                             json={**payload, "seed": 8}).json()
        w7 = {w["start"] for w in r7["windows"]}
        w8 = {w["start"] for w in r8["windows"]}
        assert w7 != w8, "异种子必须产生不同窗口集（防伪随机退化为常量）"

    def test_data_update_idempotent(self, tmp_path, monkeypatch):
        """C6 更新幂等：二次增量运行不重复落行。"""
        from unittest.mock import patch
        import core.data.updater as up_mod

        csv = "calc_time,funding_interval_hours,last_funding_rate\n1785542400000,8,0.0001\n"
        data_dir = tmp_path / "m"
        data_dir.mkdir()

        def _zip_resp():
            import io, zipfile
            from unittest.mock import MagicMock
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("d.csv", csv)
            resp = MagicMock()
            resp.status_code = 200
            resp.content = buf.getvalue()
            return resp

        with patch("core.data.fetcher._http_get", return_value=_zip_resp()):
            up_mod.update_funding_data("BTCUSDT", data_dir=data_dir,
                                       history_start="2026-08-01")
            n1 = len(pd.read_parquet(data_dir / "BTCUSDT_funding.parquet"))
            up_mod.update_funding_data("BTCUSDT", data_dir=data_dir,
                                       history_start="2026-08-01")
            n2 = len(pd.read_parquet(data_dir / "BTCUSDT_funding.parquet"))
        assert n1 == n2 == 1, f"二次更新不得重复落行: {n1} -> {n2}"


class TestFailureRecoveryChain:
    """2.9 失败恢复全链：重组任务 202 → running → failed(reason) → 轮询可见 → rejected 可查。"""

    def test_analysis_failure_chain_via_api(self, api_client, plugin_env, monkeypatch):
        _wire(api_client, plugin_env)
        # 注入必然失败的模板执行：group_a 窗口在数据内但子窗空 → 模板抛错
        resp = api_client.post("/api/research/analysis", json={
            "template": "distribution_compare",
            "params": {"column": "close", "transform": "returns",
                       "group_a": {"start": "2024-03-01T00:00:00Z",
                                   "end": "2024-03-02T00:00:00Z"},
                       "group_b": {"start": "2024-01-10T00:00:00Z",
                                   "end": "2024-02-10T00:00:00Z"}},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2024-01-01T00:00:00Z", "end": "2024-04-25T00:00:00Z"},
        })
        # 快路径：同步失败结构化直返（0.3.2 卡4——原伪 task dict 形态
        # 随缺陷裁定改写：业务拒绝走 422 三段，失败可见可自愈不变）
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "小样本" in detail["message"], "失败原因必须可见"
        assert detail["detail"]["group_a_n"] == 6
