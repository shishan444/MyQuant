"""全谱扫描 API 端到端测试（add-screen-patterns 任务 3.1/3.2/7.1）。

覆盖 spec 场景「全谱扫描执行」：POST 202 + task_id → 轮询 succeeded →
结果含 scanned 与候选清单 → screen 留痕落档 → 审计查询通道
（GET /log?entry_type=screen）可检索（G6 HTTP 层）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture()
def screen_env(tmp_path: Path):
    """900 根 1h 数据 + 研究库 + TestClient（lifespan 内启 executor）。"""
    rng = np.random.default_rng(21)
    n = 900  # EMA(200) warmup 600 bar + 统计段——前缀必须足量
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC",
                        name="timestamp")
    t = np.arange(n)
    close = pd.Series(100 + t * 0.03 - 12 * np.sin(t / 40 * np.pi)
                      + rng.normal(0, 0.5, n), index=idx)
    spread = np.abs(rng.normal(1, 0.25, n)) + 0.01
    df = pd.DataFrame({
        "open": close, "high": close + spread, "low": close - spread,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 50,
    }, index=idx)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / "BTCUSDT_1h.parquet")

    db = tmp_path / "quant.db"
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db)

    from MyQuant.api.app import create_app
    from fastapi.testclient import TestClient
    app = create_app(db_path=db, data_dir=data_dir)
    with TestClient(app) as client:
        yield {"client": client, "db": db}


def test_screen_patterns_202_poll_and_lineage(screen_env):
    client = screen_env["client"]
    body = {
        "template": "screen_patterns",
        "params": {"features": ["RSI", "EMA"], "horizons": [4, 12],
                   "top_n": 5},
        "data_range": {"symbol": "BTCUSDT", "timeframe": "1h",
                       "start": "2026-02-01T00:00:00Z",
                       "end": "2026-02-08T00:00:00Z"},
    }
    resp = client.post("/api/research/analysis", json=body)
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    assert resp.json()["poll"].endswith(task_id)

    # 轮询至终态（扫描数秒级）
    result = None
    for _ in range(120):
        got = client.get(f"/api/research/analysis/tasks/{task_id}").json()
        if got["status"] in ("succeeded", "failed"):
            result = got
            break
        time.sleep(0.5)
    assert result is not None and result["status"] == "succeeded", result
    payload = result["result"]["payload"]
    assert payload["scanned"] > 0
    assert payload["candidates"], "回调结构数据应有候选"
    c = payload["candidates"][0]
    assert {"rank", "condition", "fingerprint", "samples", "best_horizon",
            "mean", "win_rate", "q_value", "fdr_significant"} <= set(c)
    assert payload["skipped_indicators"] == []

    # screen 留痕落档（计算完成即写）
    from core.persistence.repository import research as rr
    entries = rr.list_research_log(screen_env["db"], entry_type="screen")
    assert len(entries) == 1
    assert entries[0]["ref_id"] == task_id
    summary = json.loads(entries[0]["summary"])
    assert summary["top_fingerprints"], "留痕须含 top 候选指纹清单"

    # 审计查询通道（G6 HTTP 层）：不挂假设的 screen 日志可按类型检索
    logs = client.get("/api/research/log", params={"entry_type": "screen"})
    assert logs.status_code == 200
    items = logs.json()["items"]
    assert any(i["ref_id"] == task_id for i in items)


def test_screen_patterns_unknown_feature_structured_fail(screen_env):
    """screen 恒异步：参数拒绝落 failed(reason 前缀错误码)——D8 既有协议
    （轮询方可解析错误码自纠），且拒绝不留痕。"""
    import time as _t

    client = screen_env["client"]
    resp = client.post("/api/research/analysis", json={
        "template": "screen_patterns",
        "params": {"features": ["NOT_AN_INDICATOR"]},
        "data_range": {"symbol": "BTCUSDT", "timeframe": "1h",
                       "start": "2026-02-01T00:00:00Z",
                       "end": "2026-02-08T00:00:00Z"}})
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    result = None
    for _ in range(40):
        got = client.get(f"/api/research/analysis/tasks/{task_id}").json()
        if got["status"] in ("succeeded", "failed"):
            result = got
            break
        _t.sleep(0.5)
    assert result is not None and result["status"] == "failed"
    assert "[RESEARCH_PARAM_INVALID]" in result["reason"]
    # 拒绝不留痕（无 screen 日志）
    from core.persistence.repository import research as rr
    assert rr.list_research_log(screen_env["db"], entry_type="screen") == []
