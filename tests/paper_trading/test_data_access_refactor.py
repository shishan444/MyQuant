"""运行时数据访问下推验证断言面（refactor-paper-runtime-data-access
任务 5.1 四项验证重点的运行证据承载）。

断言预期从 design.md 四卡契约推导（不从实现反推）：

- A 卡（调度器短路）：零 paper 策略=扫描域空——空转周期零任务表
  查询；有 paper 策略时终态域经 status_in 下推（与 Python 判定同源
  集合 PT_STATUS_TERMINAL）；backfill 存在性短路——零 pending 资金
  费全免任务全量拉取。
- B 卡（恢复链聚合三边界）：空集两形态（无 open fill→None→0.0；
  有 open fill 零 settled→COALESCE 0.0）；闭区间边界（settle_time
  恰等于 since 计入、早于排除——MAX 选取一并锚定）；NULL 语义
  （SUM 忽略 NULL 行+全 NULL 兜零 ≡ Python 逐行 or 0.0）；权益
  末行空集→None；格式同构前提锚（毫秒变体异构下 SQL 字典序与
  pd.Timestamp 语义的分歧实证+两同判对照——两列生产写入面
  isoformat 整点无微秒段，前提当前成立；写入面变化即红灯）。
- C 卡（终态集合单点）：reporting.FINAL_STATUSES 与
  PT_STATUS_TERMINAL 同一对象；credential._active_statuses 单点
  直返 PT_STATUS_ACTIVE；terminal∪active=状态全集且零交集。
- D 卡（列表批量化）：单次任务页组装 strategy 批查+事件批查各恰
  1 次、循环内 get_strategy 零调用；research_status 三序
  first-match-wins 判定逐行正确（序① draft/序② 事件 verified/
  序③ qualified 兜底三分支+策略行已删快照兜底）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from api.routes.paper_trading import _tasks_to_responses
from core.paper_trading.reporting import FINAL_STATUSES
from core.paper_trading.runtime.executor import _rebuild_funding_paid
from core.paper_trading.runtime.recovery import backfill_terminal_funding
from core.paper_trading.runtime.scheduler import PaperScheduler
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper as paper_repo
from core.persistence.repository import strategy as strategy_repo
from core.strategy.lifecycle import credential as lifecycle_credential

_T = "2026-10-19T00:00:00+00:00"  # 任务时间戳（轨迁字段用）


# ── 装配 helper（形制沿 test_scheduler._ReturnFlowHarness /
# test_recovery._RecoveryHarness）──


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "paper.db"
    init_db_ext(db)
    return db


def _seed_strategy(db: Path, strategy_id: str, *, status: str = "active",
                   qualified: int | None = None) -> None:
    """造策略行（draft/active 合法初始态直插；paper 经 UPDATE 直写——
    六态其余值本就该经迁移服务，测试构造行不走服务面）。"""
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    strategy_repo.save_strategy(
        db, strategy_id=strategy_id, dna_json="{}",
        symbol="BTCUSDT", timeframe="4h",
        gene_signature=f"sig-{strategy_id}", status="active")
    with engine_for(db).begin() as conn:
        if status != "active":
            conn.execute(text("UPDATE strategy SET status=:s"
                              " WHERE strategy_id=:i"),
                         {"s": status, "i": strategy_id})
        if qualified is not None:
            conn.execute(text("UPDATE strategy SET qualified=:q"
                              " WHERE strategy_id=:i"),
                         {"q": qualified, "i": strategy_id})


def _insert_draft_active_event(db: Path, strategy_id: str) -> None:
    """直插 (draft→active) 迁移事件（序②判定的转正事实——形制沿
    test_reporting 现算随动用例）。"""
    from sqlalchemy import text

    from core.persistence.orm.engine import engine_for

    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO strategy_transition_event (event_id,"
            " strategy_id, from_status, to_status, trigger,"
            " evidence_json, created_at) VALUES ('ev_dar01',"
            " :sid, 'draft', 'active', 'credential', NULL, :t)"),
            {"sid": strategy_id, "t": _T})


def _seed_task(db: Path, task_id: str, strategy_id: str, *,
               research_status: str = "unverified") -> str:
    """建 pending 任务行（直建即可——本文件只消费行存在性）。"""
    paper_repo.create_task(
        db, task_id=task_id, strategy_id=strategy_id,
        dna_snapshot_json="{}", symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot=research_status)
    return task_id


def _seed_terminal_task(db: Path, task_id: str, strategy_id: str) -> str:
    """建 completed 终态任务（合法轨迁两跳：pending→running→completed）。"""
    _seed_task(db, task_id, strategy_id)
    paper_repo.update_task(db, task_id, status="running", started_at=_T)
    paper_repo.update_task(db, task_id, status="completed", stopped_at=_T)
    return task_id


def _append_bar(db: Path, task_id: str, bar_time: str, *,
                fills: tuple | list = (), funding: tuple | list = (),
                cumulative_funding: float = 0.0) -> None:
    """单 bar 落库（open fill / funding 行 / equity 快照三通道——
    append_bar 既有形制，equity 必传逐次独占 bar_time 唯一键）。"""
    paper_repo.append_bar(
        db, task_id, bar_time=bar_time,
        fills=list(fills),
        equity={"equity": 100.0, "balance": 100.0, "unrealized_pnl": 0.0,
                "cumulative_funding": cumulative_funding},
        funding=list(funding))


def _open_fill(seq: int) -> dict:
    return {"seq": seq, "kind": "open", "side": "long",
            "price": 100.0, "quantity": 1.0}


def _settled(settle_time: str, amount: float | None,
             quantity: float = 54.0) -> dict:
    return {"settle_time": settle_time, "status": "settled",
            "rate": 0.0001, "amount": amount,
            "position_quantity": quantity, "settle_price": 100.0}


class TestCardAShortCircuits:
    """A 卡双层/存在性短路的查询计数断言（monkeypatch 计数形制——
    预期来自 design A-1/A-2：空转周期零任务表查询+终态域下推+零
    pending 全免任务拉取）。"""

    def test_replay_zero_paper_strategies_skips_task_query(
            self, tmp_path, monkeypatch):
        """零 paper 策略（扫描域空）：策略行在场但非 paper+终态任务
        在场——第一层短路生效则 list_tasks 零调用（若退回直接拉任务
        形制，此库任务表非空必被查——计数 0 即短路证明，非未触达）。"""
        db = _make_db(tmp_path)
        _seed_strategy(db, "st-live", status="active")
        _seed_terminal_task(db, "pt_short01", "st-live")
        scheduler = PaperScheduler(db, data_dir=tmp_path)

        task_calls: list[dict] = []
        orig_list_tasks = paper_repo.list_tasks

        def _spy_list_tasks(*args, **kwargs):
            task_calls.append(kwargs)
            return orig_list_tasks(*args, **kwargs)

        strat_calls: list[dict] = []
        orig_list_strategies = strategy_repo.list_strategies

        def _spy_list_strategies(*args, **kwargs):
            strat_calls.append(kwargs)
            return orig_list_strategies(*args, **kwargs)

        monkeypatch.setattr(paper_repo, "list_tasks", _spy_list_tasks)
        monkeypatch.setattr(strategy_repo, "list_strategies",
                            _spy_list_strategies)

        scheduler._replay_paper_strategies()

        # 触达证明：第一层判定确实执行且按 paper 态过滤
        assert len(strat_calls) == 1
        assert strat_calls[0].get("status") == "paper"
        # 空转周期零任务表查询
        assert task_calls == []

    def test_replay_with_paper_strategy_pushes_terminal_status_in(
            self, tmp_path, monkeypatch):
        """有 paper 策略：第二层任务查询收到 status_in=终态集合
        （SQL 下推生效——与 Python 判定同源集合）。"""
        db = _make_db(tmp_path)
        _seed_strategy(db, "st-paper", status="paper")
        _seed_task(db, "pt_push01", "st-paper")  # pending（非终态行）
        scheduler = PaperScheduler(db, data_dir=tmp_path)

        task_calls: list[dict] = []
        orig_list_tasks = paper_repo.list_tasks

        def _spy_list_tasks(*args, **kwargs):
            task_calls.append(kwargs)
            return orig_list_tasks(*args, **kwargs)

        monkeypatch.setattr(paper_repo, "list_tasks", _spy_list_tasks)

        scheduler._replay_paper_strategies()

        assert len(task_calls) == 1
        assert task_calls[0].get("status_in") == \
            paper_repo.PT_STATUS_TERMINAL

    def test_backfill_zero_pending_short_circuits(
            self, tmp_path, monkeypatch):
        """零 pending 资金费（平时常态）：has_pending_funding 存在性
        查询恰 1 次（机制锚——短路判定本身被执行且仅执行一次），
        短路生效返回两零计数；list_tasks 零调用+逐任务 funding 查询
        零调用（tasks 2.2「全免任务全量拉取与逐任务 funding 查询」
        完成标准的独立断言面——两消费点 recovery 属性访问形制，
        可拦截）。"""
        db = _make_db(tmp_path)
        _seed_task(db, "pt_bf01", "st-any")  # 任务在场、pt_funding 空

        task_calls: list[dict] = []
        orig_list_tasks = paper_repo.list_tasks

        def _spy_list_tasks(*args, **kwargs):
            task_calls.append(kwargs)
            return orig_list_tasks(*args, **kwargs)

        pending_calls: list[dict] = []
        orig_has_pending = paper_repo.has_pending_funding

        def _spy_has_pending(*args, **kwargs):
            pending_calls.append(kwargs)
            return orig_has_pending(*args, **kwargs)

        funding_calls: list[dict] = []
        orig_list_funding = paper_repo.list_funding_records

        def _spy_list_funding(*args, **kwargs):
            funding_calls.append(kwargs)
            return orig_list_funding(*args, **kwargs)

        monkeypatch.setattr(paper_repo, "list_tasks", _spy_list_tasks)
        monkeypatch.setattr(paper_repo, "has_pending_funding",
                            _spy_has_pending)
        monkeypatch.setattr(paper_repo, "list_funding_records",
                            _spy_list_funding)

        result = backfill_terminal_funding(db, data_dir=tmp_path)

        # 机制锚：存在性短路判定恰 1 次（recovery:143 属性访问——
        # 若短路逻辑被拆除，此断言红）
        assert len(pending_calls) == 1
        assert result == {"tasks_backfilled": 0, "rows_backfilled": 0}
        assert task_calls == []
        assert funding_calls == []


class TestCardBAggregationBoundaries:
    """B 卡恢复链聚合边界（预期来自 design B 表三行契约：空集→
    None/COALESCE 兜零；settle_time>=since 闭区间与 Python >= 同判；
    SUM 忽略 NULL ≡ Python 逐行 or 0.0 兜零）。"""

    def test_rebuild_task_without_fills_returns_zero(self, tmp_path):
        """空任务（无 fill 无 funding）：max(open) 空集→None→消费侧
        0.0（无持仓语义）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_empty01", "st-b")
        assert _rebuild_funding_paid(db, tid) == 0.0

    def test_rebuild_open_fill_without_settled_returns_zero(
            self, tmp_path):
        """有 open fill 但零 settled 行：SUM 空集→COALESCE 兜零 0.0
        （聚合空集边界——非 None 通道的空集形态）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_nosett01", "st-b")
        _append_bar(db, tid, "2024-01-03T00:00:00+00:00",
                    fills=[_open_fill(1)])
        assert _rebuild_funding_paid(db, tid) == 0.0

    def test_rebuild_closed_interval_boundary_and_exclusions(
            self, tmp_path):
        """闭区间边界：since=MAX(open bar_time)（两笔 open 取晚者）；
        settle_time 恰等于 since 计入（>= 闭区间）；早于 since 的
        settled 排除（旧段资金费不归当前持仓）；pending 排除（未扣款
        不计入浮亏）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_edge01", "st-b")
        t_early = "2024-01-02T00:00:00+00:00"   # 早期 open（非 MAX）
        t_since = "2024-01-03T00:00:00+00:00"   # 最近 open = since
        t_after = "2024-01-04T00:00:00+00:00"   # since 之后
        _append_bar(db, tid, t_early, fills=[_open_fill(1)])
        _append_bar(
            db, tid, t_since, fills=[_open_fill(2)],
            funding=[
                # 边界行（恰等于 since）——闭区间须计入
                _settled(t_since, 2.5),
                # 段外行（==早期 open 但 < since）——排除，且须证明
                # since 取的是 MAX 而非任意 open
                _settled(t_early, 99.0),
                # since 之后正常侧——计入
                _settled(t_after, 1.5),
                # pending（未扣款）——排除
                {"settle_time": t_after, "status": "pending",
                 "rate": None, "amount": 7.0,
                 "position_quantity": 54.0, "settle_price": 100.0},
            ])
        paid = _rebuild_funding_paid(db, tid)
        assert paid == pytest.approx(2.5 + 1.5)
        # 段外 99.0 显著大于段内合计——不含它即闭区间下侧排除证明
        assert paid < 99.0

    def test_rebuild_null_amount_settled_ignored_by_sum(self, tmp_path):
        """NULL 语义等价：settled 且 amount=None 的行被 SUM 忽略
        （合计=非 NULL 行和——Python 侧 or 0.0 逐行兜零的等价面）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_null01", "st-b")
        t_since = "2024-01-03T00:00:00+00:00"
        _append_bar(
            db, tid, t_since, fills=[_open_fill(1)],
            funding=[
                _settled(t_since, None),   # NULL 行——忽略
                _settled("2024-01-04T00:00:00+00:00", 3.0),
                _settled("2024-01-05T00:00:00+00:00", 1.0),
            ])
        assert _rebuild_funding_paid(db, tid) == pytest.approx(4.0)

    def test_rebuild_all_null_amount_coalesces_zero(self, tmp_path):
        """全 NULL 集：SUM(NULL)=NULL→COALESCE 兜零 0.0（聚合函数
        三边界的全 NULL 形态）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_allnull01", "st-b")
        t_since = "2024-01-03T00:00:00+00:00"
        _append_bar(
            db, tid, t_since, fills=[_open_fill(1)],
            funding=[_settled(t_since, None),
                     _settled("2024-01-04T00:00:00+00:00", None)])
        assert _rebuild_funding_paid(db, tid) == 0.0

    def test_latest_equity_snapshot_no_snapshots_returns_none(
            self, tmp_path):
        """无权益快照任务：latest_equity_snapshot→None（from_task_row
        的 if snap 分支——空集语义保持）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_nosnap01", "st-b")
        assert paper_repo.latest_equity_snapshot(db, tid) is None

    def test_latest_equity_snapshot_returns_last_by_bar_time(
            self, tmp_path):
        """对照侧（B③ 契约本体）：多行快照取 bar_time 时序末行
        （ORDER BY DESC LIMIT 1——原「拉全量取末行」语义）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_snap01", "st-b")
        _append_bar(db, tid, "2024-01-02T00:00:00+00:00",
                    cumulative_funding=1.25)
        _append_bar(db, tid, "2024-01-03T00:00:00+00:00",
                    cumulative_funding=2.75)
        snap = paper_repo.latest_equity_snapshot(db, tid)
        assert snap is not None
        assert snap["bar_time"] == "2024-01-03T00:00:00+00:00"
        assert snap["cumulative_funding"] == pytest.approx(2.75)


def _legacy_rebuild_funding_paid(db: Path, task_id: str) -> float:
    """改造前语义独立复算（git 67391ae^ executor 形制逐行照抄——
    pd.Timestamp 比较语义；独立参考，非当前实现回填）。"""
    fills = paper_repo.list_fills(db, task_id, limit=100_000)
    open_bars = [f["bar_time"] for f in fills if f["kind"] == "open"]
    if not open_bars:
        return 0.0
    since = pd.Timestamp(max(open_bars))
    return float(sum(
        f["amount"] or 0.0
        for f in paper_repo.list_funding_records(
            db, task_id, limit=100_000)
        if f["status"] == "settled"
        and pd.Timestamp(f["settle_time"]) >= since))


class TestCardBTimeFormatPremise:
    """B 卡等价前提锚：bar_time/settle_time「同列族 ISO 字符串，
    字典序=时间序」仅在两列格式同构时成立（executor.py _rebuild
    注释与 sum_settled_funding_since docstring 的前提）。生产写入面
    两列均出自 pd.Timestamp(...).isoformat() 网格整点（微秒恒零→
    isoformat 不产生微数段），前提当前成立；本类锚定该前提——写入
    面出现异构格式（毫秒变体等）即刻红灯（分歧实证与生产不可达
    论证见 change review-testing.md §五）。"""

    def test_rebuild_since_with_micros_excludes_same_instant_no_micros(
            self, tmp_path):
        """分歧主证：since（open bar_time）带零微秒段、settle 同时刻
        无微秒段——字典序 '+'(0x2B)<'.'(0x2E) 排除同时刻行（7.0），
        pd.Timestamp 语义相等计入（12.0）：同数据两语义不同结果。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_ms01", "st-b")
        _append_bar(
            db, tid, "2024-01-03T00:00:00.000000+00:00",
            fills=[_open_fill(1)],
            funding=[
                _settled("2024-01-03T00:00:00+00:00", 5.0),  # 分歧行
                _settled("2024-01-03T00:00:01+00:00", 7.0),  # 晚 1 秒
            ])
        current = _rebuild_funding_paid(db, tid)
        legacy = _legacy_rebuild_funding_paid(db, tid)
        # 改造后（Text 列 SQL 字典序）：分歧行被排除
        assert current == pytest.approx(7.0)
        # 改造前（pd.Timestamp 语义）：同时刻相等计入
        assert legacy == pytest.approx(12.0)
        # 分歧实证——前提「格式同构」被打破时两语义不再等价
        assert current != legacy

    def test_rebuild_since_without_micros_includes_same_instant_with(
            self, tmp_path):
        """同判对照 A：since 无微秒段、settle 同时刻带零微秒段——
        '.'>'+' 字典序与 Timestamp 相等语义同判（两侧都计入）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_ms02", "st-b")
        _append_bar(
            db, tid, "2024-01-03T00:00:00+00:00",
            fills=[_open_fill(1)],
            funding=[
                _settled("2024-01-03T00:00:00.000000+00:00", 5.0)])
        assert _rebuild_funding_paid(db, tid) == pytest.approx(5.0)
        assert _legacy_rebuild_funding_paid(db, tid) == pytest.approx(5.0)

    def test_rebuild_settle_micros_later_included_both_semantics(
            self, tmp_path):
        """同判对照 B：settle 带非零微秒且晚于 since——两语义同判
        （都计入）。"""
        db = _make_db(tmp_path)
        tid = _seed_task(db, "pt_ms03", "st-b")
        _append_bar(
            db, tid, "2024-01-03T00:00:00+00:00",
            fills=[_open_fill(1)],
            funding=[
                _settled("2024-01-03T00:00:00.500000+00:00", 5.0)])
        assert _rebuild_funding_paid(db, tid) == pytest.approx(5.0)
        assert _legacy_rebuild_funding_paid(db, tid) == pytest.approx(5.0)


class TestCardCTerminalSetSinglePoint:
    """C 卡三处归一（预期来自 design C：单点=PT_STATUS_TERMINAL，
    FINAL_STATUSES 同一对象别名、_active_statuses 零推导单点直返、
    terminal/active 为全集互补划分）。"""

    def test_final_statuses_is_paper_terminal_object(self):
        """reporting.FINAL_STATUSES 与 paper.PT_STATUS_TERMINAL 同一
        对象（独立声明已升级为单点别名——恒等由静态保证）。"""
        assert FINAL_STATUSES is paper_repo.PT_STATUS_TERMINAL

    def test_active_statuses_single_point_direct_return(self):
        """credential._active_statuses 零推导消费：直返 PT_STATUS_ACTIVE
        （薄壳兼容面，值恒等）。"""
        assert lifecycle_credential._active_statuses() == \
            paper_repo.PT_STATUS_ACTIVE

    def test_terminal_active_partition_full_and_disjoint(self):
        """terminal∪active=状态全集（keys）且零交集（互补划分完整——
        派生形制 keys−union(values) / keys−terminal 的完整语义）。"""
        keys = frozenset(paper_repo.PT_STATUS_PREDECESSORS)
        assert (paper_repo.PT_STATUS_TERMINAL
                | paper_repo.PT_STATUS_ACTIVE) == keys
        assert not (paper_repo.PT_STATUS_TERMINAL
                    & paper_repo.PT_STATUS_ACTIVE)


class TestCardDListBatching:
    """D 卡列表批量化（预期来自 design D：单次请求内 strategy 批查
    1 次+事件批查 1 次+循环内零逐行查询；research_status 三序
    first-match-wins 判定本体不变——期望值按三序规则独立推导）。"""

    def test_tasks_to_responses_batches_queries_and_research_status(
            self, tmp_path, monkeypatch):
        """五任务页（覆盖三序全分支+快照兜底）：get_strategy 零调用、
        两批查各恰 1 次、research_status 逐行正确（含已删策略行走
        快照列兜底值）。"""
        db = _make_db(tmp_path)
        # 序①：status=draft → draft
        _seed_strategy(db, "st-draft", status="draft")
        # 序②：非 draft 且存在 (draft→active) 事件 → verified
        _seed_strategy(db, "st-evt", status="active")
        _insert_draft_active_event(db, "st-evt")
        # 序③三分支：qualified=0 → verification_failed
        _seed_strategy(db, "st-q0", status="active", qualified=0)
        # 序③：qualified NULL → unverified
        _seed_strategy(db, "st-qnull", status="active")
        # 策略行已删（直建任务无策略行）→ 快照列兜底
        _seed_task(db, "pt_list01", "st-draft")
        _seed_task(db, "pt_list02", "st-evt")
        _seed_task(db, "pt_list03", "st-q0")
        _seed_task(db, "pt_list04", "st-qnull")
        _seed_task(db, "pt_list05", "st-deleted",
                   research_status="verified")

        counters = {"get_strategy": 0, "list_strategies_by_ids": 0,
                    "strategies_with_draft_to_active_event": 0}
        orig_get = strategy_repo.get_strategy

        def _spy_get(*args, **kwargs):
            counters["get_strategy"] += 1
            return orig_get(*args, **kwargs)

        orig_by_ids = paper_repo.list_strategies_by_ids

        def _spy_by_ids(*args, **kwargs):
            counters["list_strategies_by_ids"] += 1
            return orig_by_ids(*args, **kwargs)

        orig_evt = paper_repo.strategies_with_draft_to_active_event

        def _spy_evt(*args, **kwargs):
            counters["strategies_with_draft_to_active_event"] += 1
            return orig_evt(*args, **kwargs)

        monkeypatch.setattr(strategy_repo, "get_strategy", _spy_get)
        monkeypatch.setattr(paper_repo, "list_strategies_by_ids",
                            _spy_by_ids)
        monkeypatch.setattr(paper_repo,
                            "strategies_with_draft_to_active_event",
                            _spy_evt)

        rows = paper_repo.list_tasks(db, limit=100)
        assert len(rows) == 5
        out = _tasks_to_responses(db, rows)

        # 查询常数化：循环内零逐行查询，两批查各恰 1 次
        assert counters == {"get_strategy": 0,
                            "list_strategies_by_ids": 1,
                            "strategies_with_draft_to_active_event": 1}
        # 三序判定逐行正确（期望按 resolve_research_status 契约三序
        # 规则推导，非实现输出回填）
        got = {r.task_id: r.research_status for r in out}
        assert got == {
            "pt_list01": "draft",                # 序① status=draft
            "pt_list02": "verified",             # 序② 事件存在
            "pt_list03": "verification_failed",  # 序③ qualified=0
            "pt_list04": "unverified",           # 序③ qualified NULL
            "pt_list05": "verified",             # 已删行→快照列兜底
        }
