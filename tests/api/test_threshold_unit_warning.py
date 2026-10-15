"""条件阈值单位可疑警告（add-threshold-unit-warning C1~C19）。

覆盖 tasks C1/C2 完成标准八项+判定函数 unit 段（C16）+MCP 披露
（C17）+与 change A 组合（C18）。警告纯观察面：校验链/幂等/指纹/
翻译/验证零改动（C10 hash 对拍锚）。
"""

import inspect
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import hash_json, register_hypothesis
from core.analysis.registry_meta import is_decimal_ratio_subject
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import get_hypothesis


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


def _oi_cond(target=-2.33, action="gt"):
    return {
        "when_conditions": [
            {"subject": {"indicator": "OI_ChangeRate",
                         "params": {"period": 14}, "field": "oi_change_rate"},
             "action": action, "target": target}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 70}],
    }


def _register(db, conditions, direction=None):
    return register_hypothesis(
        db, conditions=conditions, statement="单位警告测试",
        data_ranges=_ranges(), direction=direction)


# ── C16/C4：判定函数 unit 段 ─────────────────────────────────

class TestIsDecimalRatioSubject:
    @pytest.mark.parametrize("indicator", [
        "CMF", "OI_ChangeRate", "OIPriceDivergence", "FundingPressure"])
    def test_members_dict_form(self, indicator):
        assert is_decimal_ratio_subject(
            {"indicator": indicator, "params": {"period": 14},
             "field": "x"}) is True

    def test_funding_rate_string_form(self):
        assert is_decimal_ratio_subject("funding_rate") is True

    @pytest.mark.parametrize("indicator", [
        "TRIX", "ROC", "VROC", "RSI", "Stochastic", "OI_ZScore", "CCI",
        "RVOL", "EMA", "MACD"])
    def test_exempt_indicators(self, indicator):
        """豁免各族代表——TRIX 为对质实证纠错点（pandas_ta scalar=100
        百分数形制）必设。"""
        assert is_decimal_ratio_subject(
            {"indicator": indicator, "params": {"period": 14},
             "field": "x"}) is False

    @pytest.mark.parametrize("col", ["close", "volume", "open",
                                     "fx_ma20_deviation_v2", "model_x"])
    def test_exempt_base_and_plugin_columns(self, col):
        assert is_decimal_ratio_subject(col) is False

    def test_other_types_false(self):
        assert is_decimal_ratio_subject(None) is False
        assert is_decimal_ratio_subject(123) is False


# ── C1~C8：登记链警告行为（服务层直调） ──────────────────────

class TestWarningBehavior:
    def test_c1_c2_oi_change_rate_warns_with_four_elements(self, db):
        """C1 核心：OI_ChangeRate target=-2.33 → 警告四要素齐备。"""
        r = _register(db, _oi_cond())
        w = r.get("threshold_unit_warnings")
        assert w and len(w) == 1
        entry = w[0]
        assert entry["field"] == "when_conditions[0]"
        assert entry["column"] == "oi_change_rate_14"
        assert entry["target"] == -2.33
        assert "单位" in entry["hint"] and "不阻断" in entry["hint"]

    @pytest.mark.parametrize("subject,action,target", [
        ({"indicator": "ROC", "params": {"period": 14}, "field": "roc"},
         "gt", -2.33),   # ROC 为曾错判点必设（百分数形制豁免）
        ("close", "gt", 60000),
        ({"indicator": "RSI", "params": {"period": 14}, "field": "rsi"},
         "gt", 70),
    ])
    def test_c2_no_warning_for_exempt(self, db, subject, action, target):
        cond = {
            "when_conditions": [
                {"subject": dict(subject) if isinstance(subject, dict)
                 else subject, "action": action, "target": target}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"}, "action": "gt", "target": 70}],
        }
        r = _register(db, cond)
        assert "threshold_unit_warnings" not in r

    def test_c3_numeric_string_same_judgment(self, db):
        """C3：数值字符串与数值同判（canonical 化不改 target 原值）。"""
        r = _register(db, _oi_cond(target="-2.33"))
        w = r.get("threshold_unit_warnings")
        assert w and w[0]["target"] == -2.33

    def test_c5_five_groups_covered_with_field_prefix(self, db):
        """C5：五组条件各构造越界 → 各产生条目且 field 前缀对组名。"""
        oi_item = {"subject": {"indicator": "OI_ChangeRate",
                               "params": {"period": 14},
                               "field": "oi_change_rate"},
                   "action": "gt", "target": 2.5}
        rsi_item = {"subject": {"indicator": "RSI", "params": {"period": 14},
                                "field": "rsi"}, "action": "gt", "target": 70}
        cond = {
            "when_conditions": [oi_item],
            "then_conditions": [rsi_item],
            "exit_partial_conditions": [rsi_item],
            "add_trigger_conditions": [oi_item],
            "add_suppress_conditions": [rsi_item],
        }
        # exit_fraction 组配对（数值键须有宿主组）
        r = register_hypothesis(
            db, conditions=cond, statement="五组",
            data_ranges=_ranges(), strategy_params={"exit_fraction": 0.5})
        w = r.get("threshold_unit_warnings")
        fields = sorted(e["field"] for e in w)
        assert fields == ["add_trigger_conditions[0]", "when_conditions[0]"]

    def test_c6_price_above_below_exempt(self, db):
        """C6：price_above/price_below 的 target 为占位——豁免。"""
        r = _register(db, _oi_cond(target=99999, action="price_above"))
        assert "threshold_unit_warnings" not in r

    def test_c7_column_name_target_exempt(self, db):
        """C7：列名 target（序列引用形）float 失败跳过。"""
        r = _register(db, _oi_cond(target="ema_20"))
        assert "threshold_unit_warnings" not in r

    def test_c8_boundary_abs_one_not_warned(self, db):
        """C8：|target|=1 边界不警（abs>1 严格）。"""
        r = _register(db, _oi_cond(target=1.0))
        assert "threshold_unit_warnings" not in r
        r2 = _register(db, _oi_cond(target=-1.0))
        assert "threshold_unit_warnings" not in r2

    def test_c9_idempotent_hit_not_carried(self, db):
        """C9：幂等命中不携带（沿 precheck 先例）。"""
        cond = _oi_cond()
        # 独立条件集避免与其他用例身份碰撞
        cond["when_conditions"][0]["target"] = -3.5
        r1 = _register(db, cond)
        assert r1.get("threshold_unit_warnings")
        r2 = _register(db, cond)
        assert r2["duplicated"] is True
        assert "threshold_unit_warnings" not in r2

    def test_c10_conditions_and_hash_unchanged(self, db):
        """C10：警告零阻断零身份影响——conditions 字面与 hash 对拍。"""
        cond_warn = _oi_cond(target=-2.33)
        cond_plain = _oi_cond(target=-0.0233)
        r1 = _register(db, cond_warn)
        r2 = _register(db, cond_plain)
        c1 = json.loads(get_hypothesis(db, r1["hypothesis_id"])["conditions"])
        c2 = json.loads(get_hypothesis(db, r2["hypothesis_id"])["conditions"])
        assert hash_json(c1) != hash_json(c2)  # target 值不同身份本应不同
        # 同一警告条件不带警告路径重登（终态后）：conditions 字面一致
        from core.analysis.hypothesis_service import transition_hypothesis
        transition_hypothesis(db, r1["hypothesis_id"],
                              target_status="abandoned", evidence_ref="t")
        r1b = _register(db, cond_warn)
        c1b = json.loads(
            get_hypothesis(db, r1b["hypothesis_id"])["conditions"])
        assert hash_json(c1) == hash_json(c1b), "警告不改 conditions 字面"
        assert r1b.get("threshold_unit_warnings"), "警告仍照常携带"

    def test_c11_warned_hypothesis_translates(self, db):
        """C11：带警告假设照常 translate（零阻断）。"""
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        cond = _oi_cond(target=-2.33)
        cond["when_conditions"][0]["target"] = -4.5
        r = _register(db, cond)
        assert r.get("threshold_unit_warnings")
        t = translate_hypothesis(db, hypothesis_id=r["hypothesis_id"])
        assert t["status"] == "draft"


# ── C13/C14/C15：REST 面与两键并存 ───────────────────────────

class TestRestFaceAndCoexistence:
    @pytest.fixture()
    def env(self, tmp_path: Path):
        import numpy as np
        import pandas as pd
        n = 500
        rng = np.random.default_rng(11)
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 120, n))
        oi = 1e6 * np.cumprod(np.where(
            np.arange(n) % 14 == 0, 1.0001, 1.0))  # 温和平稳 OI
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        pd.DataFrame({
            "open": close, "high": close + 80, "low": close - 80,
            "close": close, "volume": rng.uniform(100, 1000, n),
            "trades": 100, "open_interest": oi,
        }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        return {"db": db, "data_dir": data_dir}

    def test_c13_c14_two_keys_coexist_and_distinguishable(
            self, env, api_client):
        """C14（灵魂用例）：「单位疑似写错」与「样本量不足/触发为零」
        同场并存且可区分——产品 R4 核心语义。REST 面经 TestClient。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        body = {
            "conditions": {
                "when_conditions": [
                    {"subject": {"indicator": "OI_ChangeRate",
                                 "params": {"period": 14},
                                 "field": "oi_change_rate"},
                     "action": "gt", "target": -2.33}],  # 永不触发+越界
                "then_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 14},
                                 "field": "rsi"},
                     "action": "gt", "target": 70}],
            },
            "statement": "并存测试",
            "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                             "start": "2024-02-20T00:00:00Z",
                             "end": "2024-03-20T00:00:00Z"}],
        }
        resp = api_client.post("/api/research/hypotheses", json=body)
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out.get("threshold_unit_warnings"), "单位警告键在场"
        assert out.get("precheck_warnings"), "预检键在场（触发为零形态）"
        # 两键语义可区分：单位警告指向列与 target；预检指向三态结论
        assert out["threshold_unit_warnings"][0]["column"] \
            == "oi_change_rate_14"
        assert "verdict" in out["precheck_warnings"]

    def test_c18_direction_short_composition(self, env, api_client):
        """C18（依赖 change A）：direction=short 与警告同场——两纯增量
        键互不干扰。"""
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        body = {
            "conditions": {
                "when_conditions": [
                    {"subject": {"indicator": "CMF",
                                 "params": {"period": 20},
                                 "field": "cmf"},
                     "action": "lt", "target": -1.5}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 14},
                                 "field": "rsi"},
                     "action": "gt", "target": 70}],
            },
            "statement": "组合测试", "direction": "short",
            "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                             "start": "2024-02-20T00:00:00Z",
                             "end": "2024-03-20T00:00:00Z"}],
        }
        resp = api_client.post("/api/research/hypotheses", json=body)
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["direction"] == "short"
        assert out.get("threshold_unit_warnings")
        assert out["threshold_unit_warnings"][0]["column"] == "cmf_20"


# ── C17：MCP description 披露 ────────────────────────────────

class TestMcpDisclosure:
    def test_c17_description_carries_key_semantics(self):
        class _FakeMcp:
            registered = {}

            def tool(self):
                def deco(fn):
                    self.registered[fn.__name__] = fn
                    return fn
                return deco

        class _FakeApi:
            def post(self, url, body):
                return {}

        from mcpserver import tools as tools_mod
        mcp, api = _FakeMcp(), _FakeApi()
        tools_mod.register_research_tools(mcp, api)
        doc = inspect.getdoc(mcp.registered["register_hypothesis"])
        assert "threshold_unit_warnings" in doc
        assert "小数比率语义" in doc
        assert "不阻断登记" in doc
        assert "precheck_warnings" in doc, "与 precheck 的区分语义在文"
