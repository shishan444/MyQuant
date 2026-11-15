"""沙盒验证记录 API 面测试（add-sandbox-records-unified-flow B3——
TC-2-1/2-2/2-3/2-4：沙盒行入统一流的立即可查/三路筛选/混排同流/
level 六值扩面）。

真实跑沙盒产真行（模块级 fixture 一次共享）+直插正式行对照。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.validation_service import run_sandbox_backtest
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)

_CONDS = {
    "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
    "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
}
_FORMAL_ID = "rec_formal_1"  # 直插正式对照行（created_at 过去——混排断言用）


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """模块级环境：库+合成数据+策略+假设+一次真实沙盒执行+正式对照行。"""
    tmp = tmp_path_factory.mktemp("sb_api")
    db = tmp / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp / "market"
    d.mkdir()
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_api", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    hid = register_hypothesis(
        db, conditions=_CONDS, statement="API 统一流测试",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]
    out = run_sandbox_backtest(
        db, d, strategy_id="st_api", symbol="BTCUSDT", timeframe="4h",
        start="2023-03-01", end="2023-03-10", hypothesis_id=hid)
    # 正式对照行（直插——created_at 过去，降序流中排在沙盒行后）
    from sqlalchemy import text
    from core.persistence.orm.engine import engine_for
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, verdict, range_hash,"
            " params, result, created_at) VALUES"
            f" ('{_FORMAL_ID}', '{hid}', 'st_api', 'isolated_backtest',"
            " '', 'pass', 'rh_f1', '{}', '{}',"
            " '2026-01-01T00:00:00Z')"))
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, hid, out


class TestSandboxRecordsApi:
    def test_sandbox_record_immediately_visible(self, env):
        """TC-2-1：沙盒成功后立即 ?level=sandbox_backtest 与无过滤均含该行。"""
        client, hid, out = env
        r1 = client.get("/api/research/validation-records",
                        params={"level": "sandbox_backtest"})
        assert r1.status_code == 200
        assert out["record_id"] in {
            x["record_id"] for x in r1.json()["records"]}
        r2 = client.get("/api/research/validation-records")
        assert out["record_id"] in {
            x["record_id"] for x in r2.json()["records"]}

    def test_sandbox_filterable_three_ways(self, env):
        """TC-2-2：level/strategy_id/hypothesis_id 三路各自命中沙盒行。"""
        client, hid, out = env
        for params in ({"level": "sandbox_backtest"},
                       {"strategy_id": "st_api"},
                       {"hypothesis_id": hid}):
            r = client.get("/api/research/validation-records",
                           params=params)
            assert r.status_code == 200, params
            ids = {x["record_id"] for x in r.json()["records"]}
            assert out["record_id"] in ids, params

    def test_sandbox_rows_in_unified_stream(self, env):
        """TC-2-3：正式（早）+沙盒（晚）created_at 降序混排同一条记录流。"""
        client, hid, out = env
        r = client.get("/api/research/validation-records")
        recs = r.json()["records"]
        ids = [x["record_id"] for x in recs]
        assert out["record_id"] in ids and _FORMAL_ID in ids
        created = [x["created_at"] for x in recs]
        assert created == sorted(created, reverse=True)
        sbx = next(x for x in recs
                   if x["record_id"] == out["record_id"])
        assert sbx["level"] == "sandbox_backtest"
        assert sbx["verdict"] == ""  # 沙盒行 verdict 如实（不参与三态）

    def test_level_enum_six_values_422_face_unchanged(self, env):
        """TC-2-4：扩值不扩 422 面——新值 200、bogus 仍 422、旧五值不变。"""
        client, hid, out = env
        ok = client.get("/api/research/validation-records",
                        params={"level": "sandbox_backtest"})
        assert ok.status_code == 200
        bad = client.get("/api/research/validation-records",
                         params={"level": "bogus"})
        assert bad.status_code == 422
        d = bad.json()["detail"]
        assert d["code"] == "RESEARCH_PARAM_INVALID"
        assert d["detail"]["field"] == "level"
        # 值域披露含六值（message 内嵌值域清单——规整换行后匹配）
        assert "sandbox_backtest" in d["message"].replace("\n", "")
        for lv in ("isolated_backtest", "random_window",
                   "staged_validation", "composite", "component"):
            r = client.get("/api/research/validation-records",
                           params={"level": lv})
            assert r.status_code == 200, lv
