"""statistics 单元测试（V3 形制：wilson 区间锚定 + BH 域内直测）。

benjamini_hochberg 另有 tests/accuracy/test_statistics_fdr.py 的
裁判层独立视角（add-screen-patterns 交付物），两者并存：本文件为
scoring 域内权威直测。期望值均独立手算（Wilson 公式 / BH 逐秩
step-up 回填），不用被测实现生成自身期望。

bootstrap 两函数（add-evaluation-ruler）同域直测：确定性/拒绝面/
方向镜像/常数收敛/覆盖性质/r9 demo 交叉对账（r9_d4_final.py 前提链
确定性复现，A0 行 CI 逐位一致——期望值为 r9 实跑登记值，非本实现
自产）。
"""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

pytestmark = [pytest.mark.unit]

from core.scoring.statistics import (
    benjamini_hochberg, boot_ci, boot_diff, wilson_interval)


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


class TestBootCI:
    """单样本均值 percentile CI（add-evaluation-ruler 任务 1.1）。

    快照锚：SAMPLE 五值首跑（B=3000/ci=0.95/seed=42）端点登记为
    常量——同参数必同果的回归锚，防后续漂移。
    """

    SAMPLE = [1.0, 2.0, 3.5, -0.5, 4.0, 2.5, 1.5, 3.0]
    SNAPSHOT = (1.125, 3.0625)  # 首跑锁定（2026-11-09）

    def test_same_seed_bitwise_identical(self):
        a = boot_ci(self.SAMPLE)
        b = boot_ci(self.SAMPLE)
        assert a == b

    def test_snapshot_anchor(self):
        lo, hi = boot_ci(self.SAMPLE)
        assert (lo, hi) == self.SNAPSHOT

    def test_different_seed_or_B_may_differ_same_params_identical(self):
        base = boot_ci(self.SAMPLE)
        alt_seed = boot_ci(self.SAMPLE, seed=7)
        # 不同 seed 允许异果（同快照则该断言天然弱化——种子 7 实测不同）
        assert alt_seed == boot_ci(self.SAMPLE, seed=7)
        alt_B = boot_ci(self.SAMPLE, B=500)
        assert alt_B == boot_ci(self.SAMPLE, B=500)
        assert (alt_seed != base) or (alt_B != base)

    def test_constant_sample_converges_without_special_case(self):
        lo, hi = boot_ci([5.0] * 10)
        assert lo == 5.0
        assert hi == 5.0

    @pytest.mark.parametrize("n", [0, 1, 2])
    def test_small_sample_rejected(self, n):
        with pytest.raises(ValueError, match="至少 3 个观测"):
            boot_ci([1.0, 2.0, 3.0][:n] if n else [])

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [pytest.param({"B": 0}, r"B=0", id="零 B"),
         pytest.param({"B": 2.5}, r"B=2.5", id="非整 B"),
         pytest.param({"B": True}, r"B=True", id="bool B（CARD-01）"),
         pytest.param({"B": 100_001}, r"B=100001", id="超上限 B（CARD-01）"),
         pytest.param({"ci": 0.0}, r"ci=0.0", id="零置信"),
         pytest.param({"ci": 1.0}, r"ci=1.0", id="满置信")],
    )
    def test_invalid_params_rejected(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            boot_ci([1.0, 2.0, 3.0], **kwargs)

    def test_b_upper_bound_inclusive(self):
        """上限 BOOT_B_MAX=100_000 含等号合法（边界两侧——拒绝用例
        在参数化「超上限」档）。"""
        from core.scoring.statistics import BOOT_B_MAX
        assert BOOT_B_MAX == 100_000
        lo, hi = boot_ci([1.0, 2.0, 3.0], B=BOOT_B_MAX)
        assert lo <= hi

    def test_interval_brackets_sample_mean(self):
        # 重抽样均值分布以观测均值为中心（percentile 区间覆盖之）
        lo, hi = boot_ci(self.SAMPLE)
        mean = sum(self.SAMPLE) / len(self.SAMPLE)
        assert lo <= mean <= hi

    def test_coverage_property_normal(self):
        """覆盖性质：标称 95% CI 以标称覆盖率含总体均值（二项容忍界）。

        M=40、p=0.95 → σ=sqrt(.95×.05/40)≈0.0344，3σ 下限 ≈0.847；
        B=800 控时长（覆盖性质与重抽样次数弱耦合）。
        """
        master = np.random.default_rng(7)
        hits = 0
        for i in range(40):
            sample = master.normal(0.5, 1.0, 60)
            lo, hi = boot_ci(sample, B=800, seed=100 + i)
            hits += int(lo <= 0.5 <= hi)
        assert hits / 40 >= 0.847


class TestBootDiff:
    """双样本均值差 CI（方向 x_minus_y 显式）。"""

    X = [1.0, 2.0, 3.0, 2.5, 1.5, 2.0]
    Y = [0.5, 0.0, 1.0, 0.2, 0.8, 0.4]

    def test_direction_brackets_true_diff(self):
        lo, hi = boot_diff(self.X, self.Y)
        true_diff = sum(self.X) / len(self.X) - sum(self.Y) / len(self.Y)
        assert lo <= true_diff <= hi
        assert hi > 0  # X 均值显著大于 Y（构造使然）

    def test_swap_arguments_mirror(self):
        lo, hi = boot_diff(self.X, self.Y)
        lo2, hi2 = boot_diff(self.Y, self.X)
        # 镜像=符号翻转（蒙特卡洛噪声内容差：两次调用消耗序不同，
        # 断言符号与量级，容差取区间宽度的 5%）
        width = hi - lo
        assert hi2 == pytest.approx(-lo, abs=0.05 * width)
        assert lo2 == pytest.approx(-hi, abs=0.05 * width)

    def test_same_seed_bitwise_identical(self):
        assert boot_diff(self.X, self.Y) == boot_diff(self.X, self.Y)

    @pytest.mark.parametrize(
        ("x", "y"),
        [pytest.param([1.0], [1.0, 2.0, 3.0], id="x 不足"),
         pytest.param([1.0, 2.0, 3.0], [1.0, 2.0], id="y 不足"),
         pytest.param([], [1.0, 2.0, 3.0], id="x 空")],
    )
    def test_small_sample_rejected(self, x, y):
        with pytest.raises(ValueError, match="两侧各至少 3 个观测"):
            boot_diff(x, y)

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [pytest.param({"B": 0}, r"B=0", id="零 B"),
         pytest.param({"B": 2.5}, r"B=2.5", id="非整 B"),
         pytest.param({"B": True}, r"B=True", id="bool B（CARD-01）"),
         pytest.param({"B": 100_001}, r"B=100001", id="超上限 B（CARD-01）")],
    )
    def test_invalid_B_rejected(self, kwargs, match):
        """B 校验与 boot_ci 同款（CARD-01 三层统一——库层兜底）。"""
        with pytest.raises(ValueError, match=match):
            boot_diff(self.X, self.Y, **kwargs)


class TestBootR9CrossCheck:
    """r9 demo 交叉对账（任务 1.2——交叉第二通道，非期望值权威）。

    前提链：r9_d4_final.py 模块级 default_rng(42) 在 main() 的首个
    消耗 = arm 循环首臂 A0 锁箱行的 boot_ci；本实现同种子首调、
    逐次等长 choice 同构——满足前提链时端点逐位一致（零容差）。
    期望值为 r9 实跑登记值（2026-11-09），非本实现自产。
    """

    def test_a0_lock_ci_bitwise_matches_r9(self):
        fixture = (Path(__file__).resolve().parents[1] / "fixtures"
                   / "evaluation_ruler" / "r9_d2_trades_lock.csv")
        t = pd.read_csv(fixture)
        net = t[(t.seg == "lock") & (t.arm == "A0")]["net"].values
        assert len(net) == 60
        lo, hi = boot_ci(net)
        assert lo == pytest.approx(-0.34321666666666667, rel=1e-15)
        assert hi == pytest.approx(0.7847854166666666, rel=1e-15)
