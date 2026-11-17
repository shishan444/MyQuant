"""建表入口守护（evolution 委托族已随域退役删除——retire-legacy B1）。"""

import pytest

pytestmark = [pytest.mark.unit]

from MyQuant.core.persistence.db import init_db


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "persistence.db"


class TestInitDb:
    def test_creates_all_tables_idempotent(self, db_path):
        """建全部表（IF NOT EXISTS 幂等）——两次调用不炸。"""
        init_db(db_path)
        init_db(db_path)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "strategy" in names and "validation_records" in names
