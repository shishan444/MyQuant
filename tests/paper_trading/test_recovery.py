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
            fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: self._funding(funding_times))
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.refresh()

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
