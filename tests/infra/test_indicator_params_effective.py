"""指标参数实际生效断言（fix-research-c1-bb-std-param · C1.2）。

主题：入口声明的指标参数必须真实传达到底层计算——两处历史吞参
（BB 的 std、Keltner 的 atr_period）随本变更修复，本文件是防再犯
锚：任何人把传参改回被吞形制，断言立刻变红。

缺陷机制（pandas_ta 0.4.71b0）：
- bbands 签名为 (close, length, lower_std, upper_std, ...)，无 std
  形参——原 `std=std` 传入落入 **kwargs 被静默丢弃，恒用默认 2.0，
  造成「列名标 std 倍数、数值按 2 倍算」（fr_1fe9939c873a）。
- kc 无独立 ATR 周期概念——原 `atr_length=atr_p` 传入被吞，band
  与 basis 共用 length 平滑 true_range，atr_period 从未生效。
  修复为自算：basis=ta.ema(close, ema_p)、band=ta.atr(..., length=
  atr_p, mamode="ema")（mamode 必须显式 ema——缺省 rma 是第二处
  语义偏移；实测 atr(mamode="ema") 与 ema(true_range) 精确等价）。

防再犯红测证据（规程：临时回退错误传参验证断言必红，各一次）：
- BB：2026-10-13 实施期执行——indicators.py 临时回退 `std=std` 形，
  TestBBStdEffective 3 failed（std=3 与 std=2 输出全等 2854.77==
  2854.77，正是原缺陷行为）；恢复后 10 passed。
- KC：2026-10-13 实施期执行——临时回退 ta.kc 调用形，
  TestKeltnerAtrPeriodEffective 3 failed（atr 10 vs 30 差值 0.0；
  multiplier 维度同红——原调用形连 multiplier 都未传过、mult=1 与
  mult=3 通道全等）；恢复后 10 passed。
"""

import pandas as pd
import pandas_ta as ta
import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicators import _compute_indicator
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def ohlcv() -> pd.DataFrame:
    """400 bar 确定性数据（判别参数敏感性无需真实行情）。"""
    return make_ohlcv(n=400, freq="1h", start="2024-01-01", seed=23)


def _bb(df: pd.DataFrame, std: float) -> dict[str, pd.Series]:
    return _compute_indicator(df, "BB", {"period": 20, "std": std})


def _kc(df: pd.DataFrame, atr_p: int, mult: float = 2.0) -> dict[str, pd.Series]:
    return _compute_indicator(
        df, "Keltner",
        {"ema_period": 20, "atr_period": atr_p, "multiplier": mult})


class TestBBStdEffective:
    """BB 宽度参数 std 实际生效（吞参修复防再犯锚）。"""

    def test_std3_output_differs_from_swallowed_default(self, ohlcv):
        """std=3 与 std=2 输出不全等——回退吞参形（恒用默认 2.0）即红。"""
        bb2, bb3 = _bb(ohlcv, 2), _bb(ohlcv, 3)
        assert not bb3["bb_lower_20_3"].dropna().equals(
            bb2["bb_lower_20_2"].dropna())
        diff = (bb3["bb_lower_20_3"].dropna()
                - bb2["bb_lower_20_2"].dropna()).abs().max()
        assert diff > 0

    def test_channel_width_monotonic_in_std(self, ohlcv):
        """通道宽强化：upper−lower 均值随 std 单调增——防「生效但方向反」。"""
        widths = {}
        for std in (1.0, 2, 3):
            bb = _bb(ohlcv, std)
            tag = str(std).replace(".0", "")
            widths[std] = (bb[f"bb_upper_20_{tag}"]
                           - bb[f"bb_lower_20_{str(std).replace('.0', '')}"]
                           ).dropna().mean()
        assert widths[1.0] < widths[2] < widths[3]

    def test_domain_both_ends_effective(self, ohlcv):
        """参数域两端（1.0 与 3.0）均实际生效：端点通道宽 ≠ 默认 2。"""
        w = {}
        for std in (1.0, 2, 3):
            bb = _bb(ohlcv, std)
            tag = str(std).replace(".0", "")
            w[std] = (bb[f"bb_upper_20_{tag}"]
                      - bb[f"bb_lower_20_{tag}"]).dropna().mean()
        assert w[1.0] < w[2] and w[3] > w[2]

    def test_std2_default_path_matches_library_default(self, ohlcv):
        """std=2 缺省路径回归：与库默认调用（修复前吞参的实际行为）逐元素一致。"""
        bb2 = _bb(ohlcv, 2)
        pre = ta.bbands(ohlcv["close"], length=20)  # 修复前调用形（吞参→默认 2.0）
        got = bb2["bb_lower_20_2"].dropna()
        exp = pre["BBL_20_2.0_2.0"].dropna().reindex(got.index)
        pd.testing.assert_series_equal(got, exp, check_names=False)

    def test_same_params_stable(self, ohlcv):
        """同参重复计算结果稳定（纯函数契约）。"""
        a, b = _bb(ohlcv, 3), _bb(ohlcv, 3)
        for col in ("bb_upper_20_3", "bb_middle_20_3", "bb_lower_20_3"):
            pd.testing.assert_series_equal(a[col], b[col], check_names=False)


class TestKeltnerAtrPeriodEffective:
    """KC 独立 ATR 周期实际生效（KC-S1~S4，吞参修复防再犯锚）。"""

    def test_atr_period_changes_bands(self, ohlcv):
        """S1 主断言：atr_period=10 vs 30 输出必不等——实查「10 与 30
        全等」正是原缺陷素材；回退 ta.kc 吞参形即红。"""
        kc10, kc30 = _kc(ohlcv, 10), _kc(ohlcv, 30)
        for col10, col30 in (("kc_20_10_2.0_upper", "kc_20_30_2.0_upper"),
                             ("kc_20_10_2.0_lower", "kc_20_30_2.0_lower")):
            diff = (kc10[col10].dropna() - kc30[col30].dropna()).abs().max()
            assert diff > 0

    def test_atr_period_decoupled_from_middle(self, ohlcv):
        """S2 解耦断言：middle（basis）逐元素不变而 upper/lower 必变——
        同时锁「改对地方」与「没改错地方」。"""
        kc10, kc30 = _kc(ohlcv, 10), _kc(ohlcv, 30)
        pd.testing.assert_series_equal(
            kc10["kc_20_10_2.0_middle"], kc30["kc_20_30_2.0_middle"],
            check_names=False)
        u_diff = (kc10["kc_20_10_2.0_upper"].dropna()
                  - kc30["kc_20_30_2.0_upper"].dropna()).abs().max()
        assert u_diff > 0

    def test_degenerate_form_matches_preta_kc(self, ohlcv):
        """S3 退化等值锚：atr_period==中轨周期（20）且 mult=2 时，自算
        输出与修复前 ta.kc 同参调用逐元素一致——KC 存量债务范围全量
        特殊性下的唯一修复前对照点（真实数据基线：BTCUSDT 1h 18941
        行三列零差异，hash 820d44a1/66684aec/de90be03，2026-10-13）。"""
        kc = _kc(ohlcv, 20)
        pre = ta.kc(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                    length=20, atr_length=20, mamode="ema").dropna()
        for col, base in (("kc_20_20_2.0_upper", "KCUe_20_2"),
                          ("kc_20_20_2.0_middle", "KCBe_20_2"),
                          ("kc_20_20_2.0_lower", "KCLe_20_2")):
            got = kc[col].reindex(pre.index).dropna()
            pd.testing.assert_series_equal(
                got, pre[base], check_names=False)

    def test_multiplier_effective(self, ohlcv):
        """multiplier 维度同样生效：mult=1 与 mult=3 通道宽不同（自算
        路径新增覆盖——原 ta.kc 形 mult 由工程参数传入，自算后须自证）。"""
        kc1 = _kc(ohlcv, 10, mult=1.0)
        kc3 = _kc(ohlcv, 10, mult=3.0)
        w1 = (kc1["kc_20_10_1.0_upper"] - kc1["kc_20_10_1.0_lower"]).dropna().mean()
        w3 = (kc3["kc_20_10_3.0_upper"] - kc3["kc_20_10_3.0_lower"]).dropna().mean()
        assert w3 > w1 > 0

    def test_same_params_stable(self, ohlcv):
        """S4 同参稳定（纯函数契约）。"""
        a, b = _kc(ohlcv, 10), _kc(ohlcv, 10)
        for col in ("kc_20_10_2.0_upper", "kc_20_10_2.0_middle",
                    "kc_20_10_2.0_lower"):
            pd.testing.assert_series_equal(a[col], b[col], check_names=False)
