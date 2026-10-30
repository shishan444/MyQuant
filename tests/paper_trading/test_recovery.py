"""恢复器测试（rebuild-paper-trading 任务 3.4；启动扫描经
scheduler.startup_recover——review B2 后单入口）。

覆盖：崩溃续跑（bar N 落库后重启→bar N+1 续跑，此前成交/权益/资金费
完好不重扣）+恢复校验四判据参数化各转 error（保留数据）+可信续跑状态
重建（持仓/余额/待执行意图）+重放幂等（恢复后首 bar 与库内最后 fill
同 bar_time→唯一键吸收+游标判重跳过）+startup_recover 启动扫描。

崩溃模拟：同步驱动执行器至 bar N 后直接丢弃对象（任务行停留在
running+bars_processed=N——调度器拾取后崩溃的持久化残态）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.paper_trading.runtime.recovery import (
    recover_task,
    validate_recovery,
)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv
from tests.paper_trading.test_paper_executor import _max_loss_dna

_N_BARS = 60
_CRASH_AT = 25  # 崩溃点（bar N 落库后丢弃执行器）
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"


class _RecoveryHarness:
    """恢复测试装配（同步回放通道——崩溃=丢弃执行器对象）。"""

    def __init__(self, tmp_path: Path, *, funding_times=(), dna=None):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=_N_BARS, freq="4h", seed=7, base_price=100.0)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.enhanced = compute_all_indicators(self.df)

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=(dna or make_dna()).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=None)

        self.registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: self._funding(funding_times))
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.reconcile()  # funding 索引基线

    @staticmethod
    def _funding(times):
        if not times:
            return pd.DataFrame(
                {"funding_rate": []},
                index=pd.DatetimeIndex([], name="timestamp"))
        idx = pd.DatetimeIndex(pd.to_datetime(list(times), utc=True),
                               name="timestamp")
        return pd.DataFrame({"funding_rate": [0.0001] * len(idx)},
                            index=idx)

    def run_until_crash(self, n_bars: int) -> PaperTaskExecutor:
        """同步驱动至第 n_bars 根落库后丢弃执行器（模拟进程崩溃——
        任务行停留 running，无终态写入）。"""
        row = paper.get_task(self.db, self.task_id)
        executor = PaperTaskExecutor(
            self.db, row, registry=self.registry,
            start_after=_REPLAY_ALL)
        assert executor.prepare() is True
        paper.update_task(
            self.db, self.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.process_frames(self.enhanced.iloc[:n_bars], 0)
        return executor  # 调用方丢弃即崩溃

    def recover(self):
        """恢复入口（可信→执行器；不可信→None+error 已写）。"""
        row = paper.get_task(self.db, self.task_id)
        return recover_task(
            self.db, row, registry=self.registry,
            start_after=_REPLAY_ALL)

    def task(self) -> dict:
        return paper.get_task(self.db, self.task_id)


class TestCrashResume:
    """崩溃续跑（E3：可信状态自动续跑不丢证据）。"""

    def test_resume_from_next_bar_no_duplicates(self, tmp_path):
        """bar N 落库后重启→N+1 续跑：fills/equity/funding 完好不重扣。"""
        h = _RecoveryHarness(tmp_path, funding_times=[
            "2024-01-04 16:00:00"])  # 崩溃前已 settled 的结算点
        h.run_until_crash(_CRASH_AT)

        fills_before = paper.list_fills(h.db, h.task_id, limit=1000)
        eqs_before = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        funding_before = [
            f for f in paper.list_funding_records(h.db, h.task_id, limit=1000)
            if f["status"] == "settled"]
        balance_before = h.task()["balance"]
        assert balance_before is not None  # 崩溃前已落账户态

        executor = h.recover()
        assert executor is not None
        executor.process_frames(h.enhanced, 0)  # 全帧重放——游标判重跳过

        # 此前证据完好：fills/equity/funding 前段逐行保留且不重复
        fills_after = paper.list_fills(h.db, h.task_id, limit=1000)
        eqs_after = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        assert fills_after[:len(fills_before)] == fills_before
        assert len(fills_after) >= len(fills_before)
        assert eqs_after[:len(eqs_before)] == eqs_before
        # settled 资金费不重扣：金额/行数不变（唯一键+游标双侧幂等）
        funding_after = [
            f for f in paper.list_funding_records(h.db, h.task_id, limit=1000)
            if f["status"] == "settled"]
        assert funding_after[:len(funding_before)] == funding_before

        # 续跑推进到帧尾
        assert h.task()["bars_processed"] == _N_BARS
        assert len(eqs_after) == _N_BARS

    def test_resume_rebuilds_position_state(self, tmp_path):
        """崩溃时持仓→恢复重建持仓字段组（entry/qty/margin 连续）。"""
        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)
        crashed = h.task()
        assert crashed["position_side"] is not None  # 崩溃时持仓中

        executor = h.recover()
        assert executor is not None
        pos = executor.ledger.position
        assert pos is not None
        assert pos.side == crashed["position_side"]
        assert pos.entry_price == pytest.approx(
            crashed["position_entry"])
        assert pos.quantity == pytest.approx(
            crashed["position_quantity"])
        assert executor.ledger.balance == pytest.approx(
            crashed["balance"])
        assert executor.ledger.closed_trades == crashed["total_trades"]

    def test_resumed_task_finishes_window(self, tmp_path):
        """恢复后窗口到点正常结题（终态能力不因恢复降级）。"""
        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)
        executor = h.recover()
        assert executor is not None
        executor.task_row["window_bars"] = _N_BARS  # 窗口=帧长（就地注）
        executor.process_frames(h.enhanced, 0)
        assert h.task()["bars_processed"] == _N_BARS


class TestRecoveryValidation:
    """恢复校验四判据（不可信→error+保留数据）。"""

    def _crashed_row(self, h) -> dict:
        """构造崩溃残态基线（在基线上逐判据破坏）。"""
        return h.task()

    @pytest.fixture
    def crashed_harness(self, tmp_path):
        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)
        return h

    def test_missing_cursor_rejected(self, crashed_harness):
        """判据 1：bars_processed>0 而 last_bar_time 为空。"""
        h = crashed_harness
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[_CRASH_AT - 1].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 99, "last_bar_time": None})
        assert "游标缺失" in validate_recovery(h.task())
        assert h.recover() is None
        task = h.task()
        assert task["status"] == "error"
        assert "recovery_untrusted" in task["error_detail"]
        # 保留数据
        assert paper.list_fills(h.db, h.task_id)

    def test_null_balance_rejected(self, tmp_path):
        """判据 2：balance 为 NULL（从未落账户态的 running 残态）。"""
        h = _RecoveryHarness(tmp_path)
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 1,
                        "last_bar_time": h.df.index[0].isoformat()})
        # task_state 未写 balance——行上仍 NULL
        assert h.task()["balance"] is None
        assert "balance" in validate_recovery(h.task())
        assert h.recover() is None
        assert h.task()["status"] == "error"

    def test_partial_position_group_rejected(self, crashed_harness):
        """判据 3：持仓字段组部分 NULL 部分非 NULL。"""
        h = crashed_harness
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[_CRASH_AT - 1].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"position_side": "long",
                        "position_entry": None})  # entry 置 NULL→部分组
        detail = validate_recovery(h.task())
        assert detail and "持仓字段组" in detail
        assert h.recover() is None
        assert "recovery_untrusted" in h.task()["error_detail"]

    def test_broken_pending_intent_rejected(self, crashed_harness):
        """判据 4：pending_intent_json 解析失败。"""
        h = crashed_harness
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[_CRASH_AT - 1].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"pending_intent_json": "{broken json"})
        detail = validate_recovery(h.task())
        assert detail and "意图不可解析" in detail
        assert h.recover() is None

    def test_untrusted_keeps_data_for_report(self, crashed_harness):
        """不可信转 error 后已产出数据保留（可出报告——spec 场景）。"""
        h = crashed_harness
        row = h.task()
        row["pending_intent_json"] = "not-json"
        assert recover_task(
            h.db, row, registry=h.registry) is None
        task = h.task()
        assert task["status"] == "error"
        assert paper.list_fills(h.db, h.task_id)
        assert paper.list_equity_snapshots(h.db, h.task_id)


class TestZeroBarExemption:
    """零 bar 豁免（fr_bc61a365fdd7）：拾取后崩溃（写 running 后、
    首根闭合 bar 处理前）的残态——bars_processed=0 且 balance 自然
    NULL（首 bar 权益快照才写），非账户态损坏。

    特征记录（现状语义）：
    - 豁免域=balance NULL 且 bars=0 且持仓核心字段组全 NULL：校验层
      放行（validate_recovery 返回 None）；bars>0 而 balance NULL
      仍真损坏（上方 test_null_balance_rejected 锚）；
    - bars=0 但 balance 有值不豁免——判据 3/4 照常走；
    - 豁免收紧（CARD-02 扩充）：零 bar 而任何持仓字段非 NULL=物理
      矛盾（持仓首写随首个 bar 处理事务），validate 直接 fail-closed
      ——部分 NULL 与全满两形态单点全收；收紧前部分 NULL 形态经
      豁免放行、由 from_task_row 重建失败兜底，全满形态豁免内外均
      无拦（幽灵持仓 fail-open，缺陷历史）；
    - 意图判据 4 仍被豁免分支短路（bars=0+持仓全 NULL+坏意图也
      放行——docstring「其余判据也无从触发」的现状行为）；
    - 全链续跑（CARD-02 修复）：from_task_row 对 balance NULL 跳过
      覆写（Ledger.__init__ 已从 initial_cash 构造初始账户态）——
      修复前该处 float(None) 抛 TypeError 被兜底转 error，提交
      1ffbf91 声称的「直接可信续跑」未在全链达成（缺陷历史）。
    """

    @staticmethod
    def _crashed_before_first_bar(tmp_path: Path) -> _RecoveryHarness:
        """零 bar 残态：create_task+写 running（拾取后、首 bar 前崩溃）。"""
        h = _RecoveryHarness(tmp_path)
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        return h

    def test_zero_bar_null_balance_validate_exempts(self, tmp_path):
        """校验层豁免生效：validate_recovery 返回 None（修复前判据 2 拒）。"""
        h = self._crashed_before_first_bar(tmp_path)
        task = h.task()
        assert task["bars_processed"] == 0  # 前置锚：零 bar 残态
        assert task["balance"] is None
        assert task["last_bar_time"] is None
        assert validate_recovery(task) is None

    def test_zero_bar_full_chain_recovers_to_resume(self, tmp_path):
        """全链可信续跑（CARD-02 修复锚）：校验放行后 recover_task
        重建执行器（balance 从 initial_cash 起步），任务保持 running
        无 error。修复前 from_task_row 的 float(None) 抛 TypeError
        被兜底转 error「重建失败」——续跑未达成（缺陷历史）。"""
        h = self._crashed_before_first_bar(tmp_path)
        assert validate_recovery(h.task()) is None  # 校验层已放行
        executor = h.recover()
        assert executor is not None  # 重建成功（修复前此处失败）
        assert executor.ledger.balance == 10_000.0  # initial_cash 起步
        task = h.task()
        assert task["status"] == "running"
        assert task["error_detail"] is None

    def test_zero_bar_balance_present_not_exempt(self, tmp_path):
        """豁免域收窄：bars=0 但 balance 有值 → 判据照常走（坏意图
        仍拒）——豁免不因 bars=0 全开。"""
        h = self._crashed_before_first_bar(tmp_path)
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 0, "last_bar_time": None,
                        "balance": 10_000.0,
                        "pending_intent_json": "{broken"})
        detail = validate_recovery(h.task())
        assert detail and "意图不可解析" in detail

    # D 类现状容忍用例（意图判据 4 被豁免短路）已隔离至
    # test_recovery_tolerances.py 待口径裁决（阶段四步骤 3 V3 化）

    def test_zero_bar_partial_position_fail_closed_at_validate(
            self, tmp_path):
        """豁免收紧（部分 NULL 形态）：bars=0 但持仓字段部分非 NULL
        → validate 物理矛盾直接拒+recover 转 error。收紧前该形态经
        豁免放行、由 from_task_row 重建失败兜底转 error（缺陷历史，
        终态同 error 但路径与 detail 不同）。"""
        h = self._crashed_before_first_bar(tmp_path)
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"position_side": "long", "position_entry": None})
        detail = validate_recovery(h.task())
        assert detail and "物理矛盾" in detail
        assert h.recover() is None
        task = h.task()
        assert task["status"] == "error"
        assert "物理矛盾" in (task["error_detail"] or "")

    def test_zero_bar_full_position_fields_fail_closed(self, tmp_path):
        """豁免收紧（全满形态——security 扩充对称锚）：bars=0+持仓
        七字段全非 NULL → validate 物理矛盾拒+recover 转 error。
        收紧前该形态豁免内外均无拦（幽灵持仓 fail-open 续跑通道）。
        equity 形制按生产 _persist_bar（带 position_side 与
        cumulative_funding）。"""
        h = self._crashed_before_first_bar(tmp_path)
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0,
                              "position_side": "long",
                              "cumulative_funding": 0.0},
            task_state={"bars_processed": 0, "last_bar_time": None,
                        "position_side": "long", "position_entry": 100.0,
                        "position_quantity": 1.0, "position_margin": 50.0,
                        "position_open_cost": 100.0,
                        "position_trail_peak": 100.0,
                        "position_entry_bar_seq": 0})
        detail = validate_recovery(h.task())
        assert detail and "物理矛盾" in detail
        assert h.recover() is None
        task = h.task()
        assert task["status"] == "error"
        assert "物理矛盾" in (task["error_detail"] or "")

    _FIELD_VALUES = {
        "position_side": "long", "position_entry": 100.0,
        "position_quantity": 1.0, "position_margin": 50.0,
        "position_open_cost": 100.0, "position_trail_peak": 100.0,
        "position_entry_bar_seq": 0,
    }

    @pytest.mark.parametrize("field", [
        pytest.param(f, id=f) for f in _FIELD_VALUES])
    def test_zero_bar_any_single_position_field_fail_closed(
            self, tmp_path, field):
        """属性锚（步骤 4）：七字段任一单独非 NULL（其余保持 NULL）
        均触发物理矛盾拒——any() 单点全收判据的逐字段完备面。"""
        h = self._crashed_before_first_bar(tmp_path)
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 0, "last_bar_time": None,
                        field: self._FIELD_VALUES[field]})
        detail = validate_recovery(h.task())
        assert detail and "物理矛盾" in detail, field


class TestReplayIdempotency:
    """重放幂等（唯一键吸收+游标判重）。"""

    def test_same_bar_replay_absorbed_by_unique_key(self, tmp_path):
        """游标回拨后重放同 bar：fill/equity 唯一键吸收不重复。"""
        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)
        # 人为回拨游标一格（防御性场景：库内最后 fill 与次处理首 bar
        # 同 bar_time）
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[_CRASH_AT - 1].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={
                "last_bar_time": h.df.index[_CRASH_AT - 2].isoformat(),
                "balance": h.task()["balance"]})

        executor = h.recover()
        assert executor is not None
        fills_before = paper.list_fills(h.db, h.task_id, limit=1000)
        eqs_before = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        executor.process_frames(h.enhanced, 0)

        fills_after = paper.list_fills(h.db, h.task_id, limit=1000)
        eqs_after = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        # 无重复键（重放被唯一键吸收）
        assert len({(f["bar_time"], f["seq"]) for f in fills_after}) == \
            len(fills_after)
        assert len({e["bar_time"] for e in eqs_after}) == len(eqs_after)
        # 回拨前的行数不减（吸收非覆盖）
        assert len(fills_after) >= len(fills_before)
        assert len(eqs_after) >= len(eqs_before)

    def test_frame_replayed_twice_advances_once(self, tmp_path):
        """同帧二次投递：游标判重全跳过（零新行零新交易）。"""
        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)
        executor = h.recover()
        assert executor is not None
        executor.process_frames(h.enhanced, 0)
        bars_once = h.task()["bars_processed"]
        fills_once = len(paper.list_fills(h.db, h.task_id, limit=1000))

        executor.process_frames(h.enhanced, 0)  # 同帧重放
        assert h.task()["bars_processed"] == bars_once
        assert len(paper.list_fills(h.db, h.task_id, limit=1000)) == \
            fills_once


class TestStartupScan:
    """启动扫描（scheduler.startup_recover——lifespan 入口；scan_and_recover
    旧函数已删：其恢复执行器只计数即丢弃不起线程，保留即「假恢复」
    陷阱——review B2 裁决）。"""

    def test_scan_recovers_and_rejects(self, tmp_path):
        """混合残态：可信续跑（起线程）+不可信转 error 分桶计数。"""
        from core.paper_trading.runtime.scheduler import PaperScheduler

        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)  # 可信残态

        # 第二任务：不可信残态（running 但 balance 从未落）
        broken_id = paper.create_task(
            h.db, strategy_id="strat-2",
            dna_snapshot_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=None)
        paper.update_task(
            h.db, broken_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        paper.append_bar(
            h.db, broken_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 1,
                        "last_bar_time": h.df.index[0].isoformat()})

        scheduler = PaperScheduler(
            h.db, registry=h.registry, poll_interval=0.2)
        try:
            counts = scheduler.startup_recover()
            assert counts == {"recovered": 1, "untrusted": 1}
            assert paper.get_task(h.db, broken_id)["status"] == "error"
            # 可信任务被起线程续跑（并发槽登记在案）
            assert h.task_id in scheduler.status()["active_task_ids"]
            assert paper.get_task(h.db, h.task_id)["status"] == "running"
        finally:
            scheduler.stop()
            scheduler.join(timeout=5.0)


class TestMaxLossRecovery:
    """金额防线崩溃恢复（add-position-sizing-params 族 9）。

    场景（seed=7、mpl=190、崩溃点 22）：bars 0..21 落库——i=21 开盘
    开多+i=21 bar 区间 16:00 结算点落 settled；崩溃时浮亏≈2 未达线。
    恢复式：funding_paid=pt_funding settled 行（settle_time≥最近 open
    bar 时刻）聚合；pending 未扣款与段外 settled 行不计入。续跑后
    防线从持仓快照与最新收盘重判（纯函数式无持久化标记——pending
    意图未落库即崩溃形态下，游标判重跳过、下次收盘重判恢复防线）。
    """

    @staticmethod
    def _settled_sum(h, since_bar_time: str) -> float:
        """库侧直查聚合（恢复式的对拍口径）。"""
        since = pd.Timestamp(since_bar_time)
        return sum(
            f["amount"] for f in paper.list_funding_records(
                h.db, h.task_id, limit=100_000)
            if f["status"] == "settled"
            and pd.Timestamp(f["settle_time"]) >= since)

    @staticmethod
    def _last_open_bar_time(h) -> str:
        return max(
            f["bar_time"] for f in paper.list_fills(
                h.db, h.task_id, limit=100_000) if f["kind"] == "open")

    def test_funding_paid_rebuilt_from_settled_rows(self, tmp_path):
        """重建正确：恢复后 funding_paid==settled（≥open bar 时刻）
        库侧直查聚合值。"""
        h = _RecoveryHarness(
            tmp_path, funding_times=["2024-01-04 16:00:00"],
            dna=_max_loss_dna(190.0))
        h.run_until_crash(22)  # bars 0..21：开仓+结算点已落库
        crashed = h.task()
        assert crashed["position_side"] is not None  # 崩溃时持仓中
        settled = [
            f for f in paper.list_funding_records(h.db, h.task_id, limit=1000)
            if f["status"] == "settled"]
        assert settled, "崩溃前须有已结算资金费行"

        executor = h.recover()
        assert executor is not None
        expected = self._settled_sum(h, self._last_open_bar_time(h))
        assert expected > 0.0
        assert executor.ledger.position.funding_paid \
            == pytest.approx(expected)

    def test_pending_and_stale_settled_excluded(self, tmp_path):
        """pending 未扣款不计入+段外 settled（settle_time<持仓开始
        bar 时刻）不计入。"""
        h = _RecoveryHarness(
            tmp_path, funding_times=["2024-01-04 16:00:00"],
            dna=_max_loss_dna(190.0))
        h.run_until_crash(22)
        # 人工追加两类不入集行：pending（挂起未扣款）+段外 settled
        # （空仓期结算点——旧段资金费不归当前持仓）
        paper.append_bar(
            h.db, h.task_id, bar_time=h.df.index[21].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            funding=[
                {"settle_time": "2024-01-05T00:00:00+00:00",
                 "status": "pending", "rate": None, "amount": None,
                 "position_quantity": 54.0, "settle_price": 90.0},
                {"settle_time": "2024-01-01T08:00:00+00:00",
                 "status": "settled", "rate": 0.0001, "amount": 99.0,
                 "position_quantity": 0.0, "settle_price": 100.0},
            ])
        executor = h.recover()
        assert executor is not None
        expected = self._settled_sum(h, self._last_open_bar_time(h))
        assert executor.ledger.position.funding_paid \
            == pytest.approx(expected)
        # 段外 99.0 显著大于段内结算额——重建值不含它即证明过滤
        assert executor.ledger.position.funding_paid < 99.0

    def test_rebuild_is_read_only(self, tmp_path):
        """重建纯读零副作用：恢复前后 pt_funding/pt_fill 行数与
        内容一致（无持久化触发标记——判定纯函数式的落库侧证据）。"""
        h = _RecoveryHarness(
            tmp_path, funding_times=["2024-01-04 16:00:00"],
            dna=_max_loss_dna(190.0))
        h.run_until_crash(22)
        funding_before = paper.list_funding_records(
            h.db, h.task_id, limit=100_000)
        fills_before = paper.list_fills(h.db, h.task_id, limit=100_000)

        executor = h.recover()
        assert executor is not None
        assert paper.list_funding_records(
            h.db, h.task_id, limit=100_000) == funding_before
        assert paper.list_fills(
            h.db, h.task_id, limit=100_000) == fills_before

    def test_resume_rejudges_defense_and_exits(self, tmp_path):
        """崩溃（达线意图未落库）→恢复：游标判重跳过已处理 bar，
        下次收盘重判达线 → exit_max_loss 次根开盘成交（族 9d——
        防线从持仓快照与最新收盘重判，无需持久化触发标记）。"""
        h = _RecoveryHarness(
            tmp_path, funding_times=["2024-01-04 16:00:00"],
            dna=_max_loss_dna(190.0))
        h.run_until_crash(22)  # 崩溃时浮亏≈2 未达线、无 pending 意图

        executor = h.recover()
        assert executor is not None
        executor.process_frames(h.enhanced, 0)  # 全帧重放——判重跳过

        exits = [f for f in paper.list_fills(
            h.db, h.task_id, limit=1000) if f["kind"] == "exit_max_loss"]
        assert exits, "恢复续跑后防线须达线出场"
        exit_fill = exits[0]
        assert exit_fill["reason"] == "max_loss"
        assert exit_fill["seq"] == 3
        # 达线 bar=成交 bar 前一根：该根收盘浮亏+funding_paid≥阈值
        exit_idx = h.enhanced.index.get_loc(
            pd.Timestamp(exit_fill["bar_time"]))
        open_fill = next(
            f for f in paper.list_fills(h.db, h.task_id, limit=1000)
            if f["kind"] == "open")
        qty = open_fill["quantity"]
        entry = open_fill["price"]
        loss_at = -qty * (
            float(h.enhanced["close"].iloc[exit_idx - 1]) - entry) \
            + executor.ledger.total_funding  # funding 分量≥已付额
        assert loss_at >= 190.0
        # 全帧续跑完成（60 根全处理——崩溃前 22 根判重跳过不重扣）
        assert h.task()["bars_processed"] == _N_BARS


class TestLegacyWhitelistCleanup:
    """存量双维清场（change paper-mount-and-concurrency 任务 5）：
    白名单外 running 残行经 startup_recover 转 error（双维枚举分立
    可辨）保留数据；白名单内正常恢复续跑（清场不误伤）。"""

    def _legacy_running_row(self, h, task_id: str, *, symbol: str,
                            timeframe: str) -> str:
        """直插非白名单 running 残行（绕过受理层的存量形态——含
        可信账户态使恢复链可达白名单判据）。"""
        paper.create_task(
            h.db, strategy_id="strat-l", task_id=task_id,
            dna_snapshot_json=make_dna().to_json(),
            symbol=symbol, timeframe=timeframe,
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=None)
        paper.update_task(
            h.db, task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        paper.append_bar(
            h.db, task_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 10_000.0, "balance": 10_000.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 1,
                        "last_bar_time": h.df.index[0].isoformat(),
                        "balance": 10_000.0})
        return task_id

    def test_legacy_symbol_outside_whitelist_to_error(self, tmp_path):
        """①非白名单 symbol（SOLUSDT）running 残行→startup_recover 后
        status=error+detail=symbol_not_supported_by_platform+数据保留。"""
        from core.paper_trading.runtime.scheduler import PaperScheduler

        h = _RecoveryHarness(tmp_path)
        tid = self._legacy_running_row(
            h, "pt_lgc00000001", symbol="SOLUSDT", timeframe="4h")
        scheduler = PaperScheduler(
            h.db, registry=h.registry, poll_interval=0.2)
        try:
            counts = scheduler.startup_recover()
            task = paper.get_task(h.db, tid)
            assert task["status"] == "error"
            assert "symbol_not_supported_by_platform" \
                in task["error_detail"]
            assert "SOLUSDT" in task["error_detail"]
            assert counts["untrusted"] == 1
            # 数据保留（转 error 不删除已产出明细）
            assert paper.list_equity_snapshots(
                h.db, tid, limit=10), "清场保留数据"
        finally:
            scheduler.stop()
            scheduler.join(timeout=5.0)

    def test_legacy_timeframe_outside_whitelist_to_error(self, tmp_path):
        """②非六档 timeframe（30m）running 残行→同上（枚举
        timeframe_not_supported_by_platform）。"""
        from core.paper_trading.runtime.scheduler import PaperScheduler

        h = _RecoveryHarness(tmp_path)
        tid = self._legacy_running_row(
            h, "pt_lgc00000002", symbol="BTCUSDT", timeframe="30m")
        scheduler = PaperScheduler(
            h.db, registry=h.registry, poll_interval=0.2)
        try:
            scheduler.startup_recover()
            task = paper.get_task(h.db, tid)
            assert task["status"] == "error"
            assert "timeframe_not_supported_by_platform" \
                in task["error_detail"]
            assert "30m" in task["error_detail"]
            # 数据保留双维补强（spec「转 error 保留已产出数据」——
            # timeframe 维与 symbol 维同判据）
            assert paper.list_equity_snapshots(
                h.db, tid, limit=10), "清场保留数据（timeframe 维）"
        finally:
            scheduler.stop()
            scheduler.join(timeout=5.0)

    def test_whitelisted_row_recovers_normally(self, tmp_path):
        """③白名单内 running 残行正常恢复续跑（清场不误伤——
        recovered 计数与 running 态维持）。"""
        from core.paper_trading.runtime.scheduler import PaperScheduler

        h = _RecoveryHarness(tmp_path)
        h.run_until_crash(_CRASH_AT)  # 白名单内可信残态（BTCUSDT/4h）
        scheduler = PaperScheduler(
            h.db, registry=h.registry, poll_interval=0.2)
        try:
            counts = scheduler.startup_recover()
            assert counts == {"recovered": 1, "untrusted": 0}
            assert paper.get_task(h.db, h.task_id)["status"] == "running"
            assert h.task_id in scheduler.status()["active_task_ids"]
        finally:
            scheduler.stop()
            scheduler.join(timeout=5.0)


class TestClearanceWriteFace:
    """C3-04/S-3：清场 update_task 返回值消费——零行命中（并发处置
    前态已非 running）warning 留痕，命中行 error 落地。"""

    def test_clearance_zero_rowcount_logs_warning(self, tmp_path, caplog):
        """守门拒写面：任务已被并发置终态（非 running）→清场 update
        零行命中→warning 留痕（不静默）；终态值不被清场覆写。"""
        import logging

        from core.paper_trading.runtime.recovery import recover_task

        h = _RecoveryHarness(tmp_path)
        logging.getLogger().addHandler(caplog.handler)  # fileConfig 坑位
        # running 残行构造后被并发置 stopped（前态守门：stopped→error
        # 非法迁移零行命中）
        paper.create_task(
            h.db, strategy_id="strat-x", task_id="pt_clw00000001",
            dna_snapshot_json=make_dna().to_json(),
            symbol="SOLUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        paper.update_task(
            h.db, "pt_clw00000001", status="running", started_at="t")
        # 落账户态使 validate_recovery 四判据通过（可达清场段）
        paper.append_bar(
            h.db, "pt_clw00000001", bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 10_000.0, "balance": 10_000.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 1,
                        "last_bar_time": h.df.index[0].isoformat(),
                        "balance": 10_000.0})
        # 并发置终态（前态守门：stopped→error 非法迁移零行命中）
        paper.update_task(
            h.db, "pt_clw00000001", status="stopped",
            stopped_at="t", stop_reason="user_stop")
        row = paper.get_task(h.db, "pt_clw00000001")

        with caplog.at_level(logging.WARNING, logger="paper_runtime"):
            result = recover_task(
                h.db, row, registry=h.registry, start_after=_REPLAY_ALL)
        assert result is None
        assert "清场未落地" in caplog.text, "零行命中 warning 留痕"
        task = paper.get_task(h.db, "pt_clw00000001")
        assert task["status"] == "stopped", "终态不被清场覆写"
        assert task["stop_reason"] == "user_stop"
