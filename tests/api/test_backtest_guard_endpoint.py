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


def _seed_market(api_client):
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
    return api_client.app.state.db_path


class TestBacktestEndpointTimeframeGuard:
    def test_explicit_mismatch_422_same_face_as_bridge(self, api_client):
        """显式异轴：端点 422 RESEARCH_TIMEFRAME_MISMATCH detail 四键
        （与桥入口同码同面——守卫单源双挂）。"""
        db = _seed_market(api_client)
        # retire-legacy 后 REST backtest 端点退役——内联 DNA 面先落库
        # 再经 sandbox 通道触发（桥内 ensure_timeframe_consistent 单点）
        import json as _json
        from core.persistence.repository.strategy import save_strategy
        save_strategy(db, strategy_id="st_guard_ep",
                      dna_json=_json.dumps(_dna("4h")),
                      symbol="BTCUSDT", timeframe="4h")
        resp = api_client.post("/api/research/backtest-sandbox", json={
            "strategy_id": "st_guard_ep",
            "symbol": "BTCUSDT", "timeframe": "1d",
            "start": "2024-02-05", "end": "2024-02-20",
        })
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_TIMEFRAME_MISMATCH"
        d = body["detail"]["detail"]
        assert d["request_timeframe"] == "1d"
        assert d["dna_execution_timeframe"] == "4h"
        # need_hint 指引语义：缺省自洽+换轴重建策略（CARD-05——
        # translate 退役后不指假设侧，与 test_backtest_guard t8_1 同锚）
        assert "自洽验证" in d["need_hint"]
        assert "重建策略" in d["need_hint"]

    def test_same_timeframe_not_blocked(self, api_client):
        """同轴对照：timeframe=dna 执行周期 → 正常回测不拦（200）。"""
        db = _seed_market(api_client)
        import json as _json
        from core.persistence.repository.strategy import save_strategy
        save_strategy(db, strategy_id="st_guard_ep2",
                      dna_json=_json.dumps(_dna("4h")),
                      symbol="BTCUSDT", timeframe="4h")
        resp = api_client.post("/api/research/backtest-sandbox", json={
            "strategy_id": "st_guard_ep2",
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-02-05", "end": "2024-02-20",
        })
        assert resp.status_code == 200, resp.text
