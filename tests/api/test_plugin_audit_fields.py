"""公式插件审计两现算字段（alignment-batch β）。

行为层独立样例锚（spec plugin-audit 分层验收）：依赖闭包间接依赖、
环截断（升版互引可造）、warmup=max 不超时、rejected null 态、
响应行与推导函数输出一致（透传层不单独构成验收，此处作一致性防漂）。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.plugin_service import plugin_audit_fields


@pytest.fixture()
def plugin_env(tmp_path: Path):
    """4h 行情 fixture（与 test_research_plugins 同形制，插件求值前置）。"""
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
    from tests.api.test_research_plugins import _ensure_research_tables
    db = _ensure_research_tables(api_client)
    api_client.app.state.db_path = db
    api_client.app.state.data_dir = env["data_dir"]
    return db


def _register(api_client, name, expr):
    r = api_client.post("/api/research/plugins/formula",
                        json={"name": name, "expression": expr})
    assert r.status_code == 200, r.text
    return r.json()


class TestDependencyClosure:
    def test_indirect_dependency_enters_closure(self, api_client, plugin_env):
        """A→B→C 链：A 的闭包含 B 与 C（间接依赖入闭包），含自身。"""
        db = _wire(api_client, plugin_env)
        _register(api_client, "base", "zscore(close, 20)")   # fx_base lookback 20
        _register(api_client, "mid", "diff(fx_base, 3)")     # fx_mid  lookback 3
        _register(api_client, "top", "zscore(fx_mid, 10)")   # fx_top  lookback 10

        out = plugin_audit_fields(db, {"fx_top"})
        assert out["dependency_closure"] == ["fx_base", "fx_mid", "fx_top"]
        # warmup=闭包各表达式 lookback 的 max——间接层 fx_base 的 20 是全链最大
        assert out["warmup_bars"] == 20

    def test_cycle_truncation_via_registry_fixture(self, api_client, plugin_env):
        """A→B→A 互引环：闭包=[A,B] 各计一次含自身，warmup=两表达式
        lookback max 正常计算，不抛错不超时（visited 截断契约——现状即契约）。

        环经 fixture 直写 registry 构造：端点注册路径升版=新列名
        （version≥2 带 _vN 后缀，旧列表达式不变），闭包图恒 DAG——环
        仅数据层历史/手工态出现，正是 visited 截断的防御对象。
        """
        import uuid
        from core.persistence.repository import research as rr

        db = _wire(api_client, plugin_env)
        rr.save_plugin_with_registry(
            db, source="formula", plugin_id="fpl_cyc_a", name="a", version=1,
            expression="zscore(fx_b, 10)", output_columns='["fx_a"]',
            entry_id=f"ent_{uuid.uuid4().hex[:12]}", column_name="fx_a")
        rr.save_plugin_with_registry(
            db, source="formula", plugin_id="fpl_cyc_b", name="b", version=1,
            expression="shift(fx_a, 3)", output_columns='["fx_b"]',
            entry_id=f"ent_{uuid.uuid4().hex[:12]}", column_name="fx_b")

        out = plugin_audit_fields(db, {"fx_a"})
        assert out["dependency_closure"] == ["fx_a", "fx_b"]
        assert out["warmup_bars"] == 10  # max(zscore(fx_b,10)=10, shift(fx_a,3)=3)

    def test_self_reference_closure_single_entry(self, api_client, plugin_env):
        """A→A 自引用（表达式引用自身输出列）：闭包=仅自身一次、
        warmup 照常计算、不抛错不超时（visited 截断对自引用同样
        成立——spec plugin-audit 正文「自引用/环语义断言」的另一半，
        与互引环例互补；现状记录语义）。"""
        import uuid
        from core.persistence.repository import research as rr

        db = _wire(api_client, plugin_env)
        rr.save_plugin_with_registry(
            db, source="formula", plugin_id="fpl_selfref", name="selfref",
            version=1, expression="zscore(fx_selfref, 10)",
            output_columns='["fx_selfref"]',
            entry_id=f"ent_{uuid.uuid4().hex[:12]}", column_name="fx_selfref")

        out = plugin_audit_fields(db, {"fx_selfref"})
        assert out["dependency_closure"] == ["fx_selfref"]
        assert out["warmup_bars"] == 10


class TestAuditResponseContract:
    def test_response_rows_carry_fields_matching_derivation(self, api_client, plugin_env):
        """GET /plugins/formula 行含两字段且与推导函数输出一致（防透传漂移）。"""
        db = _wire(api_client, plugin_env)
        _register(api_client, "base", "zscore(close, 20)")
        _register(api_client, "mid", "diff(fx_base, 3)")

        resp = api_client.get("/api/research/plugins/formula")
        assert resp.status_code == 200
        rows = {r["name"]: r for r in resp.json()["items"]}
        assert set(rows) >= {"base", "mid"}
        for name in ("base", "mid"):
            cols = set(rows[name]["output_columns"] or [])
            expected = plugin_audit_fields(db, cols)
            assert rows[name]["warmup_bars"] == expected["warmup_bars"]
            assert rows[name]["dependency_closure"] == expected["dependency_closure"]

    def test_rejected_row_yields_null_warmup(self, api_client, plugin_env):
        """rejected 行（未入 registry）：warmup_bars null + closure []（无推导依据）。"""
        _wire(api_client, plugin_env)
        bad = api_client.post("/api/research/plugins/formula",
                              json={"name": "bad", "expression": "zscore(nonexistent_col, 10)"})
        assert bad.status_code == 422, bad.text

        resp = api_client.get("/api/research/plugins/formula", params={"name": "bad"})
        rows = resp.json()["items"]
        assert len(rows) >= 1
        assert rows[0]["status"] == "rejected"
        assert rows[0]["warmup_bars"] is None
        assert rows[0]["dependency_closure"] == []

    def test_empty_columns_yield_null(self, api_client, plugin_env):
        """空 registry（无插件注册）：空列集 → null/[]（与「无窗口项贡献 0」区分）。"""
        db = _wire(api_client, plugin_env)
        out = plugin_audit_fields(db, set())
        assert out["warmup_bars"] is None
        assert out["dependency_closure"] == []
