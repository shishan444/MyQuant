"""repository/paper_trading.py 特征测试网（修复期安全网）。

现状记录语义（B3c.1 迁移轨当前行为）：
- save_task 硬编码初值（status=pending / execution_model=v2 /
  balance=initial_cash / confidence_sizing_enabled 布尔入库存 int）；
- save_trade 的 INSERT OR IGNORE 幂等（B1.3 唯一键 task_id+bar_time+
  action——键相同而载荷不同仍被忽略）；
- save_equity_snapshots 批量 + (task_id, timestamp) 唯一索引幂等
  + 缺省字段（unrealized_pnl=0.0 / position_side=flat）；
- update 的列白名单过滤（白名单外静默丢弃）；
- delete 三表级联删除单事务；
- compute_trading_metrics 委托 core.scoring（零交易 win_rate 缺失
  语义不折叠 0）；
- 崩溃恢复与 runner 拾取序。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper_trading as repo


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "paper.db"
    init_db_ext(path)
    return path


def _task(db, task_id, **kw):
    kw.setdefault("dna_json", '{"genes": []}')
    repo.save_paper_trading_task(db, task_id=task_id, **kw)


class TestSaveTaskDefaults:
    """save_task 初值锚：硬编码字段不依赖调用方传参。"""

    def test_硬编码初值(self, db):
        _task(db, "t1", initial_cash=5000)
        row = repo.get_paper_trading_task(db, "t1")
        assert row["status"] == "pending"
        assert row["execution_model"] == "v2"
        assert row["balance"] == 5000, "balance 沿 initial_cash 初始化"
        assert row["created_at"] == row["updated_at"]

    def test_置信仓位布尔归一int(self, db):
        _task(db, "on", confidence_sizing_enabled=True)
        _task(db, "off", confidence_sizing_enabled=False)
        assert repo.get_paper_trading_task(db, "on")["confidence_sizing_enabled"] == 1
        assert repo.get_paper_trading_task(db, "off")["confidence_sizing_enabled"] == 0

    def test_缺失任务返回None(self, db):
        assert repo.get_paper_trading_task(db, "ghost") is None


class TestUpdateWhitelist:
    """update_paper_trading_task：白名单列过滤 + updated_at 刷新。"""

    def test_白名单内字段写入并刷新updated_at(self, db):
        _task(db, "t1")
        before = repo.get_paper_trading_task(db, "t1")["updated_at"]
        repo.update_paper_trading_task(db, "t1", status="running", balance=123.0)
        row = repo.get_paper_trading_task(db, "t1")
        assert (row["status"], row["balance"]) == ("running", 123.0)
        assert row["updated_at"] >= before

    def test_白名单外字段静默丢弃(self, db):
        _task(db, "t1")
        repo.update_paper_trading_task(db, "t1", evil_column="x",
                                       initial_cash=999.0, balance=9.0)
        row = repo.get_paper_trading_task(db, "t1")
        assert row["balance"] == 9.0, "白名单内字段仍生效"
        assert row["initial_cash"] == 100000, "白名单外字段不得被更新"

    def test_全白名单外_noop(self, db):
        _task(db, "t1")
        repo.update_paper_trading_task(db, "t1", evil_column="x")
        assert repo.get_paper_trading_task(db, "t1")["balance"] == 100000

    def test_空kwargs_noop(self, db):
        _task(db, "t1")
        repo.update_paper_trading_task(db, "t1")
        assert repo.get_paper_trading_task(db, "t1")["status"] == "pending"


class TestListCount:
    """list/count：status 过滤 + created_at 倒序 + 分页。

    created_at 不在 update 白名单内，排序断言依据创建顺序（微秒时钟
    在单进程测试内单调，seed 序 a→b→c）。
    """

    @pytest.fixture(autouse=True)
    def _seed(self, db):
        _task(db, "a")
        repo.update_paper_trading_task(db, "a", status="running")
        _task(db, "b")
        repo.update_paper_trading_task(db, "b", status="running")
        _task(db, "c")
        repo.update_paper_trading_task(db, "c", status="stopped")

    def test_倒序与过滤(self, db):
        assert [t["task_id"] for t in repo.list_paper_trading_tasks(db)] == \
            ["c", "b", "a"], "created_at DESC——最晚创建在前"
        assert sorted(t["task_id"] for t in
                      repo.list_paper_trading_tasks(db, status="running")) == \
            ["a", "b"]

    def test_分页(self, db):
        page = [t["task_id"] for t in
                repo.list_paper_trading_tasks(db, limit=1, offset=1)]
        assert page == ["b"]

    def test_计数(self, db):
        assert repo.count_paper_trading_tasks(db) == 3
        assert repo.count_paper_trading_tasks(db, status="running") == 2


class TestTradeIdempotent:
    """save_paper_trade：OR IGNORE 幂等（B1.3 唯一键）。"""

    def test_同键重放_不增行(self, db):
        _task(db, "t1")
        for _ in range(3):
            repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                                  side="buy", action="open", price=100.0,
                                  quantity=1.0)
        assert repo.count_paper_trades(db, "t1") == 1

    def test_同键不同载荷_仍被忽略(self, db):
        """OR IGNORE 语义：冲突即忽略，后来者不更新既有行。"""
        _task(db, "t1")
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                              side="buy", action="open", price=100.0, quantity=1.0)
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                              side="sell", action="open", price=999.0, quantity=9.0)
        trades = repo.list_paper_trades(db, "t1")
        assert len(trades) == 1
        assert trades[0]["price"] == 100.0, "首写胜出，重放不覆盖"

    def test_键任一部分不同_独立成行(self, db):
        _task(db, "t1")
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                              side="buy", action="open", price=100.0, quantity=1.0)
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                              side="buy", action="close", price=110.0,
                              quantity=1.0, pnl=10.0)
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T04:00",
                              side="buy", action="open", price=105.0, quantity=1.0)
        assert repo.count_paper_trades(db, "t1") == 3

    def test_bar_time倒序limit(self, db):
        _task(db, "t1")
        for bar in ("2026-01-01T00:00", "2026-01-02T00:00", "2026-01-03T00:00"):
            repo.save_paper_trade(db, task_id="t1", bar_time=bar,
                                  side="buy", action="open", price=1.0, quantity=1.0)
        latest = repo.list_paper_trades(db, "t1", limit=2)
        assert [t["bar_time"] for t in latest] == \
            ["2026-01-03T00:00", "2026-01-02T00:00"]


class TestEquitySnapshots:
    """save_equity_snapshots：批量 + (task_id, timestamp) 唯一幂等 + 缺省。"""

    def test_批量写入与缺省字段(self, db):
        _task(db, "t1")
        repo.save_equity_snapshots(db, "t1", [
            {"timestamp": "2026-01-01T00:00", "equity": 100.0, "balance": 100.0},
            {"timestamp": "2026-01-01T04:00", "equity": 105.0, "balance": 105.0,
             "unrealized_pnl": 5.0, "position_side": "long"},
        ])
        rows = repo.list_equity_snapshots(db, "t1")
        assert len(rows) == 2
        assert rows[0]["unrealized_pnl"] == 0.0, "缺省补 0.0"
        assert rows[0]["position_side"] == "flat", "缺省补 flat"
        assert rows[1]["position_side"] == "long"

    def test_同timestamp重放_不增行(self, db):
        """OR IGNORE 幂等（CARD-01 修复转正）：(task_id, timestamp) 唯一
        索引三处声明已收敛 UNIQUE（ORM models + Alembic 04b595687d2f +
        legacy_init 同规），重放被约束拦截、首写胜出。"""
        _task(db, "t1")
        batch = [{"timestamp": "2026-01-01T00:00", "equity": 100.0, "balance": 100.0}]
        repo.save_equity_snapshots(db, "t1", batch)
        batch[0]["equity"] = 999.0
        repo.save_equity_snapshots(db, "t1", batch)
        rows = repo.list_equity_snapshots(db, "t1")
        assert len(rows) == 1
        assert rows[0]["equity"] == 100.0, "首写胜出"

    def test_空列表noop(self, db):
        _task(db, "t1")
        repo.save_equity_snapshots(db, "t1", [])
        assert repo.list_equity_snapshots(db, "t1") == []

    def test_时间正序读取(self, db):
        _task(db, "t1")
        repo.save_equity_snapshots(db, "t1", [
            {"timestamp": "2026-01-03T00:00", "equity": 3.0, "balance": 3.0},
            {"timestamp": "2026-01-01T00:00", "equity": 1.0, "balance": 1.0},
        ])
        assert [r["timestamp"] for r in repo.list_equity_snapshots(db, "t1")] == \
            ["2026-01-01T00:00", "2026-01-03T00:00"]


class TestCascadeDelete:
    """delete_paper_trading_task：三表级联删除，单事务原子。"""

    def test_级联三表清空(self, db):
        _task(db, "t1")
        _task(db, "keep")
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                              side="buy", action="open", price=1.0, quantity=1.0)
        repo.save_equity_snapshots(db, "t1", [
            {"timestamp": "2026-01-01T00:00", "equity": 1.0, "balance": 1.0}])
        assert repo.delete_paper_trading_task(db, "t1") is True
        assert repo.get_paper_trading_task(db, "t1") is None
        assert repo.count_paper_trades(db, "t1") == 0
        assert repo.list_equity_snapshots(db, "t1") == []
        assert repo.get_paper_trading_task(db, "keep") is not None, \
            "无关任务不受影响"

    def test_不存在返回False(self, db):
        assert repo.delete_paper_trading_task(db, "ghost") is False


class TestComputeMetrics:
    """compute_trading_metrics：委托 core.scoring（纯计算单源）。"""

    def test_任务不存在返回None(self, db):
        assert repo.compute_trading_metrics(db, "ghost") is None

    def test_零交易_win_rate缺失语义(self, db):
        _task(db, "t1", initial_cash=1000)
        metrics = repo.compute_trading_metrics(db, "t1")
        assert metrics is not None
        assert metrics["task_id"] == "t1"
        assert metrics["win_rate"] is None, "零交易 win_rate 为 None（不折叠 0）"
        assert metrics["total_return"] == 0.0, "balance=initial_cash 时收益为零"
        assert metrics["total_trades"] == 0

    def test_有交易有快照_全指标路径(self, db):
        _task(db, "t1", initial_cash=1000)
        repo.update_paper_trading_task(db, "t1", balance=1200, total_trades=2,
                                       win_count=1, loss_count=1, total_pnl=200.0)
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T00:00",
                              side="sell", action="close", price=1.0,
                              quantity=1.0, pnl=150.0)
        repo.save_paper_trade(db, task_id="t1", bar_time="2026-01-01T04:00",
                              side="sell", action="close", price=1.0,
                              quantity=1.0, pnl=-50.0)
        repo.save_equity_snapshots(db, "t1", [
            {"timestamp": "2026-01-01T00:00", "equity": 1000.0, "balance": 1000.0},
            {"timestamp": "2026-01-01T08:00", "equity": 1200.0, "balance": 1200.0},
        ])
        metrics = repo.compute_trading_metrics(db, "t1")
        assert metrics["win_rate"] == pytest.approx(0.5)
        assert metrics["profit_factor"] == pytest.approx(3.0), "150 / 50"
        assert metrics["total_return"] == pytest.approx(0.2), "1200/1000-1"


class TestRecoveryAndPicking:
    """崩溃恢复与 runner 拾取序。"""

    def test_recover仅清running(self, db):
        for tid in ("r1", "r2"):
            _task(db, tid)
            repo.update_paper_trading_task(db, tid, status="running")
        _task(db, "p1")
        assert repo.recover_stale_trading_tasks(db) == 2
        row = repo.get_paper_trading_task(db, "r1")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "crash_recovery"
        assert repo.get_paper_trading_task(db, "p1")["status"] == "pending"

    def test_pick最早pending(self, db):
        # created_at 不在 update 白名单，拾取序按创建时刻（先建者被拾取）
        _task(db, "first")
        _task(db, "second")
        assert repo.find_pending_trading_task(db)["task_id"] == "first"

    def test_无pending返回None(self, db):
        _task(db, "t1")
        repo.update_paper_trading_task(db, "t1", status="running")
        assert repo.find_pending_trading_task(db) is None
