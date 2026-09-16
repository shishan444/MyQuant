
import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
"""第9步 live_trading_task 持久化测试（tmp_path 临时 db）。"""
import pytest

from core.persistence.repository.live_trading import (  # B3c.1：原 core.live_trading.persistence 退役
    delete_live_trading_task,
    get_live_trading_task,
    list_live_trading_tasks,
    save_live_trading_task,
    update_heartbeat,
    update_live_trading_task,
)
from core.persistence.db_ext import init_db_ext


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_live.db"
    init_db_ext(db_path)  # live 表建表已并入 init 链（migration 26）
    return db_path


def _save(db, task_id="t1", status="pending"):
    save_live_trading_task(
        db, task_id=task_id, symbol="BTCUSDT", timeframe="1h",
        dna_json='{"r":1}', direction="mixed", status=status,
    )


class TestSaveGet:
    def test_roundtrip(self, db):
        _save(db)
        row = get_live_trading_task(db, "t1")
        assert row is not None
        assert row["symbol"] == "BTCUSDT"
        assert row["timeframe"] == "1h"
        assert row["status"] == "pending"
        assert row["direction"] == "mixed"

    def test_get_missing(self, db):
        assert get_live_trading_task(db, "nope") is None


class TestUpdate:
    def test_update_fields(self, db):
        _save(db)
        update_live_trading_task(
            db, "t1", status="running", balance=9500.0, position_side="long"
        )
        row = get_live_trading_task(db, "t1")
        assert row["status"] == "running"
        assert row["balance"] == 9500.0
        assert row["position_side"] == "long"

    def test_heartbeat_updates(self, db):
        _save(db)
        update_heartbeat(db, "t1")
        row = get_live_trading_task(db, "t1")
        assert row["heartbeat_at"] is not None

    def test_update_refreshes_updated_at(self, db):
        _save(db)
        before = get_live_trading_task(db, "t1")["updated_at"]
        update_live_trading_task(db, "t1", balance=100)
        after = get_live_trading_task(db, "t1")["updated_at"]
        assert after >= before


class TestList:
    def test_list_all(self, db):
        _save(db, "t1", "pending")
        _save(db, "t2", "running")
        tasks = list_live_trading_tasks(db)
        assert len(tasks) == 2

    def test_list_by_status(self, db):
        _save(db, "t1", "pending")
        _save(db, "t2", "running")
        running = list_live_trading_tasks(db, status="running")
        assert len(running) == 1
        assert running[0]["task_id"] == "t2"


class TestDelete:
    def test_delete(self, db):
        _save(db)
        assert delete_live_trading_task(db, "t1") is True
        assert get_live_trading_task(db, "t1") is None

    def test_delete_missing(self, db):
        assert delete_live_trading_task(db, "nope") is False


class TestInitIdempotent:
    def test_double_init(self, db):
        # 幂等：重复建表不报错
        init_db_ext(db)
        _save(db)
        assert get_live_trading_task(db, "t1") is not None
