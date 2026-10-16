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
from tests.helpers.data_factory import make_dna, make_ohlcv

# 合成数据基线（seed 固定——信号分布确定：RSI 首次 <30 于 i=20）
_N_BARS = 200
_SEED = 7
# 全回放边界（合成帧历史时刻均早于它——旁路 created_at 起始过滤）
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"

# kind → bar 内 seq 期望映射（八步序审计编号锚）
_SEQ_BY_KIND = {
    "exit_liquidation": 1,
    "exit_sl": 2, "exit_tp": 2,
    "close": 3, "exit_max_hold": 3, "exit_time": 3,
    "open": 4, "add": 4, "reduce": 4,
}


def _funding_frame(times: list[str], rate: float = 0.0001) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(times, utc=True), name="timestamp")
    return pd.DataFrame({"funding_rate": [rate] * len(idx)}, index=idx)


class _Harness:
    """执行器回放测试装配（库+任务行+registry+帧）。"""

    def __init__(self, tmp_path: Path, *, dna=None, window_bars=50,
                 stop_loss_pct=None, funding_times=(), leverage=1):
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
            initial_cash=10_000.0, leverage=leverage,
            window_bars=window_bars, stop_loss_pct=stop_loss_pct)

        self.registry = FeedRegistry(
            self.data_dir,
            fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: _funding_frame(funding_times))
        # 预建带 funding 索引的 feed（executor 查费率面；dummy 订阅防回收）
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.refresh()
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


class TestMtfPrepare:
    """MTF 装载校验面（修复卡 P0②——8.2 实测：试算传 None 对 MTF DNA
    恒拒；校验序=先组装（缺周期→mtf_data_missing 拒）再试算）。"""

    def _make_mtf_harness(self, tmp_path: Path, *, with_aux: bool):
        """MTF 装配（执行周期 4h 主帧+1d 辅帧按 interval 分派）。"""
        h = _Harness(tmp_path)
        daily = make_ohlcv(n=60, freq="1d", seed=7, base_price=100.0)
        if with_aux:
            save_parquet(daily, h.data_dir / "BTCUSDT_1d.parquet")
        frames = {"4h": h.df, "1d": daily}
        h.registry = FeedRegistry(
            h.data_dir,
            fetcher=lambda **k: frames[k["interval"]],
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
