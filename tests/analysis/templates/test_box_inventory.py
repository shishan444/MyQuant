"""箱体盘点模板测试（add-evaluation-ruler 任务 4.1-4.3）。

合成样例全部人工独立推导（恒定 close/对称 high-low 使 width 解析
可控）；demo 箱账复算锚（任务 4.2）期望值来自 r9_d1_out.txt 冻结
数字。真实数据右端注记：demo 的 box_inventory 实际未截右端（用
生成时点全量数据，右端 2026-11-09），与任务书「右端 2026-11-07」
表述分立——按 demo 实际口径对账（右端 2026-11-07 时 BTC 15m 为
48 箱≠49，末端段在 11-07 后跨过 min_len 门槛）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.box_inventory import box_inventory, compute_box_segments
from core.data.fapi.rest import default_data_dir

RANGES = [{"symbol": "TESTUSDT", "timeframe": "1h",
           "start": "2024-01-01", "end": "2024-02-01"}]


def _mk_df(h_spreads, close=100.0, start="2024-01-01", freq="1h"):
    """恒定 close、对称高低差的构造器：width=2×spread/滚动均值。

    h_spreads[i] 为 bar i 的 (high−close)（=（close−low））。
    rolling(3) 下 width 取决于窗内最大 spread 与 spread 序列均值——
    mid=100（恒定 close），width=(2×max_spread)/100_moving_mean。
    """
    n = len(h_spreads)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC",
                        name="timestamp")
    spread = pd.Series(h_spreads, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + spread, "low": close - spread,
        "close": close, "volume": 1.0,
    }, index=idx)


class TestBoxSegments:
    """段提取与边界族（任务 4.1 完成标准）——人工推导。

    构造基准（恒 close=100）：窗内 max(spread)=s 时 hi−lo=2s、
    mid=100 → width=2s/100——s=4 → 0.08 恰等号（箱体态）、s=5 →
    0.10（宽态）。rolling 窗含当根前 window 根：纯箱区（连续 spread=4）
    前后各有 window−1 根过渡 False（窗内残留宽态 bar）。
    """

    def _spreads_box_then_wide(self):
        # 宽3 + 纯箱7 + 宽3：True 段=[5,9] 长 5（纯箱区 7−(3−1)=5）
        return [5.0] * 3 + [4.0] * 7 + [5.0] * 3

    def test_nan_period_never_box(self):
        """NaN 期（窗口不满）不成箱：全 spread=4 序列的箱从 bar2 起
        （bar0-1 的 rolling(3) 为 NaN→False）。"""
        boxes = compute_box_segments(
            _mk_df([4.0] * 6), request_start=None,
            window=3, min_len=2, width_thr=0.08)
        assert len(boxes) == 1
        assert boxes[0]["start"] == _mk_df([4.0] * 6).index[2]

    def test_exact_min_len_forms_min_len_minus_one_not(self):
        """恰 min_len 成箱 / min_len−1 不成（True 段 [5,9] 长 5）。"""
        df = _mk_df(self._spreads_box_then_wide())
        b5 = compute_box_segments(df, request_start=None,
                                  window=3, min_len=5, width_thr=0.08)
        assert len(b5) == 1 and b5[0]["dur"] == 5
        b6 = compute_box_segments(df, request_start=None,
                                  window=3, min_len=6, width_thr=0.08)
        assert b6 == [], "min_len−1 不成箱"

    def test_width_equality_inclusive(self):
        """width 恰等于 width_thr 按含等号处理成箱（spread=4 → 0.08；
        [5,5,4,4,4,4] 的 True 段=[4,5] 长 2，min_len=2 恰成）。"""
        boxes = compute_box_segments(
            _mk_df([5.0, 5.0, 4.0, 4.0, 4.0, 4.0]),
            request_start=None, window=3, min_len=2, width_thr=0.08)
        assert len(boxes) == 1

    def test_box_height_is_segment_width_mean(self):
        """箱高=段内 width 均值（含等号边界 bar）：窗宽 1 逐 bar 独立，
        [5,4,3,3,3,5] → width=[.10,.08,.06,.06,.06,.10]，True 段=[1,4]，
        均值=(.08+.06×3)/4=0.065。"""
        df = _mk_df([5.0, 4.0, 3.0, 3.0, 3.0, 5.0])
        boxes = compute_box_segments(
            df, request_start=None, window=1, min_len=3, width_thr=0.08)
        assert len(boxes) == 1
        assert boxes[0]["dur"] == 4
        assert boxes[0]["box_height"] == pytest.approx(0.065, rel=1e-12)

    def test_truncated_segment_restart_from_range_start(self):
        """截断语义（读法 A）：区间首 bar 落长箱体中部 → 从首 bar
        重新起算（dur=区间内长度）＋truncated_start=True；区间内部分
        不足 min_len 不成箱。"""
        df = _mk_df([4.0] * 8 + [5.0] * 3)  # 箱体态 bar2-7（窗 3 收敛后）
        mid = df.index[5]
        boxes = compute_box_segments(
            df, request_start=mid, window=3, min_len=2, width_thr=0.08)
        # 区间内段=[5,7] 长 3 ≥2 成箱；前缀末 bar（bar4）箱体态→截断
        assert len(boxes) == 1
        assert boxes[0]["start"] == mid
        assert boxes[0]["dur"] == 3
        assert boxes[0]["truncated_start"] is True
        # min_len=5：区间内 3 <5 不成箱（spec 截断场景）
        none = compute_box_segments(
            df, request_start=mid, window=3, min_len=5, width_thr=0.08)
        assert none == []

    def test_untruncated_first_segment(self):
        """区间首 bar 起段但前缀非箱体态 → truncated_start=False。"""
        df = _mk_df([5.0] * 5 + [4.0] * 5 + [5.0] * 3)
        cut = df.index[3]  # 前缀全宽态；箱体段 bar5 起（bar3-5 窗含 4）
        boxes = compute_box_segments(
            df, request_start=cut, window=3, min_len=2, width_thr=0.08)
        assert len(boxes) == 1
        assert boxes[0]["truncated_start"] is False


class TestBoxInventoryTemplate:
    """模板输出、默认参数与校验（任务 4.1）。"""

    def test_timeframe_frozen_defaults(self):
        """1h/15m 不传 window/min_len 用冻结默认；显式传参覆盖。"""
        # 全箱态 window+min_len+2 根：尾部段长 min_len+1 恰成 1 箱
        for tf, w, ml in [("1h", 120, 48), ("15m", 240, 96)]:
            df = _mk_df([4.0] * (w + ml + 2))
            out = box_inventory(
                df, data_ranges=RANGES, timeframe=tf,
                request_start=df.index[2]).to_dict()
            assert out["payload"]["window"] == w
            assert out["payload"]["min_len"] == ml
            assert out["payload"]["summary"]["n_boxes"] == 1
        df = _mk_df([4.0] * 10)
        out = box_inventory(
            df, data_ranges=RANGES, timeframe="1h",
            request_start=df.index[2], window=3, min_len=2).to_dict()
        assert out["payload"]["window"] == 3, "显式传参覆盖默认"

    def test_non_frozen_timeframe_requires_params(self):
        df = _mk_df([4.0] * 10)
        with pytest.raises(ResearchError) as ei:
            box_inventory(df, data_ranges=RANGES, timeframe="4h")
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "无冻结默认" in ei.value.message
        assert ei.value.detail["frozen_defaults"]["1h"] == [120, 48]

    def test_monthly_average_synthetic_anchor(self):
        """summary 月均=按箱起点月份去重计月（合成锚：两箱跨两月 →
        2/2=1.0；窗宽 1、门槛等号、6h 频率构造）。"""
        idx = pd.DatetimeIndex(
            [f"2024-01-05 {h:02d}:00" for h in (0, 6, 12)]
            + ["2024-01-06 00:00"]
            + [f"2024-02-05 {h:02d}:00" for h in (0, 6, 12)]
            + ["2024-02-06 00:00"], tz="UTC", name="timestamp")
        spread = [4.0, 4.0, 4.0, 5.0, 4.0, 4.0, 4.0, 5.0]
        df = pd.DataFrame({
            "open": 100.0, "high": [100 + s for s in spread],
            "low": [100 - s for s in spread], "close": 100.0,
            "volume": 1.0,
        }, index=idx)
        out = box_inventory(
            df, data_ranges=RANGES, timeframe="1h",
            request_start=None, window=1, min_len=3,
            width_thr=0.08).to_dict()
        s = out["payload"]["summary"]
        assert s["n_boxes"] == 2
        assert s["months_distinct"] == 2
        assert s["boxes_per_month"] == pytest.approx(1.0)

    def test_idempotent_bitwise(self):
        df = _mk_df([5.0] * 3 + [4.0] * 7 + [5.0] * 3)
        a = box_inventory(df, data_ranges=RANGES, timeframe="1h",
                          request_start=df.index[2], window=3, min_len=2
                          ).to_dict()
        b = box_inventory(df, data_ranges=RANGES, timeframe="1h",
                          request_start=df.index[2], window=3, min_len=2
                          ).to_dict()
        assert a == b

    @pytest.mark.parametrize(
        ("kwargs", "param"),
        [pytest.param({"window": 0}, "window", id="零 window"),
         pytest.param({"min_len": 0}, "min_len", id="零 min_len"),
         pytest.param({"width_thr": 0.0}, "width_thr", id="零门槛"),
         pytest.param({"width_thr": 1.0}, "width_thr", id="满门槛")])
    def test_invalid_params_rejected(self, kwargs, param):
        df = _mk_df([4.0] * 10)
        base = {"window": 3, "min_len": 2, "width_thr": 0.08}
        base.update(kwargs)
        with pytest.raises(ResearchError) as ei:
            box_inventory(df, data_ranges=RANGES, timeframe="1h", **base)
        assert ei.value.detail["param"] == param

    @pytest.mark.parametrize(
        ("kwargs", "param"),
        [pytest.param({"window": 120.7}, "window",
                      id="浮点 window 不再 int() 洗（CARD-04）"),
         pytest.param({"window": "120"}, "window",
                      id="字符串数字 window（CARD-04）"),
         pytest.param({"window": True}, "window",
                      id="bool window（CARD-04）"),
         pytest.param({"min_len": "48"}, "min_len",
                      id="字符串数字 min_len（CARD-04）"),
         pytest.param({"width_thr": "0.08"}, "width_thr",
                      id="字符串数字 width_thr（CARD-04）")])
    def test_raw_form_rejected_no_silent_coercion(self, kwargs, param):
        """resolve 返原始形＋guard 原始形校验（CARD-04）：旧形制
        resolve_box_params 内 int(window) 会把 120.7/'120'/'1' 洗成
        合法 int 静默放行（int(True)=1 同族）——现一律 422。"""
        df = _mk_df([4.0] * 10)
        base = {"window": 3, "min_len": 2, "width_thr": 0.08}
        base.update(kwargs)
        with pytest.raises(ResearchError) as ei:
            box_inventory(df, data_ranges=RANGES, timeframe="1h", **base)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == param

    def test_missing_end_rejected(self):
        df = _mk_df([4.0] * 10)
        with pytest.raises(ResearchError, match="无开放右端"):
            box_inventory(
                df, data_ranges=[{"symbol": "T", "timeframe": "1h",
                                  "start": "2024-01-01"}],
                timeframe="1h", window=3, min_len=2)

    def test_too_few_bars_rejected(self):
        df = _mk_df([4.0])
        with pytest.raises(ResearchError, match="< 2"):
            box_inventory(df, data_ranges=RANGES, timeframe="1h",
                          window=3, min_len=2)

    def test_zero_boxes_rejected_via_min_samples_path(self):
        df = _mk_df([5.0] * 10)  # 全宽态无箱
        with pytest.raises(ResearchError, match="小样本拒绝"):
            box_inventory(df, data_ranges=RANGES, timeframe="1h",
                          window=3, min_len=2)


class TestBoxDemoReconciliation:
    """demo 箱账复算（任务 4.2）：全历史起点＋冻结参数，四轴对账
    r9_d1_out.txt（demo 生成时点全量数据，右端显式固定 2026-11-09
    ——活数据追加不破坏复算）。
    """

    MARKET = default_data_dir()
    RIGHT_EDGE = "2026-11-09"

    @pytest.mark.parametrize(
        ("sym", "tf", "n_boxes", "per_month"),
        [("BTCUSDT", "15m", 49, 2.3), ("BTCUSDT", "1h", 34, 1.9),
         ("ETHUSDT", "15m", 82, 3.3), ("ETHUSDT", "1h", 26, 1.5)])
    def test_demo_counts_and_monthly(self, sym, tf, n_boxes, per_month):
        path = self.MARKET / f"{sym}_{tf}.parquet"
        if not path.exists():
            pytest.skip(f"真实数据 {sym} {tf} 不在库（data/market_fapi）")
        df = pd.read_parquet(path)
        df = df[df.index <= pd.Timestamp(self.RIGHT_EDGE, tz="UTC")]
        out = box_inventory(
            df, data_ranges=[{"symbol": sym, "timeframe": tf,
                              "start": str(df.index[0].date()),
                              "end": self.RIGHT_EDGE}],
            timeframe=tf, request_start=None, width_thr=0.08).to_dict()
        s = out["payload"]["summary"]
        assert s["n_boxes"] == n_boxes
        assert s["boxes_per_month"] == pytest.approx(per_month, abs=0.1), \
            "月均 ±0.1 门限（任务 4.2）"
