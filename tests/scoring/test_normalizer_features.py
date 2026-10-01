"""normalizer 特征测试网（现状记录语义）。

模块标记 DEPRECATED（仅为向后兼容保留），锚定既有分支行为：
缺失值直通、负值 Sortino 折线、封顶钳制、win_rate 线性窗。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.normalizer import normalize, piecewise_normalize


class TestMissingValue:
    def test_win_rate_none_is_zero(self):
        # 唯一接受 None 的维度：缺失 → 0.0
        assert normalize("win_rate", None) == 0.0


class TestSortinoNegativeBranch:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(-0.5, 45.0, id="轻度负值折线"),
            pytest.param(-1.0, 40.0, id="断点值"),
            pytest.param(-6.0, 0.0, id="深负值钳到零"),
        ],
    )
    def test_negative_values(self, value, expected):
        # 负值分支：max(0, 50 + value*10)，不走 value/4 比例
        assert normalize("sortino_ratio", value) == pytest.approx(expected)


class TestClampCeilings:
    def test_calmar_above_five_caps_at_hundred(self):
        # min(6/5, 1)*100 → 封顶 100
        assert normalize("calmar_ratio", 6.0) == 100.0

    def test_monthly_consistency_above_one_clamped(self):
        # 1.5*100=150 → 出口钳制在 100
        assert normalize("monthly_consistency", 1.5) == 100.0


class TestWinRateLinearWindow:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param(0.3, 0.0, id="下界锚点"),
            pytest.param(0.5, 50.0, id="窗口中点"),
            pytest.param(0.7, 100.0, id="上界锚点"),
            pytest.param(0.9, 100.0, id="越上界钳制"),
        ],
    )
    def test_window_values(self, value, expected):
        # (value-0.3)/0.4*100 线性窗 + 出口钳制
        assert normalize("win_rate", value) == pytest.approx(expected)


class TestAlphaLowerBreakpoint:
    def test_alpha_minus_020_is_fifteen(self):
        # 补全断点表：(-0.20, 15.0) 精确落点
        assert normalize("alpha", -0.20) == pytest.approx(15.0)


class TestPiecewiseBoundary:
    def test_exact_breakpoint_returns_table_value(self):
        # 值恰在断点上：命中首个包含区间，返回左端表值
        bp = [(0.0, 10.0), (1.0, 30.0)]
        assert piecewise_normalize(0.0, bp) == pytest.approx(10.0)
        assert piecewise_normalize(1.0, bp) == pytest.approx(30.0)
