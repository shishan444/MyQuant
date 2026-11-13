"""任务执行器测试（rebuild-paper-trading 任务 3.2）。

覆盖：历史 DataFrame 回放驱动完整任务（八步序产出 fills/equity/funding
落库+seq 审计编号+窗口到点自动 completed+信号次根开盘执行 shift(1)）+
stop 在 bar 边界协作退出（当前 bar 完整落库后）+装载校验三拒绝面
（no_data/atr_missing/indicators_unavailable/invalid_dna——error 不进
running）+权益逐 bar 快照+资金费基础面（数据已达 settled 直扣/未达
pending 快照不扣）。
回放通道：process_frames 注入（合成/历史 DataFrame 逐根驱动，不经
真实时钟与 feed 轮询——结构性前提）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.concurrency.task_control import TaskController
from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import (
    make_dna, make_ohlcv, make_signal_gene,
)

# 合成数据基线（seed 固定——信号分布确定：RSI 首次 <30 于 i=20）
_N_BARS = 200
_SEED = 7
# 全回放边界（合成帧历史时刻均早于它——旁路 created_at 起始过滤）
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"

# kind → bar 内 seq 期望映射（八步序审计编号锚）
_SEQ_BY_KIND = {
    "exit_liquidation": 1,
    "exit_sl": 2, "exit_tp": 2,
    "close": 3, "exit_max_hold": 3, "exit_time": 3, "exit_max_loss": 3,
    "open": 4, "add": 4, "reduce": 4,
}


def _funding_frame(times: list[str], rate: float = 0.0001) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="timestamp")
    return pd.DataFrame({"funding_rate": [rate] * len(idx)}, index=idx)


class _Harness:
    """执行器回放测试装配（库+任务行+registry+帧）。"""

    def __init__(self, tmp_path: Path, *, dna=None, window_bars=50,
                 stop_loss_pct=None, funding_times=(), leverage=1,
                 initial_cash=10_000.0):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=_N_BARS, freq="4h", seed=_SEED,
                             base_price=100.0)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.enhanced = compute_all_indicators(self.df)

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        dna = dna or make_dna()
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=initial_cash, leverage=leverage,
            window_bars=window_bars, stop_loss_pct=stop_loss_pct)

        self.registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame(funding_times))
        # 预建带 funding 索引的 feed（executor 查费率面；dummy 订阅防回收）
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.reconcile()  # funding 索引基线
        self.pushes: list[dict] = []
        self.concluded: list[tuple[str, str]] = []

    def make_executor(self, **overrides) -> PaperTaskExecutor:
        row = paper.get_task(self.db, self.task_id)
        kwargs = dict(
            registry=self.registry,
            push=lambda tid, p: self.pushes.append(p),
            on_concluded=lambda tid, st: self.concluded.append((tid, st)),
            start_after=_REPLAY_ALL,
        )
        kwargs.update(overrides)
        return PaperTaskExecutor(self.db, row, **kwargs)

    def task(self) -> dict:
        return paper.get_task(self.db, self.task_id)


class TestFullReplay:
    """历史 DataFrame 回放驱动完整任务（八步序落库全景）。"""

    def test_full_task_replay_persists_all_tables(self, tmp_path):
        """回放完整任务：fills/equity/funding 落库+窗口到点 completed。"""
        h = _Harness(tmp_path, funding_times=[
            "2024-01-04 16:00:00.001", "2024-01-05 00:00:00.002"])
        ex = h.make_executor()
        assert ex.prepare() is True
        # 模拟拾取写 running（回放通道跳过 run()——迁移链不断：
        # pending→running→completed，默认守门时代直跳会被拒）
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)

        task = h.task()
        assert task["status"] == "completed"  # 窗口到点自动结题
        assert task["bars_processed"] == 50  # window_bars=50 恰到
        assert task["stopped_at"] is not None
        assert task["total_trades"] >= 1

        fills = paper.list_fills(h.db, h.task_id, limit=1000)
        assert fills, "回放须产出成交"
        eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        assert len(eqs) == task["bars_processed"]  # 逐 bar 快照
        fundings = paper.list_funding_records(h.db, h.task_id, limit=1000)
        assert fundings, "持仓跨结算点须落资金费"

    def test_seq_audit_numbering_matches_eight_step(self, tmp_path):
        """seq 审计编号符合八步序（清算=1/SL·TP=2/出场=3/入场族=4）。"""
        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex.process_frames(h.enhanced, 0)
        for f in paper.list_fills(h.db, h.task_id, limit=1000):
            assert f["seq"] == _SEQ_BY_KIND[f["kind"]], f

    def test_signal_executed_next_bar_open(self, tmp_path):
        """信号次根开盘执行（shift(1)）：成交 bar=信号 bar 的次根。"""
        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex.process_frames(h.enhanced, 0)

        from core.strategy.executor import dna_to_signal_set

        sig = dna_to_signal_set(make_dna(), h.enhanced)
        first_signal = next(
            i for i in range(len(h.enhanced))
            if bool(sig.entries.iloc[i]))
        first_fill = paper.list_fills(
            h.db, h.task_id, limit=1)[0]
        assert first_fill["kind"] == "open"
        assert first_fill["bar_time"] == h.enhanced.index[
            first_signal + 1].isoformat()  # 次根开盘（非信号根收盘）

    def test_equity_snapshot_fields(self, tmp_path):
        """权益快照逐 bar 落库（balance/unrealized_pnl/持仓侧/累计费）。"""
        h = _Harness(tmp_path, funding_times=["2024-01-04 16:00:00"])
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链+task_state 前置）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = h.task()
        eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        last = eqs[-1]
        assert last["bar_time"] == task["last_bar_time"]
        assert last["balance"] == pytest.approx(task["balance"])
        assert last["position_side"] == task["position_side"]
        settled = [f for f in paper.list_funding_records(
            h.db, h.task_id, limit=1000) if f["status"] == "settled"]
        assert settled, "数据已达结算点须 settled"
        # 持仓 bar 的快照携带持仓侧；累计资金费=settled 金额合计
        assert any(e["position_side"] == "long" for e in eqs)


class TestNanBarSkip:
    """CARD-01 锚 A：NaN/inf bar 装载点跳过与窗口顺延语义。

    卡面契约：OHLC 任一非 finite（NaN/inf）判该 bar 无效——不成交、
    不落库、last_bar_time 不推进；NaN bar 不占 bars_processed 计数，
    任务顺延消费后续 bar 凑满 window_bars。验证形制=基线（干净帧）
    vs 污染帧（窗口内三根污染 bar：四价全 NaN/close 单值 NaN/high=inf
    三种形制各一）的快照集合差集精确断言——期望从窗口语义独立推导
    （idx 0..52 共 53 根去 3 污染根=50 有效根、末根 idx 52），不依赖
    seed 的信号分布。
    """

    def test_nan_bars_skipped_window_fills_with_valid_bars(self, tmp_path):
        """三根污染 bar 全跳过：bars_processed 仍 50（有效根口径）、
        快照差集恰为 −{30,40,45}+{50,51,52}、污染根无成交、落库数据
        全 finite、窗口凑满正常结题。"""
        for sub in ("clean", "dirty"):  # _Harness 的 data_dir 非递归建
            (tmp_path / sub).mkdir()
        h = _Harness(tmp_path / "clean")
        ex = h.make_executor()
        assert ex.prepare() is True
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)  # 基线：干净帧
        base_eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        assert len(base_eqs) == 50

        h2 = _Harness(tmp_path / "dirty")
        dirty = h2.enhanced.copy()
        for col in ("open", "high", "low", "close"):
            dirty.iloc[30, dirty.columns.get_loc(col)] = float("nan")
        dirty.iloc[40, dirty.columns.get_loc("close")] = float("nan")
        dirty.iloc[45, dirty.columns.get_loc("high")] = float("inf")
        ex2 = h2.make_executor()
        assert ex2.prepare() is True
        paper.update_task(h2.db, h2.task_id, status="running",
                          started_at="2026-10-16T00:00:00+00:00")
        ex2.process_frames(dirty, 0)

        task = h2.task()
        assert task["status"] == "completed"  # 顺延凑满后正常结题
        assert task["bars_processed"] == 50  # 污染根不占窗口计数
        eqs2 = paper.list_equity_snapshots(h2.db, h2.task_id, limit=1000)
        assert len(eqs2) == 50  # 逐有效 bar 快照（污染根不落库）
        base_times = {e["bar_time"] for e in base_eqs}
        dirty_times = {e["bar_time"] for e in eqs2}
        expect_removed = {h2.enhanced.index[k].isoformat()
                          for k in (30, 40, 45)}
        expect_added = {h2.enhanced.index[k].isoformat()
                        for k in (50, 51, 52)}
        assert dirty_times == (base_times - expect_removed) | expect_added, (
            f"快照差集异常: +{sorted(dirty_times - base_times)} "
            f"-{sorted(base_times - dirty_times)}")
        # 污染根上无成交（不成交面）
        fills = paper.list_fills(h2.db, h2.task_id, limit=1000)
        assert {f["bar_time"] for f in fills} & expect_removed == set()
        # 落库数据无 NaN/inf 侧写（不落库+不污染）
        for e in eqs2:
            assert math.isfinite(float(e["equity"])), e
        for f in fills:
            assert math.isfinite(float(f["price"])), f
            assert math.isfinite(float(f["quantity"])), f


class TestFundingSettlement:
    """资金费正常路径（数据已达零变化——Q-2 未暂停面）。"""

    def test_settled_amount_formula(self, tmp_path):
        """settled 行金额=数量×结算价×费率×方向符号（多头 rate>0 付）。"""
        h = _Harness(tmp_path, funding_times=["2024-01-04 16:00:00.001"])
        ex = h.make_executor()
        ex.process_frames(h.enhanced, 0)
        row = next(
            f for f in paper.list_funding_records(h.db, h.task_id, limit=1000)
            if f["status"] == "settled")
        assert row["rate"] == 0.0001
        expected = row["position_quantity"] * row["settle_price"] * row["rate"]
        assert row["amount"] == pytest.approx(expected)  # 多头付出（正）

    def test_uncovered_point_pends_without_deduction(self, tmp_path):
        """索引未达结算点：pending 行（rate/amount NULL+快照）且余额不扣。"""
        h_no_funding = _Harness(tmp_path)  # 空索引——全部挂起
        ex = h_no_funding.make_executor()
        ex.process_frames(h_no_funding.enhanced, 0)
        rows = paper.list_funding_records(
            h_no_funding.db, h_no_funding.task_id, limit=1000)
        assert rows
        assert all(r["status"] == "pending" for r in rows)
        assert all(r["rate"] is None and r["amount"] is None for r in rows)
        # 挂起期间 balance 未被扣（equity 快照无资金费分量）
        assert all(
            e["cumulative_funding"] == 0.0
            for e in paper.list_equity_snapshots(
                h_no_funding.db, h_no_funding.task_id, limit=1000))


class TestStopBoundary:
    """stop 协作退出（bar 边界——当前 bar 完整落库后）。"""

    def test_stop_exits_at_bar_boundary_current_bar_complete(self, tmp_path):
        """第 k 次 bar 前置检查请求 stop → 前 k−1 根完整落库后退出。"""
        h = _Harness(tmp_path, window_bars=None)
        stop_after = 10

        class StopAfter(TaskController):
            def __init__(self, n):
                super().__init__()
                self._n, self._calls = n, 0

            def check_stop(self):
                self._calls += 1
                if self._calls >= self._n:
                    self.request_stop()
                super().check_stop()

        controller = StopAfter(stop_after)
        ex = h.make_executor(controller=controller)
        paper.update_task(  # 模拟拾取写 running（迁移链+task_state 前置）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)

        task = h.task()
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "user_stop"
        assert task["bars_processed"] == stop_after - 1  # 前 9 根处理
        eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=1000)
        assert len(eqs) == stop_after - 1  # 每根完整落库（无半 bar）
        assert eqs[-1]["bar_time"] == task["last_bar_time"]

    def test_stopped_pushes_ws_and_concludes(self, tmp_path):
        """终态 WS 推送+结题回调（组 4 固化挂点）。"""
        h = _Harness(tmp_path, window_bars=None)
        ex = h.make_executor()
        ex.controller.request_stop()
        ex.process_frames(h.enhanced, 0)  # 首根前停——bar 边界协作退出内收

        task = h.task()
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "user_stop"
        assert any(p["type"] == "task_stopped" and
                   p["stop_reason"] == "user_stop" for p in h.pushes)
        assert h.concluded == [(h.task_id, "stopped")]


class TestPrepareValidation:
    """装载三就绪校验（不可就绪→error 不进 running）。"""

    def test_no_data_rejected(self, tmp_path):
        """执行周期 parquet 不存在→error(no_data)，不进 running。"""
        h = _Harness(tmp_path)
        (h.data_dir / "BTCUSDT_4h.parquet").unlink()
        ex = h.make_executor()
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "no_data" in task["error_detail"]
        assert task["started_at"] is None  # 不进 running

    def test_atr_missing_rejected(self, tmp_path):
        """atr 模式 ATR 列不可产出（数据短于周期）→error(atr_missing)。"""
        from tests.helpers.data_factory import make_signal_gene
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalRole, StrategyDNA,
        )

        dna = StrategyDNA(
            signal_genes=[
                make_signal_gene(role=SignalRole.ENTRY_TRIGGER),
                make_signal_gene(
                    role=SignalRole.EXIT_TRIGGER,
                    condition_type="gt", condition_value=70),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h",
                                           symbol="BTCUSDT"),
            risk_genes=RiskGenes(
                stop_loss=1.0, take_profit=3.0, position_size=0.5,
                leverage=1, direction="long", sl_mode="atr",
                atr_period=20),
        )
        h = _Harness(tmp_path, dna=dna)
        # 覆盖为 15 根短数据（<atr_period 20——现算不可得）
        short = make_ohlcv(n=15, freq="4h", seed=_SEED, base_price=100.0)
        save_parquet(short, h.data_dir / "BTCUSDT_4h.parquet")
        ex = h.make_executor()
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "atr_missing" in task["error_detail"]

    def test_indicators_unavailable_rejected(self, tmp_path):
        """指标不可算（缺 close 列——DNA 声明指标无法产出）→error。"""
        h = _Harness(tmp_path)
        # 只含 open 列的残缺帧：RSI（消费 close）无法产出——信号试算失败
        broken = pd.DataFrame(
            {"open": [100.0] * 50},
            index=pd.DatetimeIndex(
                pd.date_range("2024-01-01", periods=50, freq="4h", tz="UTC"),
                name="timestamp"))
        save_parquet(broken, h.data_dir / "BTCUSDT_4h.parquet")
        ex = h.make_executor()
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "indicators_unavailable" in task["error_detail"]

    def test_unknown_logic_dna_rejected_at_mount(self, tmp_path):
        """R-7 冒泡面③④（fix-silent-degradation）：非法 logic DNA
        装载期即拒——SEC-3（审查批）起通道前移：装载期 validate_dna
        值域拒绝（invalid_dna 通道，原为信号试算 indicators_unavailable
        通道），运行期实际不可达（数据完整、异常源是 logic 值域）。"""
        dna = make_dna()
        dna.logic_genes.entry_logic = "XOR"
        h = _Harness(tmp_path, dna=dna)
        full = make_ohlcv(n=50, freq="4h", seed=_SEED, base_price=100.0)
        save_parquet(full, h.data_dir / "BTCUSDT_4h.parquet")
        ex = h.make_executor()
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "invalid_dna" in task["error_detail"]
        assert "entry_logic" in task["error_detail"]

    def test_invalid_dna_rejected(self, tmp_path):
        """非法 DNA 组合（risk_budget 缺 risk_fraction）装载拒绝。"""
        dna = make_dna()
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = None  # 残缺组合
        h = _Harness(tmp_path, dna=dna)
        ex = h.make_executor()
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "invalid_dna" in task["error_detail"]

    def test_prepare_ok_marks_nothing(self, tmp_path):
        """就绪时 prepare 不写状态（running 由 run() 拾取后置）。"""
        h = _Harness(tmp_path)
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] == "pending"


class TestPrepareEscapeGuard:
    """prepare 裸异常防逃逸（CARD-02：逃逸击穿工作线程→任务永留
    pending 每 2s 无限重拾——兜底转 prepare_failed error）。"""

    def test_corrupt_parquet_rejected_not_looping(self, tmp_path):
        """坏 parquet（load 崩溃）→prepare_failed error（不逃逸）。"""
        h = _Harness(tmp_path)
        corrupt = h.data_dir / "BTCUSDT_4h.parquet"
        corrupt.unlink()
        corrupt.write_text("not a parquet at all")  # 坏文件
        ex = h.make_executor()
        assert ex._prepare_guarded() is False
        task = h.task()
        assert task["status"] == "error"
        assert "prepare_failed" in task["error_detail"]

    def test_refresh_exception_rejected_not_looping(self, tmp_path):
        """feed.refresh 抛错（MTF 组装期）→prepare_failed error。"""
        from tests.helpers.data_factory import make_mtf_dna

        h = _Harness(tmp_path)
        daily = make_ohlcv(n=60, freq="1d", seed=7, base_price=100.0)
        save_parquet(daily, h.data_dir / "BTCUSDT_1d.parquet")

        def broken_reconcile(self):
            raise RuntimeError("reconcile exploded")

        h.registry = FeedRegistry(
            h.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame([]))
        # feed.reconcile 打补丁为抛错（模拟对账通道故障）
        from core.paper_trading.runtime.feed import SymbolFeed

        original = SymbolFeed.reconcile
        SymbolFeed.reconcile = broken_reconcile
        try:
            h.task_id = paper.create_task(
                h.db, strategy_id="strat-mtf",
                dna_snapshot_json=make_mtf_dna(
                    timeframes=("1d", "4h")).to_json(),
                symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified",
                initial_cash=10_000.0, leverage=1, window_bars=30)
            ex = h.make_executor()
            assert ex._prepare_guarded() is False
        finally:
            SymbolFeed.reconcile = original
        task = h.task()
        assert task["status"] == "error"
        assert "prepare_failed" in task["error_detail"]


class TestMtfPrepare:
    """MTF 装载校验面（修复卡 P0②——8.2 实测：试算传 None 对 MTF DNA
    恒拒；校验序=先组装（缺周期→mtf_data_missing 拒）再试算）。"""

    def _make_mtf_harness(self, tmp_path: Path, *, with_aux: bool):
        """MTF 装配（执行周期 4h 主帧+1d 辅帧按 interval 分派）。"""
        h = _Harness(tmp_path)
        daily = make_ohlcv(n=60, freq="1d", seed=7, base_price=100.0)
        if with_aux:
            save_parquet(daily, h.data_dir / "BTCUSDT_1d.parquet")
        h.registry = FeedRegistry(
            h.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame([]))
        return h

    def test_mtf_dna_prepare_passes(self, tmp_path):
        """合法 MTF DNA+双周期就位：装载校验通过（不再恒拒）。"""
        from tests.helpers.data_factory import make_mtf_dna

        h = self._make_mtf_harness(tmp_path, with_aux=True)
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-mtf",
            dna_snapshot_json=make_mtf_dna(
                timeframes=("1d", "4h")).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        ex = h.make_executor()
        assert ex.prepare() is True
        assert h.task()["status"] == "pending"  # 拒绝才写 error

    def test_mtf_aux_missing_rejected(self, tmp_path):
        """辅周期数据缺失：mtf_data_missing 语义拒（非
        indicators_unavailable——校验序先组装后试算）。"""
        from tests.helpers.data_factory import make_mtf_dna

        h = self._make_mtf_harness(tmp_path, with_aux=False)  # 无 1d
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-mtf",
            dna_snapshot_json=make_mtf_dna(
                timeframes=("1d", "4h")).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        ex = h.make_executor()
        assert ex.prepare() is False
        task = h.task()
        assert task["status"] == "error"
        assert "mtf_data_missing" in task["error_detail"]
        assert "indicators_unavailable" not in task["error_detail"]


class TestRiskBudgetAtrWiring:
    """串联层 entry_atr 接线锚（修复卡缺陷③：kernel 签名有参数面而
    executor 漏接——atr 档 risk_budget 距离恒 NaN 走 (0,0) 防御回退
    fixed；恒等式断言防层间再漏接线）。"""

    def test_atr_risk_budget_identity_on_executor_chain(self, tmp_path):
        """atr 档 risk_budget 定仓恒等式（executor 全链）：
        qty×(sl_mult×执行 bar ATR)=权益×risk_fraction。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalRole, StrategyDNA,
        )
        from tests.helpers.data_factory import make_signal_gene

        dna = StrategyDNA(
            signal_genes=[
                make_signal_gene(role=SignalRole.ENTRY_TRIGGER),
                make_signal_gene(
                    role=SignalRole.EXIT_TRIGGER,
                    condition_type="gt", condition_value=70),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h",
                                           symbol="BTCUSDT"),
            risk_genes=RiskGenes(
                stop_loss=2.0, take_profit=4.0, position_size=0.5,
                leverage=1, direction="long", sl_mode="atr",
                atr_period=14, position_mode="risk_budget",
                risk_fraction=0.01),
        )
        h = _Harness(tmp_path, dna=dna)
        ex = h.make_executor()
        assert ex.prepare() is True
        ex.process_frames(h.enhanced, 0)

        open_fills = [f for f in paper.list_fills(
            h.db, h.task_id, limit=1000) if f["kind"] == "open"]
        assert open_fills, "atr 档 risk_budget DNA 须能开仓"
        first = open_fills[0]
        exec_idx = h.enhanced.index.get_loc(
            pd.Timestamp(first["bar_time"]))
        entry_atr = float(h.enhanced["atr_14"].iloc[exec_idx])
        assert entry_atr > 0.0  # 执行 bar ATR 有效（防守卫路径误判）
        # 恒等式（未截断：cap=10000×min(1,1.0) 远大于名义）：
        # qty×(sl_mult×ATR)=equity×risk_fraction=10000×0.01
        assert first["quantity"] * (2.0 * entry_atr) == \
            pytest.approx(10_000.0 * 0.01, rel=1e-9)

    def test_closed_bar_atr_column_missing_yields_none(self, tmp_path):
        """ATR 模式运行帧无 atr 列防御（闸门盲区消解）：装载校验用
        parquet 全帧（atr 列可产出故放行），运行帧可为纯 K 线（feed
        帧无指标列）——_closed_bar 的 atr 取值列缺失分支：atr=None
        不炸（下游 risk_budget 距离拒绝链与 _initial_stop 防御承接）。"""
        dna = make_dna()
        dna.risk_genes.sl_mode = "atr"
        dna.risk_genes.atr_period = 14
        dna.risk_genes.stop_loss = 2.0  # atr 档语义=sl_mult（同上方接线锚）
        dna.risk_genes.take_profit = 4.0  # atr 倍数值域（0.5-5.0 且>sl）
        h = _Harness(tmp_path, dna=dna, window_bars=10)
        ex = h.make_executor()
        assert ex.prepare() is True  # 装载全帧 atr_14 在列
        bar = ex._closed_bar(h.df, 5)  # 纯 K 线帧（无 atr_14 列）
        assert bar.atr is None


class TestRunLifecyclePaths:
    """run() 真线程生命周期/终态路径/装载拒绝细分定向锚（闸门盲区
    消解——CAS 放弃启动、空转协作停、error 终态推送、finalize 幂等
    与回调异常吞咽、MTF 订阅退订段）。

    闸门不可达判定（阶段四步骤 2，调用图依据锚定）：
    - executor.py:502（_prepare_guarded 的 TaskStopRequested 透传
      raise）：TaskStopRequested 唯一抛出源 controller.check_stop
      仅两处（_loop 空闲期 779/bar 边界 861），均在 prepare() 返回
      之后——prepare 链无抛出点，except 永不命中（防御性透传，为
      未来 prepare 加 stop 协作保留语义）；
    - executor.py:760（run() 层 TaskStopRequested 收口）：两抛出点
      均被内层 except 捕获收口（779→_loop 层 783；861→process_frames
      层 finalize+return），异常不冒泡至 run() 层 except（双保险
      防御层）；
    - executor.py:1403→1405（_finalize 非终态 status 分支）：
      _finalize 全调用点枚举（760/763/783/866/869/873）status 均为
      TERMINAL_STATUSES 成员——终态写入单点无非终态调用面。"""

    def test_intents_json_empty_values_roundtrip(self):
        """空意图序列化：to_json([])→None、from_json(None)→[]。"""
        from core.paper_trading.runtime.executor import (
            intent_to_json, intents_from_json)

        assert intent_to_json([]) is None
        assert intents_from_json(None) == []

    def test_run_cas_zero_hit_abandons_startup(self, tmp_path):
        """run() CAS 零命中放弃启动（缺陷⑤防线行为锚）：启动前任务
        已被并发 stop 直迁终态 → 不覆写不推送，终局承诺保持。"""
        h = _Harness(tmp_path)
        ex = h.make_executor()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-10-16T00:00:00+00:00")
        paper.update_task(h.db, h.task_id, status="stopped",
                          stopped_at="2026-10-16T00:00:01+00:00",
                          stop_reason="user_stop")
        ex.run()
        task = h.task()
        assert task["status"] == "stopped"  # 不被 run 覆写回 running
        assert not any(p["type"] == "task_started" for p in h.pushes)

    def test_run_precollapsed_stop_exits_idle_loop_stopped(
            self, tmp_path):
        """预置 stop 的 run()：_loop 空转（queue.Empty 分支）协作停
        → stopped(user_stop)——空闲期退出锚的线程形态。"""
        h = _Harness(tmp_path, window_bars=None)
        ex = h.make_executor()
        ex.controller.request_stop()
        ex.run()
        task = h.task()
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "user_stop"
        assert any(p["type"] == "task_stopped" for p in h.pushes)
        assert h.concluded == [(h.task_id, "stopped")]

    def test_run_bar_exception_finalizes_error_with_push(self, tmp_path):
        """bar 处理抛普通异常 → error 终态+error_detail 记因+
        task_error 推送+结题回调异常吞咽（不回滚终态）。"""
        from unittest.mock import MagicMock

        h = _Harness(tmp_path, window_bars=None)
        boom = RuntimeError("bar 处理崩")

        def crashing_concluded(tid, st):
            raise RuntimeError("结题回调崩")

        ex = h.make_executor(on_concluded=crashing_concluded)
        ex.process_frames = MagicMock(side_effect=boom)
        ex._frame_queue.put((h.enhanced, 0))
        ex.run()
        task = h.task()
        assert task["status"] == "error"
        assert "RuntimeError" in task["error_detail"]
        assert any(p["type"] == "task_error" for p in h.pushes)

    def test_run_idle_spin_then_cooperative_stop(self, tmp_path):
        """空转拍（queue.Empty 且未 stop → check_stop 不抛 → continue
        轮询下一拍）后协作停（闸门盲区消解——预置 stop 形态走不到
        continue 分支：check_stop 即抛 TaskStopRequested）。"""
        import threading
        import time

        h = _Harness(tmp_path, window_bars=None)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        t = threading.Thread(target=ex.run, daemon=True)
        t.start()
        time.sleep(4.5)  # ≥2 拍 POLL_INTERVAL（2.0s）空转
        ex.controller.request_stop()
        t.join(timeout=10.0)
        assert not t.is_alive(), "协作停须收敛退出"
        task = h.task()
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "user_stop"

    def test_finalize_idempotent_second_call_noop(self, tmp_path):
        """_finalize 幂等：二次调用零效果（终态首写保持）。"""
        h = _Harness(tmp_path)
        ex = h.make_executor()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-10-16T00:00:00+00:00")
        ex._finalize("stopped", stop_reason="user_stop")
        ex._finalize("completed")
        assert h.task()["status"] == "stopped"

    def test_invalid_symbol_rejected_at_prepare(self, tmp_path):
        """装载拒绝细分：symbol 白名单外（路径注入形）→ invalid_symbol
        （CARD-01② 直拼前白名单防线）。"""
        h = _Harness(tmp_path)
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-evil",
            dna_snapshot_json=make_dna().to_json(),
            symbol="../../etc", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "invalid_symbol" in h.task()["error_detail"]

    def test_indicator_crash_rejected_as_unavailable(self, tmp_path):
        """装载拒绝细分：指标计算崩溃（垃圾 parquet 可载但不可算）
        → indicators_unavailable。"""
        import pandas as pd

        h = _Harness(tmp_path)
        broken = pd.DataFrame(
            {"close": ["not-a-number"] * 60},
            index=pd.DatetimeIndex(
                pd.date_range("2024-01-01", periods=60, freq="4h", tz="UTC"),
                name="timestamp"))
        save_parquet(broken, h.data_dir / "BTCUSDT_4h.parquet")
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "indicators_unavailable" in h.task()["error_detail"]

    def test_mtf_subscribe_unsubscribe_lifecycle(self, tmp_path):
        """MTF 订阅/退订段：辅周期 feed 订阅登记+退订清空（引用计数
        协作面，不起线程直调）。"""
        from tests.helpers.data_factory import make_mtf_dna

        h = _Harness(tmp_path)
        daily = make_ohlcv(n=60, freq="1d", seed=7, base_price=100.0)
        save_parquet(daily, h.data_dir / "BTCUSDT_1d.parquet")
        h.registry = FeedRegistry(
            h.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame([]))
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-mtf",
            dna_snapshot_json=make_mtf_dna(
                timeframes=("1d", "4h")).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        ex = h.make_executor()
        assert ex.prepare() is True
        ex.subscribe_feeds()
        assert h.registry.get("BTCUSDT", "1d") is not None
        assert ex._main_sub is not None and set(ex._aux_subs) == {"1d"}
        ex.unsubscribe_feeds()
        assert ex._main_sub is None and ex._aux_subs == {}
        ex.unsubscribe_feeds()  # 幂等再退

    def test_intents_json_nonempty_roundtrip(self):
        """非空意图序列化往返：intent_to_json→intents_from_json 恒等
        （恢复重建消费面）。"""
        from core.paper_trading.kernel.types import Intent
        from core.paper_trading.runtime.executor import (
            intent_to_json, intents_from_json)

        from core.paper_trading.runtime.executor import PendingIntent
        intent = Intent(kind="open", direction="long",
                        size_fraction=0.5, reason="signal")
        raw = intent_to_json(
            [PendingIntent(intent, "2026-10-16T00:00:00+00:00", ())])
        assert raw is not None
        restored = intents_from_json(raw)
        assert [p.intent for p in restored] == [intent]
        # 序列化面=Intent 字段（信号上下文不序列化——恢复侧重建）
        assert json.loads(raw) == [{
            "kind": "open", "direction": "long", "size_fraction": 0.5,
            "reason": "signal", "trigger_price": None}]

    def test_prepare_with_precollapsed_stop_reraises(self, tmp_path):
        """prepare 期间预置 stop：装载正常完成（装载链无 stop 检查点
        ——check_stop 仅在 bar 边界/空闲期；prepare_failed 兜底与
        TaskStopRequested 透传分支的调用图依据见 tests/README 十二区
        不可达判定行）。"""
        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex.controller.request_stop()
        assert ex.prepare() is True  # 装载不受预置 stop 影响

    def test_empty_parquet_rejected_no_data(self, tmp_path):
        """装载拒绝细分：parquet 存在但空 DataFrame → no_data。"""
        import pandas as pd

        h = _Harness(tmp_path)
        save_parquet(pd.DataFrame(), h.data_dir / "BTCUSDT_4h.parquet")
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "no_data" in h.task()["error_detail"]

    def test_run_prepare_failure_returns_silent(self, tmp_path):
        """run() 对装载失败任务：prepare False → 直接返回（error 已
        由 prepare 写，无 started 推送）。"""
        h = _Harness(tmp_path)
        (h.data_dir / "BTCUSDT_4h.parquet").unlink()
        ex = h.make_executor()
        ex.run()
        assert h.task()["status"] == "error"
        assert not any(p["type"] == "task_started" for p in h.pushes)

    def test_loop_bar_boundary_stop_finalizes_within_loop(self, tmp_path):
        """_loop 内帧处理中的协作停：第 k 检查点 request_stop →
        TaskStopRequested 经 _loop except 收口 stopped（帧已完整落库）。"""
        h = _Harness(tmp_path, window_bars=None)

        class StopAfter(TaskController):
            def __init__(self, n):
                super().__init__()
                self._n, self._calls = n, 0

            def check_stop(self):
                self._calls += 1
                if self._calls >= self._n:
                    self.request_stop()
                super().check_stop()

        ex = h.make_executor(controller=StopAfter(3))
        ex._frame_queue.put((h.enhanced, 0))
        ex.run()
        task = h.task()
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "user_stop"

    def test_mtf_aux_handler_updates_frame_reference(self, tmp_path):
        """MTF 辅帧回调：on_aux 更新 _aux_frames 最新引用+辅帧未到
        时组装跳过该周期（None continue 分支）。"""
        from tests.helpers.data_factory import make_mtf_dna

        h = _Harness(tmp_path)
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-mtf2",
            dna_snapshot_json=make_mtf_dna(
                timeframes=("1d", "4h")).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        ex = h.make_executor()
        on_aux = ex._make_aux_handler("1d")
        on_aux(h.df, 0)
        assert ex._aux_frames["1d"] is h.df
        # 辅帧未到（_aux_frames 空）→ 组装只含主周期（None 跳过）
        ex._aux_frames.clear()
        frames = ex._assemble_mtf_frames(h.enhanced)
        assert set(frames) == {"4h"}

    def test_funding_arrived_callback_logs_only(self, tmp_path):
        """funding_arrived 回调：仅记日志不落库（补记在次 bar 第 5
        步——及时性事件与结算解耦锚）。"""
        import pandas as pd

        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex._on_funding_arrived(
            [pd.Timestamp("2026-08-30 16:00:00", tz="UTC")])
        assert h.task()["status"] == "pending"  # 零副作用

    def test_mtf_empty_aux_parquet_rejected(self, tmp_path):
        """装载拒绝细分：辅 parquet 存在但空（fetcher 产空帧）→
        刷新后 latest 空 → mtf_data_missing（刷新后无产物路径）。"""
        import pandas as pd

        from tests.helpers.data_factory import make_mtf_dna

        h = _Harness(tmp_path)
        empty_daily = pd.DataFrame()
        save_parquet(empty_daily, h.data_dir / "BTCUSDT_1d.parquet")
        h.registry = FeedRegistry(
            h.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: _funding_frame([]))
        h.task_id = paper.create_task(
            h.db, strategy_id="strat-mtf3",
            dna_snapshot_json=make_mtf_dna(
                timeframes=("1d", "4h")).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=30)
        ex = h.make_executor()
        assert ex.prepare() is False
        assert "mtf_data_missing" in h.task()["error_detail"]


def _max_loss_dna(max_position_loss: float):
    """声明金额防线的多头 DNA（seed=7 数据：entry 信号 i=20 起持续亮、
    i=21 开盘成交、之后持续下跌——浮亏单调扩大路径）。"""
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, SignalRole, StrategyDNA,
    )
    from tests.helpers.data_factory import make_signal_gene

    return StrategyDNA(
        signal_genes=[
            make_signal_gene(role=SignalRole.ENTRY_TRIGGER),
            make_signal_gene(
                role=SignalRole.EXIT_TRIGGER,
                condition_type="gt", condition_value=70),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=None, position_size=0.5,
            leverage=1, direction="long", sl_mode="pct", atr_period=14,
            max_position_loss=max_position_loss),
    )


class TestMaxLossExecution:
    """金额防线执行面（add-position-sizing-params 族 5a/5b/5f）。

    seed=7 路径锚：entry 信号 i=20 亮 → i=21 开盘开多（qty=5_000/
    开盘价、entry=开盘×(1+slip)）；价格持续下跌，mpl=100 时浮亏
    首达线 bar=26（close≈88.87、浮亏≈176；此前各 bar 浮亏均<100
    且 low 均未触 SL 线≈87.5——归因序不争抢）→ i=27 开盘全平。
    """

    @staticmethod
    def _first_fill_of_kind(h, kind: str) -> dict:
        return next(
            f for f in paper.list_fills(h.db, h.task_id, limit=1000)
            if f["kind"] == kind)

    def test_exit_max_loss_fills_next_bar_open(self, tmp_path):
        """达线次根开盘全平：seq=3/reason=max_loss/成交 bar=达线 bar
        次根、成交价=该根开盘×(1−slip)（卖出方向）+fill_event 携带
        kind+达线首 bar 验证（前一根未达线）。"""
        h = _Harness(tmp_path, dna=_max_loss_dna(100.0), window_bars=50)
        ex = h.make_executor()
        ex.process_frames(h.enhanced, 0)

        exit_fill = self._first_fill_of_kind(h, "exit_max_loss")
        assert exit_fill["seq"] == 3
        assert exit_fill["reason"] == "max_loss"
        # 成交 bar=达线 bar 次根（开盘价语义——非触发 bar 收盘）
        exit_idx = h.enhanced.index.get_loc(
            pd.Timestamp(exit_fill["bar_time"]))
        trigger_idx = exit_idx - 1
        assert trigger_idx >= 1
        # 达线首 bar：该根收盘浮亏≥阈值、前一根未达（首达线锚）
        open_fill = self._first_fill_of_kind(h, "open")
        qty = open_fill["quantity"]
        entry = open_fill["price"]
        loss_at = -qty * (float(h.enhanced["close"].iloc[trigger_idx])
                          - entry)
        loss_before = -qty * (
            float(h.enhanced["close"].iloc[trigger_idx - 1]) - entry)
        assert loss_at >= 100.0
        assert loss_before < 100.0
        # 成交价=执行 bar 开盘×(1−slip)（多头平仓=卖出——不利偏移）
        open_price = float(h.enhanced["open"].iloc[exit_idx])
        assert exit_fill["price"] == pytest.approx(
            open_price * (1.0 - 0.0005), rel=1e-9)
        # fill_event 推送自然携带 kind（无新事件类型）
        assert any(
            p["type"] == "fill_event" and p["kind"] == "exit_max_loss"
            for p in h.pushes)
        # 全平后无持仓（exit_max_loss 是全平单）
        assert h.task()["position_side"] is None

    def test_last_bar_threshold_no_exit(self, tmp_path):
        """窗口末根达线四断言：意图挂 pending 随任务终结消亡/不出场
        成交/任务正常 completed/pt_fill 不含 exit_max_loss（specs
        「窗口末根达线不出场」Scenario——窗口是运行控制不是行为干预）。"""
        probe_dir = tmp_path / "probe"
        probe_dir.mkdir()
        probe = _Harness(
            probe_dir, dna=_max_loss_dna(100.0), window_bars=50)
        probe_ex = probe.make_executor()
        probe_ex.process_frames(probe.enhanced, 0)
        exit_fill = self._first_fill_of_kind(probe, "exit_max_loss")
        trigger_idx = probe.enhanced.index.get_loc(
            pd.Timestamp(exit_fill["bar_time"])) - 1

        # 窗口恰在达线 bar 终结：达线 bar 是处理的最后一根
        h = _Harness(
            tmp_path, dna=_max_loss_dna(100.0),
            window_bars=trigger_idx + 1)
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链 pending→running→completed）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)

        task = h.task()
        assert task["status"] == "completed"  # 任务正常 completed
        assert task["bars_processed"] == trigger_idx + 1
        assert task["position_side"] is not None  # 持仓保留（未出场）
        kinds = [f["kind"] for f in paper.list_fills(
            h.db, h.task_id, limit=1000)]
        assert "exit_max_loss" not in kinds  # 不产生出场成交
        # 意图挂 pending（内存面）随任务终结消亡——无次根可执行
        # （任务 4 起 pending 是 PendingIntent——意图面取 .intent.kind）
        assert [p.intent.kind for p in ex.pending] == ["exit_max_loss"]


class TestSignalLogging:
    """信号记录挂点（change paper-signal-log 任务 4）——完成标准
    ①-⑩：原子性/被拒落库/指标快照对账/expired 收割/重放幂等/常规
    bar 零行/语义过滤不记录/gene_hits 结构/fill_seq 三元组/账户快照
    与 exec_bar_time 时点。

    构造通道：覆写 enhanced 帧的 rsi_14 列精确控制信号分布（信号
    组装消费帧列值；_closed_bar 只读 OHLC——价格另行受控）。

    闸门不可达判定（阶段四步骤 2）：executor.py:1075→1077+1077
    （_rejected_size_fraction 的 kind≠add 返回 None 分支）——该函数
    全库唯一调用点为解释器内拒绝行构造（executor.py:1032 附近
    size_fraction=self._rejected_size_fraction(rej.kind)），而求值层
    拦截按设计定形 Rejection.kind 恒 "add"（design.md Rejection
    定义+intents.py 求值层唯一形态=敞口拦截）；kind≠add 的拒绝
    （insufficient_funds/context_invalid/below_min_notional）均产生
    在消费期/收割期路径，其信号行构造不经本函数。
    """

    @staticmethod
    def _frame(closes: list[float]) -> pd.DataFrame:
        """受控价格帧（open=前收、high/low±0.3%——SL/清算不误触）。"""
        idx = pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=len(closes), freq="4h",
                          tz="UTC"), name="timestamp")
        df = pd.DataFrame({
            "open": [closes[0]] + closes[:-1],
            "high": [c * 1.003 for c in closes],
            "low": [c * 0.997 for c in closes],
            "close": closes, "volume": 1000.0,
        }, index=idx)
        return compute_all_indicators(df)

    @staticmethod
    def _rsi(enhanced: pd.DataFrame, values: list[float]) -> pd.DataFrame:
        out = enhanced.copy()
        out["rsi_14"] = values
        return out

    def test_no_signal_bar_zero_rows(self, tmp_path):
        """⑥常规无信号 bar 零 pt_signal 行。"""
        h = _Harness(tmp_path, window_bars=10)
        ex = h.make_executor()
        ex.process_frames(self._rsi(h.enhanced, [50.0] * 200), 0)
        assert paper.list_signals(h.db, h.task_id, limit=1000) == []

    def test_mixed_zero_direction_no_rows(self, tmp_path):
        """⑦mixed 符号 0 语义过滤不产记录（无 DIRECTION 基因→方向
        恒 0；entry 信号触发但策略语义不成立——非执行拦截）。"""
        dna = make_dna(direction="mixed")
        h = _Harness(tmp_path, dna=dna, window_bars=10)
        ex = h.make_executor()
        frame = self._rsi(h.enhanced, [20.0] * 200)  # entry 条件恒真
        ex.process_frames(frame, 0)
        assert paper.list_fills(h.db, h.task_id, limit=1000) == []
        assert paper.list_signals(h.db, h.task_id, limit=1000) == []

    def test_open_filled_row_semantics(self, tmp_path):
        """⑧⑨⑩pending 消费主路径：filled 行全字段语义——bar_time=
        信号 bar、exec_bar_time 差一根（C8-pending 态）、seq/fill_seq=4、
        三元组唯一定位成交、gene_hits 六字段与求值披露独立对照一致、
        指标快照该 bar 实际值（③）、账户快照=执行尝试前（C7）。"""
        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        rsi = [50.0] * 200
        rsi[5] = 20.0  # bar5 信号、bar6 开盘执行
        frame = self._rsi(h.enhanced, rsi)
        ex.process_frames(frame, 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        opens = [r for r in rows
                 if r["signal_kind"] == "open" and r["outcome"] == "filled"]
        assert len(opens) == 1
        row = opens[0]
        bar5 = h.enhanced.index[5].isoformat()
        bar6 = h.enhanced.index[6].isoformat()
        assert row["bar_time"] == bar5, "决策锚=信号产生 bar"
        assert row["exec_bar_time"] == bar6, "pending 次根执行差一根"
        assert row["seq"] == 4 and row["fill_seq"] == 4
        assert row["direction"] == "long"
        assert row["reject_reason"] is None and row["capped"] is None

        # ⑨C5 三元组唯一命中 pt_fill
        fills = [f for f in paper.list_fills(h.db, h.task_id, limit=1000)
                 if f["task_id"] == row["task_id"]
                 and f["bar_time"] == row["exec_bar_time"]
                 and f["seq"] == row["fill_seq"]]
        assert len(fills) == 1 and fills[0]["kind"] == "open"

        # ⑧gene_hits 与求值披露通道独立对照一致+六字段
        from core.strategy.executor import dna_to_signal_set
        sig = dna_to_signal_set(make_dna(), frame, with_hits=True)
        expected_hits = sig.hits["entry"][5]
        got_hits = json.loads(row["gene_hits"])
        assert got_hits == expected_hits
        if got_hits:
            assert set(got_hits[0]) == {
                "gene_index", "indicator", "field", "condition",
                "role", "timeframe"}

        # ③指标快照与该 bar 实际值一致（独立计算——直读帧列）
        snapshot = json.loads(row["indicator_snapshot"])
        assert snapshot["rsi_14"] == pytest.approx(20.0)

        # C7 执行尝试前：balance=初始资金（成交未扣 margin/fee）
        assert row["balance"] == pytest.approx(10_000.0)
        assert row["equity"] == pytest.approx(10_000.0)
        assert row["position_side"] is None, "开仓执行尝试前无持仓"

    def test_insufficient_funds_rejected_row(self, tmp_path):
        """②资金不足拒因落库：initial_cash=0 → open 意图消费时定仓
        qty=0 被拒（pending 消费时点、拒绝 bar 落库、seq=4）。"""
        h = _Harness(tmp_path, window_bars=30, initial_cash=0.0)
        ex = h.make_executor()
        rsi = [50.0] * 200
        rsi[5] = 20.0
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        rejected = [r for r in rows if r["outcome"] == "rejected"]
        assert len(rejected) == 1, "被拒信号恰一行（每根触发 bar 一条）"
        row = rejected[0]
        assert row["signal_kind"] == "open"
        assert row["reject_reason"] == "insufficient_funds"
        assert row["seq"] == 4
        assert row["bar_time"] == h.enhanced.index[5].isoformat()
        assert row["exec_bar_time"] == h.enhanced.index[6].isoformat()
        assert row["fill_seq"] is None
        assert paper.list_fills(h.db, h.task_id, limit=1000) == []

    def test_exposure_cap_rejected_row_seq7(self, tmp_path):
        """②敞口封顶拒因落库（解释器内拒绝）：seq=7、bar_time=
        exec_bar_time 同 bar（C8 即时态）、账户快照=求值时点（C7——
        与该 bar 权益快照对账：求值时点=第 5 步资金费后状态）。"""
        from core.strategy.dna import RiskGenes, SignalRole
        from dataclasses import replace as _replace

        dna = make_dna()
        rsi = [50.0] * 200
        rsi[5] = 20.0
        dna.signal_genes = [
            make_signal_gene(
                indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
                condition_type="lt", condition_value=30),
            make_signal_gene(
                indicator="RSI", role=SignalRole.ADD_TRIGGER,
                condition_type="lt", condition_value=999),  # add 恒真
        ]
        dna.risk_genes = _replace(dna.risk_genes, max_exposure=0.001)
        h = _Harness(tmp_path, dna=dna, window_bars=30)
        ex = h.make_executor()
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        capped = [r for r in rows
                  if r["reject_reason"] == "exposure_cap"]
        assert capped, "开仓后的 add 信号须产敞口拒绝行"
        row = capped[0]
        assert row["seq"] == 7
        assert row["signal_kind"] == "add"
        assert row["outcome"] == "rejected"
        assert row["bar_time"] == row["exec_bar_time"], "解释器内拒绝同 bar"
        # gene_hits 归因键=add（求值披露通道）
        hits = json.loads(row["gene_hits"])
        assert hits and all(it["role"] == "add_trigger" for it in hits)
        # C7 求值时点对账：拒绝行 equity 与该 bar 权益快照一致
        eq = {e["bar_time"]: e for e in paper.list_equity_snapshots(
            h.db, h.task_id, limit=1000)}
        assert row["equity"] == pytest.approx(
            eq[row["exec_bar_time"]]["equity"])

    def test_exit_sl_filled_same_bar_seq2(self, tmp_path):
        """⑩即时类同 bar 态（C8）：SL 触发当根成交——bar_time=
        exec_bar_time、seq=2、防线动作 gene_hits 空数组。"""
        dna = make_dna(stop_loss=0.05)
        # 价格：bar5 信号（rsi 覆写）→bar6 open=100 开仓→bar7 跌破 95
        closes = [100.0] * 7 + [93.0] + [93.0] * 12
        h = _Harness(tmp_path, dna=dna, window_bars=19)
        frame = self._frame(closes)
        rsi = [50.0] * 20
        rsi[5] = 20.0
        ex = h.make_executor()
        ex.process_frames(self._rsi(frame, rsi), 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        sl = [r for r in rows if r["signal_kind"] == "exit_sl"]
        assert len(sl) == 1
        row = sl[0]
        assert row["outcome"] == "filled" and row["seq"] == 2
        assert row["bar_time"] == row["exec_bar_time"], "即时类同 bar"
        assert json.loads(row["gene_hits"]) == [], "防线动作无基因归因"
        # 三元组命中成交
        fills = [f for f in paper.list_fills(h.db, h.task_id, limit=1000)
                 if f["bar_time"] == row["exec_bar_time"]
                 and f["seq"] == row["fill_seq"]]
        assert len(fills) == 1 and fills[0]["kind"] == "exit_sl"

    def test_window_completion_harvests_expired(self, tmp_path):
        """④窗口到点结题收割：末根 pending→expired 行（seq=0、
        exec_bar_time=last_bar_time、bar_time=意图产生 bar）。"""
        h = _Harness(tmp_path, window_bars=10)
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        rsi = [50.0] * 200
        rsi[9] = 20.0  # 末根（窗口第 10 根）信号→pending 未消费
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        assert h.task()["status"] == "completed"
        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        expired = [r for r in rows if r["outcome"] == "expired"]
        assert len(expired) == 1
        row = expired[0]
        bar9 = h.enhanced.index[9].isoformat()
        assert row["seq"] == 0
        assert row["exec_bar_time"] == bar9 == h.task()["last_bar_time"]
        assert row["bar_time"] == bar9, "意图产生于末根"
        assert row["signal_kind"] == "open"
        assert row["reject_reason"] is None and row["fill_seq"] is None
        # 账户快照完整（终态时点）
        assert row["equity"] == pytest.approx(10_000.0)

    def test_consumed_intent_produces_no_expired(self, tmp_path):
        """④反向：常规 pending 消费（次根成交）不产 expired——终态时
        pending 空、零 expired 行。"""
        h = _Harness(tmp_path, window_bars=10)
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        rsi = [50.0] * 200
        rsi[2] = 20.0  # bar2 信号→bar3 消费成交；其后无信号
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        assert h.task()["status"] == "completed"
        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        assert [r for r in rows if r["outcome"] == "expired"] == []
        filled = [r for r in rows if r["outcome"] == "filled"]
        assert len(filled) == 1 and filled[0]["signal_kind"] == "open"

    def test_replay_after_recovery_idempotent(self, tmp_path):
        """⑤恢复重放幂等：全流程→from_task_row 重建→同帧重放（游标
        判重跳过）→信号行集与 fill 关联零漂移（OR IGNORE 第二防线）。"""
        h = _Harness(tmp_path, window_bars=12)
        rsi = [50.0] * 200
        rsi[3] = 20.0
        rsi[11] = 20.0  # 末根 pending→expired 收割路径一并覆盖
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)
        before = paper.list_signals(h.db, h.task_id, limit=1000)

        restored = PaperTaskExecutor.from_task_row(
            h.db, paper.get_task(h.db, h.task_id),
            registry=h.registry, start_after=_REPLAY_ALL)
        restored.process_frames(self._rsi(h.enhanced, rsi), 0)
        after = paper.list_signals(h.db, h.task_id, limit=1000)
        # 行集不变（键级对照：幂等键三元组集相等+outcome/fill_seq 一致）
        key = lambda r: (r["bar_time"], r["exec_bar_time"], r["seq"],
                         r["outcome"], r["fill_seq"])
        assert [key(r) for r in before] == [key(r) for r in after]

    def test_signals_same_batch_as_bar_writes(self, tmp_path):
        """①同批正面锚：成交 bar 的信号行与 fills/equity 同事务产出
        （append_bar 单事务——行在场即同批；故障注入回滚面仓库层
        test_signal_atomic_with_bar_writes 在案）。"""
        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        rsi = [50.0] * 200
        rsi[5] = 20.0
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        assert rows
        for r in rows:
            assert r["exec_bar_time"] in {
                e["bar_time"] for e in paper.list_equity_snapshots(
                    h.db, h.task_id, limit=1000)}, \
                "信号行 exec_bar 须为已落库 bar（同批事务面）"

    def test_fill_accounting_columns_persisted(self, tmp_path):
        """任务 5 执行器落值：open 成交的 pt_fill 行三列落库且与
        人工核算锚一致（initial=10_000、ps=0.5、lev=1、open≈100 →
        margin_used=名义/1、available_before=10_000、balance_after=
        available_before−margin_used−fee）。"""
        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        rsi = [50.0] * 200
        rsi[5] = 20.0
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        opens = [f for f in paper.list_fills(h.db, h.task_id, limit=1000)
                 if f["kind"] == "open"]
        assert len(opens) == 1
        f = opens[0]
        assert f["equity_before"] == pytest.approx(10_000.0)
        assert f["available_before"] == pytest.approx(10_000.0)
        assert f["margin_used"] == pytest.approx(
            f["quantity"] * f["price"] / 1)  # leverage=1
        # 自洽判据闭环：available_before−margin_used−fee=成交后
        # balance——对账面=成交 bar 的权益快照行（该 bar 唯一笔成交
        # 且无资金费结算，快照 balance 即成交后时点）
        eq = {e["bar_time"]: e for e in paper.list_equity_snapshots(
            h.db, h.task_id, limit=1000)}
        assert f["available_before"] - f["margin_used"] - f["fee"] \
            == pytest.approx(eq[f["bar_time"]]["balance"])

    def test_capped_fill_disclosed_not_rejected(self, tmp_path):
        """字段族明细 capped 列：risk_budget cap 截断的成交以 capped=1
        披露（截断非拒绝——outcome=filled、reject_reason None）；截断
        事实由独立手算锚证明（qty=cap/执行 bar 开盘价，cap=权益×
        min(leverage,max_exposure)；裸值名义=risk_fraction/stop_loss
        =5×权益 必然超 cap=0.5×权益——在场即证截断发生）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalRole, StrategyDNA,
        )

        dna = StrategyDNA(
            signal_genes=[
                make_signal_gene(role=SignalRole.ENTRY_TRIGGER),
                make_signal_gene(
                    role=SignalRole.EXIT_TRIGGER,
                    condition_type="gt", condition_value=70),
            ],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(timeframe="4h",
                                           symbol="BTCUSDT"),
            risk_genes=RiskGenes(
                stop_loss=0.10, take_profit=0.20, position_size=0.5,
                leverage=1, direction="long", sl_mode="pct",
                position_mode="risk_budget", risk_fraction=0.5,
                max_exposure=0.5),
        )
        h = _Harness(tmp_path, dna=dna, window_bars=30)
        ex = h.make_executor()
        rsi = [50.0] * 200
        rsi[5] = 20.0
        ex.process_frames(self._rsi(h.enhanced, rsi), 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        opens = [r for r in rows
                 if r["signal_kind"] == "open" and r["outcome"] == "filled"]
        assert len(opens) == 1
        row = opens[0]
        assert row["capped"] == 1, "cap 截断披露标记"
        assert row["reject_reason"] is None, "截断是披露不是拒绝"
        assert row["fill_seq"] == 4
        # 独立手算锚：flat 时点权益=初始 10000，cap=10000×min(1,0.5)
        # =5000；qty 以执行 bar 开盘价（滑点前定仓基准）折算
        price = float(h.enhanced["open"].iloc[6])
        f = [x for x in paper.list_fills(h.db, h.task_id, limit=1000)
             if x["kind"] == "open"][0]
        assert f["quantity"] == pytest.approx(5_000.0 / price, rel=1e-9), \
            "截断后数量恰=cap/基准价（裸值 5×权益 的 1/10）"

    def test_close_pending_filled_seq3(self, tmp_path):
        """字段族明细 seq 枚举完备面：pending 出场族（close 次根成交）
        信号行 seq=3——bar_time=信号 bar、exec_bar_time 差一根、
        gene_hits 与求值披露 exit 键独立对照一致。"""
        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        rsi = [50.0] * 200
        rsi[5] = 20.0   # bar5 入场信号 → bar6 开仓
        rsi[9] = 80.0   # bar9 出场信号 → bar10 全平
        frame = self._rsi(h.enhanced, rsi)
        ex.process_frames(frame, 0)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        closes = [r for r in rows if r["signal_kind"] == "close"]
        assert len(closes) == 1
        row = closes[0]
        assert row["outcome"] == "filled" and row["seq"] == 3
        assert row["bar_time"] == h.enhanced.index[9].isoformat()
        assert row["exec_bar_time"] == h.enhanced.index[10].isoformat()
        assert row["direction"] == "long"
        assert row["fill_seq"] == 3
        # gene_hits 与求值披露通道独立对照（exit 键 bar9 命中）
        from core.strategy.executor import dna_to_signal_set
        sig = dna_to_signal_set(make_dna(), frame, with_hits=True)
        assert json.loads(row["gene_hits"]) == sig.hits["exit"][9]
        # 三元组唯一定位 close 成交
        fills = [f for f in paper.list_fills(h.db, h.task_id, limit=1000)
                 if f["bar_time"] == row["exec_bar_time"]
                 and f["seq"] == row["fill_seq"]]
        assert len(fills) == 1 and fills[0]["kind"] == "close"


class TestRecoveredPendingSignals:
    """恢复链 pending 消费（change paper-signal-log 任务 4 恢复语义）：
    崩溃于 bar 边界→from_task_row 重建→次根消费的真实重建路径。

    - 正面形态：恢复消费成交的信号行 bar_time=重建锚（last_bar_time）
      、gene_hits 空披露（恢复不可得——不伪造归因）、indicator_snapshot
      空 dict（行号锚 -1）、fill_seq 关联；
    - 防御形态：注入语境不符意图（持仓中 open——解释器自然流不产，
      pending_intent_json 为外部持久化面）→ context_invalid 拒绝行
      落库（paper-trading spec「成交层拦截均 SHALL 落表」）。
    """

    def test_recovered_pending_fills_with_empty_disclosure(self, tmp_path):
        """恢复链正面：bar5 信号落 pending 后中止（不 finalize）→
        重建→bar6 消费成交——filled 行四披露语义（锚/空归因/空快照/
        fill_seq），且全程零多余行。"""
        h = _Harness(tmp_path, window_bars=30)
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        rsi = [50.0] * 200
        rsi[5] = 20.0
        frame = TestSignalLogging._rsi(h.enhanced, rsi)
        ex = h.make_executor()
        ex.process_frames(frame.iloc[:6], 0)  # bar0-5：信号落 pending 后止
        assert h.task()["pending_intent_json"], "崩溃时点 pending 已持久化"

        restored = PaperTaskExecutor.from_task_row(
            h.db, paper.get_task(h.db, h.task_id),
            registry=h.registry, start_after=_REPLAY_ALL)
        restored.process_frames(frame, 6)  # bar6 起消费恢复的 pending

        assert h.task()["status"] == "completed"
        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        opens = [r for r in rows
                 if r["signal_kind"] == "open" and r["outcome"] == "filled"]
        assert len(opens) == 1, "恢复消费恰一行（不因重放翻倍）"
        row = opens[0]
        assert row["bar_time"] == h.enhanced.index[5].isoformat(), \
            "bar_time=重建锚（崩溃时点 last_bar_time=意图产生 bar）"
        assert row["exec_bar_time"] == h.enhanced.index[6].isoformat()
        assert row["seq"] == 4 and row["fill_seq"] == 4
        assert json.loads(row["gene_hits"]) == [], "恢复重建归因空披露"
        assert json.loads(row["indicator_snapshot"]) == {}, \
            "恢复语境无行号锚——快照空 dict（不伪造）"

    def test_recovered_context_conflict_intent_rejected_row(self, tmp_path):
        """恢复链防御面：注入持仓中 open 意图（pending_intent_json
        外部面）→恢复消费→context_invalid 拒绝行落库（账户快照含
        持仓六列完整）+不产成交。"""
        h = _Harness(tmp_path, window_bars=30)
        paper.update_task(  # 模拟拾取写 running（迁移链不断）
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        rsi = [50.0] * 200
        rsi[5] = 20.0
        frame = TestSignalLogging._rsi(h.enhanced, rsi)
        ex = h.make_executor()
        ex.process_frames(frame.iloc[:7], 0)  # bar0-6：bar6 开仓成交
        assert paper.list_fills(h.db, h.task_id, limit=1000), \
            "前置：持仓已建立"

        # 注入通道=直写 DB（update_task 白名单不含游标组——写者隔离
        # 使仓库 API 无法伪造崩溃态；直写模拟外部持久化面上的语境
        # 不符意图）
        import sqlite3
        payload = json.dumps([
            {"kind": "open", "direction": "long", "size_fraction": 0.5,
             "reason": "signal", "trigger_price": None}])
        with sqlite3.connect(h.db) as conn:
            conn.execute(
                "UPDATE pt_task SET pending_intent_json = ? "
                "WHERE task_id = ?", (payload, h.task_id))
        assert paper.get_task(h.db, h.task_id)["pending_intent_json"], \
            "前置：注入已落 task 行"

        restored = PaperTaskExecutor.from_task_row(
            h.db, paper.get_task(h.db, h.task_id),
            registry=h.registry, start_after=_REPLAY_ALL)
        restored.process_frames(frame, 7)  # bar7 消费注入意图

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        rejected = [r for r in rows if r["outcome"] == "rejected"]
        assert len(rejected) == 1
        row = rejected[0]
        assert row["signal_kind"] == "open"
        assert row["reject_reason"] == "context_invalid"
        assert row["seq"] == 4 and row["fill_seq"] is None
        assert row["bar_time"] == h.enhanced.index[6].isoformat(), \
            "bar_time=重建锚（注入语境同款语义）"
        assert row["exec_bar_time"] == h.enhanced.index[7].isoformat()
        assert json.loads(row["gene_hits"]) == []
        assert json.loads(row["indicator_snapshot"]) == {}
        # 账户快照完整：持仓六列在拒绝行同字段族落值；equity 与该
        # bar 权益快照对账（拒绝不落账——两时点同源同 close）
        assert row["position_side"] == "long"
        assert row["position_quantity"] > 0
        assert row["position_margin"] > 0
        eq = {e["bar_time"]: e for e in paper.list_equity_snapshots(
            h.db, h.task_id, limit=1000)}
        assert row["equity"] == pytest.approx(
            eq[row["exec_bar_time"]]["equity"])
        # 禁止变化：注入意图不产成交（open 类成交仍恰前置一笔——
        # 其后合成价格的 exit_sl 属自然价格行为不在锚定面）
        assert len([f for f in paper.list_fills(
            h.db, h.task_id, limit=1000) if f["kind"] == "open"]) == 1

    def test_execute_pending_without_equity_row_uses_task_fallback(
            self, tmp_path):
        """终态收割的 snap_row None 防御（闸门盲区消解）：pending
        非空而 equity 行不在（外部持久化面异常形态——正常路径
        pending 必随 bar 处理落 equity）→ expired 收割行账户快照
        兜底取 task 行值（balance/0.0 语义）。"""
        import sqlite3

        h = _Harness(tmp_path, window_bars=30, initial_cash=0.0)
        payload = json.dumps([
            {"kind": "open", "direction": "long", "size_fraction": 0.5,
             "reason": "signal", "trigger_price": None}])
        with sqlite3.connect(h.db) as conn:
            conn.execute(
                "UPDATE pt_task SET bars_processed=1, last_bar_time=?, "
                "balance=0.0, pending_intent_json=? WHERE task_id=?",
                (h.df.index[0].isoformat(), payload, h.task_id))
        row = paper.get_task(h.db, h.task_id)
        assert row["bars_processed"] == 1 and row["balance"] == 0.0

        ex = PaperTaskExecutor.from_task_row(
            h.db, row, registry=h.registry, start_after=_REPLAY_ALL)
        ex._finalize("stopped", stop_reason="user_stop")  # 终态收割路径

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        expired = [r for r in rows if r["outcome"] == "expired"]
        assert expired, "pending 非空终态须收割 expired 行"
        for r in expired:
            assert r["equity"] == 0.0 and r["balance"] == 0.0, \
                "兜底账户快照取 task 行值"


class TestIndicatorSnapshotGuards:
    """SEC-1：指标快照值域守卫（NaN/inf→None——裸落毒化查询面为
    非标 JSON 字面量〔oi_change_rate 等比率类指标可产 inf〕）。"""

    def test_snapshot_nan_inf_converted_to_none(self, tmp_path):
        h = _Harness(tmp_path, window_bars=10)
        ex = h.make_executor()
        frame = h.enhanced.copy()
        frame["rsi_14"] = [float("inf"), float("nan"), 25.0] + [50.0] * 197
        assert ex._indicator_snapshot(frame, 0) == {"rsi_14": None}, "inf→None"
        assert ex._indicator_snapshot(frame, 1) == {"rsi_14": None}, "NaN→None"
        assert ex._indicator_snapshot(frame, 2) == {"rsi_14": 25.0}
        # 行号越界防御（负值/超界→空 dict，df.iloc[-1] 末行语义须防）
        assert ex._indicator_snapshot(frame, -1) == {}
        assert ex._indicator_snapshot(frame, len(frame)) == {}

    def test_snapshot_json_payload_stays_standard(self, tmp_path):
        """端到端面：inf 指标 bar 的信号行 indicator_snapshot 落库值
        为标准 JSON（None），json.loads 不产非标字面量。"""
        import json as _json

        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        frame = h.enhanced.copy()
        frame["rsi_14"] = [50.0] * 200
        frame.iloc[5, frame.columns.get_loc("rsi_14")] = float("inf")
        # bar5 rsi=inf：lt 30 比较为 False——不产信号；改为 NaN 信号
        # 分布不可控。构造触发：bar4 正常触发（20.0），bar5 执行 bar
        # 的快照列 inf（执行 bar 快照不取——决策 bar 锚）；直接锚
        # 决策 bar inf 下的落库形态：bar5=inf 触发不可行（比较恒
        # False），退而直测组装函数序列化标准性
        row = ex._signal_row(
            bar_time="t", exec_bar_time="t", seq=4, kind="open",
            direction="long", size_fraction=0.5, gene_hits=[],
            indicator_snapshot={"rsi_14": None}, outcome="filled",
            account={"equity": 1.0, "balance": 1.0, "unrealized_pnl": 0.0,
                     "position_side": None, "position_quantity": None,
                     "position_margin": None})
        parsed = _json.loads(row["indicator_snapshot"])
        assert parsed == {"rsi_14": None}
        assert "Infinity" not in row["indicator_snapshot"]

    def test_inf_column_chain_writes_standard_snapshot(self, tmp_path):
        """SEC-1 端到端面（消解直测组装函数的 punt）：决策 bar 快照列
        inf（EMA20 守卫基因——inf>-999 守卫通过、不影响 rsi 入场触发），
        全链落库的 indicator_snapshot 该列转 None；属性面：全任务信号
        行两 JSON 列无 Infinity/NaN 非标字面量且可解析。"""
        from core.strategy.dna import SignalRole

        dna = make_dna()
        dna.signal_genes = [
            make_signal_gene(role=SignalRole.ENTRY_TRIGGER),
            make_signal_gene(
                indicator="EMA", params={"period": 20},
                role=SignalRole.ENTRY_GUARD,
                condition_type="gt", condition_value=-999),
        ]
        h = _Harness(tmp_path, dna=dna, window_bars=30)
        ex = h.make_executor()
        frame = h.enhanced.copy()
        rsi = [50.0] * 200
        rsi[5] = 20.0
        frame["rsi_14"] = rsi
        frame.iloc[5, frame.columns.get_loc("ema_20")] = float("inf")
        ex.process_frames(frame, 0)

        opens = [r for r in paper.list_signals(h.db, h.task_id, limit=1000)
                 if r["signal_kind"] == "open" and r["outcome"] == "filled"]
        assert len(opens) == 1
        snap = json.loads(opens[0]["indicator_snapshot"])
        assert snap == {"rsi_14": 20.0, "ema_20": None}, \
            "决策 bar inf 列转 None、rsi 决策值同时在快照（对账面）"
        # 属性扫描：全行两 JSON 列恒标准（无 Infinity/NaN 字面量）
        for r in paper.list_signals(h.db, h.task_id, limit=1000):
            for col in ("indicator_snapshot", "gene_hits"):
                assert "Infinity" not in r[col], (r["seq"], col)
                assert "NaN" not in r[col], (r["seq"], col)
                json.loads(r[col])

    def test_snapshot_column_resolution_defense_and_missing_frame_col(
            self, tmp_path):
        """快照列收集三防御+帧缺列跳过（闸门盲区消解）：
        跨轴基因（gene.timeframe 非 None——无主轴单值语义）、未注册
        指标、列名解析异常（MACD 空 params→int(params["fast"])
        KeyError）均不入快照面；快照列不在运行帧列时该列跳过
        （纯 K 线帧快照空 dict——帧实际列取值语义）。"""
        from core.strategy.dna import SignalRole

        dna = make_dna()
        cross_axis = make_signal_gene(role=SignalRole.ENTRY_TRIGGER)
        cross_axis.timeframe = "1d"  # 跨轴基因
        unregistered = make_signal_gene(
            indicator="NoSuchIndicator", params={},
            role=SignalRole.ENTRY_GUARD, condition_type="gt",
            condition_value=1.0)
        bad_params = make_signal_gene(
            indicator="MACD", params={},  # macd naming 解析抛 KeyError
            role=SignalRole.EXIT_GUARD, condition_type="gt",
            condition_value=1.0)
        dna.signal_genes = [cross_axis, unregistered, bad_params]
        h = _Harness(tmp_path, dna=dna, window_bars=10)
        ex = h.make_executor()
        assert ex._resolve_snapshot_columns() == [], "三防御列均不入快照面"

        (tmp_path / "b").mkdir()  # _Harness 的 data_dir 非递归建——子目录分置
        h2 = _Harness(tmp_path / "b", window_bars=10)
        ex2 = h2.make_executor()
        assert "rsi_14" in ex2._resolve_snapshot_columns()  # 对照：正常列入面
        assert ex2._indicator_snapshot(h2.df, 5) == {}, \
            "纯 K 线帧无快照列——该列跳过（帧实际列取值）"


class TestPrepareWhitelistSecondGate:
    """挂载双白名单装载第二道（change paper-mount-and-concurrency
    任务 3）：绕过受理层直插库行的双维非法任务 prepare 拒绝转 error
    （error_detail 双维分立可辨；数据保留）。"""

    def test_symbol_outside_whitelist_rejected_to_error(self, tmp_path):
        """③非白名单 symbol 直插行：prepare→error+
        symbol_not_supported_by_platform。"""
        h = _Harness(tmp_path, window_bars=10)
        # 直插绕过受理层（create_task 仓库直建不校验白名单）
        paper.create_task(
            h.db, strategy_id="strat-1", task_id="pt_wl000000001",
            dna_snapshot_json=make_dna().to_json(),
            symbol="SOLUSDT", timeframe="4h",
            research_status_snapshot="unverified")
        row = paper.get_task(h.db, "pt_wl000000001")
        ex = PaperTaskExecutor(
            h.db, row, registry=h.registry, start_after=_REPLAY_ALL)
        assert ex.prepare() is False
        task = paper.get_task(h.db, "pt_wl000000001")
        assert task["status"] == "error"
        assert "symbol_not_supported_by_platform" in task["error_detail"]
        assert "SOLUSDT" in task["error_detail"], "detail 含违规值可辨"

    def test_timeframe_outside_whitelist_rejected_to_error(self, tmp_path):
        """③非六档 timeframe（30m）直插行：prepare→error+
        timeframe_not_supported_by_platform。"""
        h = _Harness(tmp_path, window_bars=10)
        paper.create_task(
            h.db, strategy_id="strat-1", task_id="pt_wl000000002",
            dna_snapshot_json=make_dna(timeframe="30m").to_json(),
            symbol="BTCUSDT", timeframe="30m",
            research_status_snapshot="unverified")
        row = paper.get_task(h.db, "pt_wl000000002")
        ex = PaperTaskExecutor(
            h.db, row, registry=h.registry, start_after=_REPLAY_ALL)
        assert ex.prepare() is False
        task = paper.get_task(h.db, "pt_wl000000002")
        assert task["status"] == "error"
        assert "timeframe_not_supported_by_platform" \
            in task["error_detail"]
        assert "30m" in task["error_detail"]

    def test_whitelist_row_passes_second_gate(self, tmp_path):
        """白名单内行不受第二道影响（正常装载路径零回归——准备就绪
        返 True 面）。"""
        h = _Harness(tmp_path, window_bars=10)
        row = paper.get_task(h.db, h.task_id)
        ex = PaperTaskExecutor(
            h.db, row, registry=h.registry, start_after=_REPLAY_ALL)
        assert ex.prepare() is True


class TestUnlimitedWindowNeverAutoConcludes:
    """不限期窗口行为锚（change paper-mount-and-concurrency 归属组
    S4/验收 8）：window_bars=None 跨多 bar（200 根 > 缺省 180）无自动
    终止——与同数据同跑法的显式窗口对照，差异唯一归因窗口编码
    （None 是「不限期」而非「未到点」）。"""

    def test_none_runs_all_bars_still_running(self, tmp_path):
        """None 跑满 200 根（超缺省 180 窗口）：仍 running、零结题
        回调——不限期=不自动结题。"""
        h = _Harness(tmp_path, window_bars=None)
        ex = h.make_executor()
        paper.update_task(  # 模拟拾取写 running（迁移链+task_state 前置）
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = h.task()
        assert task["status"] == "running", "不限期跨 200 根不自动结题"
        assert task["bars_processed"] == _N_BARS, "全帧消化无截断"
        assert h.concluded == [], "零结题回调"

    def test_explicit_window_same_run_concludes_control(self, tmp_path):
        """对照组：window_bars=200（=_N_BARS）同数据同跑法到点
        completed——证明上例仍 running 是 None 编码生效而非跑法未达
        判定点。"""
        h = _Harness(tmp_path, window_bars=_N_BARS)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        assert h.task()["status"] == "completed"
        assert h.concluded == [(h.task_id, "completed")]


class TestMinNotionalSignalRow:
    """④below_min_notional 拒因行落 pt_signal（change paper-
    depletion-terminal 任务 2 完成标准④——change-2 任务 4 承接点）：
    exec_bar_time=执行尝试 bar、bar_time=意图产生 bar。"""

    def test_rejected_row_persisted(self, tmp_path, monkeypatch):
        """构造：初始资金足以过 insufficient_funds（qty>0）但名义
        低于阈值→开仓拒绝行落库（seq=4、拒因 below_min_notional）。"""
        from core.paper_trading import min_notional as mn

        h = _Harness(tmp_path, window_bars=30)
        # 阈值注入（装载表写回——executor 读表按 symbol 取）
        mn.MIN_NOTIONAL.clear()
        mn.MIN_NOTIONAL.update({"BTCUSDT": 1e9, "ETHUSDT": 20.0})
        try:
            ex = h.make_executor()
            paper.update_task(
                h.db, h.task_id, status="running",
                started_at="2026-10-28T00:00:00+00:00")
            rsi = [50.0] * 200
            rsi[5] = 20.0
            frame = h.enhanced.copy()
            frame["rsi_14"] = rsi
            ex.process_frames(frame, 0)
        finally:
            mn.MIN_NOTIONAL.clear()
            mn.MIN_NOTIONAL.update(mn.MIN_NOTIONAL_BUILTIN)

        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        rejected = [r for r in rows
                    if r["reject_reason"] == "below_min_notional"]
        assert len(rejected) == 1
        row = rejected[0]
        assert row["outcome"] == "rejected"
        assert row["signal_kind"] == "open"
        assert row["seq"] == 4
        assert row["bar_time"] == h.enhanced.index[5].isoformat(), \
            "bar_time=意图产生 bar（决策锚）"
        assert row["exec_bar_time"] == h.enhanced.index[6].isoformat(), \
            "exec_bar_time=执行尝试 bar（次根）"
        # 无成交发生（拒绝不落账）
        assert paper.list_fills(h.db, h.task_id, limit=1000) == []


class TestDepletedTerminal:
    """枯竭终态链路（change paper-depletion-terminal 任务 3）：证据
    连续计数四径+判定序+reason 分派防覆写+恢复重数+空仓前置。

    构造通道：装载表注入 BTCUSDT 高阈值（每次 open 定仓名义必低于）
    ——rsi 覆写控制信号 bar；阈值中途调整构造清零径。
    """

    @staticmethod
    def _inject_thresholds(btc: float):
        from core.paper_trading import min_notional as mn

        mn.MIN_NOTIONAL.clear()
        mn.MIN_NOTIONAL.update({"BTCUSDT": btc, "ETHUSDT": 20.0})

    @staticmethod
    def _restore_thresholds():
        from core.paper_trading import min_notional as mn

        mn.MIN_NOTIONAL.clear()
        mn.MIN_NOTIONAL.update(mn.MIN_NOTIONAL_BUILTIN)

    @staticmethod
    def _signal_bars(bars):
        """bars: [(i, rsi_value), ...]——其余 bar rsi=50（无信号）。"""
        rsi = [50.0] * 200
        for i, v in bars:
            rsi[i] = v
        return rsi

    def _run(self, tmp_path, rsi, *, window_bars=60, stop_loss_pct=None):
        h = _Harness(tmp_path, window_bars=window_bars,
                     stop_loss_pct=stop_loss_pct)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        frame = h.enhanced.copy()
        frame["rsi_14"] = rsi
        ex.process_frames(frame, 0)
        return h, ex

    def test_depleted_triggers_at_third_rejection(self, tmp_path):
        """①累计 3 次（中间隔无信号 bar——证据连续）且空仓→stopped+
        depleted+终态回调触发；⑥三次拒绝行在 pt_signal 可查。"""
        self._inject_thresholds(1e9)
        try:
            # bar5/12/20 三次开仓信号（中间隔无信号 bar 计数保持）
            h, ex = self._run(
                tmp_path, self._signal_bars(
                    [(5, 20.0), (12, 20.0), (20, 20.0)]))
        finally:
            self._restore_thresholds()
        task = h.task()
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "depleted", "枯竭终态 reason"
        assert task["stopped_at"] is not None
        assert (h.task_id, "stopped") in h.concluded, "终态回调触发（结题链）"
        # C4-05/T-4：task_stopped 推送 payload 透传 stop_reason=depleted
        stopped_pushes = [p for p in h.pushes
                          if p.get("type") == "task_stopped"]
        assert stopped_pushes and stopped_pushes[-1]["stop_reason"] == \
            "depleted", "WS 推送面 reason 透传"
        # ⑥计数证据可查：三次 below_min_notional 拒绝行
        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        rejects = [r for r in rows
                   if r["reject_reason"] == "below_min_notional"]
        assert len(rejects) == 3
        # 触发 bar=第三次拒绝所在 bar（处理完后转 stopped）
        assert rejects[-1]["exec_bar_time"] == \
            h.enhanced.index[21].isoformat()
        assert task["last_bar_time"] == h.enhanced.index[21].isoformat()

    def test_open_fill_resets_streak(self, tmp_path):
        """②清零径：两拒→开仓成交（阈值中途撤）→归零→重新累积 3 次
        才触发（成交后共 5 拒不触发——归零重数证据）。"""
        from core.paper_trading import min_notional as mn

        h = _Harness(tmp_path, window_bars=80)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        frame = h.enhanced.copy()
        rsi = self._signal_bars([(5, 20.0), (12, 20.0)])
        frame["rsi_14"] = rsi

        self._inject_thresholds(1e9)
        try:
            ex.process_frames(frame.iloc[:14].copy(), 0)  # 两拒（计数 2）
            assert ex._min_notional_streak == 2
            # 阈值撤回（装载表恢复内置——成交可得）
            mn.MIN_NOTIONAL.clear()
            mn.MIN_NOTIONAL.update(mn.MIN_NOTIONAL_BUILTIN)
            # bar20 信号→bar21 成交→计数归零
            frame2 = h.enhanced.copy()
            frame2["rsi_14"] = self._signal_bars([(20, 20.0)])
            ex.process_frames(frame2.iloc[:22].copy(), 0)
            assert ex._min_notional_streak == 0, "开仓成交归零"
            assert ex.ledger.position is not None
            # 归零径断言面收敛至此（清零语义+不触发）；归零后重新
            # 累积至触发的完整径由 test_depleted_triggers_at_third_
            # rejection 承载（触发条件不重复断言）
        finally:
            self._restore_thresholds()
        assert h.task()["status"] == "running", "两拒+成交不触发"

    def test_sparse_signal_streak_survives_gap(self, tmp_path):
        """②保持径一（显式）：信号稀疏策略跨长无信号期计数不丢——
        两拒后穿插 30+ 根无信号 bar，第三拒仍触发。"""
        self._inject_thresholds(1e9)
        try:
            h, ex = self._run(
                tmp_path, self._signal_bars(
                    [(5, 20.0), (12, 20.0), (48, 20.0)]))
        finally:
            self._restore_thresholds()
        assert h.task()["status"] == "stopped"
        assert h.task()["stop_reason"] == "depleted", \
            "跨长无信号期计数不丢（第三拒触发）"

    def test_insufficient_funds_gap_full_flow(self, tmp_path):
        """②保持径二完整流（C4-03/F-1+T-2）：两 below 拒→穿插
        insufficient_funds 拒（balance 归零构造）→计数仍 2 不提前
        触发→恢复 balance 后第三 below 拒才触发——若 insufficient
        误计将在穿插 bar 当拍提前 depleted（断言 running 红出）。"""
        h = _Harness(tmp_path, window_bars=80)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        self._inject_thresholds(1e9)
        try:
            # 两 below 拒（bar5/bar12 信号）
            frame = h.enhanced.copy()
            frame["rsi_14"] = self._signal_bars([(5, 20.0), (12, 20.0)])
            ex.process_frames(frame.iloc[:14].copy(), 0)
            assert ex._min_notional_streak == 2
            # 穿插 insufficient：balance 归零（开仓定仓 qty=0 拒因
            # insufficient_funds）——bar20 信号在 bar21 执行
            ex.ledger.balance = 0.0
            frame2 = h.enhanced.copy()
            frame2["rsi_14"] = self._signal_bars([(20, 20.0)])
            ex.process_frames(frame2.iloc[:22].copy(), 0)
            assert ex._min_notional_streak == 2, \
                "insufficient_funds 拒不递增（误计=3 将当拍触发）"
            assert h.task()["status"] == "running", "不提前触发"
            rows = paper.list_signals(h.db, h.task_id, limit=1000)
            assert [r["reject_reason"] for r in rows][-1] == \
                "insufficient_funds", "穿插拒因实录在库"
            # 恢复 balance：第三 below 拒（bar28 信号）触发
            ex.ledger.balance = 10_000.0
            frame3 = h.enhanced.copy()
            frame3["rsi_14"] = self._signal_bars([(28, 20.0)])
            ex.process_frames(frame3.iloc[:30].copy(), 0)
        finally:
            self._restore_thresholds()
        assert h.task()["status"] == "stopped"
        assert h.task()["stop_reason"] == "depleted", \
            "穿插 insufficient 后第三 below 拒正常触发"

    def test_context_invalid_rejection_does_not_count(self, tmp_path):
        """②保持径二补齐（C4-03/T-2）：context_invalid 拒因（有仓
        开仓——解释器语义下自然链路不可达〔空仓才产 open〕，白盒
        _execute 消费面断言计数不动）。"""
        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        frame = h.enhanced.copy()
        frame["rsi_14"] = self._signal_bars([(5, 20.0)])
        ex.process_frames(frame.iloc[:7].copy(), 0)  # 开仓成交→持仓
        assert ex.ledger.position is not None
        streak_before = ex._min_notional_streak
        from core.paper_trading.kernel.types import Intent

        bar = ex._closed_bar(h.enhanced, 7)
        events, sig_rows = [], []
        ex._execute(  # 白盒：有仓语境直发 open 意图
            Intent(kind="open", direction="long"), bar, events, sig_rows,
            seq=4, signal_bar_time="t", gene_hits=[],
            indicator_snapshot={})
        assert sig_rows and sig_rows[0]["reject_reason"] == \
            "context_invalid", "有仓开仓拒因实录"
        assert ex._min_notional_streak == streak_before, \
            "context_invalid 拒因不计数不中断"

    def test_recovery_restarts_count(self, tmp_path):
        """③恢复场景：计数 2 崩溃→重启（执行器重建）→归零重数——
        重建后两拒不触发、第三拒触发。"""
        self._inject_thresholds(1e9)
        try:
            # 段一：两拒后丢弃执行器（崩溃）——截帧 14 根（窗口 80
            # 不到点保持 running）
            h = _Harness(tmp_path, window_bars=80)
            ex = h.make_executor()
            paper.update_task(
                h.db, h.task_id, status="running",
                started_at="2026-10-28T00:00:00+00:00")
            frame0 = h.enhanced.copy()
            frame0["rsi_14"] = self._signal_bars([(5, 20.0), (12, 20.0)])
            ex.process_frames(frame0.iloc[:14].copy(), 0)
            assert ex._min_notional_streak == 2
            assert h.task()["status"] == "running"
            # 段二：重建（from_task_row——恢复链）后计数归零重数
            restored = PaperTaskExecutor.from_task_row(
                h.db, paper.get_task(h.db, h.task_id),
                registry=h.registry,
                push=lambda tid, p: None,
                on_concluded=lambda tid, st: None,
                start_after="2000-01-01T00:00:00+00:00")
            assert restored._min_notional_streak == 0, \
                "恢复不持久化重启归零"
            frame = h.enhanced.copy()
            frame["rsi_14"] = self._signal_bars([(20, 20.0), (28, 20.0)])
            restored.process_frames(frame.iloc[:30].copy(), 0)
            assert h.task()["status"] == "running", \
                "重建后两拒不触发（计数从零重数）"
            frame2 = h.enhanced.copy()
            frame2["rsi_14"] = self._signal_bars([(35, 20.0)])
            restored.process_frames(frame2.iloc[:37].copy(), 0)
        finally:
            self._restore_thresholds()
        assert h.task()["status"] == "stopped"
        assert h.task()["stop_reason"] == "depleted", "重数第三次触发"

    def test_window_wins_over_depleted_same_bar(self, tmp_path):
        """④判定序真对抗形态（C4-02/T-1+F-4）：第三 below 拒与 window
        到点同 bar——streak 恰在 window 到点 bar 达 3，判定序 window
        先→completed（若 depleted 判定前置则 stopped/depleted 红——
        变异 D1 杀变锚）。

        构造：window=4（第 4 根=bar3 处理完到点）；信号 bar0/1/2
        连续→执行 bar1（拒1）/bar2（拒2）/bar3（拒3——该 bar 处理完
        bars=4=window 到点，streak 与 window 同 bar 达 3）。"""
        self._inject_thresholds(1e9)
        try:
            h, ex = self._run(
                tmp_path, self._signal_bars(
                    [(0, 20.0), (1, 20.0), (2, 20.0)]), window_bars=4)
        finally:
            self._restore_thresholds()
        # 第三拒已计数（对抗前提核证——streak 与 window 同 bar 达 3）
        assert ex._min_notional_streak == 3, "第三拒与 window 同 bar 发生"
        assert h.task()["status"] == "completed", "window 判定序先赢"
        assert h.task()["stop_reason"] is None, "completed 无 stop_reason"
        # 三次拒绝行在库（枯竭证据同批落库——计数证据面完整）
        rows = paper.list_signals(h.db, h.task_id, limit=1000)
        assert len([r for r in rows
                    if r["reject_reason"] == "below_min_notional"]) == 3

    def test_position_holds_streak_no_trigger(self, tmp_path):
        """⑦空仓前置显式：持仓中 streak 累积（白盒置位模拟防御分支
        形态）不触发——空仓前置是防御面（自然路径下持仓必经开仓
        成交即清零，streak 与持仓不可同时自然出现）。"""
        from core.paper_trading import min_notional as mn

        h = _Harness(tmp_path, window_bars=30)
        ex = h.make_executor()
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        frame = h.enhanced.copy()
        frame["rsi_14"] = self._signal_bars([(5, 20.0)])
        ex.process_frames(frame.iloc[:7].copy(), 0)  # 开仓成交
        assert ex.ledger.position is not None
        ex._min_notional_streak = 3  # 白盒模拟防御形态
        ex._evaluate_conclusion(
            ex.ledger.snapshot(frame["close"].iloc[6], 6))
        assert ex._concluded_status is None, \
            "持仓中 streak≥N 不触发（空仓前置）"

    def test_depleted_reason_not_overwritten(self, tmp_path):
        """⑧stop_reason 终值：depleted 置位后分派点到达（含同帧配置
        stop_loss_pct 的场景——停止线未触及），终值 depleted 不被
        stop_condition 覆写；对照锚：stop_loss_pct 先命中（线触及）
        →stop_condition（判定序）。"""
        # 形态 A：stop_loss_pct 配置在但未触线——depleted 触发
        self._inject_thresholds(1e9)
        try:
            h, ex = self._run(
                tmp_path, self._signal_bars(
                    [(5, 20.0), (12, 20.0), (20, 20.0)]),
                stop_loss_pct=0.01)  # 线=9900（余额不动不触线）
        finally:
            self._restore_thresholds()
        assert h.task()["status"] == "stopped"
        assert h.task()["stop_reason"] == "depleted", \
            "分派点消费伴生 reason——不被 stop_condition 覆写"

        # 形态 B（对照）：stop_loss_pct 先命中→stop_condition
        from core.paper_trading import min_notional as mn

        case_b = tmp_path / "case_b"
        case_b.mkdir()
        h2 = _Harness(case_b, window_bars=30, stop_loss_pct=0.01)
        ex2 = h2.make_executor()
        paper.update_task(
            h2.db, h2.task_id, status="running",
            started_at="2026-10-28T00:00:00+00:00")
        # 白盒置位 streak=3 + 构造权益触线（判定序锚：stop_loss_pct
        # 分支先 return——reason=stop_condition）
        ex2._min_notional_streak = 3
        ex2.ledger.balance = 9_000.0  # 空仓 equity=balance<9900 线（触线）
        snap = ex2.ledger.snapshot(9_000.0, 5)
        ex2._evaluate_conclusion(snap)
        assert ex2._concluded_status == "stopped"
        assert ex2._concluded_reason == "stop_condition", "判定序锚"
