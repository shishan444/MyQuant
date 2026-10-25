"""S 回扫判据锚族（fix-mtf-execution-consistency 组 4，T4.2 构造面）。

构造库测试（真实库固化锚见 test_pollution_rescan_verification.py）：
- 判据构造：带轴 tf≠exec 入清单 / tf==exec 不入 / 无标注不入 /
  假设侧 when+add_trigger 组判据；
- 标注+SQL 可查询：两表 pollution_cause='gene_tf_drift' 可查；
- 幂等：重跑 marked=0；
- 不触碰：verdict/结果数值原样。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.rescan_pollution import POLLUTION_CAUSE, rescan  # noqa: E402

from core.persistence.db_ext import init_db_ext  # noqa: E402


def _gene(indicator="RVOL", tf=None):
    g = {"indicator": indicator, "params": {"period": 20},
         "role": "entry_trigger", "field": "rvol",
         "condition": {"type": "gt", "threshold": 1.5}}
    if tf is not None:
        g["timeframe"] = tf
    return g


def _dna_json(genes, exec_tf="4h"):
    return json.dumps({
        "signal_genes": genes,
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": exec_tf, "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.1,
                       "position_size": 0.3},
        "signal_sources": [{"type": "rule"}],
    }, ensure_ascii=False)


@pytest.fixture()
def db(tmp_path):
    d = tmp_path / "q.db"
    init_db_ext(d)
    return d


def _seed(db, *, drifted=True, hyp_drift=True, with_records=True):
    """构造：漂移/非漂移策略各一+漂移/非漂移假设各一+关联记录。"""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO strategy (strategy_id, name, dna_json, source,"
            " symbol, timeframe, created_at, updated_at, status,"
            " parent_ids)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("st_drift", "漂移", _dna_json([_gene(tf="3d")]), "manual",
             "BTCUSDT", "4h", "2026-01-01", "2026-01-01", "draft",
             "hyp_drift"))  # 谱系挂漂移假设（exec 4h≠基准 30m——精确判据）
        conn.execute(
            "INSERT INTO strategy (strategy_id, name, dna_json, source,"
            " symbol, timeframe, created_at, updated_at, status)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("st_clean", "净", _dna_json([_gene()]), "manual",
             "BTCUSDT", "4h", "2026-01-01", "2026-01-01", "draft"))
        when_drift = json.dumps([
            {"subject": {"indicator": "RVOL", "params": {"period": 20}},
             "action": "gt", "target": 1.5, "timeframe": "3d"}])
        when_clean = json.dumps([
            {"subject": {"indicator": "RVOL", "params": {"period": 20}},
             "action": "gt", "target": 1.5}])
        ranges = json.dumps([{"symbol": "BTCUSDT", "timeframe": "30m",
                              "start": "2024-01-01", "end": "2024-02-01"}])
        for hid, when in (("hyp_drift", when_drift),
                          ("hyp_clean", when_clean)):
            conn.execute(
                "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
                " conditions_hash, statement, data_ranges, ranges_hash,"
                " status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?, 'active', '2026-01-01','2026-01-01')",
                (hid, json.dumps({"when_conditions": json.loads(when),
                                  "then_conditions": []}),
                 f"hash_{hid}", hid, ranges, f"rh_{hid}"))
        if with_records:
            for rid, sid, hid in (("rec_drift_s", "st_drift", "hyp_clean"),
                                  ("rec_drift_h", "st_clean", "hyp_drift"),
                                  ("rec_clean", "st_clean", "hyp_clean")):
                conn.execute(
                    "INSERT INTO validation_records (record_id,"
                    " hypothesis_id, strategy_id, level, params, verdict,"
                    " range_hash, created_at)"
                    " VALUES (?,?,?,?,?,?,?, '2026-01-01')",
                    (rid, hid, sid, "isolated_backtest", "{}",
                     "inconclusive", f"rhx_{rid}"))
            conn.execute(
                "INSERT INTO backtest_result (result_id, strategy_id,"
                " symbol, timeframe, data_start, data_end, total_return,"
                " sharpe_ratio, max_drawdown, win_rate, total_trades,"
                " created_at)"
                " VALUES ('bt_drift', 'st_drift', 'BTCUSDT', '4h',"
                " '2024-01-01', '2024-02-01', 0.1, 1.0, -0.05, 0.5, 3,"
                " '2026-01-01')")
    return db


class TestSRescanCriteria:
    def test_criteria_and_marking_and_idempotent(self, db):
        """判据+标注+SQL 可查+幂等+不触碰 verdict 一次锁。"""
        _seed(db)
        r1 = rescan(db)
        # 判据：漂移入清单、净不入（策略/假设两侧）
        assert set(r1["drifted_strategies"]) == {"st_drift"}
        assert set(r1["drifted_hypotheses"]) == {"hyp_drift"}
        assert r1["drifted_strategies"]["st_drift"]["exec_tf"] == "4h"
        # 关联：策略命中（rec_drift_s/bt_drift）+假设命中（rec_drift_h）
        assert set(r1["linked_validation_records"]) == {
            "rec_drift_s", "rec_drift_h"}
        assert r1["linked_backtest_results"] == ["bt_drift"]
        assert r1["marked"] == {"validation_records": 2,
                                "backtest_result": 1}
        # SQL 可查询
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT record_id FROM validation_records WHERE "
                "pollution_cause='gene_tf_drift' ORDER BY record_id"
            ).fetchall()
            assert [r[0] for r in rows] == ["rec_drift_h", "rec_drift_s"]
            bt = conn.execute(
                "SELECT result_id FROM backtest_result WHERE "
                "pollution_cause='gene_tf_drift'").fetchall()
            assert [r[0] for r in bt] == ["bt_drift"]
            # 不触碰 verdict/数值
            verdicts = conn.execute(
                "SELECT record_id, verdict, pollution_cause FROM "
                "validation_records ORDER BY record_id").fetchall()
            assert all(v[1] == "inconclusive" for v in verdicts)
        # 幂等：复跑零标注
        r2 = rescan(db)
        assert r2["marked"] == {"validation_records": 0, "backtest_result": 0}
        assert r2["linked_validation_records"] == \
            r1["linked_validation_records"]

    def test_tf_equal_not_drift(self, db):
        """tf==exec（显式标同轴）不入清单——判据是 ≠ 不是 IS NOT NULL。"""
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO strategy (strategy_id, name, dna_json, source,"
                " symbol, timeframe, created_at, updated_at, status)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                ("st_same_tf", "同轴", _dna_json([_gene(tf="4h")]),
                 "manual", "BTCUSDT", "4h", "2026-01-01", "2026-01-01",
                 "draft"))
            conn.commit()
        r = rescan(db)
        assert "st_same_tf" not in r["drifted_strategies"]

    def test_hypothesis_add_trigger_timeframe_criteria(self, db):
        """假设侧判据 add_trigger 组：条目带 timeframe≠基准入清单；
        add_trigger 无 timeframe 不入（when 组判据由主用例承载——
        两组 EXISTS 子句各自可独立命中）。"""
        ranges = json.dumps([{"symbol": "BTCUSDT", "timeframe": "30m",
                              "start": "2024-01-01", "end": "2024-02-01"}])

        def _insert(hid, add_conds):
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO research_hypotheses (hypothesis_id,"
                    " conditions, conditions_hash, statement, data_ranges,"
                    " ranges_hash, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?, 'active', '2026-01-01',"
                    " '2026-01-01')",
                    (hid, json.dumps({
                        "when_conditions": [
                            {"subject": {"indicator": "RVOL",
                                         "params": {"period": 20}},
                             "action": "gt", "target": 1.5}],
                        "add_trigger_conditions": add_conds}),
                     f"hash_{hid}", hid, ranges, f"rh_{hid}"))

        _insert("hyp_add_drift", [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35, "timeframe": "1d"}])
        _insert("hyp_add_clean", [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}])
        r = rescan(db)
        assert "hyp_add_drift" in r["drifted_hypotheses"], (
            "add_trigger 条目 timeframe≠基准应入清单")
        assert "hyp_add_clean" not in r["drifted_hypotheses"]

    def test_dry_run_no_write(self, db):
        """dry-run：清点不落库。"""
        _seed(db)
        r = rescan(db, dry_run=True)
        assert r["marked"] == {"validation_records": 0, "backtest_result": 0}
        with sqlite3.connect(db) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM validation_records WHERE "
                "pollution_cause IS NOT NULL").fetchone()[0]
        assert n == 0
