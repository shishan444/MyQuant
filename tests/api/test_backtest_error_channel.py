"""strategies 回测端点错误通道测试（fix-review CARD-08 + CARD-01 后口径）。

两条通道各锚其一：
- 前置校验通道（CARD-01）：内联 DNA 在 to_dna() 后经 validate_dna
  拦截（NaN/越域/非法条件类型等值域结构类）→ 422 DNA_VALIDATION_
  FAILED，detail.errors 携带校验明细——非法 cond_type 在此层先拦
  （CARD-01 前经 engine.run 冒泡 RESEARCH_NOT_EXPRESSIBLE，前置
  拦截后该触发器不再触达执行器）。
- ResearchError 冒泡通道（CARD-08 形制保留）：过前置校验、执行期
  才可判的输入（未知 indicator——列解析失败）经 engine.run 冒泡 →
  research_error_to_http 转 422 结构化 body（detail 三段），不裸穿
  500。
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
        """非法 cond_type 被前置 validate_dna 拦截（CARD-01 后口径）→
        422 DNA_VALIDATION_FAILED+errors 明细含坏值与合法集列举
        （修复前：经 engine.run 冒泡 RESEARCH_NOT_EXPRESSIBLE）。"""
        _seed_market(api_client)
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": _bad_dna(),
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
        })
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "DNA_VALIDATION_FAILED"
        errors = body["detail"]["errors"]
        assert errors, "校验明细不得为空"
        assert any("no_such_type" in e for e in errors), errors
        assert any("condition type must be one of" in e
                   for e in errors), "合法集列举须在场"

    def test_unknown_indicator_research_error_channel(self, api_client):
        """ResearchError 冒泡通道锚（CARD-01 后唯一可达路径）：未知
        indicator 过前置校验（validate_dna 不查 indicator 合法集）、
        执行期列解析失败 → engine.run 冒泡 ResearchError → 422 结构化
        body（detail 三段，exc.detail 透传）——不裸穿 500。"""
        _seed_market(api_client)
        dna = _bad_dna()
        dna["signal_genes"][0]["condition"] = {"type": "lt",
                                               "threshold": 30}
        dna["signal_genes"][0]["indicator"] = "NO_SUCH_INDICATOR"
        resp = api_client.post("/api/strategies/backtest", json={
            "dna": dna,
            "symbol": "BTCUSDT", "timeframe": "4h",
            "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000, "fee": 0.001, "slippage": 0.0005,
        })
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "RESEARCH_CONDITION_INVALID"
        assert body["detail"]["detail"]["indicator"] == "NO_SUCH_INDICATOR"

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
