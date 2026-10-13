"""价格比较动作补实现测试（fix-research-c2-price-actions · C2.4）。

缺陷三（fr_cfbcab214136）：指标目录声明 price_above/price_below 但
执行层动作分派表无实现——未识别 action 走「warnings+全 False 掩码」
静默降级，研究者误读为「该条件历史上从未发生」。本文件锁修复后的
三层行为：
- 机制层：两键语义（close vs 指标列）+ target 占位契约 + 多指标正向；
- 拒绝面：未识别 action 结构化 422（legal_values=分派表键集）；
- 静默面边界：subject 缺列与 target 不可解析的既有 warnings 契约
  逐字不动（三层各管一段——插件列 subject 缺失由 C3 批次装载段收口）；
- hint 层：RESEARCH_PARAM_INVALID 按 detail.param 场景分派（fr_ce325c609a8d）；
- 链路层：TestClient 全链路（200 正常统计+422 结构化错误透传）。

A1 双写法一致性锚（price_below 与 lt 同得 1119，真实数据）在
tests/analysis/test_research_regression_anchors.py 随本批次启用；
A1 独立对照参照系（BB std=2 列值与库默认调用逐元素一致）已在
tests/infra/test_indicator_params_effective.py 的
test_std2_default_path_matches_library_default 覆盖（C1 批次）。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError
from core.validation.engine import _evaluate_single_condition
from mcpserver.errors import error_payload
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def df() -> pd.DataFrame:
    """250 bar 确定性数据（含已知指标列）。"""
    d = make_ohlcv(n=250, freq="1h", start="2024-01-01", seed=9)
    d["ema_10"] = d["close"].rolling(10).mean().shift(1)  # 人为下移使两向都非空
    d["sma_5"] = d["close"].rolling(5).mean()
    return d


def _mask(df, cond):
    return _evaluate_single_condition(df, cond, None)


class TestPriceActionsSemantics:
    """机制层：两键语义与 target 占位契约。"""

    def test_price_above_below_vs_manual(self, df):
        """语义=close 与 subject 列比较（回测面 executor 先例逐字对齐）。"""
        above = _mask(df, {"subject": "ema_10", "action": "price_above", "target": 0})
        below = _mask(df, {"subject": "ema_10", "action": "price_below", "target": 0})
        pd.testing.assert_series_equal(above, df["close"] > df["ema_10"], check_names=False)
        pd.testing.assert_series_equal(below, df["close"] < df["ema_10"], check_names=False)
        assert bool((above & below).sum()) == 0  # 两向互斥
        assert int(above.sum()) > 0 and int(below.sum()) > 0

    def test_target_placeholder_contract(self, df):
        """target 占位契约：数值 0/99 与列名 target 结果逐位一致。"""
        base = _mask(df, {"subject": "ema_10", "action": "price_above", "target": 0})
        for t in (99, -1.5, "sma_5"):
            other = _mask(df, {"subject": "ema_10", "action": "price_above", "target": t})
            pd.testing.assert_series_equal(base, other, check_names=False)

    def test_positive_on_ema_and_sma(self, df):
        """多指标声明面正向：EMA/SMA 各一非空样本。"""
        for subj in ("ema_10", "sma_5"):
            m = _mask(df, {"subject": subj, "action": "price_above", "target": 0})
            assert int(m.sum()) > 0, f"{subj} price_above 应有命中"


class TestUnknownActionReject:
    """拒绝面：未识别 action 结构化拒绝（消灭全 False 静默降级）。"""

    def test_unknown_action_raises_with_legal_values(self, df):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(
                df, {"subject": "ema_10", "action": "stays_above", "target": 0}, None)
        detail = ei.value.detail if isinstance(ei.value.detail, dict) else {}
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert detail.get("param") == "condition.action"
        assert "stays_above" in str(detail.get("value", ""))
        legal = detail.get("legal_values", [])
        assert "price_above" in legal and "price_below" in legal
        assert "gt" in legal and "cross_above" in legal  # 键集为单一事实源

    def test_no_false_mask_on_unknown(self, df):
        """不再产出全 False 掩码——raise 先于掩码返回（静默点已消灭）。"""
        with pytest.raises(ResearchError):
            _mask(df, {"subject": "ema_10", "action": "nope", "target": 0})


class TestSilentBoundaryUnchanged:
    """静默面边界回归：既有 warnings 契约逐字不动。

    subject 缺列（0.2.13 安全降级契约）与 target 不可解析（装载层
    strict 通道拦截后的兜底）保持「全 False + warning」——本批次只动
    action 维度，三层各管一段。
    """

    def test_missing_subject_column_still_warns(self, df):
        w = []
        out = _evaluate_single_condition(
            df, {"subject": "not_a_col", "action": "gt", "target": 1}, w)
        assert bool(out.sum()) == 0 and len(w) == 1

    def test_unresolvable_target_still_warns(self, df):
        w = []
        out = _evaluate_single_condition(
            df, {"subject": "ema_10", "action": "gt", "target": "not_a_col"}, w)
        assert bool(out.sum()) == 0 and len(w) == 1


class TestHintScenarioDispatch:
    """hint 层：RESEARCH_PARAM_INVALID 按 detail.param 场景分派
    （fr_ce325c609a8d——共用错误码下文案与场景错配）。"""

    def test_action_scene_hint(self):
        p = error_payload(
            "RESEARCH_PARAM_INVALID", "未识别的比较动作",
            {"param": "condition.action", "legal_values": ["gt", "lt"]})
        assert "legal_values" in p["hint"] and "动作" in p["hint"]

    def test_forward_bars_scene_hint(self):
        p = error_payload(
            "RESEARCH_PARAM_INVALID", "持有期非法",
            {"param": "forward_bars"})
        assert "forward_bars" in p["hint"]

    def test_no_scene_falls_back_to_generic(self):
        p = error_payload(
            "RESEARCH_PARAM_INVALID", "其他参数面", {"param": "whatever"})
        assert p["hint"]  # 回退注册表通用文案（非空、非场景句）
        assert "whatever" not in p["hint"]

    def test_other_code_untouched(self):
        p = error_payload("RESEARCH_COLUMN_NOT_AVAILABLE", "列不可用", {"param": "x"})
        assert "模板参数非法" not in p["hint"]  # 分派只及 PARAM_INVALID


class TestRouteFullChain:
    """链路层：TestClient 全链路——200 统计与 422 结构化透传。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        # 独立目录名：conftest 的 tmp_data_dir fixture 已占用 tmp_path/"data"
        # （test_analysis_warmup_route 同款 "market" 规避）。1000 bar：EMA 周期
        # 上限 200 → warmup=600 bar，请求段须落在 600 bar 后。
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        d = make_ohlcv(n=1000, freq="1h", start="2024-01-01", seed=3)
        d.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        from core.persistence.db_ext import init_db_ext
        db = tmp_path / "q.db"
        init_db_ext(db)
        return {"data_dir": data_dir, "db": db, "df": d}

    def _post(self, api_client, env, condition, indicator="EMA"):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        df = env["df"]
        return api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": indicator, "params": {"period": 20},
                       "condition": condition},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "1h",
                           "start": df.index[700].isoformat(),
                           "end": df.index[850].isoformat()}})

    def test_price_below_returns_real_stats(self, api_client, env):
        """price_below 全链路返回真实样本统计（非零样本拒绝兜底）。"""
        resp = self._post(api_client, env, {
            "subject": {"indicator": "EMA", "params": {"period": 20},
                        "field": "ema"},
            "action": "price_below", "target": 0})
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["hit_count"] > 0  # 缺陷修复前此处是 0 样本拒绝

    def test_unknown_action_422_through_route(self, api_client, env):
        """未知 action 经路由透传 422（结构化 detail 携 legal_values）。"""
        resp = self._post(api_client, env, {
            "subject": "close", "action": "nope", "target": 1})
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_PARAM_INVALID"
        assert "price_above" in body["detail"]["legal_values"]
