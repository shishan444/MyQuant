"""工程回测端点周期守卫锚（fix-mtf-execution-consistency T⑧ 端点面）。

挂点②（api/routes/strategies/backtest.py 装载前）行为锚：
- 显式异轴（内联 DNA 4h + timeframe="1d"）→ 422 RESEARCH_TIMEFRAME_
  MISMATCH，detail 四键——与桥入口（挂点①）同码同面（守卫单源）；
- 同轴对照不拦（timeframe=dna 执行周期 → 正常回测 200）。
"""
import pytest

pytestmark = [pytest.mark.integration]


def _dna(exec_tf: str = "4h") -> dict:
    """单周期内联 DNA（无层无标注——守卫面最小载体）。"""
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "lt", "threshold": 35}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 65}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": exec_tf, "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                       "position_size": 0.3},
    }


def _seed_market(api_client) -> None:
    import numpy as np
    import pandas as pd

    data_dir = api_client.app.state.data_dir
    rng = np.random.default_rng(3)
    for tf, n, sigma in (("4h", 300, 100), ("1d", 300, 200)):
        idx = pd.date_range("2024-01-01", periods=n, freq=tf,
                            tz="UTC", name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, sigma, n))
        spread = sigma / 2
        pd.DataFrame({"open": close, "high": close + spread,
                      "low": close - spread, "close": close,
                      "volume": rng.uniform(100, 800, n)},
                     index=idx).to_parquet(data_dir / f"BTCUSDT_{tf}.parquet")


class TestBacktestEndpointTimeframeGuard:
    def test_explicit_mismatch_422_same_face_as_bridge(self, api_client):
        """显式异轴：端点 422 RESEARCH_TIMEFRAME_MISMATCH detail 四键
        （与桥入口同码同面——守卫单源双挂）。"""
        _seed_market(api_client)
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": _dna("4h"),
            "symbol": "BTCUSDT", "timeframe": "1d",
            "dataset_id": "BTCUSDT_1d",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
        })
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_TIMEFRAME_MISMATCH"
        d = body["detail"]["detail"]
        assert d["request_timeframe"] == "1d"
        assert d["dna_execution_timeframe"] == "4h"
        assert d["strategy_id"] == "inline"
        assert "基准周期" in d["need_hint"]

    def test_same_timeframe_not_blocked(self, api_client):
        """同轴对照：timeframe=dna 执行周期 → 正常回测不拦（200）。"""
        _seed_market(api_client)
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": _dna("4h"),
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
        })
        assert resp.status_code == 200, resp.text
