"""strategies 回测端点 ResearchError 传播配套测试（fix-review CARD-08）。

执行器 raise 化（结构化拒绝）后，POST /api/strategies/backtest 对经
engine.run 冒泡的 ResearchError（非法条件类型等）转 422 结构化 body
（detail 三段 code/message/detail），不裸穿 500——与验证端点
research-error-channel catch 形制（validate.py → research_error_
to_http）对齐。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient  # noqa: F401（fixture 类型标注用）


def _bad_dna() -> dict:
    """内联 DNA：entry 基因条件 type 在执行器合法集外。"""
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14},
             "role": "entry_trigger", "field": None,
             "condition": {"type": "no_such_type", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": None,
             "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                       "position_size": 0.3},
    }


def _seed_market(api_client) -> None:
    """造 300 根 4h bar（指标 warmup 充足，装载链走通）。"""
    import numpy as np
    import pandas as pd

    data_dir = api_client.app.state.data_dir
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=300, freq="4h",
                        tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, 300))
    pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 800, 300),
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")


class TestBacktestResearchErrorChannel:
    def test_unknown_condition_type_422_structured(self, api_client):
        """非法 cond_type 经 engine.run 冒泡 → 422 + detail 三段
        （修复前：ResearchError 裸穿 500）。"""
        _seed_market(api_client)
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": _bad_dna(),
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
        })
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_NOT_EXPRESSIBLE"
        assert body["detail"]["detail"]["condition_type"] == "no_such_type"
        assert "legal_types" in body["detail"]["detail"]

    def test_legal_dna_still_backtests(self, api_client):
        """守卫不误伤：合法 DNA 正常回测 200（对照锚）。"""
        _seed_market(api_client)
        good = _bad_dna()
        good["signal_genes"][0]["condition"] = {"type": "lt", "threshold": 30}
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": good,
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["total_trades"] >= 0
