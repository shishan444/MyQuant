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

特征网边界锚（2026-10-13 tester 增补，现状记录语义）：
- 库门槛差异实测（pandas_ta 0.4.71b0）：ta.ema 在 n == length 即返回
  尾部 1 个有效值；ta.atr 需 n > length 才返回非 None（n == length
  返回 None）——守卫由此在退化形 n == ema_p == atr_p 下拦成空返回。
- KC 自算三列 warmup 遮罩由 max(ema_p, atr_p) 主导：atr_p > ema_p 时
  middle 与 upper/lower 的首个有效 bar 不同（band 真实按 atr_p 平滑
  的直接证据；修复前吞参形遮罩三列永远一致）。
- 退化等值锚在 NaN 遮罩层面同样逐元素一致（原 S3 断言 dropna 后比
  值，遮罩是盲区，此处补全）。
- 输入 warmup 后混单点 NaN 走 ewm 跳过语义（输出不产生对应 NaN），
  属库行为观察、设计卡未定义该口径，只记录不断言。
"""

from pathlib import Path

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


class TestBBNonIntegerStd:
    """BB 非整数 std 形（2.5）的参数生效与列名形。

    契约依据：delta spec「std 必须实传」不限于整数形；主 spec「列名
    跨层一致」要求生成层列名形制稳定（std_str 仅对整数形去尾 .0）。
    """

    def test_std25_column_name_form(self, ohlcv):
        """std=2.5 生成三列 {prefix}_20_2.5——非整数形列名锚。"""
        bb = _bb(ohlcv, 2.5)
        assert set(bb.keys()) == {
            "bb_upper_20_2.5", "bb_middle_20_2.5", "bb_lower_20_2.5"}

    def test_std25_width_between_std2_and_std3(self, ohlcv):
        """std=2.5 通道宽严格介于 std=2 与 std=3 之间——非整数形同样
        实际生效（回退吞参形则恒用 2.0、三者全等即红）。"""
        widths = {}
        for std in (2, 2.5, 3):
            bb = _bb(ohlcv, std)
            tag = str(std).replace(".0", "")
            widths[std] = (bb[f"bb_upper_20_{tag}"]
                           - bb[f"bb_lower_20_{tag}"]).dropna().mean()
        assert widths[2] < widths[2.5] < widths[3]


class TestKeltnerMultiplierForm:
    """KC multiplier 非默认形（1.5）列名形与通道宽线性。

    契约依据：修复前 ta.kc 形连 multiplier 都未传入（恒用库默认
    2.0）——mult 是修复新增生效面，须自证。
    """

    def test_mult15_column_name_form(self, ohlcv):
        """mult=1.5 生成三列 kc_20_10_1.5_{upper,middle,lower}。"""
        kc = _kc(ohlcv, 10, mult=1.5)
        assert set(kc.keys()) == {
            "kc_20_10_1.5_upper", "kc_20_10_1.5_middle",
            "kc_20_10_1.5_lower"}

    def test_channel_width_linear_in_multiplier(self, ohlcv):
        """通道宽 ∝ multiplier：逐元素比 1.5 与 3.0 精确成立——回退
        吞参形（mult 恒用 2.0）则比值全为 1，即红。"""
        widths = {}
        for m in (1.0, 1.5, 3.0):
            kc = _kc(ohlcv, 10, mult=m)
            widths[m] = (kc[f"kc_20_10_{m}_upper"]
                         - kc[f"kc_20_10_{m}_lower"]).dropna()
        denom = widths[1.0].replace(0, pd.NA)
        for m, expected in ((1.5, 1.5), (3.0, 3.0)):
            ratio = (widths[m] / denom).dropna()
            assert not ratio.empty
            pd.testing.assert_series_equal(
                ratio, pd.Series(expected, index=ratio.index),
                check_names=False)


class TestKeltnerDegenerateMaskParity:
    """S3 补全：退化等值在 NaN 遮罩层面同样逐元素一致。

    原 S3 断言 dropna 后比值，遮罩差异是被 dropna 掩盖的盲区——
    delta spec「逐元素一致」应含 NaN 位置一致。
    """

    def test_degenerate_nan_mask_matches_preta_kc(self, ohlcv):
        kc = _kc(ohlcv, 20)
        pre = ta.kc(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                    length=20, atr_length=20, mamode="ema")
        for eng_col, lib_col in (("kc_20_20_2.0_upper", "KCUe_20_2"),
                                 ("kc_20_20_2.0_middle", "KCBe_20_2"),
                                 ("kc_20_20_2.0_lower", "KCLe_20_2")):
            assert kc[eng_col].isna().equals(pre[lib_col].isna())


class TestKeltnerWarmupBoundaries:
    """KC 自算路径 warmup/短数据边界（现状记录语义，特征网锚）。

    库门槛差异（pandas_ta 0.4.71b0 实测）：ta.ema 在 n == length 即出
    尾部 1 个有效值，ta.atr 需 n > length 才返回非 None——守卫
    `basis is not None and band is not None` 由此在 n == length 的
    退化形下拦成空返回。
    """

    def test_atr_needs_more_than_length_ema_accepts_equal(self, ohlcv):
        """n == length 时 ta.atr 返回 None 而 ta.ema 出 1 个有效值——
        两库函数门槛差异的现状记录。"""
        head = ohlcv.iloc[:20]
        ema = ta.ema(head["close"], length=20)
        atr = ta.atr(head["high"], head["low"], head["close"],
                     length=20, mamode="ema")
        assert atr is None
        assert ema is not None
        assert int(ema.notna().sum()) == 1

    def test_short_data_returns_empty_columns(self, ohlcv):
        """n=19（双不足）与 n=20（ema 满足、atr 不满足）均为空 dict
        ——守卫按 None 拦截、静默空返回（现状记录）。"""
        for n in (19, 20):
            result = _compute_indicator(
                ohlcv.iloc[:n], "Keltner",
                {"ema_period": 20, "atr_period": 20, "multiplier": 2.0})
            assert result == {}

    def test_output_starts_at_max_period_minus_one(self, ohlcv):
        """n=21 起三列生成，首个有效 bar == index[max(ema_p, atr_p)-1]
        （主 spec「预热区显式界定」：界定依据为窗口参数）。"""
        kc = _compute_indicator(
            ohlcv.iloc[:21], "Keltner",
            {"ema_period": 20, "atr_period": 20, "multiplier": 2.0})
        for col in ("kc_20_20_2.0_upper", "kc_20_20_2.0_middle",
                    "kc_20_20_2.0_lower"):
            assert kc[col].first_valid_index() == ohlcv.index[19]

    def test_band_warmup_follows_max_of_periods(self, ohlcv):
        """三列 warmup 遮罩由 max(ema_p, atr_p) 主导：atr_p < ema_p 时
        三列一致；atr_p > ema_p 时 middle 仍按 ema_p 而 upper/lower 按
        atr_p——band 真实按 atr_p 平滑的直接证据（修复前吞参形遮罩
        三列永远一致，与修复前形在 atr_p≠ema_p 下遮罩不同的正是参数
        生效本身）。"""
        n = len(ohlcv)
        kc5 = _kc(ohlcv, 5)
        for col in ("kc_20_5_2.0_upper", "kc_20_5_2.0_middle",
                    "kc_20_5_2.0_lower"):
            assert kc5[col].first_valid_index() == ohlcv.index[19]
            assert int(kc5[col].notna().sum()) == n - 20 + 1
        kc40 = _kc(ohlcv, 40)
        assert kc40["kc_20_40_2.0_middle"].first_valid_index() \
            == ohlcv.index[19]
        assert int(kc40["kc_20_40_2.0_middle"].notna().sum()) == n - 20 + 1
        for col in ("kc_20_40_2.0_upper", "kc_20_40_2.0_lower"):
            assert kc40[col].first_valid_index() == ohlcv.index[39]
            assert int(kc40[col].notna().sum()) == n - 40 + 1

    def test_bb_exact_window_yields_single_value(self, ohlcv):
        """BB 对照：bbands 门槛 n >= length——n=19 空 dict，n=20 三列
        各 1 个有效值（与 KC 在 n=20 被 atr 门槛拦成空 dict 的差异
        记录）。"""
        r19 = _compute_indicator(
            ohlcv.iloc[:19], "BB", {"period": 20, "std": 2})
        assert r19 == {}
        r20 = _compute_indicator(
            ohlcv.iloc[:20], "BB", {"period": 20, "std": 2})
        assert set(r20) == {"bb_upper_20_2", "bb_middle_20_2",
                            "bb_lower_20_2"}
        assert all(int(s.notna().sum()) == 1 for s in r20.values())


class TestSwallowAuditReportDelivered:
    """吞参排查交付物存在锚（delta spec Scenario 3：全量排查报告
    落盘、两处修复记录在案、修复后复跑无残留——文档验收的存在性
    检查，不锚正文措辞）。"""

    def test_report_records_27_callsites_and_both_fixes(self):
        report = (Path(__file__).resolve().parents[2] / "docs" / "backend"
                  / "指标参数吞参排查报告-2026-10-13.md")
        text = report.read_text(encoding="utf-8")
        assert "27 个调用点" in text
        assert "修复一：布林带宽度参数" in text
        assert "修复二：Keltner 通道独立 ATR 周期" in text
        assert "28 个调用点" in text
