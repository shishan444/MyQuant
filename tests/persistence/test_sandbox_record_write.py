"""沙盒验证记录单写函数单测（add-sandbox-records-unified-flow B1/C1）。

覆盖：单 INSERT 契约字段（三不变值字面断言/五可空列全 NULL/六形参
落位）、零副作用（research_log 零新增——与 save_validation_with_log
的 validate 日志分界；明细三表零行——明细是正式验证专属）、连跑
3 次得 3 行（纯 INSERT 无幂等合并——TC-1-1 前置件；同业务实参下
record_id/range_hash 按编排层形制互异，D5 结构性保证唯一约束不撞）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from core.analysis.executor import ensure_research_tables
from core.persistence.orm.engine import engine_for
from core.persistence.repository import research as rr

pytestmark = [pytest.mark.unit]


def _seed(db: Path) -> str:
    """最小前置：假设行（沿 test_vr_signal_trace_write 先例）。"""
    ensure_research_tables(db)
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
            " conditions_hash, statement, data_ranges, ranges_hash,"
            " status, created_at, updated_at) VALUES"
            " ('hyp_t', '[]', 'ch', 's', '[]', 'rh', 'active',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"))
    return "hyp_t"


def _row(db: Path, record_id: str):
    with engine_for(db).connect() as conn:
        return conn.execute(text(
            "SELECT * FROM validation_records WHERE record_id = :rid"),
            {"rid": record_id}).mappings().first()


def _scalar(db: Path, sql: str) -> int:
    with engine_for(db).connect() as conn:
        return conn.execute(text(sql)).scalar_one()


PARAMS = '{"symbol": "BTCUSDT", "timeframe": "1h"}'
RESULT = ('{"metrics": {"total_return": 0.1}, "symbol": "BTCUSDT",'
          ' "timeframe": "1h", "bars_used": 100, "warmup_bars": 20}')


class TestSandboxRecordWrite:
    def test_single_insert_contract_fields(self, tmp_path: Path) -> None:
        """调用一次恰增一行：六形参落位+三不变值字面+五可空列全 NULL。"""
        db = tmp_path / "w.db"
        hyp = _seed(db)
        rr.save_sandbox_validation_record(
            db, record_id="rec_sb_1", hypothesis_id=hyp,
            strategy_id="strat_1", params=PARAMS, result=RESULT,
            range_hash="hash_1")
        assert _scalar(db, "SELECT COUNT(*) FROM validation_records") == 1
        row = _row(db, "rec_sb_1")
        assert row["record_id"] == "rec_sb_1"
        assert row["hypothesis_id"] == hyp
        assert row["strategy_id"] == "strat_1"
        assert row["params"] == PARAMS
        assert row["result"] == RESULT
        assert row["range_hash"] == "hash_1"
        assert row["level"] == "sandbox_backtest"
        assert row["segmentation"] == ""
        assert row["verdict"] == ""
        assert row["pollution_cause"] is None
        assert row["components"] is None
        assert row["signal_trace"] is None
        assert row["trade_semantics"] is None
        assert row["stop_price"] is None
        assert row["created_at"]

    def test_zero_side_effects(self, tmp_path: Path) -> None:
        """本函数不写日志不写明细（C1 与 save_validation_with_log 的分界）。"""
        db = tmp_path / "w.db"
        _seed(db)
        rr.save_sandbox_validation_record(
            db, record_id="rec_sb_2", hypothesis_id="hyp_t",
            strategy_id=None, params=PARAMS, result=None,
            range_hash="hash_2")
        assert _scalar(db, "SELECT COUNT(*) FROM research_log") == 0
        assert _scalar(db, "SELECT COUNT(*) FROM vr_trade_detail") == 0
        assert _scalar(db, "SELECT COUNT(*) FROM vr_equity_curve") == 0
        assert _scalar(db, "SELECT COUNT(*) FROM vr_signal_trace") == 0

    def test_repeated_calls_independent_rows(self, tmp_path: Path) -> None:
        """连跑 3 次得 3 行：纯 INSERT 无幂等合并（TC-1-1 前置件）。

        同业务实参（假设/策略/params/result），record_id 与 range_hash
        按编排层真实形制互异（D5：hash 掺 record_id 每次执行恒新）。
        """
        db = tmp_path / "w.db"
        _seed(db)
        for i in range(3):
            rr.save_sandbox_validation_record(
                db, record_id=f"rec_sb_r{i}", hypothesis_id="hyp_t",
                strategy_id="strat_1", params=PARAMS, result=RESULT,
                range_hash=f"hash_r{i}")
        assert _scalar(db, "SELECT COUNT(*) FROM validation_records") == 3
        assert _row(db, "rec_sb_r0")["verdict"] == ""
        assert _row(db, "rec_sb_r2")["verdict"] == ""
