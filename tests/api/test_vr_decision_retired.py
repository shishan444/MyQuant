"""decision_json 退役验证（retire-vr-decision-json 任务 4——T3-4 锚）。

原 be-validation-decision-records 三面测试（126 行）随能力退役删除；
本文件承载退役后语义：六面全撤净的「键不在场」断言（写入/读出/ORM/
迁移——spec 增量场景「决策字段透传」内容已翻转为键不在场语义）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis import validation_service as vs_mod
from core.analysis.executor import ensure_research_tables
from tests.helpers.vr_factory import insert_hypothesis
from core.analysis.validation_service import run_sandbox_backtest
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import StrategyDNA
from tests.fixtures.vr_trace_gen import build_sample


@pytest.fixture()
def env(tmp_path: Path):
    d = tmp_path / "mkt"
    d.mkdir()
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(9)
    idx = pd.date_range("2024-01-01", periods=740, freq="4h", tz="UTC",
                        name="timestamp")
    close = 40000 + np.cumsum(rng.normal(0, 100, 740))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 740),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    hid = insert_hypothesis(
        db,
        conditions={
            "when_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 45}],
            "then_conditions": [{"subject": "close", "action": "gt",
                                 "target": 0}]},
        statement="退役验证假设",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-01-15T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})
    s = build_sample("pct_long")
    save_strategy(db, strategy_id="st_retire",
                  dna_json=s["dna"].to_json(), symbol="BTCUSDT",
                  timeframe="4h")
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"db": db, "dir": d, "hid": hid, "client": client}


def test_write_side_key_absent(env):
    """T3-1 写侧：新验证记录 trades 载荷无 decision_json 键。"""
    out = run_sandbox_backtest(
        env["db"], env["dir"], strategy_id="st_retire",
        hypothesis_id=env["hid"], symbol="BTCUSDT", timeframe="4h",
        start="2024-03-25T00:00:00Z", end="2024-04-10T00:00:00Z")
    assert out["record_id"]
    trades = rr.get_validation_executions(env["db"], out["record_id"])
    assert trades, "fixture 须产生至少一份明细"
    for ex in trades:
        for t in ex["trades"]:
            assert "decision" not in t, "读出端 decision 键残留（半删）"


def test_detail_endpoint_decision_absent(env):
    """T3-1 读侧：detail 响应 trades 行 decision 键不在场。"""
    out = run_sandbox_backtest(
        env["db"], env["dir"], strategy_id="st_retire",
        hypothesis_id=env["hid"], symbol="BTCUSDT", timeframe="4h",
        start="2024-03-01T00:00:00Z", end="2024-03-20T00:00:00Z")
    r = env["client"].get(
        f"/api/research/validation-records/{out['record_id']}/detail")
    assert r.status_code == 200
    for ex in r.json()["executions"]:
        for t in ex["trades"]:
            assert "decision" not in t


def test_orm_and_schema_key_absent(env):
    """T3-2/T3-3：ORM 列声明撤净+实库 schema 无列（三重声明一致）。"""
    from core.persistence.orm.models import VrTradeDetail
    cols = [c.name for c in VrTradeDetail.__table__.columns]
    assert "decision_json" not in cols, "ORM 列残留"
    conn = sqlite3.connect(env["db"])
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM pragma_table_info('vr_trade_detail')"
            " WHERE name='decision_json'").fetchone()[0]
        assert n == 0, "实库列残留（迁移未生效或 ensure 未同步）"
    finally:
        conn.close()


def test_source_no_decision_json():
    """防复发源级锚：仓储与写侧源文本零 decision_json 残留。"""
    import inspect
    for mod in (rr, vs_mod):
        src = inspect.getsource(mod)
        # 只查使用形制（列引用/键写入）——退役说明注释不在禁域
        assert '["decision_json"]' not in src, "生产列引用残留"
        assert '"decision_json":' not in src, "生产键写入残留"
