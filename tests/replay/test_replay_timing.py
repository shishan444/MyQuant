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
        首主 bar 收盘时刻（切片不含未来根——越一刻不可见向）；段
        首尾相接连续覆盖主帧全长（丢 bar 回归锚——段间隙=区间内
        主 bar 无人驱动，CARD-01）。"""
        feeder = _feeder_loaded(tmp_path)
        segs = list(feeder.segments())
        assert len(segs) >= 2, f"MTF 多段（实测 {len(segs)}）"
        tf_delta = pd.Timedelta(minutes=240)
        # 段覆盖完整性：首段起 0、相邻段首尾相接、尾段收在帧末
        assert segs[0][0] == 0
        assert segs[-1][1] == len(feeder.main) - 1, (
            f"尾段段末 {segs[-1][1]} != 帧末 {len(feeder.main) - 1}")
        for (s1, e1, _), (s2, _, _) in zip(segs, segs[1:]):
            assert s2 == e1 + 1, (
                f"段间隙丢 bar：段 [{s1},{e1}] 后下段起点 {s2}")
        for seg_start, seg_end, aux_frames in segs:
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
        lengths = [segs[i][2]["1d"].shape[0] for i in range(len(segs))]
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
        assert segs[0][2] == {}
        assert segs[0][0] == 0
        assert segs[0][1] == len(feeder.main) - 1, "单段收在帧末"

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


class TestFeedSideVisibility:
    """feed 链侧可见性契约锚（fix-replay-task-lifecycle G1-4——
    最低限度替代形制）。

    回放侧（TestAuxVisibility）已锚 ReplayFeeder 段切片的「辅收盘 ≤
    主收盘含等号」契约；本组在纸盘 feed 链的组装面同口径锚：辅帧
    进入 executor 组装面的唯一入口=executor._make_aux_handler 的
    on_aux 回调（实时链=辅 feed 闭合推送驱动，此处按契约集直接驱动
    该通道）。等号语义双侧同锚：等号辅 bar 可见、越一刻不可见。"""

    def test_on_aux_channel_visibility_contract(self, tmp_path):
        """经 on_aux 回调通道推进的辅帧可见集与契约集逐位一致（含
        等号向+越一刻向双侧锚）。"""
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry
        from core.persistence.repository import paper

        feeder = _feeder_loaded(tmp_path)
        main, aux = feeder.main, feeder._aux["1d"]
        paper.create_task(
            feeder.db_path, strategy_id="strat-1",
            dna_snapshot_json=feeder.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified", task_id="vis01")
        registry = FeedRegistry(
            feeder.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        executor = PaperTaskExecutor(
            feeder.db_path, paper.get_task(feeder.db_path, "vis01"),
            registry=registry,
            start_after=feeder.main_start.isoformat())
        on_aux = executor._make_aux_handler("1d")  # feed 推送通道形制

        main_close = main.index + pd.Timedelta(minutes=240)
        aux_close = aux.index + pd.Timedelta(minutes=1440)
        # 等号锚的构造前提：存在主 bar 其收盘时刻恰等于某辅 bar
        # 收盘时刻（4h×6=24h 网格对齐——合成数据时间轴保证）
        equal_hits = sum(
            1 for mc in main_close if mc in set(aux_close))
        assert equal_hits >= 1, "等号时刻无构造面（时间轴网格错位）"

        j0 = int(main.index.searchsorted(pd.Timestamp(feeder.main_start)))
        for i in range(j0, len(main)):
            visible = int((aux_close <= main_close[i]).sum())
            on_aux(aux.iloc[:visible], 0)  # 契约集整帧引用推送
            snap = executor._aux_frames.get("1d")
            assert snap is not None and len(snap) == visible, (
                f"bar {i}：组装面可见集 {0 if snap is None else len(snap)}"
                f" != 契约集 {visible}")
            if visible < len(aux):
                # 越一刻向：下一辅 bar 收盘已过该主 bar 收盘——不可见
                assert aux_close[visible] > main_close[i]
            if visible > 0 and aux_close[visible - 1] == main_close[i]:
                pass  # 等号向命中：收盘相等的辅 bar 恰在可见集末位
        # 等号向总结锚：整个推进中至少一次「辅末收盘==主收盘」命中
        assert any(
            aux_close[int((aux_close <= mc).sum()) - 1] == mc
            for mc in main_close[j0:]
            if (aux_close <= mc).sum() > 0), "等号可见向未被推进命中"

    def test_replay_segments_vs_feed_assembly(self, tmp_path):
        """对照轴（G1-4 完整版判据）：同一 parquet 下 ReplayFeeder
        逐段组装的辅帧切片 vs feed 链逐 bar 组装的可见集——段边界
        bar 上两侧切片逐字段一致（index+values）；段内 bar 上回放
        快照不超前 feed 侧该 bar 可见集（不未来性）。"""
        feeder = _feeder_loaded(tmp_path)
        main, aux = feeder.main, feeder._aux["1d"]
        main_close = main.index + pd.Timedelta(minutes=240)
        aux_close = aux.index + pd.Timedelta(minutes=1440)
        j0 = int(main.index.searchsorted(pd.Timestamp(feeder.main_start)))
        # feed 链逐 bar 可见集（契约推进——组装通道同上一用例）
        barwise_len = {
            i: int((aux_close <= main_close[i]).sum())
            for i in range(j0, len(main))}

        segs = [s for s in feeder.segments() if s[1] >= j0]
        assert len(segs) >= 2, "MTF 多段（区间内）"
        for seg_start, seg_end, aux_frames in segs:
            seg_aux = aux_frames.get("1d")
            # 段边界追平：段尾 bar 上回放切片 == feed 侧可见集（逐字段）
            feed_cut = aux.iloc[: barwise_len[seg_end]]
            assert seg_aux.index.equals(feed_cut.index), (
                f"段尾 {seg_end} 切片 index 与 feed 组装不一致")
            assert seg_aux.equals(feed_cut), (
                f"段尾 {seg_end} 切片值与 feed 组装不一致")
            # 段内不超前：段内任意 bar 上回放快照长度 ≤ feed 侧该 bar
            # 可见集（快照滞后于逐 bar 推进合法，超前=看见未来）
            for i in range(max(seg_start, j0), seg_end + 1):
                assert len(seg_aux) <= barwise_len[i], (
                    f"段内 bar {i} 回放快照超前 feed 可见集")
