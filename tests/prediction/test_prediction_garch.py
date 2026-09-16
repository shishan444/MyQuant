"""GarchState unit tests.

Covers:
1. Default state is zero
2. update returns sigma (sqrt of sigma_sq)
3. update correctly computes GARCH(1,1) recursion
4. sigma_sq never goes below floor (1e-10)
5. init_from_history produces consistent state
6. init_from_history with varying data lengths
7. Convergence: repeated updates with constant range stabilize
"""

import math

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]


class TestGarchStateBasic:
    def test_default_state(self):
        from core.prediction.garch import GarchState
        g = GarchState()
        assert g.sigma_sq == 0.0
        assert g.epsilon == 0.0

    def test_update_returns_sigma(self):
        from core.prediction.garch import GarchState
        g = GarchState()
        sigma = g.update(actual_range=100.0, omega=1e-5, alpha=0.1, beta=0.8)
        assert sigma > 0
        assert sigma == math.sqrt(g.sigma_sq)

    def test_garch_recursion(self):
        from core.prediction.garch import GarchState
        g = GarchState()
        # Manual computation:
        # epsilon = 100.0 - sqrt(0) = 100.0
        # sigma_sq = 1e-5 + 0.1 * 100^2 + 0.8 * 0 = 1e-5 + 1000 = 1000.00001
        sigma = g.update(actual_range=100.0, omega=1e-5, alpha=0.1, beta=0.8)
        assert abs(sigma - math.sqrt(1000.00001)) < 1e-6

    def test_sigma_sq_floor(self):
        from core.prediction.garch import GarchState
        g = GarchState(sigma_sq=1e-12, epsilon=0.0)
        # With zero range and tiny sigma_sq, result should be floored
        sigma = g.update(actual_range=0.0, omega=0.0, alpha=0.0, beta=0.0)
        assert g.sigma_sq >= 1e-10

    def test_sequential_updates(self):
        from core.prediction.garch import GarchState
        g = GarchState()
        sigma1 = g.update(100.0, 1e-5, 0.1, 0.8)
        sigma2 = g.update(50.0, 1e-5, 0.1, 0.8)
        # Smaller range should lead to smaller sigma (mean reversion)
        assert sigma2 < sigma1


class TestGarchStateHistory:
    def test_init_from_history(self):
        from core.prediction.garch import GarchState
        ranges = pd.Series([100.0] * 50)
        g = GarchState.init_from_history(ranges, omega=1e-5, alpha=0.1, beta=0.8)
        assert g.sigma_sq > 0
        # GARCH(1,1) unconditional sigma = sqrt(omega / (1 - alpha - beta)) with constant range
        # But sigma converges differently; just verify it's positive and stable
        sigma = math.sqrt(g.sigma_sq)
        assert sigma > 0

    def test_init_from_history_short_series(self):
        from core.prediction.garch import GarchState
        ranges = pd.Series([100.0, 200.0, 50.0])
        g = GarchState.init_from_history(ranges, omega=1e-5, alpha=0.1, beta=0.8)
        assert g.sigma_sq > 0

    def test_init_from_history_matches_manual(self):
        from core.prediction.garch import GarchState
        ranges = pd.Series([100.0, 80.0, 120.0])
        g1 = GarchState.init_from_history(ranges, omega=1e-5, alpha=0.1, beta=0.8)

        g2 = GarchState()
        g2.update(100.0, 1e-5, 0.1, 0.8)
        g2.update(80.0, 1e-5, 0.1, 0.8)
        g2.update(120.0, 1e-5, 0.1, 0.8)

        assert abs(g1.sigma_sq - g2.sigma_sq) < 1e-10


class TestGarchStateConvergence:
    def test_converges_to_constant_range(self):
        from core.prediction.garch import GarchState
        g = GarchState()
        last_sigma = 0.0
        for _ in range(200):
            sigma = g.update(100.0, 1e-5, 0.1, 0.8)
            last_sigma = sigma
        # After 200 identical updates, sigma should be very stable
        next_sigma = g.update(100.0, 1e-5, 0.1, 0.8)
        assert abs(next_sigma - last_sigma) < 0.01
