"""series 档 fx 形式五入口 smoke（dynamic-stop-loss-series CARD-04/
S-2——路由级行为面锚）。

五引擎构造点补 db_path 透传后：series 档 fx 形式止损线经策略中心
backtest/verify/compare 路由可用（引擎构造拿到 db_path→插件注册表
可查→fx 列装载不再显式拒）。透传缺失时 fx 形式装载抛
RESEARCH_DATA_INSUFFICIENT（fx 须 db_path）→ 路由结构化错——
200/无 error 即透传生效的检出形制（真引擎真装载，无 mock）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

_SRC_RISK = {"sl_mode": "series",
             "sl_series_source": None,  # 各用例注入 fx 列名
             "take_profit": 0.10, "position_size": 0.3}


def _series_dna_dict(fx_col: str) -> dict:
    risk = dict(_SRC_RISK)
    risk["sl_series_source"] = {"column": fx_col}
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}}],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": risk,
    }


@pytest.fixture()
def fx_env(api_client):
    """真行情数据（300 根随机游走）+db 内注册 fx 数值线列+series fx
    策略入库——返回 (client, strategy_id)。"""
    from core.analysis.plugin_service import register_formula_plugin
    from core.persistence.db_ext import init_db_ext
    db = api_client.app.state.db_path
    init_db_ext(db)
    col = register_formula_plugin(
        db, name="sl_route_fx",
        expression="rolling_mean(close, 5)")["column"]
    data_dir = api_client.app.state.data_dir
    rng = np.random.default_rng(7)
    n = 300
    steps = rng.normal(0, 80, n)
    close = 60000 + np.cumsum(steps)
    pd.DataFrame({
        "open": close, "high": close + 60, "low": close - 60,
        "close": close, "volume": rng.uniform(100, 900, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                           tz="UTC", name="timestamp")).to_parquet(
        data_dir / "BTCUSDT_4h.parquet")
    r = api_client.post("/api/strategies", json={
        "name": "series fx smoke", "dna": _series_dna_dict(col),
        "symbol": "BTCUSDT", "timeframe": "4h"})
    assert r.status_code == 201, r.text
    return api_client, r.json()["strategy_id"], col


class TestSeriesSystemChain:
    """阶段四闸门⑤层级补全：system 级端到端链——研究登记→translate
    →策略中心回测（series 档全生命周期一条链，公开入口全程）。"""

    def test_register_translate_backtest_chain(self, api_client):
        """登记（POST /api/research/hypotheses，series 声明）→translate
        （POST /api/research/translate/{hid}，17 键产物）→回测（POST
        /api/strategies/backtest strategy_id 分支）→200——三段路由
        一条链。"""
        rng = np.random.default_rng(3)
        n = 300
        steps = rng.normal(0, 70, n)
        close = 60000 + np.cumsum(steps)
        pd.DataFrame({
            "open": close, "high": close + 50, "low": close - 50,
            "close": close, "volume": rng.uniform(100, 900, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp")).to_parquet(
            api_client.app.state.data_dir / "BTCUSDT_4h.parquet")
        r_reg = api_client.post("/api/research/hypotheses", json={
            "conditions": {
                "when_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 14}, "field": "rsi"},
                     "action": "lt", "target": 45}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 14}, "field": "rsi"},
                     "action": "gt", "target": 60}]},
            "statement": "series 全链",
            "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                             "start": "2024-01-20T00:00:00Z",
                             "end": "2024-02-15T00:00:00Z"}],
            "strategy_params": {
                "sl_mode": "series",
                "sl_series_source": {"indicator": "BB",
                                     "params": {"period": 20, "std": 2},
                                     "field": "lower"}},
        })
        assert r_reg.status_code == 200, r_reg.text[:300]  # 登记幂等形制 200
        hid = r_reg.json()["hypothesis_id"]
        r_tr = api_client.post(f"/api/research/translate/{hid}")
        assert r_tr.status_code == 200, r_tr.text[:300]
        out = r_tr.json()
        assert out["risk_genes_disclosure"]["values"][
            "sl_series_source"]["indicator"] == "BB"
        sid = out["strategy_id"]
        r_bt = api_client.post("/api/strategies/backtest", json={
            "strategy_id": sid, "dataset_id": "BTCUSDT_4h"})
        assert r_bt.status_code == 200, r_bt.text[:300]
        assert "total_trades" in r_bt.json()


class TestFxSeriesRouteSmoke:
    def test_backtest_route_fx_series_runs(self, fx_env):
        """主入口①：POST /api/strategies/backtest——series fx 策略真
        引擎回测 200（db_path 透传缺失时 fx 形式装载显式拒即红）。"""
        client, sid, _ = fx_env
        r = client.post("/api/strategies/backtest", json={
            "strategy_id": sid, "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005})
        assert r.status_code == 200, r.text[:300]
        assert "total_trades" in r.json()

    def test_compare_route_fx_series_runs(self, fx_env):
        """入口③：POST /api/strategies/compare——series fx 策略对比
        回测 200（第三路由构造点同形制透传）。"""
        client, sid, _ = fx_env
        r = client.post("/api/strategies/compare", json={
            "strategy_ids": [sid], "dataset_id": "BTCUSDT_4h"})
        assert r.status_code == 200, r.text[:300]


def _series_indicator_dna_dict() -> dict:
    """series 档 indicator 形式 DNA dict（BB(20,2) lower——指标 rolling
    预热期真 NaN，inline 直传形制）。"""
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}}],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {
            "sl_mode": "series",
            "sl_series_source": {"indicator": "BB",
                                 "params": {"period": 20, "std": 2},
                                 "field": "lower"},
            "take_profit": 0.10, "position_size": 0.3},
    }


def _pct_dna_dict() -> dict:
    """pct 档 DNA dict（inline 直传形制——Lab 页 POST backtest dna 分支）。"""
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}}],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"sl_mode": "pct", "stop_loss": 0.05,
                       "take_profit": 0.10, "position_size": 0.3},
    }


class TestSlSeriesRestFace:
    """REST 面 sl_series 透传锚（hypothesis-workbench 审查 CARD-01 (a)：
    BacktestResponse 扩键+组装段 NaN→null——路由级行为面，与 bridge 面
    （tests/backtest/test_sl_series_output）双点对拍）。

    spec stop-loss-modes「三入口响应透传 [{time,value}]」——REST 端点
    直调 engine（请求段不切回），NaN→null 在组装点翻译（JSON 无 NaN
    字面——前端解析前提）。
    """

    def test_backtest_response_sl_series_fx_branch(self, fx_env):
        """strategy_id 分支：series fx 策略 REST 回测 sl_series 请求段
        全段透出（300 点），首值=fx 列首值（rolling_mean min_periods=1
        语义——formula DSL :47 既定形制，首点=窗口 1 均值=close 自身），
        时间形制与 bridge 同源。"""
        client, sid, _ = fx_env
        r = client.post("/api/strategies/backtest", json={
            "strategy_id": sid, "dataset_id": "BTCUSDT_4h"})
        assert r.status_code == 200, r.text[:300]
        sl = r.json()["sl_series"]
        assert sl is not None and len(sl) == 300  # REST=请求段全段（不切回）
        # 手工锚：fx=rolling_mean(close,5) 头部非 NaN（min_periods=1
        # 部分窗口），首值=首根 close（独立读 parquet 自算）
        px = pd.read_parquet(
            client.app.state.data_dir / "BTCUSDT_4h.parquet")["close"]
        assert sl[0]["value"] == pytest.approx(px.iloc[0], rel=1e-9)
        assert sl[1]["value"] == pytest.approx(px.iloc[:2].mean(), rel=1e-9)
        # 时间形制与 bridge 单点同源（str(t) 空格分隔+UTC 偏移——前端
        # toTime 消费前提，形制锚全文见 TestBridgeTimeForm）
        t0 = sl[0]["time"]
        assert " " in t0 and "T" not in t0 and t0.endswith("+00:00")

    def test_backtest_response_sl_series_nan_to_null(self, fx_env):
        """inline dna 分支+indicator 形式（BB(20,2) lower——指标 rolling
        预热期真 NaN）：头部 20 点 value null（NaN→null 翻译，JSON 合法），
        首个有效值=lower=mean-2*std（pandas 独立自算）。"""
        client, _, _ = fx_env
        dna = _series_indicator_dna_dict()
        r = client.post("/api/strategies/backtest", json={
            "dna": dna, "symbol": "BTCUSDT",
            "timeframe": "4h", "dataset_id": "BTCUSDT_4h"})
        assert r.status_code == 200, r.text[:300]
        sl = r.json()["sl_series"]
        assert sl is not None and len(sl) == 300
        # NaN→null：BB(20) 头部 19 bar rolling 预热期无定义（index 0-18，
        # index 19=第 20 根起首个完整窗口）
        assert [p["value"] for p in sl[:19]] == [None] * 19
        # 首个有效值手工锚：lower = rolling20 mean - 2*rolling20 std
        #（ddof=1，独立自算不经被测实现）
        px = pd.read_parquet(
            client.app.state.data_dir / "BTCUSDT_4h.parquet")["close"]
        lower19 = (px.rolling(20).mean() - 2 * px.rolling(20).std()).iloc[19]
        assert sl[19]["value"] == pytest.approx(lower19, rel=1e-9)

    def test_backtest_response_non_series_null(self, fx_env):
        """非 series 档（pct，inline dna 分支——Lab 直传形制）：sl_series
        null 透传（非空序列非零值）。"""
        client, _, _ = fx_env
        r = client.post("/api/strategies/backtest", json={
            "dna": _pct_dna_dict(), "symbol": "BTCUSDT",
            "timeframe": "4h", "dataset_id": "BTCUSDT_4h"})
        assert r.status_code == 200, r.text[:300]
        assert r.json()["sl_series"] is None
