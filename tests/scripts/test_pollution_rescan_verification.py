"""S 重验通道锚族（fix-mtf-execution-consistency 组 4，T4.3）+真实库固化锚。

构造面（test_rerun_channel 语义）：
- 幂等收窄：漂移 draft 放行重建（旧产物保留审计）；一致 draft 现行
  幂等不变（回归锚）；
- 重验四步全流程：重建→重跑→supersedes/rerun_of 引用→旧记录保留；
- 不自动迁移：verdict 翻转后假设 status 不变+新 verdict 在记录。

真实库固化锚（沿 test_recalc_c5_verification 形制）：已知实例在回扫
清单+基线数字 N=2 固化——真实库只读+缺行 skip 不假绿；C1 活体锚
（82 触发对拍）依赖真实运行属 scripts 侧一次性动作（证据在
validation_records 新记录），此处锁库内产物形制。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.persistence.db_ext import init_db_ext  # noqa: E402
from core.analysis.executor import ensure_research_tables  # noqa: E402
from core.analysis.hypothesis_service import register_hypothesis  # noqa: E402
from core.analysis.hypothesis_to_strategy import translate_hypothesis  # noqa: E402
from core.persistence.repository.strategy import (  # noqa: E402
    get_strategy, save_strategy,
)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# 漂移产物 dna_json 构造（R2 前时代形制：exec_tf=4h 漂移+带 1d 标注）
_DRIFT_DNA = {
    "signal_genes": [
        {"indicator": "EMA", "params": {"period": 10},
         "role": "entry_trigger", "field": "ema",
         "condition": {"type": "price_above"}, "timeframe": "1d"},
        {"indicator": "RVOL", "params": {"period": 20},
         "role": "exit_trigger", "field": "rvol",
         "condition": {"type": "gt", "threshold": 2.0}}],
    "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
    "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
    "risk_genes": {"stop_loss": 0.05, "position_size": 0.3},
    "signal_sources": [{"type": "rule"}],
}


def _seed_env(tmp_path):
    """构造库+30m 数据+漂移假设与漂移 draft。"""
    d = tmp_path / "market"
    d.mkdir()
    rng = np.random.default_rng(9)
    idx = pd.date_range("2024-01-01", periods=9000, freq="30min",
                        tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 90, 9000))
    pd.DataFrame({"open": close, "high": close + 45, "low": close - 45,
                  "close": close, "volume": rng.uniform(100, 800, 9000),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_30m.parquet")
    idx4 = pd.date_range("2024-01-01", periods=1200, freq="4h", tz="UTC",
                         name="timestamp")
    close4 = 60000 + np.cumsum(rng.normal(0, 180, 1200))
    pd.DataFrame({"open": close4, "high": close4 + 90, "low": close4 - 90,
                  "close": close4, "volume": rng.uniform(100, 800, 1200),
                  "trades": 40}, index=idx4).to_parquet(
        d / "BTCUSDT_4h.parquet")
    idx1 = pd.date_range("2023-06-01", periods=800, freq="1D", tz="UTC",
                         name="timestamp")
    close1 = 60000 + np.cumsum(rng.normal(0, 400, 800))
    pd.DataFrame({"open": close1, "high": close1 + 200, "low": close1 - 200,
                  "close": close1, "volume": rng.uniform(100, 800, 800),
                  "trades": 40}, index=idx1).to_parquet(
        d / "BTCUSDT_1d.parquet")

    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    hid = register_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "EMA", "params": {"period": 10}},
             "action": "price_above", "target": 0, "timeframe": "1d"}],
            "then_conditions": [
                {"subject": {"indicator": "RVOL",
                             "params": {"period": 20}},
                 "action": "gt", "target": 2.0}]},
        statement="重验锚",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "30m",
                      "start": "2024-06-01", "end": "2024-07-01"}]
    )["hypothesis_id"]
    # 落一条漂移 draft（exec 4h≠基准 30m——R2 前时代产物）
    save_strategy(db, strategy_id="st_drift_old",
                  dna_json=json.dumps(_DRIFT_DNA), symbol="BTCUSDT",
                  timeframe="4h", parent_ids=hid,
                  gene_signature="sig_drift_old_unique",
                  status="draft")
    return db, d, hid


class TestIdempotencyNarrowing:
    def test_drifted_draft_bypassed_rebuild_allowed(self, tmp_path):
        """幂等收窄：漂移 draft 放行重建（新产物执行周期=基准 30m），
        旧 draft 保留审计。"""
        db, d, hid = _seed_env(tmp_path)
        out = translate_hypothesis(db, hypothesis_id=hid, data_dir=d)
        new_sid = out["strategy_id"]
        assert new_sid != "st_drift_old"
        new_exec = json.loads(
            get_strategy(db, new_sid)["dna_json"]
        )["execution_genes"]["timeframe"]
        assert new_exec == "30m"  # R2 继承基准
        # 旧 draft 保留（不删不改）
        assert get_strategy(db, "st_drift_old") is not None

    def test_malformed_ranges_first_element_not_dict_falls_back(
            self, tmp_path):
        """CARD-05（fix-mtf-review-consistency）：data_ranges 首元素非
        dict（库内损坏/手工数据形——直写库绕登记校验构造）→ 幂等函数
        base_tf 不可解析退回现行幂等（谱系有 draft 即返回，不窄化不
        重建），不裸穿 AttributeError（原 except 集不含该异常）。

        注：同款假设 translate 全链主流程取值行（hypothesis_to_
        strategy.py:488 裸 .get）仍同款裸穿——范围外专项清单在案，
        本锚只锁幂等单点防御面（畸形全链行为属范围外问题不作绿锚）。
        """
        from core.analysis.hypothesis_to_strategy import _find_draft_by_parent

        db, _, hid = _seed_env(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE research_hypotheses SET data_ranges=? "
                "WHERE hypothesis_id=?",
                (json.dumps([["4h"]]), hid))
        # _seed_env 谱系有漂移 draft（st_drift_old）——退回现行幂等
        # =谱系命中即返（base_tf=None 不进收窄判据）
        assert _find_draft_by_parent(db, hid) == "st_drift_old"

    def test_consistent_draft_idempotent_unchanged(self, tmp_path):
        """回归锚：一致 draft（执行周期==基准）现行幂等不变——重建后
        二次 translate 返回同一产物。"""
        db, d, hid = _seed_env(tmp_path)
        first = translate_hypothesis(db, hypothesis_id=hid, data_dir=d)
        second = translate_hypothesis(db, hypothesis_id=hid, data_dir=d)
        assert second["strategy_id"] == first["strategy_id"]
        assert second["duplicated"] is True


class TestRerunFourSteps:
    def test_full_chain_supersedes_and_no_auto_transition(self, tmp_path):
        """重验四步：重建→重跑（豁免并集）→supersedes/rerun_of 落键→
        旧记录保留+假设 status 不变（不自动迁移）。"""
        from scripts.rerun_polluted_validation import rerun_hypothesis

        db, d, hid = _seed_env(tmp_path)
        # 造一条旧污染记录（模拟被标注的原验证活动）
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO validation_records (record_id, hypothesis_id,"
                " strategy_id, level, params, verdict, range_hash,"
                " created_at, pollution_cause)"
                " VALUES ('rec_old', ?, 'st_drift_old',"
                " 'isolated_backtest', ?, 'inconclusive', 'rh_old',"
                " '2026-01-01', 'gene_tf_drift')",
                (hid, json.dumps({
                    "request_range": {"symbol": "BTCUSDT",
                                      "timeframe": "4h",
                                      "start": "2024-06-01",
                                      "end": "2024-07-01"}})))
            conn.commit()

        summary = rerun_hypothesis(db, d, hid)
        assert summary["superseded_record"] == "rec_old"
        # 重跑区间：时间范围沿用旧记录、周期换算假设基准（守卫自洽）
        assert summary["rerun_range"]["timeframe"] == "30m"
        assert summary["rerun_range"]["start"] == "2024-06-01"
        assert summary["rerun_range"]["end"] == "2024-07-01"

        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            new = conn.execute(
                "SELECT * FROM validation_records WHERE record_id=?",
                (summary["new_record_id"],)).fetchone()
            result_json = json.loads(new["result"])
            params_json = json.loads(new["params"])
            assert result_json["supersedes"] == "rec_old"
            assert params_json["rerun_of"] == "rec_old"
            old = conn.execute(
                "SELECT verdict, pollution_cause FROM validation_records "
                "WHERE record_id='rec_old'").fetchone()
            assert old["verdict"] == "inconclusive"
            assert old["pollution_cause"] == "gene_tf_drift"
            hyp_status = conn.execute(
                "SELECT status FROM research_hypotheses WHERE "
                "hypothesis_id=?", (hid,)).fetchone()["status"]
        assert hyp_status == "active"  # 不自动迁移


class TestRealLibraryFixedAnchor:
    """真实库固化锚（data/quant.db 只读——缺行 skip 不假绿）。"""

    REAL_DB = ROOT / "data" / "quant.db"

    def test_known_instances_in_rescan_and_baseline(self):
        """回扫基线固化（实施期精确化后口径）：
        - exec 漂移类（精确判据 exec≠源假设基准）：st_3edc19ef0b51
          一条——重验修正产物（exec=基准+带轴）不命中；
        - 旧代码求值漂移类（st_f94daa289aa3，exec 对但记录产自修复
          前）：3 条记录第一轮回扫已标注在库（正当保持——幂等重跑
          不再写、不清洗已标注）。"""
        if not self.REAL_DB.exists():
            pytest.skip("真实库不在场（只读锚不假绿）")
        from scripts.rescan_pollution import rescan
        report = rescan(self.REAL_DB, dry_run=True)
        # exec 漂移类基线（精确判据）
        assert set(report["drifted_strategies"]) == {"st_3edc19ef0b51"}, (
            f"exec 漂移策略集漂移: {sorted(report['drifted_strategies'])}")
        # 已标注记录在库（第一轮字面判据执行事实——3 条 hyp_4e5c8b
        # 448532/st_f94daa289aa3 旧代码记录）
        with sqlite3.connect(self.REAL_DB) as conn:
            n_marked = conn.execute(
                "SELECT COUNT(*) FROM validation_records WHERE "
                "pollution_cause='gene_tf_drift'").fetchone()[0]
        assert n_marked == 3, f"已标注记录基线漂移: {n_marked}"

    def test_c1_living_anchor_library_artifact(self):
        """C1 活体锚库内产物形制：重验新记录在库且 signal_counts.
        entries=82（与组 0 快照分析面 82 触发逐数对拍——2026-10-24
        活体实跑证据固化）。"""
        if not self.REAL_DB.exists():
            pytest.skip("真实库不在场（只读锚不假绿）")
        with sqlite3.connect(self.REAL_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT record_id, result, params FROM validation_records "
                "WHERE strategy_id=(SELECT strategy_id FROM strategy WHERE "
                "parent_ids='hyp_3e1debcdb373' AND status='draft' "
                "ORDER BY created_at DESC LIMIT 1) "
                "ORDER BY created_at DESC LIMIT 1").fetchall()
        if not rows:
            pytest.skip("活体重验记录不在场（一次性动作未执行）")
        result_json = json.loads(rows[0]["result"])
        sc = result_json.get("metrics", {}).get("signal_counts", {})
        assert sc.get("entries") == 82, (
            f"C1 活体锚 entries 对拍失败: {sc}")
        assert result_json["metrics"]["total_trades"] > 0, (
            "C1 活体锚非零交易失败")
