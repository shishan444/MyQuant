"""statistics.wilson_interval 特征测试网。

benjamini_hochberg 已由 tests/accuracy/test_statistics_fdr.py 密集
锚定（手算序列/单调性/边界拒绝），此处不重复。wilson_interval 此前
仅有消费方链路覆盖（stat_validator/condition_stats/forward_stats），
本文件补 scoring 域内直接锚定：期望值由 Wilson 公式独立手算；
CARD-06 后另锚定显式参数校验的拒绝语义（涉安）。
"""
import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.statistics import wilson_interval


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


class TestWilsonInterval:
    def test_zero_total_returns_origin(self):
        assert wilson_interval(0, 0) == (0.0, 0.0)

    def test_small_sample_manual_values(self):
        # s=1, n=2, z=1.0：p=0.5，denom=1.5，center=0.5，
        # half=sqrt(0.1875)/1.5=0.2886751345948129
        lower, upper = wilson_interval(1, 2, z=1.0)
        assert lower == pytest.approx(0.2113248654051871, rel=1e-9)
        assert upper == pytest.approx(0.7886751345948129, rel=1e-9)

    def test_zero_successes_lower_clamped_to_zero(self):
        # p=0 时 center 与 half 数学相等 → lower=max(0, ~0)=0，
        # upper=2*(z^2/2n)/denom（内联独立公式）
        z, n = 1.96, 10
        denom = 1 + z * z / n
        upper_expected = 2 * (z * z / (2 * n)) / denom
        lower, upper = wilson_interval(0, n, z=z)
        assert lower == pytest.approx(0.0, abs=1e-12)
        assert upper == pytest.approx(upper_expected, rel=1e-9)

    def test_full_successes_upper_clamped_to_one(self):
        # p=1 时 center+half 数学恰为 1 → min(1, ~1)=1；lower=1/denom
        z, n = 1.96, 10
        denom = 1 + z * z / n
        lower, upper = wilson_interval(n, n, z=z)
        assert upper == pytest.approx(1.0, abs=1e-12)
        assert lower == pytest.approx(1 / denom, rel=1e-9)

    def test_interval_contains_point_estimate(self):
        # 属性：区间覆盖点估计 p
        lower, upper = wilson_interval(50, 100, z=1.96)
        assert 0.0 <= lower <= 0.5 <= upper <= 1.0

    def test_wider_z_widens_interval(self):
        l1, u1 = wilson_interval(50, 100, z=1.96)
        l2, u2 = wilson_interval(50, 100, z=2.576)
        assert l2 < l1 and u2 > u1

    def test_larger_sample_narrows_interval(self):
        l_small, u_small = wilson_interval(5, 10, z=1.96)
        l_big, u_big = wilson_interval(500, 1000, z=1.96)
        assert (u_big - l_big) < (u_small - l_small)
