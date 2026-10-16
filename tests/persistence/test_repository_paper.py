"""rebuild-paper-trading 2.2：纸盘五表新仓库行为锚。

覆盖任务完成标准三面：幂等重放收敛（随机键集重放收敛首写集）、
级联删五表行数断言、OR IGNORE 语义断言；另锚单 bar 原子落库事务性、
clone 重新快照、研究状态四值映射、报告版本机制（v2 重算覆盖）。
库建形制沿 tests/persistence/test_orm_infra.py（init_db_ext=alembic
链建库——仓库写经 ORM、建库经迁移，两轨对拍天然发生）。
"""

import random
from pathlib import Path

import pytest
import sqlalchemy.exc

pytestmark = [pytest.mark.unit]


def _make_db(tmp_path: Path) -> Path:
    from core.persistence.db_ext import init_db_ext
    db = tmp_path / "paper.db"
    init_db_ext(db)
    return db


def _create_sample_task(db: Path, task_id: str = "pt_abc123000001",
                        strategy_id: str = "strat-1") -> str:
    from core.persistence.repository import paper
    return paper.create_task(
        db, task_id=task_id, strategy_id=strategy_id,
        dna_snapshot_json='{"risk_genes": {"leverage": 5}}',
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified",
        initial_cash=50_000.0, fee=0.001, slippage=0.0005,
        leverage=5, window_bars=180,
    )


def _fill(seq: int, kind: str = "open", **over):
    row = {
        "seq": seq, "kind": kind, "side": "long", "price": 100.0,
        "quantity": 1.5, "fee": 0.15, "realized_pnl": None,
        "reason": "signal",
    }
    row.update(over)
    return row


def _equity(equity: float = 50_000.0, **over):
    row = {
        "equity": equity, "balance": 49_900.0, "unrealized_pnl": 100.0,
        "position_side": "long", "cumulative_funding": -0.3,
    }
    row.update(over)
    return row


def _funding(settle_time: str, status: str = "settled", **over):
    row = {
        "settle_time": settle_time, "status": status,
        "rate": 0.0001, "amount": -0.03, "position_quantity": 1.5,
        "settle_price": 200.0,
    }
    row.update(over)
    return row


def _bar_state(bars_processed: int, **over):
    row = {
        "bars_processed": bars_processed, "last_bar_time": None,
        "balance": 49_900.0, "position_side": "long",
        "position_entry": 100.0, "position_quantity": 1.5,
        "position_margin": 30.0, "position_open_cost": 150.0,
        "position_sl_price": 95.0, "position_tp_price": 110.0,
        "position_trail_peak": 105.0, "position_entry_bar_seq": 1,
        "unrealized_pnl": 100.0, "pending_intent_json": None,
        "total_trades": 1, "win_count": 0, "loss_count": 0,
        "total_pnl": 0.0, "heartbeat_at": "2026-10-16T00:00:00+00:00",
    }
    row.update(over)
    return row


class TestTaskCrud:
    """任务 CRUD：直建通道全参直收（REST 层组 5 负责读 strategy 组装）。"""

    def test_create_get_roundtrip(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        assert tid == "pt_abc123000001"
        row = paper.get_task(db, tid)
        assert row is not None
        assert row["status"] == "pending"
        assert row["strategy_id"] == "strat-1"
        assert row["symbol"] == "BTCUSDT"
        assert row["timeframe"] == "4h"
        assert row["initial_cash"] == 50_000.0
        assert row["leverage"] == 5
        assert row["window_bars"] == 180
        # 创建即零进度+空账户+零统计
        assert row["bars_processed"] == 0
        assert row["balance"] is None
        assert row["position_side"] is None
        assert row["last_bar_time"] is None
        assert row["total_trades"] == 0
        assert row["research_status_snapshot"] == "unverified"
        assert row["created_at"] and row["updated_at"]
        assert paper.get_task(db, "nope") is None

    def test_create_generates_task_id(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db, task_id=None)
        assert len(tid) == 12 and tid != "pt_abc123000001"

    def test_update_whitelist_and_cursor_isolation(self, tmp_path: Path):
        """状态机字段可外写；游标/账户/统计组不在白名单（写者隔离——
        仅经 append_bar 原子更新）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running", started_at="2026-10-16T01:00:00+00:00")
        row = paper.get_task(db, tid)
        assert row["status"] == "running"
        assert row["started_at"] == "2026-10-16T01:00:00+00:00"

        paper.update_task(db, tid, bars_processed=99, balance=1.0,
                          total_trades=7, last_bar_time="hack")
        row = paper.get_task(db, tid)
        assert row["bars_processed"] == 0, "游标不得外写"
        assert row["balance"] is None, "账户态不得外写"
        assert row["total_trades"] == 0, "统计组不得外写"
        assert row["last_bar_time"] is None

        paper.update_task(db, tid)  # 空集 no-op
        paper.update_task(db, tid, evil_column="x")  # 全白名单外 no-op

    def test_list_filter_order_pagination(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        for i in range(3):
            _create_sample_task(db, task_id=f"task{i:012d}")
        paper.update_task(db, "task000000000001", status="running")

        assert paper.count_tasks(db) == 3
        assert paper.count_tasks(db, status="running") == 1
        assert paper.count_tasks(db, status="pending") == 2

        # desc=REST 倒序缺省（created_at 同秒微秒可分辨）
        desc_ids = [r["task_id"] for r in paper.list_tasks(db)]
        assert desc_ids == sorted(desc_ids, reverse=True)
        # asc=调度器拾取最早优先
        asc_ids = [r["task_id"] for r in paper.list_tasks(db, order="asc")]
        assert asc_ids == ["task000000000000", "task000000000001", "task000000000002"]
        assert [r["task_id"] for r in paper.list_tasks(db, status="running")] == \
            ["task000000000001"]
        assert len(paper.list_tasks(db, limit=2)) == 2
        assert len(paper.list_tasks(db, limit=2, offset=2)) == 1


class TestAppendBar:
    """单 bar 原子落库：一个事务四类写+三明细 OR IGNORE 幂等。"""

    def test_single_bar_atomic_write(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running")  # CARD-06：task_state 更新需 running
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1), _fill(2, kind="exit_sl", realized_pnl=-12.5,
                          reason="stop_loss")],
            equity=_equity(),
            funding=[_funding("2026-10-16T00:00:00+00:00")],
            task_state=_bar_state(1, last_bar_time="2026-10-16T00:00:00+00:00"),
        )
        fills = paper.list_fills(db, tid)
        assert len(fills) == 2
        assert [f["seq"] for f in fills] == [1, 2]
        assert fills[1]["kind"] == "exit_sl"
        assert fills[1]["realized_pnl"] == -12.5
        eq = paper.list_equity_snapshots(db, tid)
        assert len(eq) == 1 and eq[0]["cumulative_funding"] == -0.3
        fd = paper.list_funding_records(db, tid)
        assert len(fd) == 1 and fd[0]["status"] == "settled"
        row = paper.get_task(db, tid)
        assert row["bars_processed"] == 1
        assert row["last_bar_time"] == "2026-10-16T00:00:00+00:00"
        assert row["balance"] == 49_900.0
        assert row["position_quantity"] == 1.5

    def test_replay_converges_first_write_set(self, tmp_path: Path):
        """完成标准：随机键集重放收敛首写集（固定种子可复现）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running")  # CARD-06：task_state 更新需 running
        rng = random.Random(20261016)

        bars = []
        for i in range(6):
            bar_time = f"2026-10-16T0{i}:00:00+00:00"
            n_fills = rng.randint(1, 3)
            fills = [_fill(seq, price=100.0 + seq) for seq in range(1, n_fills + 1)]
            funding = ([_funding(bar_time)] if i % 3 == 0 else [])
            bars.append((bar_time, fills, funding))

        def _snapshot():
            fills = {(f["bar_time"], f["seq"]): (f["kind"], f["price"])
                     for f in paper.list_fills(db, tid, limit=1000)}
            equity = {(e["bar_time"], e["equity"])
                      for e in paper.list_equity_snapshots(db, tid, limit=1000)}
            funding_ = {(f["settle_time"], f["status"], f["amount"])
                        for f in paper.list_funding_records(db, tid, limit=1000)}
            return fills, equity, funding_

        for i, (bar_time, fills, funding) in enumerate(bars, start=1):
            paper.append_bar(
                db, tid, bar_time=bar_time, fills=fills,
                equity=_equity(50_000.0 + i), funding=funding,
                task_state=_bar_state(i, last_bar_time=bar_time),
            )
        first = _snapshot()

        # 按序全量重放（恢复场景：从首 bar 重跑，绝对值快照收敛）
        for i, (bar_time, fills, funding) in enumerate(bars, start=1):
            paper.append_bar(
                db, tid, bar_time=bar_time, fills=fills,
                equity=_equity(50_000.0 + i), funding=funding,
                task_state=_bar_state(i, last_bar_time=bar_time),
            )
        assert _snapshot() == first

        # 随机乱序重放明细子集（不带 task_state 的纯明细重放）
        replay = bars[:]
        rng.shuffle(replay)
        for bar_time, fills, funding in replay[:4]:
            paper.append_bar(
                db, tid, bar_time=bar_time, fills=fills,
                equity=_equity(123.0), funding=funding,
            )
        assert _snapshot() == first, "明细表首写为准，重放零漂移"
        row = paper.get_task(db, tid)
        assert row["bars_processed"] == 6, "游标不受无 task_state 重放影响"

    def test_or_ignore_first_write_wins(self, tmp_path: Path):
        """完成标准：OR IGNORE 语义——同键重放保留首写（值不更新）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1, price=100.0)],
            equity=_equity(50_000.0),
            funding=[_funding("2026-10-16T00:00:00+00:00", amount=-0.03)],
        )
        # 同键不同值重放：三表全部保留首写
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1, price=999.0)],
            equity=_equity(1.0),
            funding=[_funding("2026-10-16T00:00:00+00:00", amount=999.0)],
        )
        assert paper.list_fills(db, tid)[0]["price"] == 100.0
        assert paper.list_equity_snapshots(db, tid)[0]["equity"] == 50_000.0
        assert paper.list_funding_records(db, tid)[0]["amount"] == -0.03
        # 同 bar 不同 seq 是新键（正常落）
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(2, kind="add")], equity=_equity(50_000.0),
        )
        assert len(paper.list_fills(db, tid)) == 2

    def test_atomic_rollback_on_bad_row(self, tmp_path: Path):
        """事务原子性：任一明细写失败（参数类型违约）整体回滚——
        fills/equity 均不落、task 游标不推进。（NOT NULL 违反不适用
        此构造——SQLite OR IGNORE 会吞掉该类违约，见下一用例行为锚）"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        with pytest.raises(sqlalchemy.exc.SQLAlchemyError):
            paper.append_bar(
                db, tid, bar_time="2026-10-16T00:00:00+00:00",
                fills=[_fill(1, price={"bad": "type"})],  # 绑定类型违约
                equity=_equity(),
                funding=[_funding("2026-10-16T00:00:00+00:00")],
                task_state=_bar_state(1, last_bar_time="2026-10-16T00:00:00+00:00"),
            )
        assert paper.list_fills(db, tid) == []
        assert paper.list_equity_snapshots(db, tid) == []
        assert paper.list_funding_records(db, tid) == []
        row = paper.get_task(db, tid)
        assert row["bars_processed"] == 0
        assert row["last_bar_time"] is None

    def test_task_state_frozen_after_terminal(self, tmp_path: Path):
        """CARD-06 锚：终态任务上 append_bar（僵尸执行器迟到写形制）——
        明细行照落（append-only 披露面）+task_state 组零变化（游标/
        账户/统计/updated_at 全冻结——终态封账语义延伸）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running", started_at="t0")
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1)], equity=_equity(50_000.0),
            task_state=_bar_state(1, last_bar_time="2026-10-16T00:00:00+00:00",
                                  total_trades=1),
        )
        paper.update_task(db, tid, status="stopped", stop_reason="user_stop")
        frozen = paper.get_task(db, tid)

        # 僵尸迟到写：同 bar 重放（明细唯一键吸收）+次 bar 新写+task_state 毒写
        paper.append_bar(
            db, tid, bar_time="2026-10-16T04:00:00+00:00",
            fills=[_fill(1, kind="open", price=999.0)],  # 与首 bar 同 seq 不同 bar=新键照落
            equity=_equity(1.0),
            funding=[_funding("2026-10-16T00:00:00+00:00")],
            task_state=_bar_state(99, last_bar_time="2026-10-16T04:00:00+00:00",
                                  balance=0.5, total_trades=99),
        )
        after = paper.get_task(db, tid)
        assert after == frozen, "终态后 task_state 组零覆写（含 updated_at）"
        assert after["bars_processed"] == 1 and after["total_trades"] == 1
        # 明细照落（披露面 append-only）：新 fill 新 equity 落库，
        # funding 与首 bar 无重叠键照落
        assert len(paper.list_fills(db, tid)) == 2
        assert len(paper.list_equity_snapshots(db, tid)) == 2
        assert len(paper.list_funding_records(db, tid)) == 1

    def test_or_ignore_swallows_not_null_violation(self, tmp_path: Path):
        """行为锚（诚实披露非断言期望）：SQLite OR IGNORE 连 NOT NULL
        违约一并静默跳过（引擎语义，旧轨 save_paper_trade 同形制）——
        坏行不落库、不抛错、同事务其余行照常落。锚定此现实防误设
        「该炸」预期。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1, kind=None)],  # kind NOT NULL——OR IGNORE 吞
            equity=_equity(),
            funding=[_funding("2026-10-16T00:00:00+00:00",
                              status="pending", rate=None, amount=None,
                              position_quantity=None)],
        )
        assert paper.list_fills(db, tid) == [], "违约行静默跳过"
        assert paper.list_funding_records(db, tid) == [], "违约行静默跳过"
        assert len(paper.list_equity_snapshots(db, tid)) == 1, "合法行照常落"


class TestDeleteCascade:
    """终态级联删五表（单事务；不存在返回 False）。"""

    def test_delete_cascades_five_tables(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1)], equity=_equity(),
            funding=[_funding("2026-10-16T00:00:00+00:00")],
            task_state=_bar_state(1),
        )
        paper.save_report(
            db, tid, report_version=1, generated_at="t1", as_of="t1",
            report_json='{"v": 1}',
        )
        assert paper.list_fills(db, tid)
        assert paper.delete_task(db, tid) is True
        assert paper.list_fills(db, tid) == []
        assert paper.list_equity_snapshots(db, tid) == []
        assert paper.list_funding_records(db, tid) == []
        assert paper.get_report(db, tid) is None
        assert paper.get_task(db, tid) is None
        assert paper.delete_task(db, tid) is False, "删除不存在无错误返 False"


class TestClone:
    """clone 新建：参数同源复制+进度归零+research_status 重新快照。"""

    @staticmethod
    def _set_qualified(db: Path, strategy_id: str, qualified):
        from sqlalchemy import update
        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import Strategy

        with engine_for(db).begin() as conn:
            conn.execute(
                update(Strategy)
                .where(Strategy.strategy_id == strategy_id)
                .values(qualified=qualified)
            )

    def _seed_strategy(self, db: Path):
        from core.persistence.repository import strategy as strategy_repo
        strategy_repo.save_strategy(
            db, strategy_id="strat-1", dna_json="{}", symbol="BTCUSDT",
            timeframe="4h",
        )

    def test_clone_resnapshots_research_status(self, tmp_path: Path):
        """创建后策略验证状态变化→clone 按 clone 时点重算快照
        （「创建时快照」语义一致性——显式假设评审可推翻的锚）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        self._seed_strategy(db)
        tid = _create_sample_task(db)  # 创建时 qualified=NULL → unverified
        assert paper.get_task(db, tid)["research_status_snapshot"] == "unverified"

        self._set_qualified(db, "strat-1", 1)  # clone 前策略转 verified
        cloned = paper.clone_task(db, tid, new_task_id="pt_clone0000001")
        assert cloned is not None
        assert cloned["research_status_snapshot"] == "verified", \
            "clone 须按 clone 创建时点重新快照（非沿用原任务快照）"
        assert paper.get_task(db, tid)["research_status_snapshot"] == "unverified", \
            "原任务快照不被 clone 改写"

    def test_clone_resets_progress_and_copies_params(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        self._seed_strategy(db)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running")
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[_fill(1)], equity=_equity(),
            task_state=_bar_state(1, last_bar_time="2026-10-16T00:00:00+00:00",
                                  total_trades=3, win_count=2),
        )
        paper.update_task(db, tid, status="completed")

        cloned = paper.clone_task(db, tid)
        assert cloned["status"] == "pending"
        assert cloned["task_id"] != tid
        # 同源复制面
        for col in ("strategy_id", "dna_snapshot_json", "symbol", "timeframe",
                    "initial_cash", "fee", "slippage", "leverage",
                    "window_bars", "stop_loss_pct"):
            assert cloned[col] == paper.get_task(db, tid)[col], col
        # 归零面
        assert cloned["bars_processed"] == 0
        assert cloned["last_bar_time"] is None
        assert cloned["balance"] is None
        assert cloned["position_side"] is None
        assert cloned["total_trades"] == 0
        assert cloned["pending_intent_json"] is None
        assert cloned["started_at"] is None
        # 数据不串任务
        assert paper.list_fills(db, cloned["task_id"]) == []
        assert len(paper.list_fills(db, tid)) == 1

    def test_clone_missing_task_and_strategy(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        assert paper.clone_task(db, "nope") is None

        self._seed_strategy(db)
        tid = _create_sample_task(db)
        from core.persistence.repository import strategy as strategy_repo
        strategy_repo.delete_strategy(db, "strat-1")
        with pytest.raises(ValueError):
            paper.clone_task(db, tid)


class TestResearchStatusMapping:
    """§9.2 四值映射（resolve_research_status 单源）。"""

    @pytest.mark.parametrize("row,expected", [
        ({"status": "draft", "qualified": None}, "draft"),
        ({"status": "draft", "qualified": 1}, "draft"),  # draft 优先
        ({"status": "active", "qualified": None}, "unverified"),
        ({"status": "active", "qualified": 1}, "verified"),
        ({"status": "active", "qualified": 0}, "verification_failed"),
        ({}, "unverified"),  # 字段缺失容缺
    ])
    def test_four_values(self, row, expected):
        from core.persistence.repository.paper import resolve_research_status
        assert resolve_research_status(row) == expected


class TestReportVersions:
    """pt_report 版本机制：v1 结题固化+v2 重算覆盖（Q-2 恒 version=2）。"""

    def test_save_get_latest_and_version(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        assert paper.get_report(db, tid) is None
        paper.save_report(db, tid, report_version=1, generated_at="t1",
                          as_of="t1", report_json='{"v": 1}')
        paper.save_report(db, tid, report_version=2, generated_at="t2",
                          as_of="t2", report_json='{"v": 2}')
        latest = paper.get_report(db, tid)
        assert latest["report_version"] == 2 and latest["as_of"] == "t2"
        v1 = paper.get_report(db, tid, version=1)
        assert v1["report_json"] == '{"v": 1}', "?version=1 可取初版"
        assert paper.get_report(db, tid, version=3) is None

    def test_v2_recalc_overwrites(self, tmp_path: Path):
        """终态后补记修订版恒 version=2 重算覆盖（同 PK 覆写非追加）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        for as_of, jsn in (("t2", '{"v": 2}'), ("t3", '{"v": 2, "n": 5}')):
            paper.save_report(db, tid, report_version=2, generated_at=as_of,
                              as_of=as_of, report_json=jsn)
        latest = paper.get_report(db, tid)
        assert latest["as_of"] == "t3"
        assert latest["report_json"] == '{"v": 2, "n": 5}'


class TestCasUpdate:
    """update_task CAS 前态门（缺陷⑤ stop 吞没竞态库层前提）：
    expected_status 是 WHERE 条件非新写面，与六字段白名单并存。"""

    def test_cas_hit(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)  # pending
        n = paper.update_task(
            db, tid, expected_status="pending",
            status="running", started_at="2026-10-16T01:00:00+00:00",
        )
        assert n == 1
        row = paper.get_task(db, tid)
        assert row["status"] == "running"
        assert row["started_at"] == "2026-10-16T01:00:00+00:00"

    def test_cas_miss_rejected_atomically(self, tmp_path: Path):
        """前态已变→零行生效：status 与同批其余字段（stop_reason）均
        不写——单语句原子门，无 check-then-act 缝隙。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running")  # 前态已迁

        n = paper.update_task(
            db, tid, expected_status="pending",
            status="stopped", stop_reason="user_stop",
        )
        assert n == 0
        row = paper.get_task(db, tid)
        assert row["status"] == "running", "前态不匹配整批拒写"
        assert row["stop_reason"] is None

    def test_cas_set_form_matches_any(self, tmp_path: Path):
        """集合形：前态属集合即命中（executor 续跑写 running 用
        {"pending","running"} 含自身覆盖恢复场景）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        paper.update_task(db, tid, status="running")
        # running ∈ {pending, running} → 命中（自身覆盖）
        assert paper.update_task(
            db, tid, expected_status={"pending", "running"},
            heartbeat_at="2026-10-16T02:00:00+00:00",
        ) == 1
        # 终态不属集合 → 拒
        paper.update_task(db, tid, status="stopped")
        assert paper.update_task(
            db, tid, expected_status={"pending", "running"},
            status="running",
        ) == 0
        assert paper.get_task(db, tid)["status"] == "stopped"

    def test_default_gate_semantics(self, tmp_path: Path):
        """None=默认守门（修订版——无条件直调也过不了门）：合法迁移
        放行/非法迁移（跳态）零行拒/同态幂等覆写放行三面。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)  # pending

        # 守门命中：合法链 pending→running
        assert paper.update_task(db, tid, status="running",
                                 started_at="t1") == 1
        # 非法迁移拒：跳态 running→pending（红锚 t3 同语义——无条件
        # 直调被默认门拦）
        assert paper.update_task(db, tid, status="pending") == 0
        row = paper.get_task(db, tid)
        assert row["status"] == "running", "非法迁移整批拒写"
        assert row["stop_reason"] is None
        # 同态幂等覆写放行（恢复续跑场景）：running→running 命中
        assert paper.update_task(
            db, tid, status="running", heartbeat_at="t2") == 1
        assert paper.get_task(db, tid)["heartbeat_at"] == "t2"

    def test_default_gate_rejects_skip_and_terminal_exit(self, tmp_path: Path):
        """默认门两拒：跳态（pending→completed）与终态出发（终态吸收
        ——stopped→running 仅同态放行集不含）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        assert paper.update_task(db, tid, status="completed") == 0, \
            "跳态 pending→completed 拒"
        assert paper.get_task(db, tid)["status"] == "pending"

        paper.update_task(db, tid, status="running")
        paper.update_task(db, tid, status="stopped", stop_reason="user_stop")
        assert paper.update_task(db, tid, status="running") == 0, \
            "终态吸收：stopped→running 拒"
        assert paper.get_task(db, tid)["status"] == "stopped"

    def test_non_status_write_ungated(self, tmp_path: Path):
        """非 status 写不受守门影响：任意态心跳/错误字段无条件生效
        （expected_status 缺省时）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        assert paper.update_task(db, tid, heartbeat_at="t3",
                                 error_detail="no_data") == 1
        row = paper.get_task(db, tid)
        assert row["heartbeat_at"] == "t3" and row["error_detail"] == "no_data"
        assert row["status"] == "pending", "非 status 写不动状态"

    def test_custom_tightening_replaces_default(self, tmp_path: Path):
        """自定义收紧替换默认门：stopped 默认门含 pending（未拾取撤销
        通道），显式 expected='running' 时 pending 态被拒——stop 分流
        依据（零命中重读分流 running 分支）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)  # pending
        # 默认门：pending→stopped 放行（§7.1 端点 4 未拾取撤销）
        assert paper.update_task(db, tid, status="stopped",
                                 stop_reason="user_stop") == 1
        # 显式收紧换任务验证：pending 态不在显式集 {running} 被拒
        tid2 = _create_sample_task(db, task_id="pt_def456000002")
        assert paper.update_task(
            db, tid2, expected_status="running", status="stopped") == 0, \
            "显式收紧：pending 态不在 {running} 被拒"
        assert paper.get_task(db, tid2)["status"] == "pending"

    def test_no_expected_status_keeps_unconditional(self, tmp_path: Path):
        """None=现状无条件兼容：不传 expected_status 照常生效；返回值
        int 形（旧调用面不接收零破坏）；白名单过滤在 CAS 路径同样适用。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        assert paper.update_task(db, tid) == 0                     # 空 kwargs no-op
        # CAS 路径白名单外字段仍拒（游标不外写）
        paper.update_task(db, tid, expected_status="pending",
                          bars_processed=99, balance=1.0)
        row = paper.get_task(db, tid)
        assert row["bars_processed"] == 0 and row["balance"] is None


class TestStatusTransitionsSource:
    """前态单源派生视图锚：PT_STATUS_TRANSITIONS 与组 3 scheduler
    LEGAL_TRANSITIONS 逐对一致（硬编码锚——不 import 施工中文件，
    漂移时此锚红）。"""

    def test_transitions_view_content(self):
        from core.persistence.repository.paper import (
            PT_STATUS_PREDECESSORS, PT_STATUS_TRANSITIONS,
        )
        assert PT_STATUS_PREDECESSORS == {
            "pending": frozenset(),
            "running": frozenset({"pending"}),
            "completed": frozenset({"running"}),
            "stopped": frozenset({"pending", "running"}),
            "error": frozenset({"pending", "running"}),
        }
        assert PT_STATUS_TRANSITIONS == frozenset({
            ("pending", "running"),
            ("pending", "stopped"),   # 未拾取撤销（§7.1 端点 4）
            ("pending", "error"),     # 装载校验拒绝（tasks 3.2 不进 running）
            ("running", "completed"),
            ("running", "stopped"),
            ("running", "error"),
        })


class TestFundingBackfillInBar:
    """Q-2 运行中补旧账（append_bar.funding_backfills——tasks 3.5
    「先补旧账再记新账」，与该 bar 新结算同事务）。"""

    _PENDING = ("2026-10-15T16:00:00+00:00",)

    def _seed_pending(self, db: Path, tid: str):
        from core.persistence.repository import paper
        paper.append_bar(
            db, tid, bar_time="2026-10-15T16:00:00+00:00",
            fills=[_fill(1)], equity=_equity(),
            funding=[_funding("2026-10-15T16:00:00+00:00", status="pending",
                              rate=None, amount=None)],
            task_state=_bar_state(1, last_bar_time="2026-10-15T16:00:00+00:00"),
        )

    def test_backfill_updates_pending_row(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_pending(db, tid)

        # 次 bar：补旧账（pending 行回填）+记新账（新结算点 INSERT）同事务
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[], equity=_equity(),
            funding=[_funding("2026-10-16T00:00:00+00:00", amount=-0.04)],
            funding_backfills=[{"settle_time": "2026-10-15T16:00:00+00:00",
                                "rate": 0.00012, "amount": -0.036}],
            task_state=_bar_state(2, last_bar_time="2026-10-16T00:00:00+00:00"),
        )
        records = paper.list_funding_records(db, tid)
        assert len(records) == 2, "补旧账回填不产生新行+新账正常落"
        by_key = {r["settle_time"]: r for r in records}
        old = by_key["2026-10-15T16:00:00+00:00"]
        assert old["status"] == "settled"
        assert old["rate"] == 0.00012
        assert old["amount"] == -0.036
        assert by_key["2026-10-16T00:00:00+00:00"]["status"] == "settled"

    def test_backfill_replay_no_double_apply(self, tmp_path: Path):
        """幂等关键：崩溃重放后二次扣款路径防线——status='pending'
        前置使已 settled 行不命中（影响行数 0 静默跳过、值保持）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_pending(db, tid)
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[], equity=_equity(),
            funding_backfills=[{"settle_time": "2026-10-15T16:00:00+00:00",
                                "rate": 0.00012, "amount": -0.036}],
        )
        # 同 backfill 重放（行已 settled）：零影响不炸、值不被二次改写
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[], equity=_equity(),
            funding_backfills=[{"settle_time": "2026-10-15T16:00:00+00:00",
                                "rate": 9.9, "amount": 9.9}],
        )
        row = paper.list_funding_records(db, tid)[0]
        assert row["status"] == "settled"
        assert row["rate"] == 0.00012, "已 settled 行不被重放改写"
        assert row["amount"] == -0.036

    def test_backfill_atomic_with_bar_writes(self, tmp_path: Path):
        """一失败全回滚锚：backfill+新 funding INSERT+fills 同事务，
        类型违约→pending 行保持 pending、新行不落。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_pending(db, tid)
        with pytest.raises(sqlalchemy.exc.SQLAlchemyError):
            paper.append_bar(
                db, tid, bar_time="2026-10-16T00:00:00+00:00",
                fills=[_fill(1, price={"bad": "type"})],
                equity=_equity(),
                funding=[_funding("2026-10-16T00:00:00+00:00")],
                funding_backfills=[{"settle_time": "2026-10-15T16:00:00+00:00",
                                    "rate": 0.00012, "amount": -0.036}],
            )
        records = paper.list_funding_records(db, tid)
        assert len(records) == 1, "新结算未落"
        assert records[0]["status"] == "pending", "回填整体回滚，pending 保持"
        assert len(paper.list_fills(db, tid)) == 1, "首 bar 的 fill 不受本事务影响"

    def test_backfill_missing_row_silent(self, tmp_path: Path):
        """settle_time 不存在的 backfill→静默 no-op（不 INSERT 不炸）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_pending(db, tid)
        paper.append_bar(
            db, tid, bar_time="2026-10-16T00:00:00+00:00",
            fills=[], equity=_equity(),
            funding_backfills=[{"settle_time": "2099-01-01T00:00:00+00:00",
                                "rate": 0.1, "amount": 0.1}],
        )
        records = paper.list_funding_records(db, tid)
        assert len(records) == 1, "缺行不产生新行"
        assert records[0]["status"] == "pending"


class TestTerminalBackfillRows:
    """Q-2 终态后回补（backfill_funding_rows——终态封账专用独立事务：
    只 UPDATE pt_funding，MUST NOT 触碰 pt_task 行与任何 balance）。"""

    def _seed_terminal_pending(self, db: Path, tid: str):
        from core.persistence.repository import paper
        paper.append_bar(
            db, tid, bar_time="2026-10-15T16:00:00+00:00",
            fills=[_fill(1)], equity=_equity(),
            funding=[
                _funding("2026-10-15T08:00:00+00:00"),  # 已 settled
                _funding("2026-10-15T16:00:00+00:00", status="pending",
                         rate=None, amount=None),  # 挂起
            ],
            task_state=_bar_state(1, last_bar_time="2026-10-15T16:00:00+00:00",
                                  balance=49_900.0),
        )
        # 终态走合法链两步（status 写默认前态守门——选项 i 适配）
        paper.update_task(db, tid, status="running",
                          started_at="2026-10-15T15:00:00+00:00")
        paper.update_task(db, tid, status="completed",
                          stopped_at="2026-10-15T17:00:00+00:00")

    def test_backfill_sets_disclosure_only_and_returns_count(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_terminal_pending(db, tid)

        n = paper.backfill_funding_rows(db, tid, [
            {"settle_time": "2026-10-15T16:00:00+00:00",
             "rate": 0.00013, "amount": -0.039},
        ])
        assert n == 1, "返回实际回填行数（供回补例程披露计数）"
        by_key = {r["settle_time"]: r for r in paper.list_funding_records(db, tid)}
        backfilled = by_key["2026-10-15T16:00:00+00:00"]
        assert backfilled["status"] == "settled_disclosure_only"
        assert backfilled["rate"] == 0.00013
        assert backfilled["amount"] == -0.039
        assert by_key["2026-10-15T08:00:00+00:00"]["status"] == "settled", \
            "已 settled 行不受终态回补影响（WHERE status='pending' 防线）"

    def test_backfill_task_row_untouched(self, tmp_path: Path):
        """终态封账锚：pt_task 行零触碰——updated_at/balance/统计组
        全部不变（回补只补证据，不重写已固化成绩）。"""
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_terminal_pending(db, tid)
        before = paper.get_task(db, tid)

        paper.backfill_funding_rows(db, tid, [
            {"settle_time": "2026-10-15T16:00:00+00:00",
             "rate": 0.00013, "amount": -0.039},
        ])
        after = paper.get_task(db, tid)
        assert after == before, "终态回补对 pt_task 行零触碰（含 updated_at）"

    def test_backfill_idempotent_on_replay(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        self._seed_terminal_pending(db, tid)
        rows = [{"settle_time": "2026-10-15T16:00:00+00:00",
                 "rate": 0.00013, "amount": -0.039}]
        assert paper.backfill_funding_rows(db, tid, rows) == 1
        # 重放：已 settled_disclosure_only 不命中——返回 0、值保持
        assert paper.backfill_funding_rows(db, tid, [
            {"settle_time": "2026-10-15T16:00:00+00:00",
             "rate": 9.9, "amount": 9.9}]) == 0
        row = {r["settle_time"]: r for r in paper.list_funding_records(db, tid)} \
            ["2026-10-15T16:00:00+00:00"]
        assert row["rate"] == 0.00013 and row["amount"] == -0.039


class TestEntryLists:
    """明细读序：fills 按 bar_time+seq 升序（八步序审计序）。"""

    def test_list_fills_order_and_pagination(self, tmp_path: Path):
        from core.persistence.repository import paper

        db = _make_db(tmp_path)
        tid = _create_sample_task(db)
        for i in range(3):
            bar_time = f"2026-10-16T0{i}:00:00+00:00"
            paper.append_bar(
                db, tid, bar_time=bar_time,
                fills=[_fill(2, kind="add"), _fill(1)],  # 乱序传入
                equity=_equity(),
            )
        fills = paper.list_fills(db, tid, limit=10)
        assert [(f["bar_time"], f["seq"]) for f in fills] == [
            ("2026-10-16T00:00:00+00:00", 1),
            ("2026-10-16T00:00:00+00:00", 2),
            ("2026-10-16T01:00:00+00:00", 1),
            ("2026-10-16T01:00:00+00:00", 2),
            ("2026-10-16T02:00:00+00:00", 1),
            ("2026-10-16T02:00:00+00:00", 2),
        ]
        page = paper.list_fills(db, tid, limit=2, offset=2)
        assert [(f["bar_time"], f["seq"]) for f in page] == [
            ("2026-10-16T01:00:00+00:00", 1),
            ("2026-10-16T01:00:00+00:00", 2),
        ]
