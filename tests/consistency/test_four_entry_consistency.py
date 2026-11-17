"""四入口指标一致性（2.1，含 G-1 实测裁决；R-6 反转后升级为四入口全等）。

工程实况（Q-1=a 迁移后；verify 退役后）：backtest/compare/batch 入口同走
BacktestEngine（引擎路径）——batch-backtest 原走 ReplayRunner（模拟盘重放
路径），rebuild-paper-trading 6.2 迁回测引擎口径（batch_run 矩阵单次调用），
「全等」由两测试合证：引擎两入口全等（test_engine_two_entries_
identical）+ batch×引擎一致（test_batch_engine_parity_locked）。
G-1 历史锚：verify 分段语义 = 同窗单回测——该入口已随旧验证会话体系
退役（retire-verify-sessions），全等比对面缩为两入口+batch。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_ohlcv
from tests.reference.assert_metrics import assert_metrics_equal

METRIC_KEYS = ("total_return", "sharpe_ratio", "max_drawdown", "win_rate", "total_trades")

_DNA_PAYLOAD = {
    "signal_genes": [
        {"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger",
         "field": None, "condition": {"type": "lt", "threshold": 30}},
        {"indicator": "RSI", "params": {"period": 14}, "role": "exit_trigger",
         "field": None, "condition": {"type": "gt", "threshold": 70}},
    ],
    "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
    "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
    "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                   "position_size": 0.5, "leverage": 1, "direction": "long"},
}


@pytest.fixture(scope="module")
def seeded_client(tmp_path_factory):
    """Module 级真栈 client + parquet 数据集 + 已创建策略（跨四入口复用）。"""
    from fastapi.testclient import TestClient
    from MyQuant.api.app import create_app

    data_dir = tmp_path_factory.mktemp("consistency_data")
    db_path = tmp_path_factory.mktemp("consistency_db") / "test.db"
    df = make_ohlcv(n=300, freq="4h", base_price=40000.0, seed=42)
    df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

    app = create_app(db_path=db_path, data_dir=data_dir)
    with TestClient(app) as client:
        resp = client.post("/api/strategies", json={
            "name": "RSI Reversal",
            "dna": {
                "signal_genes": [
                    {"indicator": "RSI", "params": {"period": 14},
                     "role": "entry_trigger", "field": None,
                     "condition": {"type": "lt", "threshold": 30}},
                    {"indicator": "RSI", "params": {"period": 14},
                     "role": "exit_trigger", "field": None,
                     "condition": {"type": "gt", "threshold": 70}},
                ],
                "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
                "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
                "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                               "position_size": 0.5, "leverage": 1,
                               "direction": "long"},
            },
            "symbol": "BTCUSDT", "timeframe": "4h",
            "source": "manual", "tags": "", "notes": "",
        })
        assert resp.status_code == 201, resp.text
        sid = resp.json()["strategy_id"]
        # sandbox 链需 warmup 前扩（≥200 bar）——请求区间取深尾窗
        yield client, sid, str(df.index[220].date()), str(df.index[-1].date()), db_path, data_dir


def _pick(d: dict) -> dict:
    return {k: d[k] for k in METRIC_KEYS if k in d}


def test_engine_two_entries_identical(seeded_client):
    """G-1 裁决核心（retire-legacy 11.3 函数级保留改造）：回测引擎
    两入口形态逐指标全等——backtest/compare 两 REST 入口已随 Q1=B
    退役，等价守护本体=同引擎同参数下 dna 直传与库内 strategy 两条
    装配路径产出全等（经 sandbox 端点，metrics 同源）。"""
    client, sid, d0, d1, _, _ = seeded_client
    # 两条装配路径：①库内 strategy（save_strategy→sandbox 装载）
    r1 = client.post("/api/research/backtest-sandbox", json={
        "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
        "start": d0, "end": d1})
    assert r1.status_code == 200, r1.text
    m_via_strategy = _pick(r1.json()["metrics"])
    # ②内部桥入口直调（同库同 DNA 同窗同 warmup 语义——装配等价面：
    # REST 沙盒入口 vs run_backtest_on_range 桥入口，两路到达同一引擎）
    from core.analysis.backtest_bridge import run_backtest_on_range
    from core.analysis.validation_service import _derive_dna_warmup
    from core.persistence.repository.strategy import get_strategy
    from core.strategy.dna import StrategyDNA
    from pathlib import Path as _P
    db_path, data_dir = seeded_client[4], seeded_client[5]
    dna = StrategyDNA.from_json(
        get_strategy(db_path, sid)["dna_json"])
    warmup_bars, _ = _derive_dna_warmup(db_path, dna, "4h")
    m_bridge, _, _, _ = run_backtest_on_range(
        db_path, _P(data_dir), dna, "BTCUSDT", "4h", d0, d1, warmup_bars,
        strategy_id=sid)
    m_native = _pick(m_bridge)
    assert_metrics_equal(m_via_strategy, m_native,
                         scenario="sandbox-rest-vs-bridge-direct")
