"""sandbox-and-report-supplement 迁移 d4c7b2e8f6a9 可逆性特征测试。

pt_task 账户级硬熔断三列（halt_drawdown_pct REAL NULL＋halted_at TEXT
NULL＋halt_reason TEXT NULL）：upgrade 加列（全可空、存量 NULL=未配置
不启用）、downgrade 删列、往返再 upgrade 恢复。沿
test_strategy_params_migration.py 的 subprocess alembic 惯例。
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_OLD_HEAD = "a8e4f7d2c9b3"  # 熔断迁移前的 head
_REV = "d4c7b2e8f6a9"


def _alembic(db: Path, *args: str) -> None:
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", *args],
        capture_output=True, text=True, check=True,
    )


def _columns(db: Path, table: str) -> dict:
    with sqlite3.connect(db) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: {"type": r[2], "notnull": bool(r[3])} for r in rows}


@pytest.fixture()
def legacy_db_with_row(tmp_path: Path) -> Path:
    db = tmp_path / "mig.db"
    _alembic(db, "upgrade", _OLD_HEAD)
    # 存量任务行（升级前无熔断列）——升级后三列 NULL=未配置语义
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO pt_task (task_id, status, strategy_id,"
            " dna_snapshot_json, symbol, timeframe, initial_cash, fee,"
            " slippage, leverage, bars_processed,"
            " research_status_snapshot, created_at, updated_at)"
            " VALUES ('pt_m','pending','st_m','{}','BTCUSDT','4h',"
            " 100000, 0.001, 0.0005, 1, 0, 'draft',"
            " '2026-11-09T00:00:00', '2026-11-09T00:00:00')")
        conn.commit()
    return db


class TestHaltDrawdownMigration:
    def test_upgrade_adds_three_nullable_columns_legacy_null(
            self, legacy_db_with_row):
        """upgrade head：三列加入（halt_drawdown_pct REAL／两 TEXT 可空），
        存量行读出 NULL=未配置。"""
        _alembic(legacy_db_with_row, "upgrade", "head")
        cols = _columns(legacy_db_with_row, "pt_task")
        assert cols["halt_drawdown_pct"]["type"] == "REAL"
        assert cols["halt_drawdown_pct"]["notnull"] is False
        assert cols["halted_at"]["notnull"] is False
        assert cols["halt_reason"]["notnull"] is False
        with sqlite3.connect(legacy_db_with_row) as conn:
            row = conn.execute(
                "SELECT halt_drawdown_pct, halted_at, halt_reason"
                " FROM pt_task WHERE task_id='pt_m'").fetchone()
        assert row == (None, None, None), "存量行三列 NULL=未配置"

    def test_downgrade_drops_columns_and_roundtrip_restores(
            self, legacy_db_with_row):
        """downgrade 本迁移：三列拆除；再 upgrade 恢复（往返可逆）。"""
        _alembic(legacy_db_with_row, "upgrade", _REV)
        _alembic(legacy_db_with_row, "downgrade", _OLD_HEAD)
        cols = _columns(legacy_db_with_row, "pt_task")
        assert "halt_drawdown_pct" not in cols
        assert "halted_at" not in cols
        assert "halt_reason" not in cols
        _alembic(legacy_db_with_row, "upgrade", _REV)
        cols = _columns(legacy_db_with_row, "pt_task")
        assert {"halt_drawdown_pct", "halted_at", "halt_reason"} <= set(cols)
