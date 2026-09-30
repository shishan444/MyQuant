"""asof_completed 权威对齐模块测试（multi-timeframe-conditions 1.1/1.2）。

锚族（design T1）：
- 三投影确定样例（参数化 tf 组合与缺口索引；末 bar 窗口=+tf 对照
  旧硬编码 1 天差分）；
- asof 前视差分锚（核心·不变量法——形成中 bar 值改掉后输出不变）；
- 掩码投影语义锚（独立对照双路推导）——cross 状态窗 / spike 自洽
  （对照值投影恒 False）/ gt-lt 逐 bar 等价；
- warmup 换算样例 + 容量锚（3d/RSI 可用区间 > 200 bar——N1 floor
  语义效果）；
- assert_higher_tf 边界。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.mtf_alignment import (
    WARMUP_FLOOR_BARS,
    assert_higher_tf,
    high_tf_warmup_span,
    project_completed,
    project_completed_bool,
    pulse_completed,
)
from core.data.timeframes import TF_SECONDS

_T0 = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")


def _bars(values: list, start: pd.Timestamp, tf: str) -> pd.Series:
    """高周期 bar 序列（索引=开盘时刻，等距 tf）。"""
    step = pd.Timedelta(seconds=TF_SECONDS[tf])
    return pd.Series(
        values, index=[start + i * step for i in range(len(values))])


def _base_grid(start: pd.Timestamp, n: int, tf: str) -> pd.DatetimeIndex:
    step = pd.Timedelta(seconds=TF_SECONDS[tf])
    return pd.DatetimeIndex([start + i * step for i in range(n)])


# ---------------------------------------------------------------- 三投影确定样例

class TestProjectCompleted:
    @pytest.mark.parametrize("tf_high,tf_base", [
        ("4h", "1h"), ("3d", "1h"), ("1w", "4h"), ("1d", "1h"),
    ], ids=["4h对1h", "3d对1h", "1w对4h", "1d对1h"])
    def test_值投影_已收盘可见与首段NaN_逐段确定(self, tf_high, tf_base):
        """高周期 bar T 的值在 T+tf（收盘时刻）起可见；首段 NaN。

        独立对照：3 根高周期 bar [10, 20, 30]，基准轴覆盖前 3 个
        高周期窗——每个窗看到上一根的值；首窗（首根收盘前）NaN。
        """
        high = _bars([10.0, 20.0, 30.0], _T0, tf_high)
        # 基准轴：从首根高周期开盘起，覆盖 3 个高周期窗（每窗按基准粒度取 4 根）
        base_step = pd.Timedelta(seconds=TF_SECONDS[tf_base])
        span = pd.Timedelta(seconds=TF_SECONDS[tf_high])
        n = int(span / base_step) * 3
        base = _base_grid(_T0, n, tf_base)
        out = project_completed(high, base, tf_high)
        per_win = int(span / base_step)
        # 首窗 NaN；此后第 k 窗见第 k-1 根值
        for i in range(per_win):
            assert pd.isna(out.iloc[i])
        for k in range(1, 3):
            for i in range(k * per_win, (k + 1) * per_win):
                assert out.iloc[i] == pytest.approx(float([10, 20, 30][k - 1]))

    def test_值投影_缺口索引_保守无前视(self):
        """高周期缺 bar 时值挂载仍按 +tf 推导（位置 shift 会错位挂载）。

        4h 数据缺第二根：bar0=10@00:00、bar2=30@08:00。08:00 开盘的
        基准 bar 处于 bar2 形成期，只能见 bar0（10）——不能因缺口
        提前见到 bar2 或错挂 NaN。
        """
        high = pd.Series(
            [10.0, 30.0],
            index=[_T0, _T0 + pd.Timedelta(hours=8)])
        base = _base_grid(_T0, 12, "1h")
        out = project_completed(high, base, "4h")
        # 00:00-03:00 首段 NaN（bar0 未收盘）
        for i in range(4):
            assert pd.isna(out.iloc[i])
        # 04:00-11:00 见 bar0=10（bar2 于 12:00 收盘后才可见）
        for i in range(4, 12):
            assert out.iloc[i] == pytest.approx(10.0)

    def test_布尔投影_首段False而非NaN(self):
        high = _bars([True, False], _T0, "4h")
        base = _base_grid(_T0, 8, "1h")
        out = project_completed_bool(high, base, "4h")
        assert out.iloc[:4].tolist() == [False] * 4
        assert out.iloc[4:].tolist() == [True] * 4


# ---------------------------------------------------------------- asof 前视差分锚

class TestAsofDifferentialAnchors:
    @pytest.mark.parametrize("tf_high,tf_base", [
        ("4h", "1h"), ("1d", "1h"),
    ], ids=["4h对1h", "1d对1h"])
    def test_前视差分_形成中bar值改掉_布尔投影不变(self, tf_high, tf_base):
        """不变量法：最后一根高周期 bar 处于形成期（其收盘挂载点晚于
        投影域末端）——改掉它的值不影响任何输出 bar。"""
        span = pd.Timedelta(seconds=TF_SECONDS[tf_high])
        base_step = pd.Timedelta(seconds=TF_SECONDS[tf_base])
        per_win = int(span / base_step)
        high_a = _bars([10.0, 20.0, 30.0], _T0, tf_high)
        high_b = _bars([10.0, 20.0, 999.0], _T0, tf_high)  # 形成中 bar 改值
        # 投影域止于末根收盘前（形成期内）：[T0, T0+2*span)
        base = _base_grid(_T0, per_win * 2, tf_base)
        mask_a = project_completed_bool(high_a > 15, base, tf_high)
        mask_b = project_completed_bool(high_b > 15, base, tf_high)
        pd.testing.assert_series_equal(mask_a, mask_b)

    def test_前视差分_值投影同构(self):
        """值投影同款不变量：形成中 bar 的值改掉，投影域（其收盘前）
        的投影值逐点不变。"""
        high_a = _bars([10.0, 20.0, 30.0], _T0, "4h")
        high_b = _bars([10.0, 20.0, 999.0], _T0, "4h")
        base = _base_grid(_T0, 8, "1h")   # 止于第三根收盘（12:00）前
        out_a = project_completed(high_a, base, "4h")
        out_b = project_completed(high_b, base, "4h")
        pd.testing.assert_series_equal(out_a, out_b)


# ---------------------------------------------------------------- 掩码投影语义锚

class TestMaskProjectionSemantics:
    def test_语义锚_cross挂高周期_状态窗持续至下次高周期收盘(self):
        """独立对照：4h close [10, 20, 10] cross_above 15——高周期轴
        仅 bar1 True；投影后 1h 轴 bar1 收盘挂载（08:00）起持续 True
        直至 bar2 收盘挂载（12:00）覆盖为 False——状态窗非单 bar。"""
        close = _bars([10.0, 20.0, 10.0], _T0, "4h")
        cross = (close.shift(1) < 15) & (close >= 15)
        assert cross.tolist() == [False, True, False]  # 高周期轴单点穿越
        base = _base_grid(_T0, 16, "1h")               # 覆盖 3 个 4h 窗
        out = project_completed_bool(cross, base, "4h")
        expected = (
            [False] * 4   # 首窗（bar0 收盘前）
            + [False] * 4  # bar1 收盘挂载前的 04:00-07:00 仍见 bar0 掩码
            + [True] * 4   # 08:00-11:00：bar1 穿越状态窗
            + [False] * 4  # 12:00-15:00：bar2 收盘，掩码回 False
        )
        assert out.tolist() == expected

    def test_语义锚_spike高周期轴自洽_非恒False(self):
        """spike=高周期轴环比振幅类动作——掩码投影全族自洽。

        对照：值投影下 subject 退化为阶梯常数列，rolling 均值与现值
        同为常数 → spike 恒 False（语义失效）；掩码路径在高周期轴
        求值，尖刺 bar 掩码 True。
        """
        # 高周期 volume：平稳后一根 10 倍尖刺
        vals = [100.0] * 25 + [1000.0]
        volume = _bars(vals, _T0, "4h")
        rolling = volume.rolling(window=20, min_periods=1).mean()
        mask_high = volume >= rolling * 2.0
        assert mask_high.iloc[-1] is True or bool(mask_high.iloc[-1]) is True
        assert mask_high.iloc[:25].sum() == 0

        # 值投影失效对照：投影后的阶梯常数列在平坦区（两次高周期
        # 收盘之间恒定）上 spike 恒 False——环比振幅语义失效（挂载
        # 跳变点上的误触发恰是值投影语义变形的实证，不作为期望）
        base = _base_grid(_T0, 4 * 27, "1h")   # 覆盖尖刺收盘挂载点之后
        stepped = project_completed(volume, base, "4h")
        stepped_rolling = stepped.rolling(window=20, min_periods=1).mean()
        stepped_spike = stepped >= stepped_rolling * 2.0
        # 平坦区：第 2..25 个高周期窗内部（每窗第 2 根起为平坦段）
        for w in range(1, 25):
            assert not stepped_spike.iloc[w * 4 + 1: (w + 1) * 4].any()

        # 掩码投影：尖刺收盘后状态窗可见（bar25 收盘挂载 104h 起）
        out = project_completed_bool(mask_high, base, "4h")
        assert out.iloc[:104].sum() == 0
        assert out.iloc[104:108].tolist() == [True] * 4

    def test_语义锚_gt状态族_掩码投影与阶梯常数比较逐bar等价(self):
        """规划锚点区两语义等价：高周期轴 gt 掩码后投影 ≡ 投影后值列 gt。

        双路独立对照（含首段 NaN→False 对齐与缺口窗）。
        """
        values = [10.0, 25.0, 5.0, 30.0, 30.0]
        high = _bars(values, _T0, "4h")
        base = _base_grid(_T0, 20, "1h")
        # 路 1：掩码投影
        via_mask = project_completed_bool(high > 15, base, "4h")
        # 路 2：值投影（阶梯常数）后比较——NaN 比较为 False
        stepped = project_completed(high, base, "4h")
        via_value = (stepped > 15).fillna(False).astype(bool)
        pd.testing.assert_series_equal(via_mask, via_value)


# ---------------------------------------------------------------- 脉冲投影

class TestPulseCompleted:
    def test_脉冲_收盘时刻落位所在基准bar(self):
        """4h bar1（开盘 04:00，收盘 08:00）脉冲 → 08:00 开盘的基准 bar。"""
        signal = _bars([False, True, False], _T0, "4h")
        base = _base_grid(_T0, 16, "1h")
        out = pulse_completed(signal, base, "4h")
        assert out.sum() == 1
        assert bool(out.loc[_T0 + pd.Timedelta(hours=8)]) is True

    def test_脉冲_收盘晚于基准末端不落位(self):
        signal = _bars([False, True], _T0, "4h")   # 收盘 08:00
        base = _base_grid(_T0, 5, "1h")            # 末端 04:00 < 08:00
        out = pulse_completed(signal, base, "4h")
        assert out.sum() == 0

    def test_脉冲_末bar窗口为tf时长_对照旧硬编码1天(self):
        """末 bar 窗口=+tf（替换旧硬编码 1 天）——1w 周期末 bar 脉冲
        收盘于开盘+7d，恰在基准末端上仍可落位；旧 1 天窗口会把
        超出 1 天的收盘误判为不可落位。"""
        week = 7 * 24 * 3600
        signal = _bars([True, False], _T0, "1w")           # bar0 收盘 T0+7d
        base = _base_grid(_T0, 8, "1d")                    # 末端恰为 T0+7d
        out = pulse_completed(signal, base, "1w")
        assert out.sum() == 1
        assert bool(out.loc[_T0 + pd.Timedelta(seconds=week)]) is True
        # 基准轴不含收盘时刻（末端 T0+6d）→ 不落位
        base_short = _base_grid(_T0, 7, "1d")
        assert pulse_completed(signal, base_short, "1w").sum() == 0


# ---------------------------------------------------------------- warmup 与方向判定

class TestWarmupAndAssert:
    def test_warmup_空指标清单_floor20bar(self):
        span = high_tf_warmup_span([], "4h")
        assert span == pd.Timedelta(seconds=20 * TF_SECONDS["4h"])

    def test_warmup_条件实际参数粒度_RSI14收敛42bar(self):
        """真实收敛按条件实际参数推导（indicator_convergence）——
        RSI(14) 收敛 14 而非注册表 max 50（机械沿用 max 会把 warmup
        从 42 bar 虚增至 150 bar）。"""
        span = high_tf_warmup_span([("RSI", {"period": 14})], "3d")
        assert span == pd.Timedelta(seconds=42 * TF_SECONDS["3d"])

    def test_warmup_与隔离回测200floor分离_3dRSI可用区间超200bar(self):
        """N1 floor 语义效果：RSI(14) 的 3d warmup = 42 bar，显著
        小于隔离回测 200 floor——990 天 3d 数据 319 bar 下可用区间
        277 bar > 200 bar（机械沿用 200 floor 可用仅 119 bar）。"""
        from core.analysis.registry_meta import indicator_convergence

        conv = indicator_convergence("RSI", {"period": 14})
        span = high_tf_warmup_span([("RSI", {"period": 14})], "3d")
        bars = int(span.total_seconds() // TF_SECONDS["3d"])
        assert conv == 14
        assert bars == max(conv * 3, WARMUP_FLOOR_BARS)
        # 容量锚：319 bar 数据可用区间 > 200 bar
        assert 319 - bars > 200
        # 与 200 floor 分离的显式断言（200 floor 会吃掉 63% 数据）
        assert span < pd.Timedelta(seconds=200 * TF_SECONDS["3d"])
        assert 319 - 200 <= 200   # 机械沿用下的可用区间对照（119 < 200）

    @pytest.mark.parametrize("tf_high,tf_base", [
        ("3d", "1h"), ("1w", "4h"), ("4h", "4h"),
    ], ids=["3d对1h", "1w对4h", "等周期通过"])
    def test_换算_高低周期组合覆盖(self, tf_high, tf_base):
        high = _bars([1.0, 2.0], _T0, tf_high)
        base = _base_grid(
            _T0, max(2 * TF_SECONDS[tf_high] // TF_SECONDS[tf_base], 2), tf_base)
        out = project_completed_bool(high > 0, base, tf_high)
        # 首窗 False（未收盘），其后恒 True（值恒 > 0）
        span = pd.Timedelta(seconds=TF_SECONDS[tf_high])
        step = pd.Timedelta(seconds=TF_SECONDS[tf_base])
        per_win = int(span / step)
        assert out.iloc[:per_win].tolist() == [False] * per_win
        assert out.iloc[per_win:].tolist() == [True] * (len(out) - per_win)

    def test_方向判定_低周期拒绝附降基准指引(self):
        with pytest.raises(ValueError, match="降基准"):
            assert_higher_tf("1h", "4h")

    def test_方向判定_未知周期拒绝附合法清单(self):
        with pytest.raises(ValueError, match="合法键集"):
            assert_higher_tf("2d", "1h")

    def test_方向判定_高周期与等周期通过(self):
        assert_higher_tf("4h", "1h") is None
        assert_higher_tf("4h", "4h") is None
