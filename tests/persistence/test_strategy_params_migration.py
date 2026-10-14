"""0.4.2 迁移 e5b9f3c7a1d4 可逆性特征测试。

research_hypotheses.strategy_params 列（声明块载体）：upgrade 加列
（TEXT 可空、存量 NULL=无声明）、downgrade 删列、往返再 upgrade 恢复。
沿 test_identity_migration.py 的 subprocess alembic 惯例。
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_OLD_HEAD = "a1c3e8f5b7d2"  # strategy_params 迁移前的 head
_REV = "c4e8a1f7b3d2"  # head 随变更三 8.1 result_truncated 迁移上移（2026-10-14）


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
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO research_hypotheses (hypothesis_id, status,"
            " conditions, conditions_hash, statement, data_ranges,"
            " ranges_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("hyp_m", "active", "{}", "hash_m", "迁移测试", "[]",
             "rhash_m", "2026-10-05T00:00:00", "2026-10-05T00:00:00"))
        conn.commit()
    return db


class TestStrategyParamsMigration:
    def test_upgrade_adds_nullable_column_legacy_null(self,
                                                     legacy_db_with_row):
        """upgrade head：列加入（TEXT、可空），存量行读出 NULL=无声明。"""
        _alembic(legacy_db_with_row, "upgrade", "head")
        cols = _columns(legacy_db_with_row, "research_hypotheses")
        assert "strategy_params" in cols
        assert cols["strategy_params"]["type"] == "TEXT"
        assert cols["strategy_params"]["notnull"] is False
        with sqlite3.connect(legacy_db_with_row) as conn:
            val = conn.execute(
                "SELECT strategy_params FROM research_hypotheses"
                " WHERE hypothesis_id='hyp_m'").fetchone()[0]
        assert val is None  # 存量零回填（翻译走平台默认）

    def test_downgrade_drops_column_roundtrip(self, legacy_db_with_row):
        """downgrade 删列；再 upgrade 往返恢复（迁移可逆性）。"""
        _alembic(legacy_db_with_row, "upgrade", "head")
        _alembic(legacy_db_with_row, "downgrade", _OLD_HEAD)
        assert "strategy_params" not in _columns(
            legacy_db_with_row, "research_hypotheses")
        _alembic(legacy_db_with_row, "upgrade", "head")
        assert "strategy_params" in _columns(
            legacy_db_with_row, "research_hypotheses")

    def test_head_revision_is_current(self, legacy_db_with_row):
        """当前 head=本迁移（后续有新迁移时此锚同步上移）。"""
        _alembic(legacy_db_with_row, "upgrade", "head")
        with sqlite3.connect(legacy_db_with_row) as conn:
            ver = conn.execute(
                "SELECT version_num FROM alembic_version").fetchone()[0]
        assert ver == _REV
