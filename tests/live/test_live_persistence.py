
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


class TestUpdateWhitelist:
    """CARD-03 值域锚：update 列白名单（形制同 paper_trading）。

    身份列（task_id/symbol/timeframe/dna_json/direction/created_at，
    恰为模型全列减白名单）创建后不可经 update 改写——白名单外静默
    过滤；runner 持仓/账户/统计/心跳与 API PATCH 全键在白名单内。
    """

    def test_身份列静默过滤_白名单内生效(self, db):
        _save(db)
        before = get_live_trading_task(db, "t1")
        update_live_trading_task(db, "t1", symbol="ETHUSDT", timeframe="4h",
                                 dna_json="evil", direction="short",
                                 created_at="1970-01-01", status="stopped")
        after = get_live_trading_task(db, "t1")
        assert after["symbol"] == "BTCUSDT", "身份列不得被改写"
        assert after["timeframe"] == "1h"
        assert after["dna_json"] == before["dna_json"]
        assert after["direction"] == "mixed"
        assert after["created_at"] == before["created_at"]
        assert after["status"] == "stopped", "白名单内字段照常生效"

    def test_runner与PATCH全键可写并刷新updated_at(self, db):
        _save(db)
        before = get_live_trading_task(db, "t1")
        runner_keys = dict(status="running", position_side="long",
                           position_qty=0.5, position_entry=100.0,
                           position_sl=95.0, position_tp=120.0, balance=1000.0,
                           equity=1005.0, unrealized_pnl=5.0, stop_reason="t",
                           config_json='{"k":1}', total_trades=3, total_pnl=15.0,
                           win_count=2, loss_count=1,
                           last_bar_time="2026-01-01T00:00",
                           heartbeat_at="2026-01-01T00:01")
        update_live_trading_task(db, "t1", **runner_keys)
        row = get_live_trading_task(db, "t1")
        for k, v in runner_keys.items():
            assert row[k] == v, f"runner 写入键 {k} 未生效"
        assert row["updated_at"] >= before["updated_at"]

    def test_全白名单外与空kwargs_noop(self, db):
        _save(db)
        update_live_trading_task(db, "t1", status="running")
        snap = get_live_trading_task(db, "t1")
        update_live_trading_task(db, "t1", evil_column="x")
        update_live_trading_task(db, "t1")
        row = get_live_trading_task(db, "t1")
        assert row["status"] == "running", "全过滤须零效果"
        assert row["updated_at"] == snap["updated_at"], "noop 不刷新 updated_at"

    @pytest.mark.parametrize("status", ["pending", "running", "stopped", "error"])
    def test_状态四合法值可写(self, db, status):
        _save(db)
        update_live_trading_task(db, "t1", status=status)
        assert get_live_trading_task(db, "t1")["status"] == status
