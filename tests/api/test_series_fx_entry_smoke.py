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

    def test_verify_route_fx_series_runs(self, fx_env):
        """主入口②：POST /api/strategies/verify——series fx 策略多窗
        验证 200 且 results[0].error 为 None（引擎构造点透传——fx 列
        装载跑通到验证回测）。"""
        client, sid, _ = fx_env
        r = client.post("/api/strategies/verify", json={
            "strategy_ids": [sid],
            "data_ranges": [{"start": "2024-01-20T00:00:00Z",
                             "end": "2024-02-05T00:00:00Z"}]})
        assert r.status_code == 200, r.text[:300]
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["strategy_id"] == sid
        assert results[0]["error"] is None, \
            f"fx 装载失败会落 error 字段，实际 {results[0]['error']}"

    def test_compare_route_fx_series_runs(self, fx_env):
        """入口③：POST /api/strategies/compare——series fx 策略对比
        回测 200（第三路由构造点同形制透传）。"""
        client, sid, _ = fx_env
        r = client.post("/api/strategies/compare", json={
            "strategy_ids": [sid], "dataset_id": "BTCUSDT_4h"})
        assert r.status_code == 200, r.text[:300]
