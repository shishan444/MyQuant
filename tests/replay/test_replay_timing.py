"""时序纪律验收（L2-D 精炼）——辅周期可见性契约+段边界。"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.replay.feeder import ReplayFeeder
from tests.replay._env import ReplayEnv, make_mtf_dna_main4h


def _feeder_loaded(tmp_path):
    # 主轴四维预算含 1d 辅轴折算（200 根 1d≈1200 根 4h 前缀）——
    # 主数据 1800 根（warmup 1200+区间 300+裕量）、辅 520 根
    env = ReplayEnv(tmp_path, dna=make_mtf_dna_main4h(), n_bars=1800)
    env.add_mtf_aux(freq="1d", n=520)
    start, end = env.range(head_bars=300)
    feeder = ReplayFeeder(
        env.data_dir, env.db, env.dna, "BTCUSDT", "4h", start, end)
    feeder.load()
    return feeder


class TestAuxVisibility:
    """收盘时刻 ≤ 当前含等号契约（可见性两向）。"""

    def test_segments_count_and_no_future(self, tmp_path):
        """段边界正确性：MTF 多段；每段辅帧切片末根收盘时刻 ≤ 段
        首主 bar 收盘时刻（切片不含未来根——越一刻不可见向）。"""
        feeder = _feeder_loaded(tmp_path)
        segs = list(feeder.segments())
        assert len(segs) >= 2, f"MTF 多段（实测 {len(segs)}）"
        tf_delta = pd.Timedelta(minutes=240)
        for seg_start, aux_frames in segs:
            aux_df = aux_frames.get("1d")
            if aux_df is None or aux_df.empty:
                continue
            aux_last_close = aux_df.index[-1] + pd.Timedelta(minutes=1440)
            seg_first_close = feeder.main.index[seg_start] + tf_delta
            assert aux_last_close <= seg_first_close, (
                f"段 {seg_start} 辅帧切片含未来根：辅末收盘 "
                f"{aux_last_close} > 主首收盘 {seg_first_close}")

    def test_visibility_equal_close_included(self, tmp_path):
        """含等号向：辅 bar 收盘时刻恰等于主 bar 收盘时刻→该辅 bar
        可见（切片包含它）。"""
        feeder = _feeder_loaded(tmp_path)
        segs = list(feeder.segments())
        # 第二段起每段首根=可见集增长点：辅切片长度单调不减
        lengths = [segs[i][1]["1d"].shape[0] for i in range(len(segs))]
        assert lengths == sorted(lengths), "可见集单调不减（含等号推进）"
        assert lengths[-1] > lengths[0], "可见集确实增长（分段有意义）"

    def test_single_tf_single_segment(self, tmp_path):
        """L2-D4：单周期策略 segments 产单段全区间+空辅帧表。"""
        env = ReplayEnv(tmp_path, n_bars=400)
        start, end = env.range(head_bars=200)
        feeder = ReplayFeeder(
            env.data_dir, env.db, env.dna, "BTCUSDT", "4h", start, end)
        feeder.load()
        segs = list(feeder.segments())
        assert len(segs) == 1
        assert segs[0][1] == {}
        assert segs[0][0] == 0

    def test_closed_end_semantics(self, tmp_path):
        """L2-D5 闭端：end 恰等于某主 bar 时刻→该 bar 含入（区间
        末根=bar_time==end）。"""
        env = ReplayEnv(tmp_path, n_bars=400)
        idx = env.df.index
        start, end = idx[-100].isoformat(), idx[-1].isoformat()
        feeder = ReplayFeeder(
            env.data_dir, env.db, env.dna, "BTCUSDT", "4h", start, end)
        feeder.load()
        assert feeder.main.index[-1] <= pd.Timestamp(end)
        # 主帧含请求区间末根（闭端含入）
        assert pd.Timestamp(end) in feeder.main.index
