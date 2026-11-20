"""回放记录列表通道 API 测试（fix-replay-list-channel）：
_LEVELS 扩值筛选/落库 total_return 计算键/worst 排序键位路由（含
历史行 PnL 代理回退）/verdict_counts 第五键。

真实跑 replay 产真行（REST 全链——响应回显与落库增键一并覆盖）
+直插历史形对照行（无 total_return——代理回退分支）+直插 isolated
对照行（收益排序位次锚）。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis.executor import ensure_research_tables
from tests.helpers.vr_factory import insert_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)

_HIST_ID = "rec_replay_hist_1"  # 历史形直插行（无 total_return 键）
_ISO_ID = "rec_iso_ctrl_1"      # isolated 对照行（排序位次锚）


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """模块级环境：库+合成数据+策略+假设+一次真实 replay（REST）+对照行。"""
    tmp = tmp_path_factory.mktemp("replay_list")
    db = tmp / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp / "market"
    d.mkdir()
    rng = np.random.default_rng(11)
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
    insert_hypothesis(
        db, conditions={
            "when_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                  "action": "lt", "target": 35}],
            "then_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 30}},
                                  "action": "gt", "target": 60}]},
        statement="replay 列表通道测试",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}])
    # 对照行：isolated（metrics.total_return=0.05 排序锚）+历史形 replay
    # （result.replay 无 total_return——代理回退分支）
    from sqlalchemy import text
    from core.persistence.orm.engine import engine_for
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, verdict, range_hash,"
            " params, result, created_at) VALUES"
            f" ('{_ISO_ID}', 'adhoc', 'st_api', 'isolated_backtest',"
            " '', 'pass', 'rh_iso', '{}',"
            " '{\"metrics\": {\"total_return\": 0.05}}',"
            " '2026-01-01T00:00:00Z')"))
        conn.execute(text(
            f"INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, verdict, range_hash,"
            " params, result, created_at) VALUES"
            f" ('{_HIST_ID}', 'adhoc', 'st_api', 'replay',"
            " '', '', 'rh_hist', '{}',"
            " '{\"replay\": {\"task_id\": \"t_hist\", \"total_pnl\":"
            " -900.0, \"final_equity\": 99100.0}}',"
            " '2026-01-02T00:00:00Z')"))
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as c:
        # 真实 replay 全链（REST——落库增键+响应回显一并覆盖）
        r = c.post("/api/research/backtest-replay", json={
            "strategy_id": "st_api", "start": "2023-03-01",
            "end": "2023-03-10", "initial_cash": 100000})
        assert r.status_code == 200, r.text
        yield c, r.json(), db, d


class TestReplayListChannel:
    def test_response_carries_total_return(self, env):
        """REST 响应回显 total_return 计算键（=final_equity/initial_cash−1）。"""
        _, body, _, _ = env
        assert body["total_return"] == pytest.approx(
            body["final_equity"] / 100000 - 1)

    def test_level_filter_hits_replay_rows(self, env):
        """?level=replay 筛选命中新行与历史行（六值扩第七值）。"""
        c, body, _, _ = env
        r = c.get("/api/research/validation-records",
                  params={"level": "replay", "limit": 50})
        assert r.status_code == 200, r.text
        ids = [x["record_id"] for x in r.json()["records"]]
        assert body["record_id"] in ids
        assert _HIST_ID in ids
        assert all(x["level"] == "replay" for x in r.json()["records"])

    def test_six_level_filters_regression(self, env):
        """六值存量筛选行为回归（isolated 仍命中 iso 行不含 replay）。"""
        c, _, _, _ = env
        r = c.get("/api/research/validation-records",
                  params={"level": "isolated_backtest", "limit": 50})
        assert r.status_code == 200, r.text
        ids = [x["record_id"] for x in r.json()["records"]]
        assert _ISO_ID in ids
        assert _HIST_ID not in ids

    def test_return_sort_position_and_proxy_fallback(self, env):
        """total_return_desc 混合集位次：新行按计算键、历史行按 PnL
        代理参与（不落 unsortable）。"""
        c, body, _, _ = env
        r = c.get("/api/research/validation-records",
                  params={"sort": "total_return_desc", "limit": 50})
        assert r.status_code == 200, r.text
        j = r.json()
        ids = [x["record_id"] for x in j["records"]]
        # 三行收益：iso=0.05；replay 新行=final_equity/1e5−1（近 0）；
        # 历史行代理=-900（PnL 绝对值与比率不同纲但序位断言按实际值）
        assert set(ids) >= {_ISO_ID, _HIST_ID, body["record_id"]}
        assert j["unsortable_count"] == 0, "replay 行不应计入 unsortable"
        # 位次断言：iso(0.05) > 新行(≈0) > 历史行（final_equity 99100
        # →若按比率-0.009；代理 PnL -900 参与排序——同为最负，末位稳）
        assert ids.index(_ISO_ID) < ids.index(body["record_id"])
        assert ids.index(body["record_id"]) < ids.index(_HIST_ID)

    def test_verdict_counts_fifth_key(self, env):
        """verdict_counts 含 replay 键且与 total 同基数（新行+历史行=2）。"""
        c, body, _, _ = env
        r = c.get("/api/research/validation-records", params={"limit": 50})
        assert r.status_code == 200, r.text
        vc = r.json()["verdict_counts"]
        assert vc["replay"] == 2
        assert vc["pass"] == 1  # iso 行 pass；replay 行空串不入三态
        assert r.json()["total"] == 3  # 同基数直锚（iso 1+replay 2）
        assert r.json()["total"] == 3  # 同基数直锚（iso 1+replay 2）
        assert r.json()["total"] == 3  # 同基数直锚（iso 1+replay 2）

    def test_new_row_result_carries_key(self, env):
        """落库行 result.replay 携带 total_return（列表 params/result 读回）。"""
        c, body, _, _ = env
        r = c.get("/api/research/validation-records",
                  params={"level": "replay", "limit": 50})
        row = next(x for x in r.json()["records"]
                   if x["record_id"] == body["record_id"])
        assert row["result"]["replay"]["total_return"] == pytest.approx(
            body["final_equity"] / 100000 - 1)
        # 历史行无键（如实缺省）
        hist = next(x for x in r.json()["records"]
                    if x["record_id"] == _HIST_ID)
        assert "total_return" not in hist["result"]["replay"]

    def test_level_replay_with_strategy_combo(self, env):
        """S5 组合筛选：level=replay×strategy_id 下钻——组合交集等集
        +排他锚（strategy 维不参与下推时 total 不会为 0）。"""
        c, body, _, _ = env
        r = c.get("/api/research/validation-records",
                  params={"level": "replay", "strategy_id": "st_api",
                          "limit": 50})
        assert r.status_code == 200, r.text
        j = r.json()
        ids = [x["record_id"] for x in j["records"]]
        assert set(ids) == {body["record_id"], _HIST_ID}
        assert all(x["level"] == "replay" for x in j["records"])
        # 计数键随组合过滤同基数收窄（不回全局基数）
        assert j["verdict_counts"]["replay"] == 2
        # 排他：不存在的 strategy 组合恒空（strategy 维真实下推）
        r2 = c.get("/api/research/validation-records",
                   params={"level": "replay", "strategy_id": "st_none",
                           "limit": 50})
        assert r2.status_code == 200, r2.text
        assert r2.json()["total"] == 0
        assert r2.json()["records"] == []

    def test_new_row_identity_and_params_form(self, env):
        """真实 replay 行身份与落库形制：hypothesis_id='adhoc' 字面量、
        verdict 恒空串、params 顶层八键平铺（spec 落库形制——
        init_cash 为落库实符键名，Lead 已裁决修正 delta 文本）。"""
        c, body, _, _ = env
        r = c.get("/api/research/validation-records",
                  params={"level": "replay", "limit": 50})
        row = next(x for x in r.json()["records"]
                   if x["record_id"] == body["record_id"])
        assert row["hypothesis_id"] == "adhoc"
        assert row["verdict"] == ""
        prm = row["params"]
        assert set(prm) == {"symbol", "timeframe", "start", "end",
                            "fee", "slippage", "init_cash", "leverage"}
        assert prm["symbol"] == "BTCUSDT"
        assert prm["timeframe"] == "4h"
        assert prm["init_cash"] == 100000
