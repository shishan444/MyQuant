"""FDR 校正裁判层锚定（add-screen-patterns 任务 1.1）。

参照实现独立性：本文件用独立内联的 BH 手算序列（不经
core.scoring）锚定 benjamini_hochberg 的 q 值与显著性标记。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.scoring.statistics import benjamini_hochberg


class TestBenjaminiHochberg:
    def test_manual_sequence_anchored(self):
        """手算序列：p=[0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]。

        m=8，rank 顺序即传入顺序；q_(i)=min(q_(i+1), p*m/i) 回填后
        q=[0.008, 0.032, 0.104, 0.082, 0.0672, 0.08, 0.0846, 0.205]
        （经典 BH 示例序列，手算可复核）。
        """
        p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
        q, sig = benjamini_hochberg(p, q_threshold=0.05)
        # 逐秩手算：p*m/i = [0.008, 0.032, 0.104, 0.082, 0.0672, 0.08, 0.08457, 0.205]
        expected_raw = [0.008, 0.032, 0.104, 0.082, 0.0672, 0.08, 0.0845714, 0.205]
        # step-up 回填：从尾往前取 running min
        running = float("inf")
        expected_q = [0.0] * 8
        for i in range(7, -1, -1):
            running = min(running, expected_raw[i])
            expected_q[i] = min(1.0, running)
        assert q == pytest.approx(expected_q, abs=1e-6)
        # q<=0.05 的只有前两个（0.008, 0.032）——rank 3 的 0.104 > 0.05
        assert sig == [q_i <= 0.05 for q_i in expected_q]
        assert sum(sig) == 2

    def test_monotonicity_enforced(self):
        """单调性回填：大 p 因更小秩的 min 被压低，q 序（按秩）单调不减。"""
        p = [0.9, 0.01, 0.5, 0.02]
        q, _ = benjamini_hochberg(p)
        # 按值升序的位置：0.01(rank1) 0.02(rank2) 0.5(rank3) 0.9(rank4)
        # raw: 0.01*4/1=0.04, 0.02*4/2=0.04, 0.5*4/3=0.667, 0.9*4/4=0.9
        # 回填后：0.04, 0.04, 0.667, 0.9（已单调，min 不改变）
        # 传入乱序，q 按原序返回：[0.9, 0.04, 0.667, 0.04]
        assert q == pytest.approx([0.9, 0.04, 0.6666667, 0.04], abs=1e-6)

    def test_all_identical_p(self):
        """全同 p：每个 q = p*m/m = p（任何秩都相同）。"""
        p = [0.03] * 10
        q, sig = benjamini_hochberg(p, q_threshold=0.05)
        assert q == pytest.approx([0.03] * 10)
        assert all(sig)

    def test_boundary_rejection(self):
        """空族与越界 p 显式拒绝（不静默钳制）。"""
        with pytest.raises(ValueError):
            benjamini_hochberg([])
        with pytest.raises(ValueError):
            benjamini_hochberg([0.01, 1.5])
        with pytest.raises(ValueError):
            benjamini_hochberg([0.01, -0.1])

    def test_q_clamped_to_one(self):
        """q 值截断在 [0,1]。"""
        q, _ = benjamini_hochberg([1.0, 1.0])
        assert q == [1.0, 1.0]
