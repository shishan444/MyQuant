"""statistics 单元测试（V3 形制：wilson 区间锚定 + BH 域内直测）。

benjamini_hochberg 另有 tests/accuracy/test_statistics_fdr.py 的
裁判层独立视角（add-screen-patterns 交付物），两者并存：本文件为
scoring 域内权威直测。期望值均独立手算（Wilson 公式 / BH 逐秩
step-up 回填），不用被测实现生成自身期望。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.statistics import benjamini_hochberg, wilson_interval


class TestWilsonIntervalValues:
    def test_zero_total_returns_origin(self):
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_small_sample_manual_values(self):
        # s=1, n=2, z=1.0：p=0.5，denom=1.5，center=0.5，
        # half=sqrt(0.1875)/1.5=0.2886751345948129
        lower, upper = wilson_interval(1, 2, z=1.0)
        assert lower == pytest.approx(0.2113248654051871, rel=1e-9)
        assert upper == pytest.approx(0.7886751345948129, rel=1e-9)

    def test_zero_successes_lower_clamped_zero(self):
        # p=0 时 center 与 half 数学相等 → lower=max(0, ~0)=0，
        # upper=2*(z^2/2n)/denom（内联独立公式）
        z, n = 1.96, 10
        denom = 1 + z * z / n
        upper_expected = 2 * (z * z / (2 * n)) / denom
        lower, upper = wilson_interval(0, n, z=z)
        assert lower == pytest.approx(0.0, abs=1e-12)
        assert upper == pytest.approx(upper_expected, rel=1e-9)

    def test_full_successes_upper_clamped_one(self):
        # p=1 时 center+half 数学恰为 1 → min(1, ~1)=1；lower=1/denom
        z, n = 1.96, 10
        denom = 1 + z * z / n
        lower, upper = wilson_interval(n, n, z=z)
        assert upper == pytest.approx(1.0, abs=1e-12)
        assert lower == pytest.approx(1 / denom, rel=1e-9)


class TestWilsonInputValidation:
    """CARD-06 显式参数校验：计数矛盾与非法 z 显式拒绝（涉安锚点）。

    旧行为对照：successes 越界时 p*(1-p)<0 裸 math domain error、
    z<0 时静默产倒置区间——现统一为带语义 ValueError。
    """

    @pytest.mark.parametrize(
        ("successes", "total"),
        [
            pytest.param(-5, 10, id="负成功数"),
            pytest.param(15, 10, id="超总次数"),
            pytest.param(1, 0, id="零总数却有成功"),
            pytest.param(-1, 0, id="零总数且负成功"),
        ],
    )
    def test_successes_out_of_range_rejected(self, successes, total):
        with pytest.raises(ValueError, match=r"successes=.* 不在 \[0, total="):
            wilson_interval(successes, total)

    @pytest.mark.parametrize(
        "z",
        [
            pytest.param(0, id="零 z"),
            pytest.param(-1.96, id="负 z（旧版静默倒置）"),
            pytest.param(float("nan"), id="NaN"),
            pytest.param(float("inf"), id="正无穷"),
            pytest.param(float("-inf"), id="负无穷"),
        ],
    )
    def test_invalid_z_rejected(self, z):
        with pytest.raises(ValueError, match=r"z=.* 须为正的有限置信水平"):
            wilson_interval(50, 100, z=z)

    def test_legal_boundaries_not_rejected(self):
        # s=0 / s=n / 极小正 z 均为合法输入，不受新校验影响
        assert wilson_interval(0, 10)[1] > 0.0
        assert wilson_interval(10, 10, z=0.5)[0] > 0.0

    def test_zero_total_early_return_preserved(self):
        # (0, 0) 合法早退保留，不落入 successes 校验
        assert wilson_interval(0, 0) == (0.0, 0.0)


class TestBenjaminiHochbergValues:
    def test_manual_sequence_q_and_significance(self):
        """手算序列：p=[0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]。

        m=8，p 已升序（秩序=原序）；raw p*m/i =
        [0.008, 0.032, 0.104, 0.082, 0.0672, 0.08, 0.08457, 0.205]；
        从尾 step-up 回填 running min：
        q=[0.008, 0.032, 0.0672, 0.0672, 0.0672, 0.08, 0.08457, 0.205]
        （秩 3 的 0.104 被更小秩 5 的 0.0672 压低——单调性语义）。
        """
        p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
        q, sig = benjamini_hochberg(p, q_threshold=0.05)
        expected_q = [0.008, 0.032, 0.0672, 0.0672, 0.0672,
                      0.08, 0.0845714, 0.205]
        assert q == pytest.approx(expected_q, abs=1e-6)
        assert sum(sig) == 2  # q<=0.05 仅前两个

    def test_monotonicity_backfill_out_of_order_input(self):
        # 乱序输入：q 按原序返回，秩序经回填单调
        q, _ = benjamini_hochberg([0.9, 0.01, 0.5, 0.02])
        assert q == pytest.approx([0.9, 0.04, 0.6666667, 0.04], abs=1e-6)

    def test_identical_p_q_equals_p(self):
        # 全同 p：每个 q = p*m/m = p
        q, sig = benjamini_hochberg([0.03] * 10, q_threshold=0.05)
        assert q == pytest.approx([0.03] * 10)
        assert all(sig)

    def test_q_clamped_to_one(self):
        q, _ = benjamini_hochberg([1.0, 1.0])
        assert q == [1.0, 1.0]


class TestBenjaminiHochbergValidation:
    def test_empty_family_rejected(self):
        with pytest.raises(ValueError):
            benjamini_hochberg([])

    @pytest.mark.parametrize(
        ("p_values", "case_id"),
        [
            pytest.param([0.01, 1.5], "越上界", id="越上界"),
            pytest.param([0.01, -0.1], "负值", id="负值"),
        ],
    )
    def test_out_of_range_p_rejected(self, p_values, case_id):
        with pytest.raises(ValueError, match="不在 \[0,1\]"):
            benjamini_hochberg(p_values)


class TestWilsonProperty:
    """不变量与属性（V3 方法）：区间覆盖点估计且界在 [0,1]。"""

    @pytest.mark.parametrize(
        ("successes", "total", "z"),
        [(k, 100, 1.96) for k in range(0, 101, 10)]
        + [(7, 23, 2.576), (1, 2, 1.0), (99, 100, 0.5)],
    )
    def test_interval_contains_point_estimate_in_unit(self, successes, total, z):
        lower, upper = wilson_interval(successes, total, z=z)
        p = successes / total
        # 界由 max/min 钳制恒成立；包含性在出口浮点余量（1e-12）内成立
        # （s=n 时 center+half 可为 0.999...9 < p=1.0）
        assert 0.0 <= lower
        assert lower <= p + 1e-12
        assert p <= upper + 1e-12
        assert upper <= 1.0
