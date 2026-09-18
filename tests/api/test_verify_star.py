"""Unit tests for compute_verify_star rating function."""
import pytest

pytestmark = [pytest.mark.unit]
from api.routes.strategies import compute_verify_star


class TestComputeVerifyStar:
    """Star rating: 1-5 for all-qualified strategies, 0 otherwise."""

    # --- Non-qualified (all return 0) ---

    def test_zero_periods(self):
        assert compute_verify_star(1.0, 0, 0) == 0

    def test_partial_qualified(self):
        assert compute_verify_star(1.5, 3, 5) == 0

    def test_none_qualified(self):
        assert compute_verify_star(0.5, 0, 5) == 0

    def test_negative_fitness_all_qualified(self):
        assert compute_verify_star(-0.5, 5, 5) == 0

    def test_zero_fitness_all_qualified(self):
        assert compute_verify_star(0.0, 5, 5) == 0

    # --- 1 star: all qualified, 0 < fitness < 2.0 ---

    def test_one_star_min(self):
        assert compute_verify_star(0.001, 3, 3) == 1

    def test_one_star_typical(self):
        assert compute_verify_star(1.0, 2, 2) == 1

    def test_one_star_boundary(self):
        assert compute_verify_star(1.9999, 1, 1) == 1

    # --- 2 stars: all qualified, 2.0 <= fitness < 3.0 ---

    def test_two_star_min(self):
        assert compute_verify_star(2.0, 3, 3) == 2

    def test_two_star_typical(self):
        assert compute_verify_star(2.5, 2, 2) == 2

    def test_two_star_boundary(self):
        assert compute_verify_star(2.9999, 1, 1) == 2

    # --- 3 stars: all qualified, 3.0 <= fitness < 4.5 ---

    def test_three_star_min(self):
        assert compute_verify_star(3.0, 3, 3) == 3

    def test_three_star_typical(self):
        assert compute_verify_star(3.5, 2, 2) == 3

    def test_three_star_boundary(self):
        assert compute_verify_star(4.4999, 1, 1) == 3

    # --- 4 stars: all qualified, 4.5 <= fitness < 6.0 ---

    def test_four_star_min(self):
        assert compute_verify_star(4.5, 3, 3) == 4

    def test_four_star_typical(self):
        assert compute_verify_star(5.0, 2, 2) == 4

    def test_four_star_boundary(self):
        assert compute_verify_star(5.9999, 1, 1) == 4

    # --- 5 stars: all qualified, fitness >= 6.0 ---

    def test_five_star_min(self):
        assert compute_verify_star(6.0, 3, 3) == 5

    def test_five_star_high(self):
        assert compute_verify_star(8.0, 2, 2) == 5

    # --- Single period edge cases ---

    def test_single_period_qualified(self):
        assert compute_verify_star(1.0, 1, 1) == 1

    def test_single_period_not_qualified(self):
        assert compute_verify_star(1.0, 0, 1) == 0
