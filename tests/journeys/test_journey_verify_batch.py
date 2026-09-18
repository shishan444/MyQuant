"""链 E/F · 验证链与批量链 L2（3.4）。

验证链 L2：多段 verify → 每段指标=同窗单回测（G-1 口径落地的链路版）→
汇总数值=逐段聚合自洽 → 会话/历史跨步可取。
批量链 L2：processor 流式序列语义完整（progress 步数=steps 声明、序号连续、
终态唯一）——socket 传输层由 B8/Phase 3 覆盖，此处锚计算与序列语义。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from tests.reference.fixture_gen import gen_ohlcv

N = 300


@pytest.fixture(scope="module")
def vb_client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from MyQuant.api.app import create_app

    data_dir = tmp_path_factory.mktemp("vb_data")
    db_path = tmp_path_factory.mktemp("vb_db") / "t.db"
    gen_ohlcv("normal", seed=42, n=N).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    app = create_app(db_path=db_path, data_dir=data_dir)
    with TestClient(app) as client:
        created = client.post("/api/strategies", json={
            "name": "verify-journey", "dna": _DNA(), "symbol": "BTCUSDT",
            "timeframe": "4h", "source": "manual", "tags": "", "notes": "",
        })
        assert created.status_code == 201, created.text
        sid = created.json()["strategy_id"]
        yield client, sid, db_path, data_dir


def _DNA():
    return {
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


def _windows(vb_client):
    import pandas as pd
    c, _, _, data_dir = vb_client
    df = pd.read_parquet(data_dir / "BTCUSDT_4h.parquet")
    d0 = str(df.index[0].date())
    mid = df.index[len(df) // 2]
    d1 = str(mid.date())
    d2 = str(df.index[-1].date())
    return d0, d1, d2


def test_verify_journey_multi_segment_l2(vb_client):
    """两段 verify：每段=同窗单回测（全等），汇总=逐段聚合。"""
    from tests.reference.assert_metrics import assert_metrics_equal

    client, sid, _, _ = vb_client
    d0, d1, d2 = _windows(vb_client)
    keys = ("total_return", "sharpe_ratio", "max_drawdown", "win_rate", "total_trades")

    # G-1 链路版：每段与「传同窗切片的单回测」全等
    for (s, e) in [(d0, d1), (d1, d2)]:
        r_seg = client.post("/api/strategies/verify", json={
            "strategy_ids": [sid], "data_ranges": [{"start": s, "end": e}],
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005, "leverage": 1,
        })
        assert r_seg.status_code == 200, r_seg.text
        seg = r_seg.json()["results"][0]
        assert seg.get("error") is None, seg

        r_bt = client.post("/api/strategies/backtest", json={
            "dna": _DNA(), "dataset_id": "BTCUSDT_4h",
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
            "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": s, "data_end": e,
        })
        assert r_bt.status_code == 200, r_bt.text
        assert_metrics_equal({k: r_bt.json()[k] for k in keys},
                             {k: seg[k] for k in keys},
                             scenario=f"verify-seg-{s}")

    # 多段一次：汇总自洽（avg 指标=逐段均值；达标计数=逐段 qualified 数）
    r_multi = client.post("/api/strategies/verify", json={
        "strategy_ids": [sid],
        "data_ranges": [{"start": d0, "end": d1}, {"start": d1, "end": d2}],
        "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005, "leverage": 1,
    })
    assert r_multi.status_code == 200, r_multi.text
    body = r_multi.json()
    results = body["results"]
    assert len(results) == 2, f"两段应有两行结果: {len(results)}"
    summary = body.get("summary") or []
    assert summary, "汇总缺失"
    sm = summary[0]
    # 汇总自洽：avg_fitness = per_period_metrics fitness 均值；
    # qualified_count = 逐段 qualified 计数；total_periods = 段数
    per = sm.get("per_period_metrics") or []
    assert len(per) == 2, f"per_period 应两段: {per}"
    fvals = [float(p.get("fitness") or 0.0) for p in per]
    assert float(sm["avg_fitness"]) == pytest.approx(
        sum(fvals) / len(fvals), rel=1e-9, abs=1e-12
    ), f"avg_fitness {sm['avg_fitness']} ≠ 逐段均值 {sum(fvals)/len(fvals)}"
    assert int(sm["qualified_count"]) == sum(1 for p in per if p.get("qualified"))
    assert int(sm["total_periods"]) == 2

    # 会话/历史跨步可取（会话结果落库可回放）
    sessions = client.get("/api/strategies/verify/sessions").json()
    items = sessions.get("sessions") or sessions.get("items") or sessions
    assert isinstance(items, list) and items, "会话列表空"
    session_id = items[0].get("session_id") or items[0].get("id")
    res = client.get(f"/api/strategies/verify/sessions/{session_id}/results")
    assert res.status_code == 200, res.text
    hist = client.get("/api/strategies/verify/history").json()
    assert hist is not None


def test_batch_journey_stream_sequence_l2(vb_client):
    """批量流式序列语义：progress 步数=steps 声明、序号连续、终态唯一。"""
    client, sid, db_path, data_dir = vb_client
    d0, d1, d2 = _windows(vb_client)

    from api.routes.strategies.batch import _BatchBacktestProcessor
    from api.schemas import BatchBacktestRequest

    payload = BatchBacktestRequest(
        strategy_ids=[sid],
        data_ranges=[{"start": d0, "end": d1}, {"start": d1, "end": d2}],
        init_cash=100000.0, fee=0.001, slippage=0.0005, leverage=1,
    )
    proc = _BatchBacktestProcessor(payload, db_path, data_dir)
    proc.init()

    progresses = []
    for i in range(len(proc.steps)):
        out = proc.process_step(i)
        progresses.append(out)
    final = proc.finalize()

    # 序列语义：current 连续 1..n；total 声明=实际 progress 次数
    assert len(progresses) == len(proc.steps)
    currents = [p.get("current") for p in progresses]
    assert currents == list(range(1, len(proc.steps) + 1)), f"步序不连续: {currents}"
    assert int(progresses[0]["total"]) == len(progresses), \
        f"total 声明 {progresses[0]['total']} ≠ 实际 {len(progresses)}"
    # 终态：finalize 产出完整收尾结构（summary+results），且此后无新 step
    assert "summary" in final and "results" in final, final
    assert len(final["results"]) == 2
    # 结果落库可取（每段一个 result_id，跨步引用）
    ok_items = [r for r in proc.all_results if getattr(r, "error", None) is None]
    assert len(ok_items) == 2, f"两段应两行: {proc.all_results}"
    for it in ok_items:
        detail = client.get(f"/api/strategies/batch-backtest/{it.result_id}")
        assert detail.status_code == 200, "批量结果跨步取不回"
