"""vr_signal_trace 表结构断言（add-vr-signal-trace 任务 1.1 验证）。

建库后 PRAGMA 直读（持久层 schema 声明坑纪律：ORM/Alembic/ensure_
research_tables 三方一致性以实库表结构为证——索引验证须 PRAGMA
index_list，工程坑位）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.analysis.executor import ensure_research_tables

TRACE_COLS = [
    "id", "record_id", "exec_ref", "bar_time", "seq", "outcome",
    "reason", "event_code", "size", "price", "capped", "gene_hits",
]


def test_vr_signal_trace_schema(tmp_path: Path) -> None:
    db = tmp_path / "schema.db"
    ensure_research_tables(db)
    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(vr_signal_trace)")]
        assert cols == TRACE_COLS, f"列契约漂移: {cols}"
        idx = {r[1]: (r[2], bool(r[3])) for r in conn.execute(
            "PRAGMA index_list(vr_signal_trace)")}
        assert "ux_vr_signal_trace" in idx and idx["ux_vr_signal_trace"][1], (
            "幂等键族唯一索引缺失（防御层）")
        assert "ix_vr_signal_trace_query" in idx, "份级查询索引缺失"
        # 非空约束面（record_id/exec_ref/bar_time/seq/outcome/event_code）
        notnull = {r[1]: r[3] for r in conn.execute(
            "PRAGMA table_info(vr_signal_trace)")}
        for col in ("record_id", "exec_ref", "bar_time", "seq",
                    "outcome", "event_code"):
            assert notnull[col], f"{col} 须 NOT NULL"
        # 三布尔列（create_all 形库同批验证——三重声明第三处）
        vr_cols = {r[1]: r[3] for r in conn.execute(
            "PRAGMA table_info(validation_records)")}
        for col in ("signal_trace", "trade_semantics", "stop_price"):
            assert col in vr_cols, f"validation_records 缺 {col}"
            assert not vr_cols[col], f"{col} 须可空（NULL=存量降级态）"
    finally:
        conn.close()
