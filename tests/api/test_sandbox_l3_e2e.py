"""L3-1 AI 研究闭环流端到端（add-sandbox-records-unified-flow 6.3）。

register → 策略 → 沙盒×2（同区间不同 fee）→ 无过滤列表混排 →
level 过滤 → verdict_counts → 卡片快照 → 下钻取尽 → detail 空骨架 →
同区间隔离验证 409（judged_ranges 含沙盒区间）——A1/A2/A3/A5/A6+
红线 1 全链一致性。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import (
    ResearchError, register_hypothesis)
from core.analysis.validation_service import (
    run_isolated_backtest, run_sandbox_backtest)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)

_CONDS = {
    "when_conditions": [{"subject": {"indicator": "RSI",
                                     "params": {"period": 14}},
                         "action": "lt", "target": 35}],
    "then_conditions": [{"subject": {"indicator": "RSI",
                                     "params": {"period": 30}},
                         "action": "gt", "target": 60}],
}


@pytest.fixture()
def l3_env(tmp_path: Path):
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp_path / "market"
    d.mkdir()
    rng = np.random.default_rng(21)
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
    save_strategy(db, strategy_id="st_l3", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    hid = register_hypothesis(
        db, conditions=_CONDS, statement="L3 端到端假设",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, db, d, hid


class TestL3AiResearchLoop:
    def test_full_chain(self, l3_env):
        """L3-1 全链：沙盒×2 入统一流→呈现四面→下钻取尽→detail→
        同区间隔离验证 409（并集拦截保持）。"""
        client, db, d, hid = l3_env
        # 沙盒×2（同区间不同 fee——range_hash 互异两行）
        o1 = run_sandbox_backtest(
            db, d, strategy_id="st_l3", symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01", end="2023-03-10", fee=0.001,
            hypothesis_id=hid)
        o2 = run_sandbox_backtest(
            db, d, strategy_id="st_l3", symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01", end="2023-03-10", fee=0.002,
            hypothesis_id=hid)
        assert o1["record_id"] != o2["record_id"]
        # 无过滤列表混排可见
        full = client.get("/api/research/validation-records").json()
        full_ids = {x["record_id"] for x in full["records"]}
        assert {o1["record_id"], o2["record_id"]} <= full_ids
        # level 过滤命中 2 行且 range_hash 互异
        lv = client.get("/api/research/validation-records",
                        params={"level": "sandbox_backtest"}).json()
        lv_ids = [x["record_id"] for x in lv["records"]]
        assert {o1["record_id"], o2["record_id"]} == set(lv_ids)
        hashes = {x["range_hash"] for x in lv["records"]}
        assert len(hashes) == 2
        # verdict_counts：sandbox=2 三键纯净（A3/A5）
        assert lv["verdict_counts"] == {
            "pass": 0, "fail": 0, "inconclusive": 0, "sandbox": 2}
        # 卡片快照三数字不变（沙盒不污染正式口径——C5）
        cards = client.get("/api/research/hypothesis-cards",
                           params={"page_size": 200}).json()["items"]
        card = next(c for c in cards if c["hypothesis_id"] == hid)
        assert card["validation_count"] == 0
        assert card["latest_verdict"] is None
        assert card["latest_record_id"] is None
        # 下钻取尽（A6 数据链路）
        drill = client.get(
            "/api/research/validation-records",
            params={"hypothesis_id": hid, "level": "sandbox_backtest",
                    "limit": 1}).json()
        assert drill["total"] == 2 and drill["has_more"] is True
        page2 = client.get(
            "/api/research/validation-records",
            params={"hypothesis_id": hid, "level": "sandbox_backtest",
                    "limit": 1, "offset": 1}).json()
        collected = ({x["record_id"] for x in drill["records"]}
                     | {x["record_id"] for x in page2["records"]})
        assert collected == {o1["record_id"], o2["record_id"]}
        assert page2["has_more"] is False
        # 沙盒 record detail 200 空骨架
        det = client.get(
            f"/api/research/validation-records/{o1['record_id']}/detail")
        assert det.status_code == 200
        assert det.json()["detail_available"] is False
        assert det.json()["executions"] == []
        # 同区间隔离验证 409（judged_ranges 含沙盒区间——红线 1）
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(
                db, d, hypothesis_id=hid, strategy_id="st_l3",
                request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                               "start": "2023-03-02T00:00:00Z",
                               "end": "2023-03-08T00:00:00Z"})
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP"
        judged = ei.value.detail["judged_ranges"]
        assert any(r["start"].startswith("2023-03-01") for r in judged), \
            "沙盒区间应出现在判定并集明细中"
