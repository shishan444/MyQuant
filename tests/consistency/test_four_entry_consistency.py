"""四入口指标一致性（2.1，含 G-1 实测裁决；R-6 反转后升级为四入口全等）。

工程实况（Q-1=a 迁移后）：backtest/compare/verify/batch 四入口同走
BacktestEngine（引擎路径）——batch-backtest 原走 ReplayRunner（模拟盘重放
路径），rebuild-paper-trading 6.2 迁回测引擎口径（batch_run 矩阵单次调用），
「四入口全等」由两测试合证：引擎三入口全等（test_engine_three_entries_
identical）+ batch×引擎一致（test_batch_engine_parity_locked）。
G-1：verify 分段语义 = 同窗单回测（process_step 用同 engine.batch_run，
同 init_cash/fee/slippage 参数路径）→ 单段全窗时应与 backtest 全等。
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
        yield client, sid, str(df.index[0].date()), str(df.index[-1].date()), db_path, data_dir


def _pick(d: dict) -> dict:
    return {k: d[k] for k in METRIC_KEYS if k in d}


def test_engine_three_entries_identical(seeded_client):
    """G-1 裁决核心：backtest / compare / verify（单段全窗）三入口逐指标全等。"""
    client, sid, d0, d1, _, _ = seeded_client
    # R-4（G-1 口径裁决）：verify 分段切片语义 = [start 00:00, end 00:00] 闭区间
    # （df.index <= end 日期），与全量回测差尾部同日 bar——同窗对齐后公平比对
    # R-5（D-5 已登记）：dna 直传必须同时给 symbol/timeframe，否则
    # load_and_prepare_df 按 "UNKNOWN" 找盘失败 → 静默走 legacy fallback
    # （无切片+逐 gene 指标路径）——口径漂移无告警
    base = {"dataset_id": "BTCUSDT_4h", "init_cash": 100000.0,
            "fee": 0.001, "slippage": 0.0005,
            "data_start": d0, "data_end": d1}
    common = {**base, "symbol": "BTCUSDT", "timeframe": "4h"}  # backtest 专用

    # D-4（已登记 findings）：strategy_id 路径因 to_json/from_json 环变性
    # （is_mtf 翻转）500；基准入口用 dna 直传（同引擎同参数，计算等价）
    r1 = client.post("/api/strategies/backtest",
                     json={"dna": _DNA_PAYLOAD, **common})
    assert r1.status_code == 200, r1.text
    m_backtest = _pick(r1.json())

    r2 = client.post("/api/strategies/compare",
                     json={"strategy_ids": [sid], **base})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    items2 = body2["results"] if isinstance(body2, dict) and "results" in body2 else body2
    m_compare = _pick(items2[0])

    r3 = client.post("/api/strategies/verify",
                     json={"strategy_ids": [sid],
                           "data_ranges": [{"start": d0, "end": d1}],
                           "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
                           "leverage": 1})
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    m_verify = _pick(body3["results"][0])
    assert body3["results"][0].get("error") is None, body3["results"][0]

    assert_metrics_equal(m_backtest, m_compare, scenario="backtest-vs-compare")
    assert_metrics_equal(m_backtest, m_verify, scenario="backtest-vs-verify")


def test_batch_engine_parity_locked(seeded_client):
    """R-6 反转（Q-1=a 裁决，rebuild-paper-trading 6.2 迁移）：batch 入口
    迁 BacktestEngine.batch_run，与单策略回测同引擎同口径——batch×引擎
    逐指标全等，连同 test_engine_three_entries_identical 构成「四入口全等」。

    历史注记：原哨兵（test_batch_replay_semantics_locked）锁定 batch=
    ReplayRunner 重放语义与引擎确定性不同（差异形态登记 findings R-6）；
    迁移后反转锁定一致语义。若本断言翻红=batch 与引擎路径出现分叉
    （重估 Q-1 迁移的口径保真）。
    """
    client, sid, d0, d1, db_path, data_dir = seeded_client

    r1 = client.post("/api/strategies/backtest", json={
        "dna": _DNA_PAYLOAD, "dataset_id": "BTCUSDT_4h",
        "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
        "symbol": "BTCUSDT", "timeframe": "4h",
        "data_start": d0, "data_end": d1,
    })
    assert r1.status_code == 200, r1.text
    m_backtest = _pick(r1.json())

    from api.routes.strategies.batch import _BatchBacktestProcessor
    from api.schemas import BatchBacktestRequest
    payload = BatchBacktestRequest(
        strategy_ids=[sid],
        data_ranges=[{"start": d0, "end": d1}],
        init_cash=100000.0, fee=0.001, slippage=0.0005, leverage=1,
    )
    proc = _BatchBacktestProcessor(payload, db_path, data_dir)
    proc.init()
    for i in range(len(proc.steps)):
        proc.process_step(i)
    proc.finalize()
    items = [r for r in proc.all_results if getattr(r, "error", None) is None]
    assert items, f"batch 无成功结果: {proc.all_results}"
    result_id = items[0].result_id

    detail = client.get(f"/api/strategies/batch-backtest/{result_id}")
    assert detail.status_code == 200, detail.text
    m_batch = _pick(detail.json())
    assert all(k in m_batch for k in METRIC_KEYS), m_batch

    # 一致锁定（迁移后行为）：batch = 引擎（同引擎同参数同窗，逐指标全等）
    assert_metrics_equal(m_batch, m_backtest, scenario="batch-vs-engine")


def test_d4_sentinel_strategy_id_backtest_fixed(seeded_client):
    """D-4 已修复（improve-mcp-agent-loop M3：backtest.py 守卫同修为
    is_mtf 即装载）——strategy_id 路径不再 500，回测可执行。

    哨兵退役注记：原哨兵断言该路径 500（to_json/from_json 环变性使
    显式 MTF DNA 缺 dfs_by_timeframe）；守卫放宽后单层/多层均可装载
    执行。findings 清单 D-4 状态应更新为已修复。
    """
    client, sid, _, _, _, _ = seeded_client
    resp = client.post("/api/strategies/backtest", json={
        "strategy_id": sid, "dataset_id": "BTCUSDT_4h",
        "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
    })
    assert resp.status_code == 200, resp.text
