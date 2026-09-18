"""四入口指标一致性（2.1，含 G-1 实测裁决）。

工程实况：backtest/compare/verify 三入口同走 BacktestEngine（引擎路径），
batch-backtest 走 ReplayRunner（模拟盘重放路径）——「四入口全等」拆两层：
引擎三入口全等（应然）+ batch 跨路径一致性（实测裁决：全等或登记分歧形态）。
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


def test_batch_replay_semantics_locked(seeded_client):
    """R-6（口径裁决）：batch 入口 = ReplayRunner 重放语义（DecisionPipeline
    bar 级订单流 + VirtualAccount），与引擎三入口（信号回测语义）设计性
    不同构——「四入口全等」由 test_engine_three_entries_identical 以
    「三引擎入口全等」落地；batch 正确性锚 = 重放器自身一致性（组 3 模拟
    盘链同源管线）。

    本测试锁定当前行为：①结果结构完整可取回；②与引擎结果确定性不同
    （差异形态登记 findings R-6——若未来两路径收敛对齐，此断言翻红提醒
    更新本裁决）。
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

    # 差异锁定（当前行为）：重放语义 ≠ 引擎语义（确定性，非随机）
    assert m_batch["total_trades"] != m_backtest["total_trades"] or (
        abs(m_batch["total_return"] - m_backtest["total_return"]) > 1e-6
    ), (
        "R-6 行为变化：重放路径与引擎路径出现收敛——重估裁决"
        f"（batch={m_batch} engine={m_backtest}）"
    )


def test_d4_sentinel_strategy_id_backtest_broken(seeded_client):
    """D-4 哨兵：strategy_id 路径当前 500（to_json/from_json 环变性——
    auto-wrap layers 被序列化，读回判为显式 MTF）。

    修复日此哨兵翻红 → 更新 findings 清单 D-4 状态与四入口测试注记
    （backtest 基准入口换回 strategy_id 真实路径）。
    """
    client, sid, _, _, _, _ = seeded_client
    with pytest.raises(ValueError, match="MTF DNA requires"):
        client.post("/api/strategies/backtest", json={
            "strategy_id": sid, "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
        })
