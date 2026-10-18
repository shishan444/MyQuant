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
        assert row["research_status_snapshot"] == "unverified"
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
        # fixture 调度器 max_running 缺省 10；造 10 个 running 行占满
        from core.persistence.repository import paper

        for i in range(10):
            tid = f"pt_run{i:09d}"
            paper.create_task(
                db, task_id=tid, strategy_id="st-1",
                dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified")
            paper.update_task(db, tid, status="running")
        row = _create(client)
        assert row["status"] == "pending"  # 第 11 个恒 201 排队

    @pytest.mark.parametrize("status", [
        "draft", "archived", "candidate", "live"])
    def test_create_non_precondition_status_409(self, paper_api, status) -> None:
        """B' 前置集收紧（build-strategy-lifecycle 5.1，spec delta
        MODIFIED「上盘流转」：任意可上盘→{active,paper} 可上盘）：
        四态→409 STRATEGY_ILLEGAL_TRANSITION（detail 含合法后继集），
        任务不建+策略不变+零事件（失败回滚两侧）。candidate/live 的
        正当通道=迭代克隆→重验证→active。"""
        client, db = paper_api
        from core.persistence.repository import paper

        _seed_strategy(db, strategy_id="st-1", status="draft")
        _force_strategy_status(db, "st-1", status)
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        assert resp.status_code == 409, (status, resp.text)
        detail = resp.json()["detail"]
        assert detail["code"] == "STRATEGY_ILLEGAL_TRANSITION"
        assert detail["detail"]["to_status"] == "paper"
        assert paper.count_tasks(db) == 0, "409 不建任务"
        assert _strategy_status(db, "st-1") == status, "策略不变（回滚）"
        assert _lifecycle_events(db, "st-1") == [], "零事件（回滚）"


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
        # research_status_snapshot 按 clone 时点重快照（qualified=1→verified）
        assert new["research_status_snapshot"] == "verified"

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

    def test_paper_segment_precedes_runner_start(self) -> None:
        import inspect

        from api import app as app_mod

        lines = inspect.getsource(app_mod).splitlines()

        def _code_line(token: str) -> int:
            """token 首个非注释出现行（摆位纪律注释自身含
            "runner.start()" 字样——排除防误中）。"""
            return next(i for i, ln in enumerate(lines)
                        if token in ln
                        and not ln.strip().startswith("#"))

        paper_line = _code_line("startup_recover()")
        start_line = _code_line("runner.start()")
        assert paper_line < start_line, (
            f"纸盘段（startup_recover 行 {paper_line + 1}）须先于 "
            f"runner.start()（行 {start_line + 1}）——摆位纪律见 "
            f"app.py 纸盘段注释")


class TestSchedulerUnavailableDegradation:
    """调度器未挂载的降级面（闸门盲区消解）。"""

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
