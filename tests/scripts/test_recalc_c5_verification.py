"""C5 真实库重算核验（R1~R5+A9）——只读断言（真实库行缺时 skip 不假绿）。

2026-10-13 用户终局裁决：历史策略产物已全部清除（purge_legacy_
strategies.py——744 策略/3652 回测行清零，双备份在案）——本文件中
依赖已清除行的核验断言转为「历史核验已完成」的清除后形制（库行
缺失 skip）；产物文件（manifest/对照/映射）与 research_log 留痕
断言持续有效（清除不触不可变日志与落盘产物）。
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "data/quant.db"
PRE_DB = REPO_ROOT / "data/quant.db.pre_c5_backup"
OUT = REPO_ROOT / "scripts/recalc_out"
MARKET = REPO_ROOT / "data/market"

# 执行时点基线（2026-10-13 重算产物固化；重算若再次执行须随产物更新）
BASELINE = {
    "affected_strategies": 86,   # ①类入清单策略数（design §1 实查基线）
    "backtest_rows": 998,        # ①类回写主面行数
    "snapshot_rows": 17067,      # 快照涉事行级明细行数
    "changed_rows": 640,         # 库中数字实际变化行数（648 可重算中 8 行重算值与旧值全同）
    "not_recalculable": 350,     # warmup 不足保留旧值行数
    "bandwidth_std3_hits": 138,  # R2：close lt bb_lower_20_3 触发数
    "bandwidth_std2_hits": 1119, # R2：close lt bb_lower_20_2 触发数（=三锚 A1）
}

NUMERIC_COLS = ("total_return", "sharpe_ratio", "max_drawdown",
                "win_rate", "total_trades", "total_score", "fitness")


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture(scope="module")
def manifest():
    p = OUT / "manifest.json"
    if not p.exists():
        pytest.skip("manifest.json 不在（重算产物缺失）")
    return json.loads(p.read_text())


@pytest.fixture(scope="module")
def backup():
    p = OUT / "backup_pre_recalc.json"
    if not p.exists():
        pytest.skip("backup_pre_recalc.json 不在（回滚备份缺失）")
    return json.loads(p.read_text())


@pytest.fixture(scope="module")
def comparison():
    p = OUT / "comparison_backtest.json"
    if not p.exists():
        pytest.skip("comparison_backtest.json 不在（对照明细缺失）")
    return json.loads(p.read_text())


@pytest.fixture(scope="module")
def affected(manifest):
    return [s["strategy_id"]
            for s in manifest["classes"]["strategy_evolution"]["strategies"]]


@pytest.fixture(scope="module")
def log_rows():
    if not DB.exists():
        pytest.skip("data/quant.db 不在")
    conn = _ro(DB)
    rows = conn.execute(
        "SELECT log_id, ref_id, summary, created_at FROM research_log "
        "WHERE entry_type='recalc_bb' ORDER BY log_id").fetchall()
    conn.close()
    if not rows:
        pytest.skip("research_log 无 recalc_bb 条目（重算未执行）")
    return [dict(r) for r in rows]


@pytest.fixture(scope="module")
def bt_rows_now(affected):
    """当前库涉事回测行全量（一次拉取，供对账与状态核验共用）。"""
    conn = _ro(DB)
    ph = ",".join("?" * len(affected))
    rows = {r["result_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM backtest_result WHERE strategy_id IN ({ph})",
        affected)}
    conn.close()
    return rows


# ---------------------------------------------------------------- R1 完备性


# 清除后形制守卫（用户裁决 2026-10-13）：历史核验对象（86 行清单）全部
# 不在库即已清除。判据按清单逐 ID 查询而非表行数——清库后新策略进场
# 不影响判定（新行 ID 不在清单，与「历史行是否在场」正交）。
def _get_legacy_ids() -> list[str] | None:
    """历史核验清单：OUT/manifest.json 的 86 项 strategy_id（与 affected
    fixture 同源表达式）。manifest 不可得 → None（守卫侧保守不跳过，
    manifest 缺失场景由 manifest fixture 自身的 skip 兜住）。"""
    p = OUT / "manifest.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return [s["strategy_id"]
            for s in data["classes"]["strategy_evolution"]["strategies"]]


def _legacy_rows_absent(db_path: Path, legacy_ids: list[str] | None) -> bool:
    """判据：legacy_ids 逐 ID 均不在 strategy 表 → True（守卫显式跳过）；
    任一在场 → False（核验有对象、正常跑）。一次 IN 查询命中集非空即
    False，不做行数比较。缺失值保守不跳过：db_path 不存在、清单为
    None 或空 list → False。"""
    if not db_path.exists():
        return False
    if not legacy_ids:
        return False
    conn = _ro(db_path)
    try:
        ph = ",".join("?" * len(legacy_ids))
        hits = conn.execute(
            f"SELECT strategy_id FROM strategy WHERE strategy_id IN ({ph})",
            legacy_ids).fetchall()
        return not hits
    finally:
        conn.close()
class TestR1Completeness:
    """①类全量口径：三载体各自全量、三方一致、逐条旧值在。"""

    def test_strategy_entry_count_equals_manifest_enumeration(
            self, manifest, log_rows, affected, backup):
        """86 行入清单策略逐条 research_log 条目：枚举数=条目数=备份数。"""
        c1 = manifest["classes"]["strategy_evolution"]
        entry_sids = {r["ref_id"].split(":", 1)[1] for r in log_rows
                      if r["ref_id"].startswith("strategy:")}
        assert c1["affected_std_ne_2"] == BASELINE["affected_strategies"]
        assert len(affected) == BASELINE["affected_strategies"]
        assert entry_sids == set(affected)
        assert len(entry_sids) == BASELINE["affected_strategies"]
        assert len(backup["strategies"]) == BASELINE["affected_strategies"]
        # 处置记录旁注：含 BB 但 std=2 不入清单（策略级 123-86=37）
        assert c1["total_with_bb"] - c1["affected_std_ne_2"] \
            == c1["note_std2_excluded"]

    def test_every_strategy_entry_keeps_old_values(
            self, log_rows, comparison, backup):
        """逐条旧值在：86 条条目 summary 计数累计=998，对照明细每行带旧值。"""
        bt_backup = {b["result_id"] for b in backup["backtest_rows"]}
        total = 0
        for r in log_rows:
            if not r["ref_id"].startswith("strategy:"):
                continue
            s = json.loads(r["summary"])
            assert "backtest_rows" in s and "levels" in s
            total += s["backtest_rows"]
        assert total == BASELINE["backtest_rows"]
        assert {c["result_id"] for c in comparison} == bt_backup
        for c in comparison:
            assert c["old"] is not None  # 旧值保留（D-6 不可变）

    def test_backtest_writeback_face_is_998_and_reconciles(
            self, manifest, backup):
        """998 行回写主面：manifest 枚举=备份行集，逐行可对账。"""
        c1 = manifest["classes"]["strategy_evolution"]
        assert len(c1["backtest_rows"]) == BASELINE["backtest_rows"]
        assert len(backup["backtest_rows"]) == BASELINE["backtest_rows"]
        m_ids = {b["result_id"] for b in c1["backtest_rows"]}
        b_ids = {b["result_id"] for b in backup["backtest_rows"]}
        assert m_ids == b_ids  # 回写面与备份可对账

    def test_comparison_levels_closed_and_sum_to_face(self, comparison):
        """对照三档+环境不可重算档封闭，总数=回写面。"""
        from collections import Counter
        lv = Counter(c["level"] for c in comparison)
        assert set(lv) <= {"numeric_change", "conclusion_sensitive",
                           "no_change", "not_recalculable_data_env"}
        assert sum(lv.values()) == BASELINE["backtest_rows"]
        assert lv["not_recalculable_data_env"] == BASELINE["not_recalculable"]

    def test_snapshot_three_way_consistency(self, manifest, log_rows):
        """快照三方一致：manifest 枚举数=明细文件行数=汇总条目数。"""
        p = OUT / "snapshot_comparison.jsonl"
        if not p.exists():
            pytest.skip("snapshot_comparison.jsonl 不在")
        detail = len([l for l in p.read_text().splitlines() if l.strip()])
        total = manifest["classes"]["evolution"]["snapshot_rows_total"]
        summary = next(r for r in log_rows
                       if r["ref_id"] == "generation_snapshot:summary")
        s = json.loads(summary["summary"])
        assert s["disposition"] == "compare_only"  # 只对照不回写
        assert total == detail == s["rows_total"] == BASELINE["snapshot_rows"]

    def test_empty_set_formal_entries_present(self, manifest, log_rows):
        """空集正式条目（pm 底线 1）：②③④/evolution_task 枚举条目在库。"""
        refs = {r["ref_id"]: json.loads(r["summary"]) for r in log_rows}
        expected = {
            "enumeration:evolution_task":
                manifest["classes"]["evolution"]["evolution_task_affected"],
            "enumeration:③类历史假设枚举":
                manifest["classes"]["hypotheses"]["count"],
            "enumeration:④类 draft 资产枚举":
                manifest["classes"]["draft_assets"]["count"],
            "enumeration:②类扫描存档枚举":
                manifest["classes"]["scan_archive"]["count"],
        }
        for ref, count in expected.items():
            assert ref in refs, f"{ref} 空集/枚举正式条目缺失"
            assert refs[ref] == {"count": len(count) if isinstance(count, list)
                                 else count}, f"{ref} 计数与 manifest 不一致"
        # ③④/evolution_task 契约空集；②类以执行时点为准（manifest 互证）


class TestR1WritebackReconciliation:
    """回写对账：库值 vs 备份旧值 vs 对照记录新值（998 行全量）。"""

    def test_numeric_columns_follow_writeback_rules(
            self, backup, comparison, bt_rows_now):
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")
        """数字列回写规则：不可重算→旧值保留；可重算→库=新值；
        新值缺失（动态列集不写 None）→旧值保留。"""
        cmp_of = {c["result_id"]: c for c in comparison}
        for rid, b in ((x["result_id"], x)
                       for x in backup["backtest_rows"]):
            now = bt_rows_now[rid]
            c = cmp_of[rid]
            newvals = c["new"] or {}
            for col in NUMERIC_COLS:
                lib_v, old_v = now[col], b[col]
                if c["level"] == "not_recalculable_data_env":
                    assert lib_v == old_v, f"{rid}.{col} 不可重算却被改"
                elif newvals.get(col) is not None:
                    assert abs(float(lib_v) - float(newvals[col])) < 1e-9, \
                        f"{rid}.{col} 库值!=对照新值"
                else:
                    assert lib_v == old_v, f"{rid}.{col} 应保留旧值"

    def test_changed_row_count_matches_baseline(
self, backup, bt_rows_now):
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")

        """实际数字变化行数=640（lead 口径回写面对账固化）。"""
        changed = set()
        for b in backup["backtest_rows"]:
            now = bt_rows_now[b["result_id"]]
            if any(now[c] != b[c] for c in NUMERIC_COLS):
                changed.add(b["result_id"])
        assert len(changed) == BASELINE["changed_rows"]

    def test_non_numeric_columns_untouched(
self, backup, bt_rows_now):
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")

        """非数字列（equity_curve/trades_json/参数列等）逐行不变。"""
        skip = set(NUMERIC_COLS)
        for b in backup["backtest_rows"]:
            now = bt_rows_now[b["result_id"]]
            for col in b:
                if col in skip:
                    continue
                assert now[col] == b[col], \
                    f"{b['result_id']}.{col} 非数字列被改"

    def test_strategy_metrics_json_synced_best_score_kept(
self, manifest, backup, comparison, affected):
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")

        """strategy 行：metrics_json 同名键同步最新可重算值、其余键与
        best_score 保留（重算通道不产 best_score 新值——口径待裁决，
        现状断言=不变，见核验报告偏差清单）。"""
        conn = _ro(DB)
        cmp_of = {c["result_id"]: c for c in comparison}
        sid_rows = {}
        for b in backup["backtest_rows"]:
            sid_rows.setdefault(b["strategy_id"], []).append(b["result_id"])
        for sid in affected:
            now = conn.execute(
                "SELECT metrics_json, best_score FROM strategy "
                "WHERE strategy_id=?", (sid,)).fetchone()
            sb = next(s for s in backup["strategies"]
                      if s["strategy_id"] == sid)
            assert now["best_score"] == sb["best_score"]
            old_mj = json.loads(sb["metrics_json"] or "{}")
            new_mj = json.loads(now["metrics_json"] or "{}")
            recalcable = [cmp_of[rid] for rid in sid_rows[sid]
                          if cmp_of[rid]["level"] != "not_recalculable_data_env"]
            latest_new = recalcable[-1]["new"] if recalcable else {}
            for k, v in old_mj.items():
                if k in latest_new and latest_new[k] is not None:
                    assert abs(float(new_mj[k]) - float(latest_new[k])) < 1e-9, \
                        f"{sid}.{k} 未同步最新重算值"
                else:
                    assert new_mj.get(k) == v, f"{sid}.{k} 无关键被改"
        conn.close()


# ---------------------------------------------------------------- R2 可区分

class TestR2Distinguishability:
    """抽样验证可区分性（全量完成后抽样，非重算抽样）。"""

    @pytest.fixture(autouse=True)
    def _need_market(self):
        if not (MARKET / "BTCUSDT_1h.parquet").exists():
            pytest.skip("真实数据 BTCUSDT 1h 不在库（data/market）")

    def test_bandwidth_std3_distinguishable_from_std2(self):
        """std=3 与 std=2 带宽数字可区分：同区间同链路触发数不同，
        且 std=2 面与三锚 A1 基线一致（链路正确性交叉验证）。"""
        from api.routes.research.analysis import _load_with_condition_refs
        from core.analysis.templates.condition_stats import condition_stats

        rng = {"symbol": "BTCUSDT", "timeframe": "1h",
               "start": "2024-02-01", "end": "2026-04-01"}

        def _hits(std):
            cond = {"subject": "close", "action": "lt",
                    "target": f"bb_lower_20_{std}"}
            df, request_start = _load_with_condition_refs(
                MARKET, rng, "BB", condition=cond)
            d = condition_stats(
                df, indicator="BB", params={"period": 20, "std": std},
                condition=cond, data_ranges=[{**rng}], forward_bars=24,
                request_start=request_start).to_dict()
            return d["payload"]["hit_count"]

        h3, h2 = _hits(3), _hits(2)
        assert h3 == BASELINE["bandwidth_std3_hits"]
        assert h2 == BASELINE["bandwidth_std2_hits"]  # A1 锚同值
        assert h3 != h2  # 可区分：std 参数实质改变带宽语义

    @pytest.mark.parametrize("std_layer", [1.0, 1.5, 2.5, 3.0])
    def test_sampled_row_matches_independent_recalc(
self, manifest, comparison, std_layer):
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")

        """分层抽样（std 1.0/1.5/2.5/3.0 各一行·有交易可重算行）：
        库中新值 == 独立调 run_backtest_on_range 同参复算值（手算对照
        ——证明回写值由修复后引擎按原 DNA 的 std≠2 语义产生）。"""
        from core.analysis.backtest_bridge import run_backtest_on_range
        from core.analysis.validation_service import _derive_dna_warmup
        from core.strategy.dna import StrategyDNA

        stds_of = {s["strategy_id"]: s["bb_stds"]
                   for s in manifest["classes"]["strategy_evolution"]["strategies"]}
        rid2sid = {b["result_id"]: b["strategy_id"] for b in
                   manifest["classes"]["strategy_evolution"]["backtest_rows"]}
        sample = next(
            c for c in comparison
            if c["level"] != "not_recalculable_data_env"
            and c["new"] and c["new"].get("total_trades", 0) > 0
            and std_layer in stds_of[rid2sid[c["result_id"]]])

        conn = _ro(DB)
        row = conn.execute(
            "SELECT * FROM backtest_result WHERE result_id=?",
            (sample["result_id"],)).fetchone()
        dj = conn.execute(
            "SELECT dna_json FROM strategy WHERE strategy_id=?",
            (row["strategy_id"],)).fetchone()["dna_json"]
        conn.close()
        dna = StrategyDNA.from_json(dj)
        warmup, _ = _derive_dna_warmup(DB, dna)
        metrics, _, _, _ = run_backtest_on_range(
            DB, MARKET, dna, row["symbol"], row["timeframe"],
            row["data_start"], row["data_end"], warmup,
            init_cash=float(row["init_cash"]), fee=float(row["fee"]),
            slippage=float(row["slippage"]))
        for col in ("total_return", "sharpe_ratio", "max_drawdown",
                    "total_trades", "win_rate"):
            ref_v = metrics.get(col)
            if ref_v is None:
                continue  # 复算产物无此键（库值保留旧值，R1 对账已覆盖）
            assert abs(float(row[col]) - float(ref_v)) < 1e-9, \
                f"std={std_layer} {sample['result_id'][:8]}.{col} " \
                f"库值 {row[col]} != 独立复算 {ref_v}"


# ---------------------------------------------------------------- R3 对应关系表

class TestR3ArchiveMapping:
    """R3：RR 档案对应关系表存在且与对照记录一致。"""

    def test_mapping_exists_and_consistent_with_comparison(self, comparison):
        p = OUT / "rr_archive_mapping.json"
        if not p.exists():
            pytest.skip("rr_archive_mapping.json 不在（导出段未执行）")
        m = json.loads(p.read_text())
        assert {"generated_at", "note", "rows"} <= set(m)
        expected = [c for c in comparison if c["level"] != "no_change"]
        assert len(m["rows"]) == len(expected)  # 从对照记录生成
        assert m["note"]  # 空表语义说明在（非空表时数字对照以对照明细为准）


# ---------------------------------------------------------------- R5/A9 状态零迁移

class TestR5StateZeroMigration:
    """R5/A9：重算不得触碰任何资产的生命周期状态字段（全量核验）。"""

    def test_backtest_qualified_unchanged_all_rows(
self, backup, bt_rows_now):
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")

        """A9-⑤：backtest_result.qualified 998 行逐行不变（vs 备份）。"""
        for b in backup["backtest_rows"]:
            assert bt_rows_now[b["result_id"]]["qualified"] == b["qualified"]

    def test_strategy_status_active_and_not_migrated(self, affected):
        """涉事 86 行 status 全 active（①类枚举前提），pre_c5 可见行零迁移。"""
        # 清除后形制（用户裁决 2026-10-13：历史策略已全部清除——
        # 本断言的历史核验已在清除前完成并留档，行缺失时跳过不假绿）
        if _legacy_rows_absent(DB, _get_legacy_ids()):
            pytest.skip("历史策略已清除（用户裁决）——清除前核验已留档")
        conn = _ro(DB)
        now = {r[0]: r[1] for r in conn.execute(
            "SELECT strategy_id, status FROM strategy")}
        conn.close()
        for sid in affected:
            assert now[sid] == "active"
        if PRE_DB.exists():
            pre = _ro(PRE_DB)
            pre_s = {r[0]: r[1] for r in pre.execute(
                "SELECT strategy_id, status FROM strategy")}
            pre.close()
            for sid in affected:  # 涉事行均为老策略，pre_c5 可见
                assert pre_s.get(sid) == now[sid], f"{sid} status 迁移"

    def test_validation_verdict_not_migrated(self):
        """A9/D-12：validation_records.verdict 零迁移（pre_c5 可见行）。"""
        if not PRE_DB.exists():
            pytest.skip("quant.db.pre_c5_backup 不在")
        conn, pre = _ro(DB), _ro(PRE_DB)
        va = {r[0]: r[1] for r in pre.execute(
            "SELECT record_id, verdict FROM validation_records")}
        vb = {r[0]: r[1] for r in conn.execute(
            "SELECT record_id, verdict FROM validation_records")}
        conn.close()
        pre.close()
        migrated = [k for k in va if k in vb and va[k] != vb[k]]
        assert migrated == []

    def test_generation_snapshot_byte_identical(self, manifest):
        """①类快照只对照不回写：涉事 17067 行 best_dna/population_json
        逐字节不变（sha256 vs pre_c5）。"""
        if not PRE_DB.exists():
            pytest.skip("quant.db.pre_c5_backup 不在")
        conn, pre = _ro(DB), _ro(PRE_DB)
        snaps = manifest["classes"]["evolution"]["snapshot_rows"]
        bad = missing = 0
        for s in snaps:
            r2 = pre.execute(
                "SELECT best_dna, population_json FROM generation_snapshot "
                "WHERE task_id=? AND generation=?",
                (s["task_id"], s["generation"])).fetchone()
            if r2 is None:
                missing += 1
                continue
            h_pre = hashlib.sha256(
                (r2["best_dna"] or "").encode() + b"|" +
                (r2["population_json"] or "").encode()).hexdigest()
            r1 = conn.execute(
                "SELECT best_dna, population_json FROM generation_snapshot "
                "WHERE task_id=? AND generation=?",
                (s["task_id"], s["generation"])).fetchone()
            h_now = hashlib.sha256(
                (r1["best_dna"] or "").encode() + b"|" +
                (r1["population_json"] or "").encode()).hexdigest()
            bad += (h_pre != h_now)
        conn.close()
        pre.close()
        assert bad == 0, f"快照 {bad} 行被改写"
        assert missing == 0, f"快照 {missing} 行 pre_c5 缺失（超出已知缺行面）"

    def test_hypotheses_conditions_untouched(self):
        """③类不动：research_hypotheses.conditions 零变化（pre_c5 可见行）。"""
        if not PRE_DB.exists():
            pytest.skip("quant.db.pre_c5_backup 不在")
        conn, pre = _ro(DB), _ro(PRE_DB)
        ha = {r[0]: r[1] for r in pre.execute(
            "SELECT hypothesis_id, conditions FROM research_hypotheses")}
        hb = {r[0]: r[1] for r in conn.execute(
            "SELECT hypothesis_id, conditions FROM research_hypotheses")}
        conn.close()
        pre.close()
        assert [k for k in ha if k in hb and ha[k] != hb[k]] == []

    def test_analysis_tasks_result_ref_not_updated(self):
        """②类不回写负向：analysis_tasks.result_ref 零变化（pre_c5 可见行）。"""
        if not PRE_DB.exists():
            pytest.skip("quant.db.pre_c5_backup 不在")
        conn, pre = _ro(DB), _ro(PRE_DB)
        ta = {x[0]: x[1] for x in pre.execute(
            "SELECT task_id, result_ref FROM analysis_tasks")}
        tb = {x[0]: x[1] for x in conn.execute(
            "SELECT task_id, result_ref FROM analysis_tasks")}
        conn.close()
        pre.close()
        assert [k for k in ta if k in tb and ta[k] != tb[k]] == []

    def test_screen_audit_rows_fingerprint_unchanged(self):
        """②类负向：research_log screen 审计行 summary 逐字节未 UPDATE。"""
        if not PRE_DB.exists():
            pytest.skip("quant.db.pre_c5_backup 不在")
        conn, pre = _ro(DB), _ro(PRE_DB)
        sa = {x[0]: x[1] for x in pre.execute(
            "SELECT log_id, summary FROM research_log "
            "WHERE entry_type='screen'")}
        sb = {x[0]: x[1] for x in conn.execute(
            "SELECT log_id, summary FROM research_log "
            "WHERE entry_type='screen'")}
        conn.close()
        pre.close()
        assert [k for k in sa if k in sb and sa[k] != sb[k]] == []


# ------------------------------------------------- ②类涉事行处置契约（偏差保留）

class TestClass2DisposalContract:
    """spec「扫描存档处置条目完整性与来源联动」MUST 的契约保留测试。

    C5-P1 已修（fix/research-c7 补 scan: 处置条目+两字段契约+脚本
    _append_once 幂等）——原类级 xfail(strict) 锚 XPASS 翻转转正。
    """

    def test_affected_scan_row_has_disposal_entry_with_two_fields(
            self, manifest, log_rows):
        sa = manifest["classes"]["scan_archive"]
        if sa["count"] == 0:
            pytest.skip("②类空集——两字段契约无涉事对象")
        # 契约：每条涉事扫描存档行有处置条目，summary 含两字段+联动
        asset_refs = []
        for h in sa["analysis_tasks"]:
            asset_refs.extend((f"scan:{h['task_id']}", h["task_id"]))
        for h in sa["research_log_screen"]:
            asset_refs.append(f"screen:{h['log_id']}")
        entries = [r for r in log_rows
                   if any(a in (r["ref_id"] or "") for a in asset_refs)]
        assert entries, "涉事扫描存档行缺对照处置条目（C5-P1）"
        for r in entries:
            s = json.loads(r["summary"])
            assert s.get("result_ref_completeness") in {
                "complete", "truncated", "absent"}
            assert s.get("old_value_source") in {
                "result_ref", "research_archive", "none"}
            comp, src = s["result_ref_completeness"], s["old_value_source"]
            if comp == "complete":
                assert src == "result_ref"
            else:
                assert src in {"research_archive", "none"}


# ------------------------------------------- 判据 _legacy_rows_absent 三场景锚
# 合成清单与生产清单等价（判据是纯集合判定），锚零 OUT 产物依赖——
# 产物缺失环境可独立跑。守卫 skip 路径不写内联元测试，以全量实证
# （skipped 计数恰=10）为守卫接线的等效锚。
class TestGuardLegacyRowsAbsent:
    """三场景（S1/S2/S3）+两缺失值边界（U4/U5），直断判据返回值。"""

    LEGACY = [f"legacy_{i:03d}" for i in range(86)]

    @staticmethod
    def _mk_db(path: Path, ids) -> Path:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE strategy (strategy_id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO strategy VALUES (?)",
                         [(i,) for i in ids])
        conn.commit()
        conn.close()
        return path

    def test_s1_legacy_rows_present_returns_false(self, tmp_path):
        """S1 历史行在场（清单抽样≥3）→False（守卫不跳过、正常跑）。"""
        db = self._mk_db(tmp_path / "q.db", self.LEGACY[::17])  # 抽样 6 个
        assert _legacy_rows_absent(db, self.LEGACY) is False

    def test_s2_legacy_rows_absent_returns_true(self, tmp_path):
        """S2 历史行不在场（空表）→True（守卫显式跳过）。"""
        db = self._mk_db(tmp_path / "q.db", [])
        assert _legacy_rows_absent(db, self.LEGACY) is True

    def test_s3_new_rows_only_still_returns_true(self, tmp_path):
        """S3 新行在场+历史不在→True（仍显式跳过——当前十红的修复
        归宿；旧判据 COUNT(*)==0 在此场景误判为 False，唯一判别锚）。"""
        db = self._mk_db(tmp_path / "q.db", ["new_001", "new_002"])
        assert _legacy_rows_absent(db, self.LEGACY) is True

    def test_u4_db_missing_returns_false(self, tmp_path):
        """U4 边界：db 文件不存在→False（保守不跳过）。"""
        assert _legacy_rows_absent(tmp_path / "absent.db", self.LEGACY) \
            is False

    def test_u5_none_ids_returns_false(self, tmp_path):
        """U5 边界：清单 None（manifest 不可得）→False（保守不跳过，
        manifest 缺失场景由 manifest fixture 自身 skip 兜住）。"""
        db = self._mk_db(tmp_path / "q.db", [])
        assert _legacy_rows_absent(db, None) is False
