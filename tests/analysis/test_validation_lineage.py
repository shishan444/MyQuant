"""验证档案谱系段测试（0.4.2 R4，research-validation-protocol 规格，
B6 用例组 29-32）。

覆盖：lineage 结构/终态变体谱系互见（身份族全实体互见+单假设单实体
回归+异条件不进族）/聚合不经 200 截断（SQL 聚合）/只读零写入/
无 parent 容错（进化/手工来源）/无记录 404 不变。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import (
    register_hypothesis,
    transition_hypothesis,
)
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import (
    count_validation_records_by_strategy,
    list_hypotheses_by_identity,
    list_strategies_by_parent_hypotheses,
)
from core.persistence.repository.strategy import save_strategy
from fastapi.testclient import TestClient

RSI = {"indicator": "RSI", "params": {"period": 14}, "field": "rsi"}


def _cond(period=14, lo=45, hi=75):
    return {
        "when_conditions": [{"subject": dict(RSI, params={"period": period}),
                             "action": "lt", "target": lo}],
        "then_conditions": [{"subject": dict(RSI, params={"period": period}),
                             "action": "gt", "target": hi}],
    }


def _ranges():
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


def _insert_record(db: Path, record_id: str, strategy_id: str,
                   hypothesis_id: str, verdict: str,
                   level: str = "isolated_backtest",
                   created_at: str = "2024-03-01T00:00:00Z") -> None:
    """直插验证记录（测试证据构造——绕开服务链获得受控 verdict 分布）。"""
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO validation_records (record_id, hypothesis_id, "
            "strategy_id, level, segmentation, params, result, verdict, "
            "range_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, hypothesis_id, strategy_id, level, "",
             "{}", json.dumps({"metrics": {}}), verdict,
             f"rh_{record_id}", created_at))


def _client(db: Path, tmp_data_dir: Path) -> TestClient:
    from api.app import create_app

    app = create_app(db_path=db, data_dir=tmp_data_dir)
    return TestClient(app)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


@pytest.fixture()
def env(db, tmp_data_dir):
    """变体谱系环境：H→D1（2 条记录）→终态→重登记 H2→翻译 D2。"""
    h1 = register_hypothesis(db, conditions=_cond(), statement="v1",
                             data_ranges=_ranges(),
                             strategy_params={"stop_loss": 0.05})
    t1 = translate_hypothesis(db, hypothesis_id=h1["hypothesis_id"])
    d1 = t1["strategy_id"]
    _insert_record(db, "rec_d1_a", d1, h1["hypothesis_id"], "pass")
    _insert_record(db, "rec_d1_b", d1, h1["hypothesis_id"], "fail",
                   level="random_window")
    transition_hypothesis(db, h1["hypothesis_id"],
                          target_status="abandoned", evidence_ref="variant")
    # 变体路径核心：同条件集+同区间集+异声明（九字段异→不分流→D2 新实体）
    h2 = register_hypothesis(db, conditions=_cond(), statement="v2",
                             data_ranges=_ranges(),
                             strategy_params={"stop_loss": 0.10})
    t2 = translate_hypothesis(db, hypothesis_id=h2["hypothesis_id"])
    d2 = t2["strategy_id"]
    _insert_record(db, "rec_d2_a", d2, h2["hypothesis_id"], "pass")
    return {"h1": h1["hypothesis_id"], "h2": h2["hypothesis_id"],
            "d1": d1, "d2": d2}


class TestVariantLineage:
    def test_variant_family_mutual_visibility(self, db, env, tmp_data_dir):
        """终态变体谱系互见（B6-30）：读 D1 或 D2 的报告 lineage 均列
        D1、D2 两实体（族=同 conditions_hash+ranges_hash 含终态变体）。"""
        with _client(db, tmp_data_dir) as c:
            for sid in (env["d1"], env["d2"]):
                resp = c.get(f"/api/research/validation/{sid}/report")
                assert resp.status_code == 200, resp.text
                lineage = resp.json()["lineage"]
                ids = [e["strategy_id"] for e in lineage["entities"]]
                assert set(ids) == {env["d1"], env["d2"]}, (sid, ids)
                # is_current 区分报告实体
                current = [e for e in lineage["entities"] if e["is_current"]]
                assert len(current) == 1 and current[0]["strategy_id"] == sid
                # parent 回链
                assert lineage["parent_hypothesis_id"] in (
                    env["h1"], env["h2"])

    def test_lineage_records_attribution(self, db, env, tmp_data_dir):
        """各实体记录归属清晰可辨（D1 两条：isolated pass+random fail；
        D2 一条：isolated pass）。"""
        with _client(db, tmp_data_dir) as c:
            resp = c.get(f"/api/research/validation/{env['d1']}/report")
        lineage = resp.json()["lineage"]
        by_id = {e["strategy_id"]: e["records"] for e in lineage["entities"]}
        assert by_id[env["d1"]]["isolated_backtest"]["pass"] == 1
        assert by_id[env["d1"]]["random_window"]["fail"] == 1
        assert by_id[env["d2"]]["isolated_backtest"]["pass"] == 1
        assert "random_window" not in by_id[env["d2"]]

    def test_single_hypothesis_single_entity(self, db, tmp_data_dir):
        """单假设单实体回归：同假设重复翻译恒分流返回同一实体，
        lineage 恒单实体。"""
        h = register_hypothesis(db, conditions=_cond(period=21),
                                statement="s", data_ranges=_ranges())
        t1 = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        t2 = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t1["strategy_id"] == t2["strategy_id"]  # draft 幂等
        _insert_record(db, "rec_solo", t1["strategy_id"],
                       h["hypothesis_id"], "pass")
        with _client(db, tmp_data_dir) as c:
            resp = c.get(f"/api/research/validation/{t1['strategy_id']}/report")
        lineage = resp.json()["lineage"]
        assert len(lineage["entities"]) == 1
        assert lineage["entities"][0]["is_current"] is True

    def test_different_conditions_not_in_family(self, db, env):
        """异条件假设不进族锚：同区间异条件=不同族（identity 查询不命中）。"""
        from core.persistence.repository.research import get_hypothesis
        h1 = get_hypothesis(db, env["h1"])
        fam = list_hypotheses_by_identity(
            db, h1["conditions_hash"], h1["ranges_hash"])
        fam_ids = {h["hypothesis_id"] for h in fam}
        assert fam_ids == {env["h1"], env["h2"]}
        # 异条件假设（period 33）不在族内
        other = register_hypothesis(db, conditions=_cond(period=33),
                                    statement="other", data_ranges=_ranges())
        assert other["hypothesis_id"] not in fam_ids

    def test_evolution_strategy_not_misattached(self, db, env):
        """进化来源策略不误命中：parent_ids=策略 id 数组（非假设 id）
        不进族（精确成员判定——id 恰含假设 id 字面子串也不行）。"""
        save_strategy(db, strategy_id="st_evo_xyz",
                      dna_json="{}", symbol="BTCUSDT", timeframe="4h",
                      name="evo", source="evolution",
                      parent_ids=json.dumps([env["d1"]]))  # 策略 id 非假设
        hits = list_strategies_by_parent_hypotheses(
            db, [env["h1"], env["h2"]])
        assert "st_evo_xyz" not in {s["strategy_id"] for s in hits}


class TestAggregationAndReadOnly:
    def test_aggregation_not_truncated(self, db):
        """聚合不经 200 截断（B6-31）：>200 条记录计数与实况一致。"""
        h = register_hypothesis(db, conditions=_cond(period=28),
                                statement="bulk", data_ranges=_ranges())
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        sid = t["strategy_id"]
        for i in range(230):
            _insert_record(db, f"rec_bulk_{i}", sid, h["hypothesis_id"],
                           "pass", created_at=f"2024-03-01T00:{i%60:02d}:{i%60:02d}")
        counts = count_validation_records_by_strategy(db, [sid])
        assert counts[sid]["isolated_backtest"]["count"] == 230

    def test_report_readonly_zero_writes(self, db, env, tmp_data_dir):
        """只读零写入（B6-32）：报告读取不产生新记录/日志。"""
        from core.persistence.repository.research import (
            count_validation_records, count_research_log)
        before_recs = count_validation_records(db)
        before_logs = count_research_log(db)
        with _client(db, tmp_data_dir) as c:
            assert c.get(
                f"/api/research/validation/{env['d1']}/report").status_code == 200
            assert c.get(
                f"/api/research/validation/{env['d2']}/report").status_code == 200
        assert count_validation_records(db) == before_recs
        assert count_research_log(db) == before_logs

    def test_no_parent_strategy_tolerance(self, db, tmp_data_dir):
        """无 parent 策略容错：进化/手工来源（parent_ids 非假设）
        lineage.parent=null、entities 仅含自身（不炸）。"""
        save_strategy(db, strategy_id="st_manual_lg",
                      dna_json="{}", symbol="BTCUSDT", timeframe="4h",
                      name="m", source="manual", parent_ids=None)
        _insert_record(db, "rec_m", "st_manual_lg", "hyp_none", "pass")
        with _client(db, tmp_data_dir) as c:
            resp = c.get("/api/research/validation/st_manual_lg/report")
        assert resp.status_code == 200
        lineage = resp.json()["lineage"]
        assert lineage["parent_hypothesis_id"] is None
        assert len(lineage["entities"]) == 1
        assert lineage["entities"][0]["strategy_id"] == "st_manual_lg"

    def test_no_records_404_unchanged(self, db, tmp_data_dir):
        """当前实体无任何记录的 404 语义不变。"""
        h = register_hypothesis(db, conditions=_cond(period=35),
                                statement="norec", data_ranges=_ranges())
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        with _client(db, tmp_data_dir) as c:
            resp = c.get(f"/api/research/validation/{t['strategy_id']}/report")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_REPORT_NOT_FOUND"
