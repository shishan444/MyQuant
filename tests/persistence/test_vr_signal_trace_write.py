"""第五写+三布尔打标+留痕读的持久层单测（add-vr-signal-trace 任务 4.2
——T1-I2/T1-S5 锚）。

覆盖：trace 传入→行+三布尔同事务落库；留痕写失败→整体回滚（「记录
在而留痕残缺」不可达）；不传→与现状逐位一致（三布尔 NULL 零行）；
空列表亦打三布尔（管线通而事实空）；读函数排序与过滤。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from core.analysis.executor import ensure_research_tables
from core.persistence.repository import research as rr


def _seed(db: Path) -> str:
    """最小前置：假设行（ValidationRecord 外键依赖）。"""
    ensure_research_tables(db)
    from core.persistence.orm.engine import engine_for
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
            " conditions_hash, statement, data_ranges, ranges_hash,"
            " status, created_at, updated_at) VALUES"
            " ('hyp_t', '[]', 'ch', 's', '[]', 'rh', 'active',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"))
    return "hyp_t"


def _save(db: Path, record_id: str, trace=None, hyp="hyp_t"):
    rr.save_sandbox_validation_record(
        db, record_id=record_id, hypothesis_id=hyp, strategy_id=None,
        params="{}", result="{}", range_hash=f"rh_{record_id}",
        trace=trace)


def _three_bools(db: Path, record_id: str):
    from core.persistence.orm.engine import engine_for
    with engine_for(db).connect() as conn:
        row = conn.execute(text(
            "SELECT signal_trace, trade_semantics, stop_price FROM"
            " validation_records WHERE record_id = :rid"),
            {"rid": record_id}).mappings().first()
    return (row["signal_trace"], row["trade_semantics"],
            row["stop_price"]) if row else None


TRACE_ROWS = [
    {"exec_ref": "main", "bar_time": "2024-01-01 00:00:00+00:00",
     "seq": 0, "outcome": "filled", "reason": None, "event_code": 10,
     "size": 1.0, "price": 100.0, "capped": None, "gene_hits": None},
    {"exec_ref": "main", "bar_time": "2024-01-01 04:00:00+00:00",
     "seq": 0, "outcome": "rejected", "reason": "exposure_cap",
     "event_code": 30, "size": None, "price": None, "capped": None,
     "gene_hits": None},
]


def test_trace_write_with_flags(tmp_path: Path):
    db = tmp_path / "w.db"
    _seed(db)
    _save(db, "rec_a", trace=TRACE_ROWS)
    assert _three_bools(db, "rec_a") == (1, 1, 1)
    rows = rr.get_vr_signal_trace_rows(db, "rec_a")
    assert len(rows) == 2
    assert rows[0]["event_code"] == 10 and rows[0]["outcome"] == "filled"
    assert rows[1]["reason"] == "exposure_cap"


def test_empty_trace_still_flags(tmp_path: Path):
    """零信号记录：空列表亦打三布尔（管线通而事实空，非降级）。"""
    db = tmp_path / "w.db"
    _seed(db)
    _save(db, "rec_b", trace=[])
    assert _three_bools(db, "rec_b") == (1, 1, 1)
    assert rr.get_vr_signal_trace_rows(db, "rec_b") == []


def test_none_trace_flag_null_unchanged(tmp_path: Path):
    """不传→与现状逐位一致（三布尔 NULL——沙盒等调用方零适配）。"""
    db = tmp_path / "w.db"
    _seed(db)
    _save(db, "rec_c", trace=None)
    assert _three_bools(db, "rec_c") == (None, None, None)
    assert rr.get_vr_signal_trace_rows(db, "rec_c") == []


def test_trace_write_failure_rolls_back(tmp_path: Path, monkeypatch):
    """留痕写失败→整体回滚：「记录在而留痕残缺」不可达。"""
    db = tmp_path / "w.db"
    _seed(db)
    import core.persistence.repository.research as rmod
    from core.persistence.orm import models as m
    real = rmod.insert

    def fake_insert(table):
        if table is m.VrSignalTrace:
            raise RuntimeError("留痕写失败注入")
        return real(table)

    monkeypatch.setattr(rmod, "insert", fake_insert)
    with pytest.raises(RuntimeError):
        _save(db, "rec_d", trace=TRACE_ROWS)
    # 整体回滚：记录不在库（同事务原子性）
    assert _three_bools(db, "rec_d") is None


def test_corrupted_gene_hits_structured_reject(tmp_path: Path):
    """CARD-10：gene_hits 损坏行→结构化拒绝（422 非炸 500）+行级定位。

    库内直改损坏串（外部改写/截断形态）经 get_vr_signal_trace_rows
    读出→ResearchError RESEARCH_STORED_DATA_INVALID，detail 携带
    幂等键族四标识+parse_error（损坏行可精确定位——涉安项的取证面）。
    """
    from core.analysis.errors import ResearchError
    db = tmp_path / "w.db"
    _seed(db)
    _save(db, "rec_corrupt", trace=TRACE_ROWS)
    from core.persistence.orm.engine import engine_for
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "UPDATE vr_signal_trace SET gene_hits = '{oops'"
            " WHERE record_id = :rid AND seq = 0 AND event_code = 30"),
            {"rid": "rec_corrupt"})
    with pytest.raises(ResearchError) as ei:
        rr.get_vr_signal_trace_rows(db, "rec_corrupt")
    assert ei.value.code == "RESEARCH_STORED_DATA_INVALID"
    assert ei.value.status == 422
    d = ei.value.detail
    assert d["record_id"] == "rec_corrupt"
    assert d["exec_ref"] == "main"
    assert d["seq"] == 0
    assert "parse_error" in d, "损坏串取证信息必须在场"
    assert rr.get_vr_signal_trace_rows(db, "rec_d") == []


def test_read_filter_and_order(tmp_path: Path):
    db = tmp_path / "w.db"
    _seed(db)
    rows = TRACE_ROWS + [
        {"exec_ref": "win_0", "bar_time": "2024-01-01 00:00:00+00:00",
         "seq": 0, "outcome": "swallowed",
         "reason": "entry_exit_conflict", "event_code": 33, "size": None,
         "price": None, "capped": None, "gene_hits": None}]
    _save(db, "rec_e", trace=rows)
    only_win = rr.get_vr_signal_trace_rows(db, "rec_e", exec_ref="win_0")
    assert len(only_win) == 1 and only_win[0]["event_code"] == 33
    all_rows = rr.get_vr_signal_trace_rows(db, "rec_e")
    assert [r["exec_ref"] for r in all_rows] == ["main", "main", "win_0"]


def test_unique_index_defense_layer(tmp_path: Path):
    """幂等防御层（T1-I8）：同 (record, exec_ref, bar_time, seq) 重复
    插入被 UNIQUE 索引直接拒绝（生产幂等由前置查询保证——此处锁
    防御层物理约束本身）。"""
    import sqlalchemy.exc

    from core.persistence.orm.engine import engine_for
    from core.persistence.orm.models import VrSignalTrace
    from sqlalchemy import insert as sa_insert

    db = tmp_path / "w.db"
    _seed(db)
    _save(db, "rec_f", trace=TRACE_ROWS)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with engine_for(db).begin() as conn:
            conn.execute(sa_insert(VrSignalTrace), [
                dict(TRACE_ROWS[0], record_id="rec_f")])
