"""同构验收（锚①）——分段驱动 vs 逐帧注入逐笔对账（L2-A 精炼）。

方法论（同码抽出性质）：两侧是同一执行核心的两条喂帧路径——不存在
两套实现的系统差，主对照轴用真实参数面（非零费滑）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.validation_service import dna_indicator_names
from core.analysis.warmup_loader import load_with_warmup
from core.features.indicators import compute_all_indicators
from core.persistence.repository import paper
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry
from core.replay import ReplayRunner
from tests.replay._env import ReplayEnv


def _frame_by_frame_run(env, start, end, task_id):
    """逐帧模拟实时驱动（对照轴）：executor 直构+全帧一次喂（单段
    特例——与分段驱动同核心不同喂法；真实参数面同 fee/slip）。"""
    paper.create_task(
        env.db, strategy_id=env.strategy_id,
        dna_snapshot_json=env.dna.to_json(),
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot="unverified",
        fee=0.001, slippage=0.0005, task_id=task_id)
    row = paper.get_task(env.db, task_id)
    registry = FeedRegistry(
        env.data_dir,
        rest_fetcher=lambda *a, **k: pd.DataFrame(),
        funding_refresher=lambda **k: pd.DataFrame())
    ex = PaperTaskExecutor(env.db, row, registry=registry,
                           start_after=start)
    paper.update_task(env.db, task_id, status="running")
    names = dna_indicator_names(env.dna)
    df, _start_ts = load_with_warmup(
        env.data_dir, "BTCUSDT", "4h", start, end, names)
    ex.process_frames(compute_all_indicators(df), 0)
    if ex._concluded_status is None:
        ex._finalize("completed")


class TestSingleTfEquivalence:
    """L2-A1 单周期：分段 vs 逐帧三表逐位一致。"""

    def test_fills_and_equity_identical(self, tmp_path):
        env = ReplayEnv(tmp_path, n_bars=600)
        start, end = env.range(head_bars=300)
        rr = ReplayRunner(env.data_dir)
        r_seg = rr.run(env.db, env.strategy_id, start, end,
                       task_id="seg01", fee=0.001, slippage=0.0005)
        _frame_by_frame_run(env, start, end, "ff01")
        f_seg = paper.list_fills(env.db, "seg01", limit=1000)
        f_ff = paper.list_fills(env.db, "ff01", limit=1000)
        assert len(f_seg) == len(f_ff), (
            f"成交笔数一致（分段 {len(f_seg)} vs 逐帧 {len(f_ff)}）")
        for a, b in zip(f_seg, f_ff):
            assert a["kind"] == b["kind"]
            assert a["quantity"] == pytest.approx(b["quantity"])
            assert a["price"] == pytest.approx(b["price"])
        t_ff = paper.get_task(env.db, "ff01")
        assert r_seg.total_trades == t_ff["total_trades"]
        assert r_seg.total_pnl == pytest.approx(t_ff["total_pnl"])


class TestMtfEquivalence:
    """L2-A2 MTF：主 4h 辅 1d——分段驱动区间全覆盖+分段与逐帧
    逐笔一致（CARD-01 回归锚：丢 bar 缺陷的两条验收判据）。"""

    def test_mtf_segmented_run_completes(self, tmp_path):
        """分段驱动区间全覆盖：bars_processed==区间根数（弱断言
        >0 升级——丢 bar 缺陷下此断言红：实测 50/300）。"""
        from tests.replay._env import make_mtf_dna_main4h

        env = ReplayEnv(tmp_path, dna=make_mtf_dna_main4h(), n_bars=1800)
        env.add_mtf_aux(freq="1d", n=520)
        start, end = env.range(head_bars=300)
        rr = ReplayRunner(env.data_dir)
        res = rr.run(env.db, env.strategy_id, start, end,
                     task_id="segm1", fee=0.001, slippage=0.0005)
        assert res.status in ("completed", "stopped")
        assert res.bars_processed == 300, (
            f"区间主 bar 全驱动（实测 {res.bars_processed}/300）")

    def test_mtf_segmented_vs_frame_by_frame_identical(self, tmp_path):
        """分段 vs 逐帧逐笔一致（R4.1 验收场景）：恒触发 mtf dna
        （两层 entry 恒真——两轴都有成交，对账有实质面）；对照轴
        逐 bar 喂帧，辅帧可见集按契约手写推进（辅收盘时刻 ≤ 主收盘
        含等号——独立于 feeder 段实现）。"""
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry
        from core.replay.feeder import ReplayFeeder
        from tests.replay._env import make_mtf_loop_dna_main4h

        env = ReplayEnv(tmp_path, dna=make_mtf_loop_dna_main4h(),
                        n_bars=1800)
        env.add_mtf_aux(freq="1d", n=520)
        start, end = env.range(head_bars=300)
        r_seg = ReplayRunner(env.data_dir).run(
            env.db, env.strategy_id, start, end,
            task_id="segm2", fee=0.001, slippage=0.0005)

        # 逐帧对照轴：契约可见性推进+executor 直构逐 bar 喂
        feeder = ReplayFeeder(
            env.data_dir, env.db, env.dna, "BTCUSDT", "4h", start, end)
        feeder.load()
        main, aux = feeder.main, feeder._aux["1d"]
        paper.create_task(
            env.db, strategy_id=env.strategy_id,
            dna_snapshot_json=env.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            fee=0.001, slippage=0.0005, task_id="ffm2")
        registry = FeedRegistry(
            env.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        ex = PaperTaskExecutor(
            env.db, paper.get_task(env.db, "ffm2"), registry=registry,
            start_after=feeder.main_start.isoformat())
        assert ex.prepare()
        paper.update_task(env.db, "ffm2", status="running")
        main_close = main.index + pd.Timedelta(minutes=240)
        aux_close = aux.index + pd.Timedelta(minutes=1440)
        j0 = int(main.index.searchsorted(pd.Timestamp(feeder.main_start)))
        for i in range(j0, len(main)):
            visible = int((aux_close <= main_close[i]).sum())
            ex._aux_frames["1d"] = aux.iloc[:visible]
            ex.process_frames(main.iloc[: i + 1], i)
            if ex._concluded_status is not None:
                break
        if ex._concluded_status is None:
            ex._finalize("completed")

        f_seg = paper.list_fills(env.db, "segm2", limit=2000)
        f_ff = paper.list_fills(env.db, "ffm2", limit=2000)
        assert len(f_seg) > 0, "恒触发构造下分段轴有成交（对账实质面）"
        assert len(f_seg) == len(f_ff), (
            f"成交笔数一致（分段 {len(f_seg)} vs 逐帧 {len(f_ff)}）")
        for a, b in zip(f_seg, f_ff):
            assert a["kind"] == b["kind"]
            assert a["quantity"] == pytest.approx(b["quantity"])
            assert a["price"] == pytest.approx(b["price"])
        t_ff = paper.get_task(env.db, "ffm2")
        assert r_seg.total_trades == t_ff["total_trades"]
        assert r_seg.total_pnl == pytest.approx(t_ff["total_pnl"])
