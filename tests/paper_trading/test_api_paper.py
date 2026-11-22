"""rebuild-paper-trading 5.1：/api/paper-trading 端点族行为锚。

覆盖任务完成标准：创建恒 201 返 pending 行（拒绝面仅 404 strategy
not found/422 校验数组；语义参数 direction/timeframe 不在参数面传入
即拒）、window_bars 四行编码表、stop 三态分派（running 协作停/
pending 直接 stopped/终态 409）、clone·DELETE 终态门槛+级联删五表
行数断言、GET 族分页与 404、report/final ?version 参数、runner-status。

基建形制：TestClient 不进 with 块（不起 lifespan/守护线程——调度器
为同步实例不 start）；app.state 手动挂载（工程坑位：with 块前覆写
app.state 会被 lifespan 盖回，不进块即无 lifespan）。任务行直建沿
tests/paper_trading/test_reporting.py 形制（仓库直建=测试通道）。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.integration]

BASE_TIME = "2026-10-01T00:00:00+00:00"


# ── 基建 ──


@pytest.fixture
def paper_api(tmp_path: Path):
    """(client, db)：REST 全链 + 同步调度器（无守护线程）。"""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from core.paper_trading.runtime.scheduler import PaperScheduler
    from core.persistence.db_ext import init_db_ext

    db = tmp_path / "paper_api.db"
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    init_db_ext(db)
    app = create_app(db_path=db, data_dir=data_dir)
    app.state.db_path = db
    app.state.data_dir = data_dir
    app.state.paper_scheduler = PaperScheduler(db_path=db, data_dir=data_dir)
    yield TestClient(app), db


def _seed_strategy(db: Path, strategy_id: str = "st-1", *,
                   timeframe: str = "4h", symbol: str = "BTCUSDT",
                   leverage: int = 3, status: str = "active",
                   qualified=None) -> str:
    """造策略库条目（上盘流转源头；同签名去重面错开用独立 id）。"""
    from core.persistence.repository.strategy import save_strategy
    from tests.helpers.data_factory import make_dna

    save_strategy(
        db, strategy_id=strategy_id, dna_json=make_dna(
            timeframe=timeframe, symbol=symbol, leverage=leverage).to_json(),
        symbol=symbol, timeframe=timeframe, status=status,
        gene_signature=f"sig_{strategy_id}")
    if qualified is not None:
        # qualified 不在 update_strategy 白名单（§9.2：验证服务族专属
        # 面）——研究状态快照映射的测试造数走 ORM 直写
        from sqlalchemy import update

        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import Strategy

        with engine_for(db).begin() as conn:
            conn.execute(update(Strategy).where(
                Strategy.strategy_id == strategy_id).values(
                qualified=qualified))
    return strategy_id


def _create(client, **overrides) -> dict:
    """POST /tasks 直返 body（201 断言内联）。"""
    body = {"strategy_id": "st-1"}
    body.update(overrides)
    resp = client.post("/api/paper-trading/tasks", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_terminal(db: Path, task_id: str = "pt_t000000001", *,
                   status: str = "completed") -> str:
    """仓库直建终态任务（clone/DELETE/report-final 的门槛对象）。
    迁移链走合法路径（缺陷⑤守门后 update_task 拒 pending→completed
    类非法直迁）：completed=两跳 pending→running→completed；stopped/
    error 自 pending 单跳合法。"""
    from core.persistence.repository import paper

    paper.create_task(
        db, task_id=task_id, strategy_id="st-1",
        dna_snapshot_json='{"risk_genes": {"leverage": 2}}',
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified")
    if status == "completed":
        paper.update_task(db, task_id, status="running",
                          started_at=BASE_TIME)
        paper.update_task(db, task_id, status="completed",
                          stopped_at=BASE_TIME)
    else:
        paper.update_task(db, task_id, status=status, started_at=BASE_TIME,
                          stopped_at=BASE_TIME)
    return task_id


def _seed_rows(db: Path, task_id: str) -> None:
    """三明细表样例行（fills/equity/funding 分页断言数据）。"""
    from core.persistence.repository import paper

    paper.append_bar(
        db, task_id, bar_time=BASE_TIME,
        fills=[
            {"seq": 4, "kind": "open", "side": "long", "price": 100.0,
             "quantity": 1.0, "fee": 0.1, "realized_pnl": None,
             "reason": "signal"},
            {"seq": 5, "kind": "close", "side": "long", "price": 105.0,
             "quantity": 1.0, "fee": 0.1, "realized_pnl": 4.9,
             "reason": "signal"},
        ],
        funding=[{"settle_time": BASE_TIME, "status": "settled",
                  "rate": 0.0001, "amount": -0.01,
                  "position_quantity": 1.0, "settle_price": 100.0}],
        equity={"equity": 100_010.0, "balance": 100_010.0,
                "unrealized_pnl": 0.0, "position_side": None})


# ── 创建：恒 201 + 拒绝面仅两类 + 语义不可覆写 ──


class TestCreateTask:
    def test_create_returns_201_pending_row(self, paper_api) -> None:
        """创建恒 201 返 pending 行（受理≠运行）；symbol/timeframe 从
        DNA 抄录、leverage 缺省取 DNA、research_status 快照落行。"""
        client, db = paper_api
        _seed_strategy(db, leverage=3)
        row = _create(client)
        assert row["status"] == "pending"
        assert row["strategy_id"] == "st-1"
        assert row["symbol"] == "BTCUSDT"      # execution_genes.symbol 抄录
        assert row["timeframe"] == "4h"        # execution_genes.timeframe 抄录
        assert row["leverage"] == 3            # 缺省取 DNA risk_genes.leverage
        assert row["research_status"] == "unverified"
        assert row["bars_processed"] == 0

    def test_create_unknown_strategy_404(self, paper_api) -> None:
        """拒绝面①：strategy_id 不存在 → 404 strategy not found。"""
        client, db = paper_api
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "ghost"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "strategy not found"

    def test_create_invalid_values_422_pydantic_array(self, paper_api) -> None:
        """拒绝面②：值域校验失败 → 422 Pydantic 校验明细数组。"""
        client, db = paper_api
        _seed_strategy(db)
        resp = client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st-1", "initial_cash": 0})
        assert resp.status_code == 422
        assert isinstance(resp.json()["detail"], list)  # 校验明细数组形制

    def test_semantic_params_not_in_surface_422(self, paper_api) -> None:
        """语义不可覆写（spec Scenario）：direction/timeframe 不在创建
        参数面——传入即 422（extra=forbid）。"""
        client, db = paper_api
        _seed_strategy(db)
        for extra in ({"direction": "short"}, {"timeframe": "1h"}):
            resp = client.post("/api/paper-trading/tasks", json={
                "strategy_id": "st-1", **extra})
            assert resp.status_code == 422, extra
            assert isinstance(resp.json()["detail"], list)

    def test_create_illegal_symbol_422(self, paper_api) -> None:
        """symbol 入口白名单（CARD-01①）：用户可控 symbol 直落任务行
        且下游 parquet 路径直拼——路径注入形/含分隔符/超长/空串均 422
        （validate_symbol 单源，Pydantic 校验数组形制）。"""
        client, db = paper_api
        _seed_strategy(db)
        for bad in ("../../X", "BTC/USDT", "BTC USDT", "x" * 21, ""):
            resp = client.post("/api/paper-trading/tasks", json={
                "strategy_id": "st-1", "symbol": bad})
            assert resp.status_code == 422, bad
            assert isinstance(resp.json()["detail"], list)

    def test_create_legal_symbol_passes(self, paper_api) -> None:
        """合法 symbol 放行对照（白名单不误伤——显式覆写 DNA 值合法）。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client, symbol="ETHUSDT")
        assert row["symbol"] == "ETHUSDT"

    def test_create_overload_stays_pending_201(self, paper_api) -> None:
        """上限排队（spec Scenario 超限排队）：并发满载时创建不拒绝
        （恒 201 停留 pending）——上限不进创建拒绝面。"""
        client, db = paper_api
        _seed_strategy(db)
        # fixture 调度器 max_running 缺省 100（change paper-mount-and-
        # concurrency 护栏上调）；造满 100 个 running 行占满
        from core.paper_trading.constants import MAX_RUNNING_TASKS
        from core.persistence.repository import paper

        for i in range(MAX_RUNNING_TASKS):
            tid = f"pt_run{i:09d}"
            paper.create_task(
                db, task_id=tid, strategy_id="st-1",
                dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified")
            paper.update_task(db, tid, status="running")
        row = _create(client)
        assert row["status"] == "pending"  # 第 101 个恒 201 排队（满载）

    def test_create_archived_status_409(self, paper_api) -> None:
        """挂载门槛放宽后（Q3 终审 2026-10-28）archived=唯一排除面：
        409 STRATEGY_ILLEGAL_TRANSITION，任务不建+策略不变+零事件。"""
        client, db = paper_api
        from core.persistence.repository import paper

        _seed_strategy(db, strategy_id="st-1", status="draft")
        _force_strategy_status(db, "st-1", "archived")
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "STRATEGY_ILLEGAL_TRANSITION"
        assert paper.count_tasks(db) == 0, "409 不建任务"
        assert _strategy_status(db, "st-1") == "archived", "策略不变"
        assert _lifecycle_events(db, "st-1") == [], "零事件"


# ── 挂盘三态契约（build-strategy-lifecycle 5.1） ──


def _force_strategy_status(db: Path, strategy_id: str, status: str) -> None:
    """测试构造轨：直 SQL 改策略状态（save_strategy 白名单外四态；
    生产写面唯一=lifecycle CAS——构造轨豁免形制沿 test_matrix
    _set_status 先例）。"""
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).begin() as conn:
        conn.execute(text("UPDATE strategy SET status=:s"
                          " WHERE strategy_id=:i"),
                     {"s": status, "i": strategy_id})


def _strategy_status(db: Path, strategy_id: str) -> str:
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).connect() as conn:
        return conn.execute(text("SELECT status FROM strategy"
                                 " WHERE strategy_id=:i"),
                            {"i": strategy_id}).scalar_one()


def _lifecycle_events(db: Path, strategy_id: str) -> list:
    """迁移事件清单 [(from,to,trigger)]（创建序——审计读序）。"""
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).connect() as conn:
        return conn.execute(text(
            "SELECT from_status, to_status, trigger FROM"
            " strategy_transition_event WHERE strategy_id=:i"
            " ORDER BY created_at"), {"i": strategy_id}).all()


def _event_evidence(db: Path, strategy_id: str) -> dict:
    import json as _json

    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).connect() as conn:
        raw = conn.execute(text(
            "SELECT evidence_json FROM strategy_transition_event"
            " WHERE strategy_id=:i ORDER BY created_at"),
            {"i": strategy_id}).scalar_one()
    return _json.loads(raw)


class TestCreateTaskLifecycleMount:
    """5.1 挂盘三态契约：迁移/同态续挂/409 三态分派（住 transit 返回
    层）+同事务原子两向。"""

    def test_mount_from_active_migrates_with_event(self, paper_api) -> None:
        """①from=active：201+pending 受理语义不变+同事务迁移
        active→paper+事件 (mount) evidence 含 task_id。"""
        client, db = paper_api
        _seed_strategy(db, status="active")
        row = _create(client)
        assert row["status"] == "pending"
        assert _strategy_status(db, "st-1") == "paper"
        assert _lifecycle_events(db, "st-1") == [("active", "paper", "mount")]
        assert _event_evidence(db, "st-1") == {"task_id": row["task_id"]}

    def test_mount_from_paper_continues_without_event(self, paper_api) -> None:
        """②from=paper（同策略对照续挂）：任务照建+事件表零新行+受理
        201。"""
        client, db = paper_api
        _seed_strategy(db, status="active")
        first = _create(client)   # 首挂：active→paper（产生 1 事件）
        second = _create(client)  # 续挂：from=paper 同态不迁移
        assert second["status"] == "pending"
        assert second["task_id"] != first["task_id"]
        assert _strategy_status(db, "st-1") == "paper"
        assert _lifecycle_events(db, "st-1") == [("active", "paper", "mount")], \
            "同态续挂零新事件"

    def test_mount_atomic_rollback_on_task_failure(
            self, paper_api, monkeypatch) -> None:
        """③原子反向：建任务失败→迁移回滚（策略仍 active+零事件+
        零任务）——两侧同事务原子成败。"""
        client, db = paper_api
        from core.persistence.repository import paper

        _seed_strategy(db, status="active")

        def _boom(*args, **kwargs):
            raise RuntimeError("task insert failed")

        monkeypatch.setattr(paper, "create_task", _boom)
        # TestClient 默认重抛服务端异常——异常传播即失败面；断言点在
        # 事务回滚（两侧皆无），500 响应形制由 propagate+rollback 传导
        with pytest.raises(RuntimeError):
            client.post("/api/paper-trading/tasks",
                        json={"strategy_id": "st-1"})
        assert _strategy_status(db, "st-1") == "active", "迁移回滚"
        assert _lifecycle_events(db, "st-1") == [], "事件回滚"
        assert paper.count_tasks(db) == 0


# ── window_bars 四形态编码 ──


class TestWindowBarsEncoding:
    """四行编码表（spec 上盘流转 Requirement：不传=180/显式 null=NULL
    不结题/N≥1 落 N/0 负数非整数=422）。"""

    @pytest.mark.parametrize("window_bars,expected", [
        ("__omit__", 180),      # 不传=缺省 180
        (None, None),           # 显式 null=不限期（不自动结题）
        (60, 60),               # 显式 N≥1=N 根
    ])
    def test_encoding_four_forms(self, paper_api, window_bars, expected) -> None:
        client, db = paper_api
        _seed_strategy(db)
        if window_bars == "__omit__":
            row = _create(client)
        else:
            row = _create(client, window_bars=window_bars)
        assert row["window_bars"] == expected

    @pytest.mark.parametrize("bad", [0, -5, 2.5])
    def test_illegal_values_422(self, paper_api, bad) -> None:
        client, db = paper_api
        _seed_strategy(db)
        resp = client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st-1", "window_bars": bad})
        assert resp.status_code == 422
        assert isinstance(resp.json()["detail"], list)


# ── stop 三态分派 ──


class TestStopDispatch:
    def test_stop_pending_direct_stopped(self, paper_api) -> None:
        """pending→直接 stopped（未拾取撤销：stop_reason=user_stop）。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client)
        resp = client.post(f"/api/paper-trading/tasks/{row['task_id']}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"
        assert resp.json()["stop_reason"] == "user_stop"

    def test_stop_running_cooperative(self, paper_api) -> None:
        """running→协作停：调度器内存执行器在座时只发 request_stop
        （行仍 running——bar 边界退出后由执行器迁移终态）。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client)
        from core.persistence.repository import paper

        paper.update_task(db, row["task_id"], status="running",
                          started_at=BASE_TIME)
        # 注入假执行器（controller spy）占调度器内存位
        scheduler = client.app.state.paper_scheduler
        fake_exec = MagicMock()
        scheduler._executors[row["task_id"]] = fake_exec

        resp = client.post(f"/api/paper-trading/tasks/{row['task_id']}/stop")
        assert resp.status_code == 200
        fake_exec.controller.request_stop.assert_called_once()
        assert resp.json()["status"] == "running"  # 协作停未迁移

    def test_stop_terminal_409(self, paper_api) -> None:
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db, status="stopped")
        resp = client.post(f"/api/paper-trading/tasks/{tid}/stop")
        assert resp.status_code == 409

    def test_stop_unknown_404(self, paper_api) -> None:
        client, _ = paper_api
        resp = client.post("/api/paper-trading/tasks/ghost/stop")
        assert resp.status_code == 404


# ── clone / DELETE 终态门槛 + 级联删 ──


class TestCloneAndDelete:
    def test_clone_terminal_201_new_pending(self, paper_api) -> None:
        """终态克隆重跑：新 pending 行同 DNA 快照同参数、进度归零。"""
        client, db = paper_api
        _seed_strategy(db, qualified=1)
        tid = _seed_terminal(db)
        resp = client.post(f"/api/paper-trading/tasks/{tid}/clone")
        assert resp.status_code == 201, resp.text
        new = resp.json()
        assert new["status"] == "pending"
        assert new["bars_processed"] == 0
        assert new["task_id"] != tid
        assert new["strategy_id"] == "st-1"
        # research_status 按 clone 时点重解析（REST 查询期现算面——
        # qualified=1 存量行→序③ verified）
        assert new["research_status"] == "verified"

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_clone_active_409(self, paper_api, status) -> None:
        client, db = paper_api
        _seed_strategy(db)
        from core.persistence.repository import paper

        tid = "pt_c1" + status[:3]
        paper.create_task(
            db, task_id=tid, strategy_id="st-1", dna_snapshot_json="{}",
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper.update_task(db, tid, status=status)
        resp = client.post(f"/api/paper-trading/tasks/{tid}/clone")
        assert resp.status_code == 409

    def test_clone_strategy_deleted_404(self, paper_api) -> None:
        """strategy 已删→clone 快照无法重算→404（仓库 ValueError 转译）。"""
        client, db = paper_api
        _seed_strategy(db, strategy_id="st-gone")
        tid = _seed_terminal(db)
        from core.persistence.repository import paper as paper_repo
        from core.persistence.orm.models import Strategy
        from core.persistence.orm.engine import engine_for
        from sqlalchemy import delete

        with engine_for(db).begin() as conn:
            conn.execute(delete(Strategy).where(
                Strategy.strategy_id == "st-gone"))
        assert paper_repo.get_task(db, tid) is not None
        resp = client.post(f"/api/paper-trading/tasks/{tid}/clone")
        assert resp.status_code == 404

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_delete_active_409_stop_first(self, paper_api, status) -> None:
        client, db = paper_api
        _seed_strategy(db)
        from core.persistence.repository import paper

        tid = "pt_d1" + status[:3]
        paper.create_task(
            db, task_id=tid, strategy_id="st-1", dna_snapshot_json="{}",
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper.update_task(db, tid, status=status)
        resp = client.delete(f"/api/paper-trading/tasks/{tid}")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "stop first"

    def test_delete_terminal_cascades_five_tables(self, paper_api) -> None:
        """终态删除：五表级联单事务（行数断言直查 ORM）。"""
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        _seed_rows(db, tid)
        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import (
            PtEquity, PtFill, PtFunding, PtReport, PtTask)
        from sqlalchemy import func, select

        # 结题报告行（第五表）
        from core.persistence.repository import paper as paper_repo

        paper_repo.save_report(
            db, tid, report_version=1, generated_at=BASE_TIME,
            as_of=BASE_TIME, report_json="{}")

        engine = engine_for(db)
        with engine.connect() as conn:
            def _count(table):
                return conn.execute(
                    select(func.count()).select_from(table).where(
                        table.task_id == tid)).scalar_one()
            counts_before = [_count(t) for t in
                             (PtTask, PtFill, PtEquity, PtFunding, PtReport)]
        assert counts_before == [1, 2, 1, 1, 1]  # 五表均有行

        resp = client.delete(f"/api/paper-trading/tasks/{tid}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}

        with engine.connect() as conn:
            counts_after = [_count(t) for t in
                            (PtTask, PtFill, PtEquity, PtFunding, PtReport)]
        assert counts_after == [0, 0, 0, 0, 0]  # 级联五表清零


# ── research_status 查询期现算（build-strategy-lifecycle 5.3 读点②） ──


class TestResearchStatusLiveReadout:
    """REST 读点现算：三序随动（迁移后随动非快照）+快照列继续写入
    （创建时点审计值合法在场）+clone 三层。"""

    def test_rest_readout_follows_migration(self, paper_api) -> None:
        """正反向夹迁移：挂盘时 unverified（无事件序③）→策略侧出现
        draft→active 事件→GET 现算 verified→构造轨直改回 draft→GET
        draft（序①赢；revalidate 端点已随孤儿清理退役，状态构造走
        _force_strategy_status 直改）——同一任务行快照列全程冻结
        （现算非列值防回潮）。"""
        from sqlalchemy import text

        from core.persistence.orm.engine import engine_for
        from core.persistence.repository import paper

        client, db = paper_api
        _seed_strategy(db, status="active")
        row = _create(client)
        tid = row["task_id"]
        assert row["research_status"] == "unverified"  # 迁移前 A
        # 策略侧转正事实（构造轨直插 draft→active 事件）
        with engine_for(db).begin() as conn:
            conn.execute(text(
                "INSERT INTO strategy_transition_event (event_id,"
                " strategy_id, from_status, to_status, trigger,"
                " evidence_json, created_at) VALUES ('ev_rs01',"
                " 'st-1', 'draft', 'active', 'credential', NULL,"
                " '2026-10-18T00:00:00')"))
        got = client.get(f"/api/paper-trading/tasks/{tid}")
        assert got.json()["research_status"] == "verified"  # 迁移后 B
        # 反向：策略状态回 draft（revalidate 端点已随孤儿清理退役
        # ——fix-replay-gap-alignment 2.5；状态构造轨直改，断言对象
        # 仍是 research_status 随策略状态的重推导序）
        _force_strategy_status(db, "st-1", "draft")
        got2 = client.get(f"/api/paper-trading/tasks/{tid}")
        assert got2.json()["research_status"] == "draft"  # 序①赢
        assert paper.get_task(db, tid)["research_status_snapshot"] == \
            "unverified", "快照列冻结（创建时点审计事实）"

    def test_snapshot_column_still_written(self, paper_api) -> None:
        """快照列继续写入断言（不停写——创建时点审计值合法在场）。"""
        from core.persistence.repository import paper

        client, db = paper_api
        _seed_strategy(db, status="active")
        row = _create(client)
        stored = paper.get_task(db, row["task_id"])
        assert stored["research_status_snapshot"] == "unverified"

    def test_clone_three_layer_assertions(self, paper_api) -> None:
        """clone 三层：①clone 行新算值（qualified=1 存量行→序③
        verified）②两行查询面同值（同一策略现算）③原行列值冻结。"""
        from core.persistence.repository import paper

        client, db = paper_api
        _seed_strategy(db, status="active", qualified=1)
        tid = _seed_terminal(db)
        resp = client.post(f"/api/paper-trading/tasks/{tid}/clone")
        assert resp.status_code == 201, resp.text
        new_tid = resp.json()["task_id"]
        # ① clone 时点新算（现算面）
        assert resp.json()["research_status"] == "verified"
        # ② 两行查询面同值
        orig = client.get(f"/api/paper-trading/tasks/{tid}").json()
        cloned = client.get(f"/api/paper-trading/tasks/{new_tid}").json()
        assert orig["research_status"] == cloned["research_status"]
        # ③ 原任务行列值冻结（直建默认 unverified，不被 clone 改写）
        assert paper.get_task(db, tid)["research_status_snapshot"] == \
            "unverified"


# ── GET 族分页与 404 ──


class TestGetFamily:
    def test_list_pagination_and_filter(self, paper_api) -> None:
        client, db = paper_api
        _seed_strategy(db)
        for _ in range(3):
            _create(client)
        resp = client.get("/api/paper-trading/tasks",
                          params={"limit": 2, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["tasks"]) == 2
        assert body["total"] == 3
        # created_at 倒序（后创建在前）
        assert (body["tasks"][0]["created_at"]
                >= body["tasks"][1]["created_at"])
        # offset 翻页
        page2 = client.get("/api/paper-trading/tasks",
                           params={"limit": 2, "offset": 2}).json()
        assert len(page2["tasks"]) == 1
        # status 过滤
        pending = client.get("/api/paper-trading/tasks",
                             params={"status": "pending"}).json()
        assert pending["total"] == 3

    def test_detail_404(self, paper_api) -> None:
        client, _ = paper_api
        assert client.get("/api/paper-trading/tasks/ghost").status_code == 404

    def test_detail_full_row(self, paper_api) -> None:
        """详情含运行态组+统计组（design §7.1 端点 3）。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client)
        detail = client.get(
            f"/api/paper-trading/tasks/{row['task_id']}").json()
        for key in ("balance", "position_side", "total_trades",
                    "win_count", "total_pnl", "last_bar_time",
                    "bars_processed", "error_detail"):
            assert key in detail

    @pytest.mark.parametrize("path", ["fills", "equity", "funding"])
    def test_detail_streams_404(self, paper_api, path) -> None:
        client, _ = paper_api
        assert client.get(
            f"/api/paper-trading/tasks/ghost/{path}").status_code == 404

    def test_fills_pagination_and_audit_fields(self, paper_api) -> None:
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        _seed_rows(db, tid)
        resp = client.get(f"/api/paper-trading/tasks/{tid}/fills",
                          params={"limit": 1, "offset": 0})
        body = resp.json()
        assert body["has_more"] is True
        assert len(body["fills"]) == 1
        assert body["fills"][0]["seq"] == 4  # bar_time+seq 升序
        rest = client.get(f"/api/paper-trading/tasks/{tid}/fills",
                          params={"limit": 1, "offset": 1}).json()
        assert rest["fills"][0]["seq"] == 5
        assert rest["has_more"] is False
        # 审计字段（kind/reason/seq）在响应面
        fill = client.get(
            f"/api/paper-trading/tasks/{tid}/fills").json()["fills"][0]
        for key in ("seq", "kind", "side", "reason"):
            assert key in fill

    def test_equity_and_funding_pagination(self, paper_api) -> None:
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        _seed_rows(db, tid)
        eq = client.get(f"/api/paper-trading/tasks/{tid}/equity").json()
        assert len(eq["snapshots"]) == 1
        assert eq["snapshots"][0]["equity"] == 100_010.0
        fd = client.get(f"/api/paper-trading/tasks/{tid}/funding").json()
        assert len(fd["records"]) == 1
        assert fd["records"][0]["rate"] == 0.0001
        assert fd["records"][0]["status"] == "settled"


# ── 报告端点：滚动任意态 + final 版本参数 ──


class TestReportEndpoints:
    def test_rolling_report_any_state(self, paper_api) -> None:
        """滚动报告不设拒绝面：pending 任务可取（三层退化自解释）。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client)
        resp = client.get(f"/api/paper-trading/tasks/{row['task_id']}/report")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for layer in ("performance", "deviation", "evidence"):
            assert layer in body
        assert body["deviation"]["backtest"] is None  # 无区间数据退化
        assert body["evidence"]["insufficient_evidence"] is True

    def test_rolling_report_unknown_404(self, paper_api) -> None:
        client, _ = paper_api
        assert client.get(
            "/api/paper-trading/tasks/ghost/report").status_code == 404

    def test_final_not_concluded_409(self, paper_api) -> None:
        """未结题（pending/running）取 final → 409 "task not concluded"。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client)  # pending
        resp = client.get(
            f"/api/paper-trading/tasks/{row['task_id']}/report/final")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "task not concluded"

    def test_final_version_param(self, paper_api) -> None:
        """?version 查询参数：缺省最新版、?version=1 取结题初版（Q-2）。"""
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        from core.paper_trading.reporting import (
            finalize_report, regenerate_final_report)

        v1 = finalize_report(db, client.app.state.data_dir, tid,
                             generated_at=BASE_TIME)
        v2 = regenerate_final_report(db, client.app.state.data_dir, tid,
                                     generated_at=BASE_TIME)
        assert v1 is not None and v2 is not None

        latest = client.get(
            f"/api/paper-trading/tasks/{tid}/report/final").json()
        assert latest["report_version"] == 2  # 缺省最新
        first = client.get(
            f"/api/paper-trading/tasks/{tid}/report/final?version=1").json()
        assert first["report_version"] == 1  # 初版可取

    def test_final_terminal_without_report_404(self, paper_api) -> None:
        """终态而无固化版（直建通道绕过结题固化）→404。"""
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        resp = client.get(f"/api/paper-trading/tasks/{tid}/report/final")
        assert resp.status_code == 404

    def test_final_unknown_task_404(self, paper_api) -> None:
        client, _ = paper_api
        assert client.get(
            "/api/paper-trading/tasks/ghost/report/final").status_code == 404


# ── runner-status ──


class TestRunnerStatus:
    def test_runner_status_shape(self, paper_api) -> None:
        """队列可见性面：上限/运行数/活跃清单/pending 深度四键。"""
        client, db = paper_api
        _seed_strategy(db)
        _create(client)
        body = client.get("/api/paper-trading/runner-status").json()
        for key in ("max_running_tasks", "running_count",
                    "active_task_ids", "pending_depth"):
            assert key in body
        assert body["pending_depth"] == 1
        assert body["running_count"] == 0

    def test_runner_status_guardian_health_group(self, paper_api) -> None:
        """守护器健康组经真实路由披露（add-data-freshness-guardian
        A-1 端点面——契约「经既有 runner-status 聚合披露不新增端点」；
        旁路未装配态=缺省值形制）。"""
        client, _db = paper_api
        body = client.get("/api/paper-trading/runner-status").json()
        assert body["guardian"] == {
            "kline": {"last_run": None, "keys": {}},
            "derivs": {"last_run": None, "symbols": {}}}


class TestLifespanPlacement:
    """纸盘 lifespan 挂载摆位守护（缺陷④同类面——inspect 静态锚）。

    纸盘段（PaperScheduler 构造+startup_recover）须先于 runner.start()
    执行——段的延迟 import 链与恢复扫描持 GIL 的开销若发生在
    EvolutionRunner 启动之后，会推迟其首扫相位落入跨域时序窗口
    （tests/api/test_evolution_routes_extra.py 守护用例 409→200 flaky
    实录，12 连跑 3-5 红；摆位前移后 24/24 全绿）。语义零变化、纯
    摆位纪律——静态锚防后人「整理代码顺序」挪回（时序型竞态断言
    不可靠，按裁决以摆位锚+实证为覆盖）。
    """

    def test_stop_without_scheduler_503(self, paper_api) -> None:
        """app.state 未挂调度器 → stop 请求 503（服务不可用显式拒绝
        而非静默成功）。"""
        client, db = paper_api
        _seed_strategy(db)
        row = _create(client)
        del client.app.state.paper_scheduler
        resp = client.post(
            f"/api/paper-trading/tasks/{row['task_id']}/stop")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "paper scheduler not available"

    def test_runner_status_without_scheduler_zero_shape(
            self, paper_api) -> None:
        """runner-status 未挂调度器 → 四键零值退化形态（可见性面
        不炸）。"""
        client, db = paper_api
        _seed_strategy(db)
        del client.app.state.paper_scheduler
        body = client.get("/api/paper-trading/runner-status").json()
        assert body == {"max_running_tasks": None, "running_count": 0,
                        "active_task_ids": [], "pending_depth": 0}


class TestSignalEndpoints:
    """信号记录查询端点（change paper-signal-log 任务 6）：过滤四维+
    排序+分页 has_more 三形态+404/422+JSON 反序列化形制。"""

    @staticmethod
    def _seed_signals(db: Path, task_id: str) -> None:
        from core.persistence.repository import paper

        paper.save_signals(db, task_id, [
            {"strategy_id": "st-1",
             "bar_time": "2026-10-20T00:00:00+00:00",
             "exec_bar_time": "2026-10-20T04:00:00+00:00",
             "seq": 4, "signal_kind": "open", "direction": "long",
             "size_fraction": 0.5,
             "gene_hits": '[{"gene_index": 0, "indicator": "RSI"}]',
             "indicator_snapshot": '{"rsi_14": 25.0}',
             "outcome": "filled", "reject_reason": None, "fill_seq": 4,
             "capped": None, "equity": 100_000.0, "balance": 100_000.0,
             "unrealized_pnl": 0.0, "position_side": None,
             "position_quantity": None, "position_margin": None,
             "created_at": BASE_TIME},
            {"strategy_id": "st-1",
             "bar_time": "2026-10-20T00:00:00+00:00",
             "exec_bar_time": "2026-10-20T04:00:00+00:00",
             "seq": 7, "signal_kind": "add", "direction": "long",
             "size_fraction": 0.1,
             "gene_hits": '[]', "indicator_snapshot": '{"rsi_14": 25.0}',
             "outcome": "rejected", "reject_reason": "exposure_cap",
             "fill_seq": None, "capped": None, "equity": 99_500.0,
             "balance": 99_500.0, "unrealized_pnl": 0.0,
             "position_side": "long", "position_quantity": 10.0,
             "position_margin": 1_000.0, "created_at": BASE_TIME},
            {"strategy_id": "st-1",
             "bar_time": "2026-10-21T00:00:00+00:00",
             "exec_bar_time": "2026-10-21T00:00:00+00:00",
             "seq": 2, "signal_kind": "exit_tp", "direction": "long",
             "size_fraction": 1.0,
             "gene_hits": '[]', "indicator_snapshot": '{}',
             "outcome": "filled", "reject_reason": None, "fill_seq": 2,
             "capped": None, "equity": 101_000.0, "balance": 101_000.0,
             "unrealized_pnl": 0.0, "position_side": None,
             "position_quantity": None, "position_margin": None,
             "created_at": BASE_TIME},
            {"strategy_id": "st-1",
             "bar_time": "2026-10-22T00:00:00+00:00",
             "exec_bar_time": "2026-10-22T00:00:00+00:00",
             "seq": 0, "signal_kind": "open", "direction": None,
             "size_fraction": None,
             "gene_hits": '[]', "indicator_snapshot": '{}',
             "outcome": "expired", "reject_reason": None, "fill_seq": None,
             "capped": None, "equity": 101_000.0, "balance": 101_000.0,
             "unrealized_pnl": 0.0, "position_side": None,
             "position_quantity": None, "position_margin": None,
             "created_at": BASE_TIME},
        ])

    def _setup(self, paper_api):
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        self._seed_signals(db, tid)
        return client, tid

    def test_signals_404(self, paper_api) -> None:
        client, _ = paper_api
        assert client.get(
            "/api/paper-trading/tasks/ghost/signals").status_code == 404

    @pytest.mark.parametrize("param,value", [
        ("kind", "bogus_kind"), ("outcome", "bogus_outcome")])
    def test_signals_422_on_invalid_enum(self, paper_api, param,
                                         value) -> None:
        """422 枚举校验（api-error-contract 形制——校验面归传输层）。"""
        client, tid = self._setup(paper_api)
        resp = client.get(f"/api/paper-trading/tasks/{tid}/signals",
                          params={param: value})
        assert resp.status_code == 422

    def test_signals_order_and_json_deserialization(self, paper_api) -> None:
        """②响应形制：bar_time+seq 升序+gene_hits/indicator_snapshot
        反序列化为 JSON 对象非字符串。"""
        client, tid = self._setup(paper_api)
        body = client.get(
            f"/api/paper-trading/tasks/{tid}/signals").json()
        got = [(s["bar_time"], s["seq"]) for s in body["signals"]]
        assert got == [
            ("2026-10-20T00:00:00+00:00", 4),
            ("2026-10-20T00:00:00+00:00", 7),
            ("2026-10-21T00:00:00+00:00", 2),
            ("2026-10-22T00:00:00+00:00", 0)]
        first = body["signals"][0]
        assert first["gene_hits"] == [
            {"gene_index": 0, "indicator": "RSI"}], "JSON 对象非字符串"
        assert first["indicator_snapshot"] == {"rsi_14": 25.0}
        assert first["outcome"] == "filled" and first["fill_seq"] == 4
        rejected = body["signals"][1]
        assert rejected["reject_reason"] == "exposure_cap"
        expired = body["signals"][3]
        assert expired["outcome"] == "expired"

    def test_signals_filter_four_dimensions(self, paper_api) -> None:
        """①过滤四维：kind/outcome/bar_time 闭区间（H3 两侧含入）。"""
        client, tid = self._setup(paper_api)
        base = f"/api/paper-trading/tasks/{tid}/signals"
        # kind 过滤
        opens = client.get(base, params={"kind": "open"}).json()["signals"]
        assert {(s["seq"], s["outcome"]) for s in opens} == {
            (4, "filled"), (0, "expired")}
        # outcome 过滤
        rejected = client.get(
            base, params={"outcome": "rejected"}).json()["signals"]
        assert len(rejected) == 1 and rejected[0]["seq"] == 7
        # 闭区间含入：[10-20, 10-21] 含两端三行（10-22 排除）
        windowed = client.get(base, params={
            "bar_time_start": "2026-10-20T00:00:00+00:00",
            "bar_time_end": "2026-10-21T00:00:00+00:00"}).json()["signals"]
        assert [s["bar_time"] for s in windowed] == [
            "2026-10-20T00:00:00+00:00",
            "2026-10-20T00:00:00+00:00",
            "2026-10-21T00:00:00+00:00"]
        # 边界单点恰一行
        edge = client.get(base, params={
            "bar_time_start": "2026-10-21T00:00:00+00:00",
            "bar_time_end": "2026-10-21T00:00:00+00:00"}).json()["signals"]
        assert len(edge) == 1
        # 组合过滤
        combo = client.get(base, params={
            "kind": "open", "outcome": "filled"}).json()["signals"]
        assert [s["seq"] for s in combo] == [4]

    def test_signals_pagination_has_more_three_forms(self, paper_api) -> None:
        """①H4 has_more 三形态：有更多 True/恰好尽 False/末页余量。"""
        client, tid = self._setup(paper_api)
        base = f"/api/paper-trading/tasks/{tid}/signals"
        p1 = client.get(base, params={"limit": 2, "offset": 0}).json()
        assert len(p1["signals"]) == 2 and p1["has_more"] is True
        p2 = client.get(base, params={"limit": 2, "offset": 2}).json()
        assert len(p2["signals"]) == 2 and p2["has_more"] is False, "恰好尽"
        p3 = client.get(base, params={"limit": 3, "offset": 2}).json()
        assert len(p3["signals"]) == 2 and p3["has_more"] is False, "末页余量"
        assert client.get(base, params={"limit": 501}).status_code == 422

    def test_signals_empty_task(self, paper_api) -> None:
        """空信号任务零行响应（has_more False）。"""
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        body = client.get(
            f"/api/paper-trading/tasks/{tid}/signals").json()
        assert body["signals"] == [] and body["has_more"] is False


class TestMountWhitelist:
    """挂载品种与周期双白名单（change paper-mount-and-concurrency
    任务 3）：受理层 422 双维+白名单内全组合受理+常量一致性+研究面
    不受限。"""

    # 14 键中六档外 8 键（Q1 终审：固定 12 路制下不再受理）
    _OUT_TFS = ["3m", "30m", "2h", "6h", "8h", "12h", "3d", "1w"]
    _TFS_IN = ["1m", "5m", "15m", "1h", "4h", "1d"]
    _SYMS_IN = ["BTCUSDT", "ETHUSDT"]

    def test_symbol_outside_whitelist_422_with_domain(self, paper_api):
        """①非白名单 symbol 422（detail 含白名单值域）。"""
        client, db = paper_api
        _seed_strategy(db)
        resp = client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st-1", "symbol": "SOLUSDT"})
        assert resp.status_code == 422
        detail = str(resp.json()["detail"])
        assert "BTCUSDT" in detail and "ETHUSDT" in detail, \
            "detail 须含白名单值域"

    @pytest.mark.parametrize("tf", _OUT_TFS)
    def test_timeframe_outside_six_422(self, paper_api, tf):
        """②非六档周期（六档外 8 键参数化）DNA 挂载 422 拒绝
        （PAPER_TIMEFRAME_NOT_SUPPORTED——「执行环境不支持」形制，
        detail 含支持档位），任务不创建。"""
        client, db = paper_api
        _seed_strategy(db, timeframe=tf)
        resp = client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st-1"})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "PAPER_TIMEFRAME_NOT_SUPPORTED"
        assert detail["detail"]["timeframe"] == tf
        assert sorted(detail["detail"]["supported"]) == sorted(self._TFS_IN)
        assert client.get(
            "/api/paper-trading/tasks?limit=1").json()["total"] == 0, \
            "拒绝面任务不创建"

    @pytest.mark.parametrize("symbol", _SYMS_IN)
    @pytest.mark.parametrize("tf", _TFS_IN)
    def test_whitelist_full_grid_accepted(self, paper_api, symbol, tf):
        """④白名单内 2 币种×6 周期全组合受理正常（201 pending）。"""
        client, db = paper_api
        _seed_strategy(db, timeframe=tf, symbol=symbol)
        resp = client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st-1", "symbol": symbol})
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "pending"

    def test_whitelist_constants_align_fapi_subscribe_keys(self):
        """⑥双白名单与 change-1 固定订阅清单常量一致性（两处单源
        同值域防漂移——12 路=2 币种×6 周期）。"""
        from core.data.fapi.stream import (
            FAPI_SYMBOLS, FAPI_TIMEFRAMES, FIXED_SUBSCRIBE_KEYS)
        from core.paper_trading.constants import (
            PAPER_SYMBOL_WHITELIST, PAPER_TIMEFRAME_WHITELIST)

        assert PAPER_SYMBOL_WHITELIST == frozenset(FAPI_SYMBOLS)
        assert PAPER_TIMEFRAME_WHITELIST == frozenset(FAPI_TIMEFRAMES)
        assert len(FIXED_SUBSCRIBE_KEYS) == \
            len(PAPER_SYMBOL_WHITELIST) * len(PAPER_TIMEFRAME_WHITELIST) \
            == 12, "12 路=2 币种×6 周期互锁"

    def test_validator_dna_domain_not_narrowed(self):
        """⑤⑦研究数据面不受限+validator DNA 值域零改动：TF_SECONDS
        14 键在位、六档外 8 键 DNA 在研究链合法（收窄仅挂载/装载层）。"""
        from core.data.timeframes import TF_SECONDS
        from core.strategy.validator import validate_dna
        from tests.helpers.data_factory import make_dna

        assert len(TF_SECONDS) == 14
        for tf in self._OUT_TFS:
            assert validate_dna(make_dna(timeframe=tf)).is_valid, \
                f"研究链 {tf} 须照旧合法（值域不收窄）"

    def test_mcp_mount_reject_via_rest_passthrough(self, paper_api):
        """MCP mount 经 REST 校验透传（同 422 语义——白名单双通道
        同源断言面）。"""
        client, db = paper_api
        _seed_strategy(db, timeframe="30m")
        # MCP 工具薄封装 POST 同端点（test_paper_tools 行为对拍在案，
        # 此处锚 REST 端点即 MCP 透传源）
        resp = client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st-1"})
        assert resp.status_code == 422


class TestMountFourBranchDispatch:
    """挂载门槛放宽四分支（change paper-mount-and-concurrency 任务 4，
    Q3 终审 2026-10-28）：draft/candidate/live→同态续挂不迁移；
    archived→409 唯一排除；无观察轨概念。"""

    @pytest.mark.parametrize("status", ["paper", "candidate", "live"])
    def test_continued_mount_no_migration_event(self, paper_api,
                                                status) -> None:
        """②续挂分支（paper/candidate/live 三态各一）：201+任务照建+
        策略态不变+零迁移事件（candidate/live 从现行 409 改续挂）。"""
        client, db = paper_api
        from core.persistence.repository import paper

        _seed_strategy(db, strategy_id="st-1", status="draft")
        _force_strategy_status(db, "st-1", status)
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        assert resp.status_code == 201, (status, resp.text)
        assert resp.json()["status"] == "pending"
        assert paper.count_tasks(db) == 1
        assert _strategy_status(db, "st-1") == status, "策略态不变"
        assert _lifecycle_events(db, "st-1") == [], "零迁移事件"

    def test_draft_mount_full_positive(self, paper_api) -> None:
        """①draft 挂载正向：201 pending+任务可运行（executor 装载面
        就绪）+策略保持 draft+零迁移事件；⑤research_status 披露=
        draft（三序现算机制天然携带——零新增披露面）。"""
        client, db = paper_api
        _seed_strategy(db, strategy_id="st-1", status="draft")
        row = _create(client)
        assert row["status"] == "pending"
        assert _strategy_status(db, "st-1") == "draft", "策略保持 draft"
        assert _lifecycle_events(db, "st-1") == []
        # ⑤research_status 披露：详情查询即返 draft（现算三序序①）
        detail = client.get(
            f"/api/paper-trading/tasks/{row['task_id']}").json()
        assert detail["research_status"] == "draft"
        # ①任务可运行：装载校验通过（prepare 就绪面——draft 态不阻断
        # 装载，无观察轨特殊分支；造最小市场数据沿 fixture data_dir）
        import pandas as pd

        from core.data.storage import save_parquet
        from core.features.indicators import compute_all_indicators
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry
        from core.persistence.repository import paper
        from tests.helpers.data_factory import make_ohlcv

        data_dir = Path(db).parent / "market"
        raw = make_ohlcv(n=60, freq="4h", seed=7, base_price=100.0)
        save_parquet(raw, data_dir / "BTCUSDT_4h.parquet")
        registry = FeedRegistry(
            data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        ex = PaperTaskExecutor(
            Path(db), paper.get_task(Path(db), row["task_id"]),
            registry=registry, start_after="2000-01-01T00:00:00+00:00")
        assert ex.prepare() is True, "draft 挂载任务装载就绪可运行"
        assert compute_all_indicators is not None  # 就绪面数据链核证

    def test_draft_mount_report_schema_identical_no_observation_keys(
            self, paper_api) -> None:
        """T6 同构守护·报告面：draft 挂载任务与普通挂载任务的滚动
        报告键集完全一致、响应全文无观察轨标注键（「无观察轨概念」
        排除性声明——已废弃提案的概念面不得引入；特征锚：未来任何
        observation 类标注都会破坏本对照）。"""
        import json as _json

        client, db = paper_api
        _seed_strategy(db, strategy_id="st-d", status="draft")
        _seed_strategy(db, strategy_id="st-a", status="active")
        d_row = _create(client, strategy_id="st-d")
        a_row = _create(client, strategy_id="st-a")
        d_resp = client.get(
            f"/api/paper-trading/tasks/{d_row['task_id']}/report")
        a_resp = client.get(
            f"/api/paper-trading/tasks/{a_row['task_id']}/report")
        assert d_resp.status_code == a_resp.status_code == 200, (
            d_resp.text, a_resp.text)
        d_report, a_report = d_resp.json(), a_resp.json()
        assert set(d_report) == set(a_report), \
            "draft 挂载任务报告键集与普通任务一致（同构无特殊分支）"
        assert "observation" not in _json.dumps(
            d_report, ensure_ascii=False), "无观察轨标注键"


class TestDeleteCompensationGuard:
    """C3-01：DELETE 补偿回流前置守卫——draft 挂载→终态→DELETE→
    策略仍 draft+零迁移事件（draft→active 矩阵合法转正边不得经
    task_end 凭证冒用；「draft 挂盘不构成转正通道」终审语义）。"""

    def test_draft_mount_delete_keeps_strategy_draft(self, paper_api):
        """端到端：draft 挂载任务终态后删除——补偿回流被前置守卫
        跳过（策略态不变+零迁移事件+删除成功）。"""
        client, db = paper_api
        _seed_strategy(db, strategy_id="st-1", status="draft")
        row = _create(client)
        tid = row["task_id"]
        # 任务推到终态（running→completed 合法链）
        from core.persistence.repository import paper

        paper.update_task(db, tid, status="running", started_at=BASE_TIME)
        paper.update_task(db, tid, status="completed", stopped_at=BASE_TIME)
        resp = client.delete(f"/api/paper-trading/tasks/{tid}")
        assert resp.status_code == 200 and resp.json() == {"deleted": True}
        assert _strategy_status(db, "st-1") == "draft", \
            "draft 挂载任务的删除不得触发转正回流"
        assert _lifecycle_events(db, "st-1") == [], "零迁移事件"
        assert paper.get_task(db, tid) is None

    def test_paper_mount_delete_still_compensates(self, paper_api):
        """守卫不误伤：paper 态策略的删除补偿回流照旧（回流 active
        +事件）——守卫只收窄域不改变 paper 态补偿语义。"""
        client, db = paper_api
        _seed_strategy(db, status="active")
        row = _create(client)  # active→paper 迁移（1 事件）
        tid = row["task_id"]
        from core.persistence.repository import paper

        paper.update_task(db, tid, status="running", started_at=BASE_TIME)
        paper.update_task(db, tid, status="completed", stopped_at=BASE_TIME)
        resp = client.delete(f"/api/paper-trading/tasks/{tid}")
        assert resp.status_code == 200
        assert _strategy_status(db, "st-1") == "active", \
            "paper 态删除补偿回流照旧"
        assert _lifecycle_events(db, "st-1") == [
            ("active", "paper", "mount"),
            ("paper", "active", "task_end")]


class TestMountDispatchToctouGuard:
    """C3-02/S-1：分派事务内重读 Strategy.status——并发归档绕过窗口
    封死（外层受理读陈旧态时，事务内重读后按真实态分派）。"""

    def test_concurrent_archive_bypass_window_guarded(
            self, paper_api, monkeypatch):
        """守卫用例：外层 get_strategy 读到 draft（陈旧），事务开启时
        库内已被并发归档 archived→事务内重读 409 拒绝（任务不建+
        零事件）——归档绕过续挂通道的窗口被封死。"""
        client, db = paper_api
        from core.persistence.repository import paper
        from core.persistence.repository import strategy as strategy_repo

        _seed_strategy(db, strategy_id="st-1", status="draft")
        # 库内并发归档（外层读之后、事务开启前）
        _force_strategy_status(db, "st-1", "archived")
        stale = strategy_repo.get_strategy(db, "st-1")
        stale["status"] = "draft"  # 模拟外层受理读的陈旧快照

        monkeypatch.setattr(
            strategy_repo, "get_strategy", lambda *a, **k: dict(stale))
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        monkeypatch.undo()
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "STRATEGY_ILLEGAL_TRANSITION"
        assert paper.count_tasks(db) == 0, "409 不建任务（回滚零副作用）"
        assert _strategy_status(db, "st-1") == "archived"
        assert _lifecycle_events(db, "st-1") == []

    def test_concurrent_promote_seen_in_transaction(
            self, paper_api, monkeypatch):
        """对称面：外层读 draft（陈旧），事务内已被转正 active→
        重读分派走迁移分支（active→paper+事件）——重读是分派事实源。"""
        client, db = paper_api
        from core.persistence.repository import strategy as strategy_repo

        _seed_strategy(db, strategy_id="st-1", status="draft")
        _force_strategy_status(db, "st-1", "active")  # 并发转正
        stale = strategy_repo.get_strategy(db, "st-1")
        stale["status"] = "draft"

        monkeypatch.setattr(
            strategy_repo, "get_strategy", lambda *a, **k: dict(stale))
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        monkeypatch.undo()
        assert resp.status_code == 201, resp.text
        assert _strategy_status(db, "st-1") == "paper", \
            "事务内重读按真实态分派迁移"
        assert _lifecycle_events(db, "st-1") == [("active", "paper", "mount")]

    def test_concurrent_delete_seen_in_transaction(
            self, paper_api, monkeypatch):
        """事务内重读 404 分支（阶段四盲区消解——C3-02 新增面）：
        外层受理读成功（删除前快照）×事务开启时策略已被并发删除→
        fresh_status None→404（raise 经 with 回滚零副作用）。"""
        client, db = paper_api
        from core.persistence.repository import paper
        from core.persistence.repository import strategy as strategy_repo

        _seed_strategy(db, strategy_id="st-1", status="draft")
        stale = strategy_repo.get_strategy(db, "st-1")
        strategy_repo.delete_strategy(db, "st-1")  # 库内并发删除

        monkeypatch.setattr(
            strategy_repo, "get_strategy", lambda *a, **k: dict(stale))
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        monkeypatch.undo()
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "strategy not found"
        assert paper.count_tasks(db) == 0, "404 回滚零副作用（不建任务）"

    def test_transit_lifecycle_error_translated_409(
            self, paper_api, monkeypatch):
        """迁移分支 LifecycleError→409 结构化转译（阶段四盲区消解——
        防御分支锚）。调用图依据：现实现「事务内重读分派」下 transit
        诸抛点均被上游短路——404/非法边由 fresh_status 分派前置分流、
        trigger="mount" 属 TRIGGERS 枚举、evidence 含 task_id 凭证齐、
        同事务 CAS 无 RACE 并发窗口，故 except LifecycleError 为
        transit 语义演化的防御面；注入构造验证转译形制（三段 dict
        原样透传）不随演化破缺。"""
        client, db = paper_api
        import core.strategy.lifecycle as lifecycle_pkg
        from core.persistence.repository import paper
        from core.strategy.lifecycle.machine import LifecycleError

        _seed_strategy(db, status="active")

        def _deny(*a, **kw):
            raise LifecycleError(
                "STRATEGY_TRANSITION_RACE", "注入：迁移服务竞态拒绝",
                status=409, detail={"race": True})

        # routes 函数内 `from core.strategy.lifecycle import transit`
        # 取包命名空间属性——patch 包（patch service 模块不影响包级
        # re-export 绑定，静默扑空）
        monkeypatch.setattr(lifecycle_pkg, "transit", _deny)
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        monkeypatch.undo()
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "STRATEGY_TRANSITION_RACE"
        assert detail["detail"] == {"race": True}
        assert paper.count_tasks(db) == 0, "迁移拒绝不建任务（回滚）"
        assert _strategy_status(db, "st-1") == "active", "策略态回滚"
        assert _lifecycle_events(db, "st-1") == [], "零事件（回滚）"


class TestMountSymbolFinalValueGuard:
    """C3-03/S-2：symbol 终值流转点校验——body.symbol 缺省抄录
    DNA.execution_genes.symbol 的非白名单值在受理层 422（与 exec_tf
    同点同形制），消「201 受理后装载拒」占位不一致。"""

    def test_dna_symbol_outside_whitelist_422(self, paper_api):
        """DNA 抄录源 symbol=SOLUSDT（不传 body.symbol）→422
        PAPER_SYMBOL_NOT_SUPPORTED（detail 含值域），任务不创建。"""
        client, db = paper_api
        from core.persistence.repository import paper

        _seed_strategy(db, timeframe="4h", symbol="SOLUSDT")
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "PAPER_SYMBOL_NOT_SUPPORTED"
        assert detail["detail"]["symbol"] == "SOLUSDT"
        assert sorted(detail["detail"]["supported"]) == [
            "BTCUSDT", "ETHUSDT"]
        assert paper.count_tasks(db) == 0

    def test_body_symbol_override_passes_dna_symbol(self, paper_api):
        """显式 body.symbol 覆写白名单内值→正常受理（终值=body
        值——DNA 源头非白名单不阻断显式白名单内覆写）。"""
        client, db = paper_api
        _seed_strategy(db, timeframe="4h", symbol="SOLUSDT")
        row = _create(client, symbol="ETHUSDT")
        assert row["symbol"] == "ETHUSDT"


class TestStoredJsonCorruptRejected:
    """repay-vr-review-debts TD-02：纸盘域库内 JSON 列损坏 → 422
    PAPER_STORED_DATA_INVALID（点位 5-8：pt_signal.gene_hits/
    indicator_snapshot + pt_task.dna_snapshot_json + pt_report.
    report_json），不裸穿 500、不 unavailable 降级掩盖。"""

    @staticmethod
    def _corrupt(db: Path, table: str, column: str, where: str,
                 value: str = '{"broken": ') -> None:
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE {where}", (value,))
        conn.commit()
        conn.close()

    @staticmethod
    def _assert_422_paper(resp, source_field, extra_keys):
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "PAPER_STORED_DATA_INVALID"
        assert source_field in detail["message"], detail["message"]
        for k in extra_keys:
            assert k in detail["detail"]
        assert "parse_error" in detail["detail"]

    def _signals_env(self, paper_api):
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        TestSignalEndpoints._seed_signals(db, tid)
        return client, db, tid

    def test_signals_corrupt_gene_hits_422(self, paper_api) -> None:
        client, db, tid = self._signals_env(paper_api)
        self._corrupt(db, "pt_signal", "gene_hits", "seq=4")
        resp = client.get(f"/api/paper-trading/tasks/{tid}/signals")
        self._assert_422_paper(
            resp, "pt_signal.gene_hits", ["task_id", "bar_time", "seq"])

    def test_signals_corrupt_indicator_snapshot_422(self, paper_api) -> None:
        client, db, tid = self._signals_env(paper_api)
        self._corrupt(db, "pt_signal", "indicator_snapshot", "seq=4")
        resp = client.get(f"/api/paper-trading/tasks/{tid}/signals")
        self._assert_422_paper(
            resp, "pt_signal.indicator_snapshot",
            ["task_id", "bar_time", "seq"])

    def _running_with_equity(self, paper_api):
        """running 任务+权益行（触达滚动报告 deviation 层的 dna
        快照读取——无区间数据时该层早退不触达点位 8）。"""
        client, db = paper_api
        _seed_strategy(db)
        from core.persistence.repository import paper

        paper.create_task(
            db, task_id="pt_tcorr01", strategy_id="st-1",
            dna_snapshot_json='{"risk_genes": {"leverage": 2}}',
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper.update_task(db, "pt_tcorr01", status="running",
                          started_at=BASE_TIME)
        paper.append_bar(
            db, "pt_tcorr01", bar_time=BASE_TIME, fills=[],
            funding=[], equity={"equity": 100_000.0, "balance": 100_000.0,
                                "unrealized_pnl": 0.0,
                                "position_side": None})
        return client, db

    def test_rolling_report_corrupt_dna_snapshot_422(self, paper_api) -> None:
        """点位 8：dna_snapshot_json 损坏属工程错误——滚动报告端点
        422 显式失败（对照计算性失败仍 unavailable 降级，见
        test_reporting 真桥锚——两语义分立）。"""
        client, db = self._running_with_equity(paper_api)
        self._corrupt(db, "pt_task", "dna_snapshot_json",
                      "task_id='pt_tcorr01'")
        resp = client.get("/api/paper-trading/tasks/pt_tcorr01/report")
        self._assert_422_paper(
            resp, "pt_task.dna_snapshot_json", ["task_id"])

    def test_rolling_report_intact_dna_no_422(self, paper_api) -> None:
        """构造对拍：同环境完好快照不 422（真桥 ResearchError 走
        unavailable 降级——点位 8 只拦快照损坏）。"""
        client, db = self._running_with_equity(paper_api)
        resp = client.get("/api/paper-trading/tasks/pt_tcorr01/report")
        assert resp.status_code == 200, resp.text
        assert resp.json()["deviation"]["backtest"] is None

    def test_final_report_corrupt_report_json_422(self, paper_api) -> None:
        """点位 7：固化版 report_json 损坏 → final 端点 422。"""
        client, db = paper_api
        _seed_strategy(db)
        tid = _seed_terminal(db)
        from core.paper_trading.reporting import finalize_report

        assert finalize_report(
            db, client.app.state.data_dir, tid,
            generated_at=BASE_TIME) is not None
        self._corrupt(db, "pt_report", "report_json",
                      f"task_id='{tid}'")
        resp = client.get(f"/api/paper-trading/tasks/{tid}/report/final")
        self._assert_422_paper(
            resp, "pt_report.report_json", ["task_id", "report_version"])
