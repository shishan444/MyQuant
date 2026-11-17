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
    """L2-A2 MTF：主 4h 辅 1d——分段驱动可完成出数（辅帧可见集
    一致性由时序纪律用例逐段锚定）。"""

    def test_mtf_segmented_run_completes(self, tmp_path):
        from tests.replay._env import make_mtf_dna_main4h

        env = ReplayEnv(tmp_path, dna=make_mtf_dna_main4h(), n_bars=1800)
        env.add_mtf_aux(freq="1d", n=520)
        start, end = env.range(head_bars=300)
        rr = ReplayRunner(env.data_dir)
        res = rr.run(env.db, env.strategy_id, start, end,
                     task_id="segm1", fee=0.001, slippage=0.0005)
        assert res.status in ("completed", "stopped")
        assert res.bars_processed > 0
