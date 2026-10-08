"""repository/evolution.py 特征测试网（修复期安全网）。

现状记录语义（B3d.1 迁移轨当前行为）：
- update_task 的 D3 条件更新三态——data-layer-ownership spec 规定
  「守护测试全绿」为 repository 迁移后验收线，现有守护
  test_task_state_machine.py 走 db.py 通道，本文件锚 repository 轨；
- save_snapshot/save_history 的 OR REPLACE 复合主键幂等；
- restart_crashed_tasks 的 bounded retry（上限跳过/坏 JSON 容错/
  有 champion 不重启）；
- update_best_score_if_higher 乐观锁（NULL 写/低不写/高覆盖）；
- runner 拾取序与游标/冠军回写各单点。
"""

import json

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import evolution as repo
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "evo.db"
    init_db_ext(path)
    return path


@pytest.fixture
def dna():
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, None,
                       {"type": "lt", "threshold": 30}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
    )


def _task(db, task_id: str, dna) -> None:
    repo.save_task(db, task_id, 80.0, "profit_first", "BTCUSDT", "4h", dna)


class TestGuardedUpdateD3:
    """update_task 条件更新（batch1 D3）：前态守护与无条件写两形态。"""

    def test_合法前态_条件更新生效返回True(self, db, dna):
        _task(db, "t1", dna)
        ok = repo.update_task(db, "t1", status="running",
                              expected_statuses=("pending",))
        assert ok is True
        assert repo.get_task(db, "t1")["status"] == "running"

    def test_非法前态_拒绝返回False且状态不变(self, db, dna):
        _task(db, "t1", dna)
        ok = repo.update_task(db, "t1", status="stopped",
                              expected_statuses=("running",))
        assert ok is False, "非法前态须以 False 拒绝（调用方转 409）"
        assert repo.get_task(db, "t1")["status"] == "pending"

    def test_不提供前态_无条件写(self, db, dna):
        _task(db, "t1", dna)
        ok = repo.update_task(db, "t1", status="stopped",
                              champion_dna=dna, stop_reason="manual")
        assert ok is True
        row = repo.get_task(db, "t1")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "manual"

    def test_提供前态但不写status_条件不附加(self, db, dna):
        """精细语义：前态条件仅在本次写入 status 时生效（实现判式
        expected_statuses is not None and status is not None）。"""
        _task(db, "t1", dna)
        ok = repo.update_task(db, "t1", stop_reason="note",
                              expected_statuses=("running",))
        assert ok is True, "不写 status 时不加前态条件——无条件写生效"
        assert repo.get_task(db, "t1")["stop_reason"] == "note"

    def test_任务不存在_返回False(self, db):
        assert repo.update_task(db, "ghost", status="running") is False


class TestSnapshotHistoryIdempotent:
    """OR REPLACE 复合主键幂等：同键重写整体替换，不报错不增行。"""

    def test_同代快照重写_替换生效(self, db, dna):
        _task(db, "t1", dna)
        repo.save_snapshot(db, "t1", generation=3, best_score=50.0,
                           avg_score=40.0, best_dna=dna, population=[dna])
        repo.save_snapshot(db, "t1", generation=3, best_score=72.5,
                           avg_score=60.0, best_dna=dna, population=[dna, dna])
        rows = repo.list_top_snapshots(db, "t1", limit=10)
        assert len(rows) == 1, "同 (task_id, generation) 重写须替换而非追加"
        assert rows[0]["best_score"] == 72.5

    def test_同代历史重写_替换生效(self, db, dna):
        _task(db, "t1", dna)
        for score in (10.0, 20.0):
            repo.save_history(db, "t1", generation=2, best_score=score,
                              avg_score=score / 2, top3_summary="[]")
        hist = repo.get_history(db, "t1")
        assert len(hist) == 1
        assert hist[0]["best_score"] == 20.0

    def test_历史按generation正序(self, db, dna):
        _task(db, "t1", dna)
        for gen in (3, 1, 2):
            repo.save_history(db, "t1", generation=gen, best_score=gen * 10.0,
                              avg_score=gen * 5.0, top3_summary="[]")
        assert [h["generation"] for h in repo.get_history(db, "t1")] == [1, 2, 3]


class TestListCount:
    """list_all_tasks/count_all_tasks：过滤 + created_at 倒序 + 分页。"""

    @pytest.fixture(autouse=True)
    def _seed(self, db, dna):
        for tid, status, created in [
            ("a", "running", "2026-01-03T00:00:00+00:00"),
            ("b", "running", "2026-01-01T00:00:00+00:00"),
            ("c", "completed", "2026-01-02T00:00:00+00:00"),
        ]:
            _task(db, tid, dna)
            repo.update_task(db, tid, status=status)
            # created_at 显式固定，排序断言不依赖时钟精度
            repo.update_task_config(db, tid, created_at=created)

    def test_倒序与status过滤(self, db):
        ids = [t["task_id"] for t in repo.list_all_tasks(db)]
        assert ids == ["a", "c", "b"], "created_at DESC"
        running = [t["task_id"] for t in repo.list_all_tasks(db, status="running")]
        assert running == ["a", "b"]

    def test_偏移分页(self, db):
        page = [t["task_id"] for t in repo.list_all_tasks(db, limit=1, offset=1)]
        assert page == ["c"]

    def test_计数全量与过滤(self, db):
        assert repo.count_all_tasks(db) == 3
        assert repo.count_all_tasks(db, status="running") == 2


class TestCrashRecovery:
    """崩溃恢复对：recover（running→stopped）与 restart（bounded retry）。"""

    def _crash_residual(self, db, dna, task_id, progress=None, with_champion=False):
        """构造崩溃残留态：stopped + crash_recovery + 无 champion。"""
        _task(db, task_id, dna)
        repo.update_task(db, task_id, status="stopped",
                         stop_reason="crash_recovery",
                         champion_dna=dna if with_champion else None)
        if progress is not None:
            repo.update_progress(db, task_id, progress)

    def test_recover仅清running_返回清理数(self, db, dna):
        for tid in ("r1", "r2"):
            _task(db, tid, dna)
            repo.update_task(db, tid, status="running")
        _task(db, "p1", dna)  # pending 不属于残留态
        assert repo.recover_stale_tasks(db) == 2
        assert repo.get_task(db, "r1")["stop_reason"] == "crash_recovery"
        assert repo.get_task(db, "p1")["status"] == "pending"

    def test_restart无champion_计数递增重置pending(self, db, dna):
        self._crash_residual(db, dna, "t1",
                             progress=json.dumps({"restart_count": 1}))
        assert repo.restart_crashed_tasks(db) == 1
        row = repo.get_task(db, "t1")
        assert row["status"] == "pending"
        assert row["stop_reason"] is None
        assert json.loads(row["progress_json"])["restart_count"] == 2

    def test_restart达上限_跳过(self, db, dna):
        self._crash_residual(db, dna, "t1",
                             progress=json.dumps({"restart_count": 3}))
        assert repo.restart_crashed_tasks(db, max_restarts=3) == 0
        assert repo.get_task(db, "t1")["status"] == "stopped"

    def test_restart有champion_不重启(self, db, dna):
        self._crash_residual(db, dna, "t1", with_champion=True)
        assert repo.restart_crashed_tasks(db) == 0
        assert repo.get_task(db, "t1")["status"] == "stopped"

    def test_restart坏progress_json_容错为空(self, db, dna):
        self._crash_residual(db, dna, "t1", progress="not-json")
        assert repo.restart_crashed_tasks(db) == 1
        assert json.loads(repo.get_task(db, "t1")["progress_json"]) == \
            {"restart_count": 1}

    def test_restart_非crash残留态不动(self, db, dna):
        _task(db, "t1", dna)
        repo.update_task(db, "t1", status="stopped", stop_reason="manual")
        assert repo.restart_crashed_tasks(db) == 0


class TestRunnerPicking:
    """runner 拾取序：最早创建的 pending。"""

    def test_多pending取最早(self, db, dna):
        for tid, created in [("new", "2026-01-03T00:00:00+00:00"),
                             ("old", "2026-01-01T00:00:00+00:00")]:
            _task(db, tid, dna)
            repo.update_task_config(db, tid, created_at=created)
        assert repo.find_pending_task(db)["task_id"] == "old"

    def test_无pending返回None(self, db, dna):
        _task(db, "t1", dna)
        repo.update_task(db, "t1", status="running")
        assert repo.find_pending_task(db) is None


class TestTopSnapshots:
    """list_top_snapshots：best_score 倒序 + limit（路由 top-strategies）。"""

    def test_按best_score倒序限条(self, db, dna):
        _task(db, "t1", dna)
        for gen, score in [(1, 30.0), (2, 90.0), (3, 60.0), (4, 10.0)]:
            repo.save_snapshot(db, "t1", generation=gen, best_score=score,
                               avg_score=score, best_dna=dna, population=[dna])
        top = repo.list_top_snapshots(db, "t1", limit=2)
        assert [(r["generation"], r["best_score"]) for r in top] == \
            [(2, 90.0), (3, 60.0)]
        assert set(top[0].keys()) == {"generation", "best_score", "best_dna"}, \
            "仅投影三列（路由消费形制）"


class TestCursorAndChampionWrites:
    """游标/进度/冠军回写单点：runner 每代与终局路径。"""

    @pytest.fixture(autouse=True)
    def _seed(self, db, dna):
        _task(db, "t1", dna)

    def test_phase与progress原样落库(self, db):
        repo.update_phase(db, "t1", "evolving")
        repo.update_progress(db, "t1", '{"gen": 7}')
        row = repo.get_task(db, "t1")
        assert row["current_phase"] == "evolving"
        assert row["progress_json"] == '{"gen": 7}'

    def test_current_generation推进(self, db):
        repo.update_current_generation(db, "t1", 12)
        assert repo.get_task(db, "t1")["current_generation"] == 12

    def test_champion_metrics终局三写(self, db):
        repo.update_champion_metrics(
            db, "t1", best_fitness=1.25, champion_metrics='{"ret": 0.3}',
            champion_dimension_scores='{"profit": 0.8}')
        row = repo.get_task(db, "t1")
        assert row["best_fitness"] == 1.25
        assert row["champion_metrics"] == '{"ret": 0.3}'
        assert row["champion_dimension_scores"] == '{"profit": 0.8}'

    def test_oos_results写入(self, db):
        repo.update_oos_results(db, "t1", oos_fitness=0.9, oos_qualified=1,
                                oos_metrics='{"oos": true}')
        row = repo.get_task(db, "t1")
        assert (row["oos_fitness"], row["oos_qualified"]) == (0.9, 1)

    def test_champion_full_双分同值(self, db):
        repo.update_champion_full(db, "t1", best_score=88.0,
                                  champion_metrics="{}",
                                  champion_dimension_scores="{}")
        row = repo.get_task(db, "t1")
        assert row["best_score"] == row["best_fitness"] == 88.0

    def test_best_score乐观锁_NULL写入_低不动_高覆盖(self, db):
        repo.update_best_score_if_higher(db, "t1", 50.0)
        assert repo.get_task(db, "t1")["best_score"] == 50.0, "NULL 时写入"
        repo.update_best_score_if_higher(db, "t1", 30.0)
        assert repo.get_task(db, "t1")["best_score"] == 50.0, "低分不覆盖"
        repo.update_best_score_if_higher(db, "t1", 70.0)
        row = repo.get_task(db, "t1")
        assert (row["best_score"], row["best_fitness"]) == (70.0, 70.0), \
            "高分覆盖且 best_fitness 同步"

    def test_update_task_config直写列(self, db):
        repo.update_task_config(db, "t1", population_size=30, mode="explore")
        row = repo.get_task(db, "t1")
        assert row["population_size"] == 30
        assert row["mode"] == "explore"

    def test_update_task_config显式updated_at被内部刷新覆盖(self, db):
        """CARD-06 边界锚（553e66f 覆盖式合并，paper_trading 先例）：
        显式携带不 TypeError，updated_at 以仓储内部刷新为准。"""
        repo.update_task_config(db, "t1", updated_at="1999-01-01T00:00:00+00:00",
                                population_size=42)
        row = repo.get_task(db, "t1")
        assert row["updated_at"] != "1999-01-01T00:00:00+00:00", \
            "内部时间戳须覆盖显式携带"
        assert row["population_size"] == 42, "业务列须正常写入"

    def test_五函数刷新updated_at_CARD06(self, db, dna):
        """CARD-06 契约锚：冠军/OOS/配置写路径统一刷新 updated_at；
        乐观锁未命中（低分）零行更新——updated_at 不动。"""
        import time
        ids = ("u1", "u2", "u3", "u4", "u5")  # 独立任务，避开 autouse _seed 的 t1
        for tid in ids:
            _task(db, tid, dna)
            repo.update_task_config(db, tid, created_at="2020-01-01T00:00:00+00:00")
        time.sleep(0.002)

        writers = [
            (lambda: repo.update_champion_metrics(
                db, "u1", best_fitness=1.0, champion_metrics="{}",
                champion_dimension_scores="{}"), "u1"),
            (lambda: repo.update_oos_results(
                db, "u2", oos_fitness=0.9, oos_qualified=1, oos_metrics="{}"), "u2"),
            (lambda: repo.update_champion_full(
                db, "u3", best_score=88.0, champion_metrics="{}",
                champion_dimension_scores="{}"), "u3"),
            (lambda: repo.update_best_score_if_higher(db, "u4", 50.0), "u4"),
            (lambda: repo.update_task_config(db, "u5", population_size=30), "u5"),
        ]
        for write, tid in writers:
            before = repo.get_task(db, tid)["updated_at"]
            time.sleep(0.002)
            write()
            after = repo.get_task(db, tid)["updated_at"]
            assert after > before, f"{tid} 写路径未刷新 updated_at"

        # 乐观锁未命中：零行更新，updated_at 精确不动
        snap = repo.get_task(db, "u4")["updated_at"]
        time.sleep(0.002)
        repo.update_best_score_if_higher(db, "u4", 30.0)
        assert repo.get_task(db, "u4")["updated_at"] == snap, \
            "未命中零行更新——updated_at 不得刷新"
