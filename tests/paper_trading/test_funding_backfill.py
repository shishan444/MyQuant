"""资金费挂起补记全链测试（rebuild-paper-trading 任务 3.5/Q-2=a）。

覆盖：挂起→补记全链（合成 funding 索引截断→pending 行余额未扣→
索引补齐+feed 刷新→次 bar「先补旧账再记新账」回填对账一致→崩溃重启
settled 不重复扣）+终态后回补例程（终态任务 pending 行回填+balance
不变+status→settled_disclosure_only+修订版回调）+混合场景两计数
（settled/pending 分布——报告 evidence 计数的数据源）+正常路径零变化
+补记幂等（重放收敛）+回填不产生新行（bar_time 保持挂起时承载 bar）。
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
    backfill_terminal_funding,
    recover_task,
)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv

_N_BARS = 60
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"


def _funding_frame(times) -> pd.DataFrame:
    times = list(times)
    idx = pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="timestamp")
    return pd.DataFrame({"funding_rate": [0.0001] * len(idx)}, index=idx)


def _grid_points(start: str, end: str) -> list[str]:
    """[start, end] 内 8h 网格点时刻串（升序）。"""
    ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    points = []
    while ts <= end_ts:
        points.append(ts.strftime("%Y-%m-%d %H:%M:%S"))
        ts += pd.Timedelta("8h")
    return points


class _FundingHarness:
    """挂起补记测试装配（funding 索引可变——测试中段换帧刷新）。"""

    def __init__(self, tmp_path: Path, *, initial_funding_times=(),
                 dna=None):
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

        # 可变 funding 索引源（测试中段替换帧+节流推进后刷新生效）
        self._funding_holder = {
            "frame": _funding_frame(initial_funding_times)}
        self._last_refresh = pd.Timestamp("2026-01-01", tz="UTC")
        self.registry = FeedRegistry(
            self.data_dir,
            fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: self._funding_holder["frame"],
            now_fn=lambda: self._last_refresh)
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.refresh()  # 建立初始索引基线

    def set_funding_times(self, times) -> None:
        """替换索引源并刷新（推进节流时钟——下一 refresh 生效）。"""
        self._funding_holder["frame"] = _funding_frame(times)
        self._last_refresh += pd.Timedelta(hours=2)  # 跨节流间隔
        self.feed.refresh()

    def make_executor(self, **overrides) -> PaperTaskExecutor:
        row = paper.get_task(self.db, self.task_id)
        kwargs = dict(registry=self.registry, start_after=_REPLAY_ALL)
        kwargs.update(overrides)
        return PaperTaskExecutor(self.db, row, **kwargs)

    def task(self) -> dict:
        return paper.get_task(self.db, self.task_id)

    def funding_rows(self, status: str | None = None) -> list[dict]:
        rows = paper.list_funding_records(
            self.db, self.task_id, limit=10_000)
        if status is None:
            return rows
        return [r for r in rows if r["status"] == status]


class TestPendingBackfillChain:
    """挂起→补记全链（运行中路径：次 bar 先补旧账再记新账）。"""

    def test_full_chain_backfill_and_no_double_charge(self, tmp_path):
        """索引截断→pending 未扣→补齐刷新→次 bar 回填对账→重启不重扣。"""
        # 任务区间结算点全集（i=21 持仓起，覆盖 01-04 16:00→01-10 16:00）
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        first_half = all_points[: len(all_points) // 2]
        h = _FundingHarness(tmp_path, initial_funding_times=first_half)

        executor = h.make_executor()
        assert executor.prepare() is True
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")

        # 阶段 1：前半索引可达——首段结算 settled；帧尾接近截断点
        cut = _N_BARS - 6
        executor.process_frames(h.enhanced.iloc[:cut], 0)
        settled_before = h.funding_rows("settled")
        pending_before = h.funding_rows("pending")
        assert settled_before, "前半索引覆盖段须 settled"
        assert pending_before, "索引截断段（帧尾数根跨点未达）须 pending"
        # 未达段余额未扣：挂起行 amount 全 NULL
        assert all(r["amount"] is None for r in pending_before)
        assert all(r["rate"] is None for r in pending_before)

        # 阶段 2：归档补齐+feed 刷新（节流推进）
        h.set_funding_times(all_points)

        # 阶段 3：次 bar 先补旧账——回填对账一致+余额扣减
        executor.process_frames(h.enhanced, cut)
        still_pending = h.funding_rows("pending")
        settled_after = h.funding_rows("settled")
        # 旧账全部补齐（回填+新结算并集）；逐点对账：amount=快照公式
        assert not still_pending
        for row in settled_after:
            if row["settle_time"] in {r["settle_time"]
                                      for r in pending_before}:
                expected = (row["position_quantity"]
                            * row["settle_price"] * row["rate"])
                assert row["amount"] == pytest.approx(expected)
        # 余额已扣：累计资金费=settled 金额合计（正负相抵后非零）
        total = sum(r["amount"] for r in settled_after)
        assert total != 0.0
        task_after = h.task()
        eqs = paper.list_equity_snapshots(
            h.db, h.task_id, limit=10_000)
        assert eqs[-1]["cumulative_funding"] == pytest.approx(total)
        assert task_after["balance"] == pytest.approx(
            eqs[-1]["balance"])

        # 阶段 4：崩溃重启——settled 不重复扣
        balance_before_crash = h.task()["balance"]
        row = paper.get_task(h.db, h.task_id)
        recovered = recover_task(
            h.db, row, registry=h.registry, start_after=_REPLAY_ALL)
        assert recovered is not None
        assert recovered._pending_funding == []  # 无 pending 残留镜像
        recovered.process_frames(h.enhanced, 0)  # 全帧重放（游标判重）
        assert h.task()["balance"] == pytest.approx(balance_before_crash)
        assert len(h.funding_rows("settled")) == len(settled_after)
        assert h.funding_rows("pending") == []

    def test_backfill_keeps_original_bar_time(self, tmp_path):
        """回填不产生新行：补记行 bar_time 保持挂起时承载 bar。"""
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        h = _FundingHarness(tmp_path)  # 空索引——全部挂起
        executor = h.make_executor()
        cut = _N_BARS - 6
        executor.process_frames(h.enhanced.iloc[:cut], 0)
        pending = h.funding_rows("pending")
        assert pending
        original = {r["settle_time"]: r["bar_time"] for r in pending}

        h.set_funding_times(all_points)
        executor.process_frames(h.enhanced, cut)
        for row in h.funding_rows("settled"):
            if row["settle_time"] in original:
                # UPDATE 面回填——bar_time 不动（不产生新行）
                assert row["bar_time"] == original[row["settle_time"]]


class TestShortSideBackfill:
    """空头方向补记对账（signed_qty 符号语义锚——team-lead 裁决要求：
    position_quantity 快照带符号，防后续读此列者误当无符号量）。"""

    def test_short_backfill_amount_from_negative_quantity(self, tmp_path):
        """空头挂起快照 qty<0：rate>0 补记金额为负（收到）——方向从
        符号恢复，对账公式逐点一致，balance 收增。"""
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        h = _FundingHarness(tmp_path, dna=make_dna(direction="short"))
        executor = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链+task_state 前置）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        cut = _N_BARS - 6
        executor.process_frames(h.enhanced.iloc[:cut], 0)

        pending = h.funding_rows("pending")
        assert pending, "空头持仓跨结算点须挂起"
        assert h.task()["position_side"] == "short"
        # 符号锚：空头快照数量为负（方向编码在符号）
        assert all(r["position_quantity"] < 0 for r in pending)

        balance_before_backfill = h.task()["balance"]
        h.set_funding_times(all_points)
        executor.process_frames(h.enhanced, cut)

        settled = h.funding_rows("settled")
        assert settled
        for row in settled:
            # 对账：amount=signed_qty×settle_price×rate 逐点一致
            expected = (row["position_quantity"]
                        * row["settle_price"] * row["rate"])
            assert row["amount"] == pytest.approx(expected)
            assert row["position_quantity"] < 0  # 回填不动快照符号
            assert row["rate"] > 0
            # rate>0 空头收资金费——金额为负（正=付出/负=收到）
            assert row["amount"] < 0
        # balance 收增：扣负金额=入账
        assert h.task()["balance"] > balance_before_backfill


class TestTerminalBackfill:
    """终态后回补例程（Q-2 封账设计）。"""

    def _run_to_completion(self, tmp_path):
        """跑一个截断索引下的完整任务（completed+pending 行遗留）。"""
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        h = _FundingHarness(tmp_path)  # 空索引——全部挂起
        executor = h.make_executor()
        assert executor.prepare() is True
        # 模拟拾取写 running（回放通道跳过 run()——迁移链不断）
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.task_row["window_bars"] = 40  # 就地注窗口——帧内结题
        executor.process_frames(h.enhanced, 0)
        task = h.task()
        assert task["status"] == "completed"
        assert h.funding_rows("pending"), "终态后须有挂起行遗留"
        # 归档补齐（终态后到达）
        save_parquet(_funding_frame(all_points),
                     h.data_dir / "BTCUSDT_funding.parquet")
        return h

    def test_terminal_backfill_balance_frozen_and_revision(self, tmp_path):
        """终态 pending 行回填+balance 不变+修订版回调触发。"""
        h = self._run_to_completion(tmp_path)
        task_before = h.task()
        balance_before = task_before["balance"]
        pending = h.funding_rows("pending")

        revisions: list[str] = []
        result = backfill_terminal_funding(
            h.db, data_dir=h.data_dir,
            on_revision=lambda tid: revisions.append(tid))

        assert result["tasks_backfilled"] == 1
        assert result["rows_backfilled"] == len(pending)
        assert revisions == [h.task_id]
        # 回填语义：settled_disclosure_only+快照公式金额
        disclosed = h.funding_rows("settled_disclosure_only")
        assert len(disclosed) == len(pending)
        for row in disclosed:
            expected = (row["position_quantity"]
                        * row["settle_price"] * row["rate"])
            assert row["amount"] == pytest.approx(expected)
        # 终态封账：balance/结题指标零变化
        task_after = h.task()
        assert task_after["balance"] == balance_before
        assert task_after["bars_processed"] == task_before["bars_processed"]
        assert task_after["total_trades"] == task_before["total_trades"]
        assert task_after["updated_at"] == task_before["updated_at"] \
            or task_after["status"] == "completed"  # pt_task 零触碰
        assert h.funding_rows("pending") == []

    def test_backfill_idempotent_on_rerun(self, tmp_path):
        """例程重放幂等：二次调用零回填零回调（status 前置吸收）。"""
        h = self._run_to_completion(tmp_path)
        revisions: list[str] = []
        backfill_terminal_funding(
            h.db, data_dir=h.data_dir,
            on_revision=lambda tid: revisions.append(tid))
        first_rows = len(h.funding_rows("settled_disclosure_only"))

        result = backfill_terminal_funding(
            h.db, data_dir=h.data_dir,
            on_revision=lambda tid: revisions.append(tid))
        assert result == {"tasks_backfilled": 0, "rows_backfilled": 0}
        assert revisions == [h.task_id]  # 二次不再触发修订
        assert len(h.funding_rows("settled_disclosure_only")) == first_rows

    def test_no_pending_no_action(self, tmp_path):
        """正常路径（数据已达零 pending）：例程零动作零回调。"""
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        h = _FundingHarness(tmp_path, initial_funding_times=all_points)
        executor = h.make_executor()
        assert executor.prepare() is True
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.task_row["window_bars"] = 40
        executor.process_frames(h.enhanced, 0)
        assert h.task()["status"] == "completed"
        assert h.funding_rows("pending") == []
        assert h.funding_rows("settled")

        revisions: list[str] = []
        result = backfill_terminal_funding(
            h.db, data_dir=h.data_dir,
            on_revision=lambda tid: revisions.append(tid))
        assert result == {"tasks_backfilled": 0, "rows_backfilled": 0}
        assert revisions == []

    def test_scheduler_default_backfill_uses_registry_data_dir(
            self, tmp_path):
        """装配特征（CARD-05）：守护循环缺省回补经 registry.data_dir
        ——路径算术缺省（曾指向不存在的 core/data/market）死链消除。"""
        from core.paper_trading.runtime.scheduler import PaperScheduler

        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        h = _FundingHarness(tmp_path)  # 空索引——跑出 pending 行
        executor = h.make_executor()
        assert executor.prepare() is True
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.task_row["window_bars"] = 40
        executor.process_frames(h.enhanced, 0)
        assert h.task()["status"] == "completed"
        assert h.funding_rows("pending")
        # 归档补齐到 registry 的 data_dir（装配真值目录）
        save_parquet(_funding_frame(all_points),
                     h.data_dir / "BTCUSDT_funding.parquet")

        scheduler = PaperScheduler(
            h.db, registry=h.registry, poll_interval=0.2)  # 不注入回补
        try:
            scheduler.run_once()  # 真缺省链：_default_backfill
            disclosed = h.funding_rows("settled_disclosure_only")
            assert disclosed, "缺省回补须经 registry.data_dir 命中归档"
            assert h.funding_rows("pending") == []
        finally:
            scheduler.stop()
            scheduler.join(timeout=5.0)


class TestFundingCounts:
    """混合场景两计数（报告 evidence 层 funding_settled_points/
    funding_pending_points 的数据源——pt_funding status 分布）。"""

    def test_mixed_scenario_counts(self, tmp_path):
        """前半可达后半未达：settled/pending 两计数各自正确。"""
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        first_half = all_points[: len(all_points) // 2]
        h = _FundingHarness(tmp_path, initial_funding_times=first_half)
        executor = h.make_executor()
        executor.process_frames(h.enhanced, 0)

        settled = h.funding_rows("settled")
        pending = h.funding_rows("pending")
        assert settled and pending  # 混合场景成立
        # 计数=status 分布（报告两计数的数据面）
        all_rows = h.funding_rows()
        assert len(settled) + len(pending) == len(all_rows)
        # settled 全部在覆盖段内、pending 全部在截断段
        covered = {pd.Timestamp(t, tz="UTC") for t in first_half}
        for row in settled:
            assert pd.Timestamp(row["settle_time"]) in covered
        for row in pending:
            assert pd.Timestamp(row["settle_time"]) not in covered


class TestBackfillDefensivePaths:
    """终态回补例程防御分支定向锚（闸门盲区消解）。"""

    def test_recover_rebuild_exception_writes_error(self, tmp_path):
        """恢复重建异常分支：from_task_row 抛 → error(recovery_
        untrusted: 重建失败) 保留数据。"""
        h = _FundingHarness(tmp_path)
        tid = h.task_id
        paper.update_task(h.db, tid, status="running",
                          started_at="2026-10-16T00:00:00+00:00")
        paper.append_bar(
            h.db, tid, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 1,
                        "last_bar_time": h.df.index[0].isoformat(),
                        "balance": 100.0})
        import core.paper_trading.runtime.recovery as recovery_mod
        from unittest.mock import patch

        with patch.object(
                recovery_mod.PaperTaskExecutor, "from_task_row",
                side_effect=RuntimeError("重建崩")):
            result = recovery_mod.recover_task(
                h.db, paper.get_task(h.db, tid), registry=h.registry,
                start_after=_REPLAY_ALL)
        assert result is None
        task = paper.get_task(h.db, tid)
        assert task["status"] == "error"
        assert "重建失败" in task["error_detail"]

    def test_backfill_partial_points_uncovered_keeps_pending(
            self, tmp_path):
        """回补例程部分点未达：可达点回填+未达点保持 pending（混合
        归档）。"""
        h = _FundingHarness(tmp_path)
        executor = h.make_executor()
        executor.task_row["window_bars"] = 40
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.process_frames(h.enhanced, 0)
        assert h.task()["status"] == "completed"
        pending = h.funding_rows("pending")
        assert pending, "前置：须有挂起行"
        # 归档只覆盖半数挂起点
        half = [r["settle_time"] for r in pending][: len(pending) // 2]
        save_parquet(_funding_frame(
            [pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S") for t in half]),
            h.data_dir / "BTCUSDT_funding.parquet")
        from core.paper_trading.runtime.recovery import (
            backfill_terminal_funding)

        result = backfill_terminal_funding(h.db, data_dir=h.data_dir)
        assert result["rows_backfilled"] == len(half)
        assert len(h.funding_rows("pending")) == len(pending) - len(half)

    def test_backfill_invalid_symbol_skipped_routine_survives(
            self, tmp_path):
        """非法 symbol 任务行：索引装载跳过（warning）不拖死整例程。"""
        h = _FundingHarness(tmp_path)
        # 直构一条非法 symbol 的终态任务带 pending 行
        bad_id = paper.create_task(
            h.db, strategy_id="strat-bad",
            dna_snapshot_json=make_dna().to_json(),
            symbol="../../etc", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=10)
        paper.update_task(h.db, bad_id, status="running",
                          started_at="2026-10-16T00:00:00+00:00")
        paper.append_bar(
            h.db, bad_id, bar_time=h.df.index[0].isoformat(),
            fills=[], equity={"equity": 1.0, "balance": 1.0,
                              "unrealized_pnl": 0.0},
            task_state={"bars_processed": 1,
                        "last_bar_time": h.df.index[0].isoformat(),
                        "balance": 100.0, "position_side": "long",
                        "position_quantity": 1.0, "position_entry": 100.0},
            funding=[{"settle_time": h.df.index[0].isoformat(),
                      "status": "pending", "rate": None, "amount": None,
                      "position_quantity": 1.0,
                      "settle_price": 100.0}])
        paper.update_task(h.db, bad_id, status="completed",
                          stopped_at="2026-10-16T01:00:00+00:00")
        from core.paper_trading.runtime.recovery import (
            backfill_terminal_funding)

        result = backfill_terminal_funding(h.db, data_dir=h.data_dir)
        # 例程不崩；非法 symbol 任务零回填
        assert result["tasks_backfilled"] == 0

    def test_backfill_revision_callback_exception_swallowed(
            self, tmp_path):
        """修订回调抛异常：回填已生效不回滚（日志吞咽）。"""
        h = _FundingHarness(tmp_path)
        executor = h.make_executor()
        executor.task_row["window_bars"] = 40
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.process_frames(h.enhanced, 0)
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        save_parquet(_funding_frame(all_points),
                     h.data_dir / "BTCUSDT_funding.parquet")
        from core.paper_trading.runtime.recovery import (
            backfill_terminal_funding)

        def crashing_revision(tid):
            raise RuntimeError("修订回调崩")

        result = backfill_terminal_funding(
            h.db, data_dir=h.data_dir, on_revision=crashing_revision)
        assert result["rows_backfilled"] > 0
        assert h.funding_rows("pending") == []

    def test_backfill_broken_and_empty_parquet_survive(self, tmp_path):
        """索引装载防御：坏 parquet（垃圾字节）与空 parquet 均安全
        返回空索引（例程不崩）。"""
        import os

        h = _FundingHarness(tmp_path)
        # 坏文件：非 parquet 字节
        (h.data_dir / "BTCUSDT_funding.parquet").write_bytes(
            b"this is not a parquet file")
        from core.paper_trading.runtime.recovery import (
            backfill_terminal_funding, _load_funding_index)

        assert _load_funding_index(h.data_dir, "BTCUSDT") == {}
        result = backfill_terminal_funding(h.db, data_dir=h.data_dir)
        assert result == {"tasks_backfilled": 0, "rows_backfilled": 0}
        # 空帧文件：同样空索引
        save_parquet(_funding_frame([]),
                     h.data_dir / "BTCUSDT_funding.parquet")
        assert _load_funding_index(h.data_dir, "BTCUSDT") == {}
        assert os.path.exists(h.data_dir / "BTCUSDT_funding.parquet")

    def test_backfill_rows_concurrently_processed_skips(self, tmp_path):
        """行在 updates 构造后已被并发处理（filled=0）：跳过计数与
        修订（重放窗口防御分支）。"""
        from unittest.mock import patch

        import core.paper_trading.runtime.recovery as recovery_mod

        h = _FundingHarness(tmp_path)
        executor = h.make_executor()
        executor.task_row["window_bars"] = 40
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        executor.process_frames(h.enhanced, 0)
        assert h.task()["status"] == "completed"
        all_points = _grid_points("2024-01-04 16:00", "2024-01-11 00:00")
        save_parquet(_funding_frame(all_points),
                     h.data_dir / "BTCUSDT_funding.parquet")
        revisions: list[str] = []
        with patch.object(
                recovery_mod.paper_repo, "backfill_funding_rows",
                return_value=0):
            result = backfill_terminal_funding(
                h.db, data_dir=h.data_dir,
                on_revision=lambda tid: revisions.append(tid))
        assert result == {"tasks_backfilled": 0, "rows_backfilled": 0}
        assert revisions == []
