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


class TestSignalSideParity:
    """signal 侧对账（fix-replay-task-lifecycle G1-1）：回放段组装与
    契约逐 bar 组装喂入 dna_to_signal_set 的输出，在段尾 bar 上
    entries/exits/adds/reduces/direction 五序列逐位一致——帧一致
    （G1-4）之上的 signal 求值一致（执行核心同码，差异面只剩喂帧）。"""

    def test_mtf_segment_vs_barwise_signal_parity(self, tmp_path):
        from core.replay.feeder import ReplayFeeder
        from core.strategy.executor import dna_to_signal_set
        from tests.replay._env import make_mtf_threshold_dna_main4h

        # 阈值型构造（判别力）：阈值取两轴 RSI 中位附近——辅帧末端
        # 可见集差一根即可能翻转段尾信号（恒真构造对差一不敏感）
        env = ReplayEnv(tmp_path,
                        dna=make_mtf_threshold_dna_main4h(), n_bars=1800)
        env.add_mtf_aux(freq="1d", n=520)
        start, end = env.range(head_bars=300)
        feeder = ReplayFeeder(
            env.data_dir, env.db, env.dna, "BTCUSDT", "4h", start, end)
        feeder.load()
        main, aux = feeder.main, feeder._aux["1d"]
        main_close = main.index + pd.Timedelta(minutes=240)
        aux_close = aux.index + pd.Timedelta(minutes=1440)
        j0 = int(main.index.searchsorted(pd.Timestamp(feeder.main_start)))

        def _sig_at(main_frame, aux_frame):
            sig = dna_to_signal_set(
                env.dna, main_frame,
                {"4h": main_frame, "1d": aux_frame})
            return (
                bool(sig.entries.iloc[-1]), bool(sig.exits.iloc[-1]),
                bool(sig.adds.iloc[-1]), bool(sig.reduces.iloc[-1]),
                (float(sig.entry_direction.iloc[-1])
                 if sig.entry_direction is not None else None))

        # 逐 bar 契约可见性推进（独立于 feeder 段实现——同 MTF 对账
        # 轴手写契约：辅收盘 ≤ 主收盘 含等号）
        barwise: dict[int, tuple] = {}
        for i in range(j0, len(main)):
            visible = int((aux_close <= main_close[i]).sum())
            barwise[i] = _sig_at(main.iloc[: i + 1], aux.iloc[:visible])

        # 回放段组装：段尾行五值与逐 bar 组装同 bar 值逐位一致（仅
        # 对照区间内段——warmup 前缀段（seg_end<j0）bar 侧无组装）
        mismatch = []
        checked = 0
        for seg_start, seg_end, aux_frames in feeder.segments():
            if seg_end < j0:
                continue
            seg_aux = aux_frames.get("1d", aux.iloc[:0])
            seg_sig = _sig_at(main.iloc[: seg_end + 1], seg_aux)
            checked += 1
            if seg_sig != barwise[seg_end]:
                mismatch.append(seg_end)
        assert checked >= 2, f"区间内段数不足（{checked}）——对照面空转"
        assert not mismatch, f"段尾 signal 不一致: {mismatch}"
        # 实质面：阈值型两层 AND 下 entry 非全零（对账非空转）
        assert any(v[0] for v in barwise.values()), "entry 序列全零（无实质面）"


class TestActionDirectionMatrix:
    """开/平/加/减×多/空矩阵补角（fix-replay-task-lifecycle G1-3）：
    add 动作与 short 方向的分段 vs 逐帧 fills 对账。已有角：long 开平
    （TestSingleTfEquivalence）+long 开减（make_reduce_loop_dna 族）；
    本组补 long 开加、short 开平、short 开加减三缺角。"""

    @staticmethod
    def _assert_parity(env, start, end, seg_task, ff_task):
        f_seg = paper.list_fills(env.db, seg_task, limit=2000)
        f_ff = paper.list_fills(env.db, ff_task, limit=2000)
        assert len(f_seg) > 0, "恒触发构造下分段轴有成交（对账实质面）"
        assert len(f_seg) == len(f_ff), (
            f"成交笔数一致（分段 {len(f_seg)} vs 逐帧 {len(f_ff)}）")
        for a, b in zip(f_seg, f_ff):
            assert a["kind"] == b["kind"]
            assert a["quantity"] == pytest.approx(b["quantity"])
            assert a["price"] == pytest.approx(b["price"])

    def test_long_add_loop_parity(self, tmp_path):
        """long 开/加：add 恒触发——fills 含 kind=add（add 路径实质面）。"""
        from tests.replay._env import make_add_loop_dna

        env = ReplayEnv(tmp_path, dna=make_add_loop_dna(direction="long"),
                        n_bars=600)
        start, end = env.range(head_bars=300)
        ReplayRunner(env.data_dir).run(
            env.db, env.strategy_id, start, end,
            task_id="maddl", fee=0.001, slippage=0.0005)
        _frame_by_frame_run(env, start, end, "maddf")
        self._assert_parity(env, start, end, "maddl", "maddf")
        kinds = {f["kind"] for f in paper.list_fills(
            env.db, "maddl", limit=2000)}
        assert "add" in kinds, f"add 路径未覆盖（kinds={kinds}）"

    def test_short_entry_exit_parity(self, tmp_path):
        """short 开/平：方向承载 risk_genes.direction——fills side=short。"""
        from tests.helpers.data_factory import make_dna

        env = ReplayEnv(
            tmp_path,
            dna=make_dna(direction="short", entry_condition="gt",
                         entry_value=0, exit_condition="gt",
                         exit_value=60),
            n_bars=600)
        start, end = env.range(head_bars=300)
        ReplayRunner(env.data_dir).run(
            env.db, env.strategy_id, start, end,
            task_id="msegl", fee=0.001, slippage=0.0005)
        _frame_by_frame_run(env, start, end, "mseff")
        self._assert_parity(env, start, end, "msegl", "mseff")
        seg_fills = paper.list_fills(env.db, "msegl", limit=2000)
        closes = [f for f in seg_fills if f["kind"] == "close"]
        assert closes, "short 开平角无平仓成交（矩阵角无实质面）"
        assert all(f["side"] == "short" for f in seg_fills), (
            "方向未承载到 fills side（short 角失效）")

    def test_short_add_reduce_loop_parity(self, tmp_path):
        """short 开/加/减：三动作并存（执行序确定性由执行核心单源）。"""
        from tests.replay._env import make_add_reduce_loop_dna

        env = ReplayEnv(
            tmp_path,
            dna=make_add_reduce_loop_dna(direction="short"),
            n_bars=600)
        start, end = env.range(head_bars=300)
        ReplayRunner(env.data_dir).run(
            env.db, env.strategy_id, start, end,
            task_id="msarl", fee=0.001, slippage=0.0005)
        _frame_by_frame_run(env, start, end, "msarf")
        self._assert_parity(env, start, end, "msarl", "msarf")
        kinds = {f["kind"] for f in paper.list_fills(
            env.db, "msarl", limit=2000)}
        assert {"add", "reduce"} <= kinds, (
            f"add/reduce 路径未双覆盖（kinds={kinds}）")
