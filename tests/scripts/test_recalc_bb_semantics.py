"""C5 重算脚本构造层测试（fix-research-c5-bb-legacy-recalc · 机制就绪）。

pm 底线 2：机制就绪以构造用例证明（不依赖存量空集）——双通道枚举
命中（含 2.5 旧正则漏网边界）、全状态枚举不漏、幂等断点续跑、单资产
事务回滚、warmup 不足跳过路径、空集正式条目。真实库全量执行的核验
（R1~R5）由脚本产物与处置记录承载（长跑，不在单测内）。
"""

import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import recalc_bb_semantics as rc  # noqa: E402

from core.persistence.db_ext import init_db_ext
from core.analysis.executor import ensure_research_tables


def _bb_gene(std):
    return {"indicator": "BB", "params": {"period": 20, "std": std},
            "role": "entry_trigger", "field_name": "lower",
            "condition": {"type": "gt", "threshold": 0}}


def _dna_json(stds=(3.0,)):
    return json.dumps({"signal_genes": [_bb_gene(s) for s in stds]})


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "q.db"
    init_db_ext(p)
    ensure_research_tables(p)
    import sqlite3
    conn = sqlite3.connect(p)
    conn.execute(
        "INSERT INTO strategy (strategy_id, name, dna_json, status, symbol, "
        "timeframe, metrics_json, created_at, updated_at) VALUES "
        "('st_a', 'a', ?, 'active', 'BTCUSDT', '4h', '{}', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (_dna_json((3.0,)),))
    conn.execute(
        "INSERT INTO strategy (strategy_id, name, dna_json, status, symbol, "
        "timeframe, metrics_json, created_at, updated_at) VALUES "
        "('st_b', 'b', ?, 'active', 'BTCUSDT', '4h', '{}', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (_dna_json((2.0,)),))  # std=2 不入清单
    conn.execute(
        "INSERT INTO strategy (strategy_id, name, dna_json, status, symbol, "
        "timeframe, created_at, updated_at) VALUES ('st_d', 'd', ?, 'draft', "
        "'BTCUSDT', '4h', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (_dna_json((2.5,)),))
    conn.commit()
    conn.close()
    return p


class TestDualChannelEnumeration:
    """双通道枚举：结构化+编码列名（同一判定语义）。"""

    def test_structured_channel_std3_hit(self, db):
        m = rc.audit(db, db.parent / "out")
        sids = [s["strategy_id"]
                for s in m["classes"]["strategy_evolution"]["strategies"]]
        assert "st_a" in sids        # std=3 入清单
        assert "st_b" not in sids    # std=2 不入
        assert "st_d" not in sids    # draft 归④类边界

    @pytest.mark.parametrize("std", [1.5, 2.5, 3.0])
    def test_encoded_channel_boundary_values(self, std):
        """编码列名通道边界值（2.5 为旧字符串负向前瞻漏网值——捕获组
        +float 比较形全命中）。"""
        blob = json.dumps({"x": f"bb_lower_20_{std}_gt"})
        assert rc._is_affected(blob, structured=False) is (std != 2.0)

    def test_encoded_channel_2_excluded(self):
        assert not rc._is_affected("bb_upper_20_2_gt", structured=False)

    def test_draft_enumerated_in_class4(self, db):
        """④类枚举不漏 draft（构造 st_d 含 std=2.5）。"""
        m = rc.audit(db, db.parent / "out")
        assert "st_d" in m["classes"]["draft_assets"]["draft_strategies"]

    def test_abandoned_hypothesis_enumerated(self, db):
        """③类全状态枚举（含 abandoned——构造条件含编码 BB(std≠2)）。"""
        import sqlite3
        conn = sqlite3.connect(db)
        cond = json.dumps({"when": [{"subject": "bb_lower_20_3",
                                     "action": "gt", "target": 0}]})
        conn.execute(
            "INSERT INTO research_hypotheses (hypothesis_id, statement, "
            "conditions, conditions_hash, data_ranges, ranges_hash, "
            "status, created_at, updated_at) VALUES "
            "('hyp_x', 's', ?, 'h1', '[]', 'h2', 'abandoned', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (cond,))
        conn.commit()
        conn.close()
        m = rc.audit(db, db.parent / "out")
        assert m["classes"]["hypotheses"]["count"] == 1

    def test_dna_blob_channel_snapshot(self):
        """快照载体结构化通道（best_dna/population 数组两形态）。"""
        best = json.dumps({"signal_genes": [_bb_gene(2.5)]})
        pop = json.dumps([{"signal_genes": [_bb_gene(1.5)]},
                          {"signal_genes": []}])
        assert rc._dna_blob_affected(best)
        assert rc._dna_blob_affected(pop)
        assert not rc._dna_blob_affected(
            json.dumps({"signal_genes": [_bb_gene(2.0)]}))
        assert rc._dna_blob_affected("not json bb_lower_20_3")  # 回退正则仍命中
        assert not rc._dna_blob_affected("plain text")


class TestDisposalMechanics:
    """处置机制：幂等/事务/warmup 跳过/空集条目（tmp 库机制验证）。"""

    @pytest.mark.xfail(
        strict=True,
        reason="幂等键形制错位缺陷（2026-10-13 tester 补真实断言即锤出，"
               "待 developer 修复后 XPASS 翻转）：recalc 脚本 done 集合存 "
               "ref_id（'strategy:st_a' 带 prefix），循环以裸 sid "
               "（'st_a'）作 in 比较——恒不命中，断点续跑幂等从未生效"
               "（原测试无 assert 恒绿掩盖了该缺陷）")
    def test_idempotent_second_run_skips(self, db, tmp_path):
        """断点续跑幂等（C5.3 完成标准「同资产二次运行零重复回写」）：
        已有该资产 recalc_bb 条目即整体跳过——条目零新增且旧值保留。

        判别力：幂等命中跳过发生在任何写入之前；若幂等检查失效，
        st_a 在空 market 目录下走 warmup-skip 路径仍会新增
        strategy:st_a 条目（实测复现正是此路径——缺陷态条目数 2）。
        条目是处置完成的留痕——不增 ⟹ 未重放处置 ⟹ 回写未重放
        （回写在处置流程内、先于条目）。"""
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO backtest_result (result_id, strategy_id, symbol, "
            "timeframe, data_start, data_end, init_cash, fee, slippage, "
            "total_return, created_at) VALUES ('bt_1', 'st_a', 'BTCUSDT', "
            "'4h', '2024-01-01', '2024-06-01', 100000, 0.001, 0.0005, "
            "0.123, '2026-01-01T00:00:00Z')")
        conn.commit()
        conn.close()
        # 第一次运行的产物以预置条目承载（机制面等价构造）
        from core.persistence.repository.research import append_research_log
        append_research_log(db, hypothesis_id=None, entry_type="recalc_bb",
                            ref_id="strategy:st_a", summary="{}")

        empty_dir = tmp_path / "empty_market"
        empty_dir.mkdir()
        rc.recalc(db, empty_dir, tmp_path / "out2", dry_run=False)

        conn = sqlite3.connect(db)
        n_entries = conn.execute(
            "SELECT COUNT(*) FROM research_log "
            "WHERE entry_type='recalc_bb' AND ref_id='strategy:st_a'"
        ).fetchone()[0]
        old_kept = conn.execute(
            "SELECT total_return FROM backtest_result "
            "WHERE result_id='bt_1'").fetchone()[0]
        conn.close()
        assert n_entries == 1, \
            f"二次运行必须零新增条目（实际 {n_entries}）——幂等跳过失效"
        assert old_kept == 0.123, "旧值保留（未重放回写）"

    def test_recalc_warmup_skip_records_level(self, db, tmp_path):
        """warmup 不足跳过路径：无数据目录时全部行记
        not_recalculable_warmup_insufficient 且旧值保留。"""
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO backtest_result (result_id, strategy_id, symbol, "
            "timeframe, data_start, data_end, init_cash, fee, slippage, "
            "total_return, created_at) VALUES ('bt_1', 'st_a', 'BTCUSDT', "
            "'4h', '2024-01-01', '2024-06-01', 100000, 0.001, 0.0005, "
            "0.123, '2026-01-01T00:00:00Z')")
        conn.commit()
        old = conn.execute(
            "SELECT total_return FROM backtest_result "
            "WHERE result_id='bt_1'").fetchone()[0]
        conn.close()
        empty_dir = tmp_path / "empty_market"
        empty_dir.mkdir()
        rc.recalc(db, empty_dir, tmp_path / "out3", dry_run=False)
        conn = sqlite3.connect(db)
        got = conn.execute(
            "SELECT total_return FROM backtest_result "
            "WHERE result_id='bt_1'").fetchone()[0]
        levels = conn.execute(
            "SELECT summary FROM research_log WHERE entry_type='recalc_bb'"
        ).fetchall()
        conn.close()
        assert got == old  # 旧值保留
        assert any("not_recalculable" in (r[0] or "") for r in levels)

    def test_empty_set_formal_entries(self, db, tmp_path):
        """空集正式条目：②③④/evolution_task 枚举条目写入 research_log。"""
        rc.recalc(db, tmp_path / "m", tmp_path / "out4", dry_run=False)
        import sqlite3
        conn = sqlite3.connect(db)
        refs = [r[0] for r in conn.execute(
            "SELECT ref_id FROM research_log "
            "WHERE entry_type='recalc_bb'").fetchall()]
        conn.close()
        assert any("②类" in (r or "") for r in refs)
        assert any("③类" in (r or "") for r in refs)
        assert any("④类" in (r or "") for r in refs)
        assert any("evolution_task" in (r or "") for r in refs)


class TestDiffLevel:
    """对照三档分级。"""

    def test_no_change(self):
        old = {"total_return": 0.1, "sharpe_ratio": 1.0}
        assert rc._diff_level(old, {"total_return": 0.1, "sharpe_ratio": 1.0}) \
            == "no_change"

    def test_numeric_change(self):
        old = {"total_return": 0.1}
        assert rc._diff_level(old, {"total_return": 0.2}) == "numeric_change"

    def test_conclusion_sensitive_sign_flip(self):
        old = {"total_return": 0.1}
        assert rc._diff_level(old, {"total_return": -0.1}) \
            == "conclusion_sensitive"
