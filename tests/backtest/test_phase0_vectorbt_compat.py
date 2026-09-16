"""Phase 0: Verify vectorbt 0.28.5 API compatibility."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]
import vectorbt as vbt
from vectorbt.portfolio.enums import Direction

def test_vectorbt_version():
    assert vbt.__version__ == "0.28.5"

def test_from_order_func_exists():
    assert hasattr(vbt.Portfolio, "from_order_func")

def test_direction_both_equals_2():
    assert int(Direction.Both) == 2

def test_nb_order_nb_importable():
    from vectorbt.portfolio import nb

    assert hasattr(nb, "order_nb")

def test_no_order_importable():
    from vectorbt.portfolio.enums import NoOrder

    assert NoOrder is not None

def test_from_order_func_smoke():
    """Smoke test: run from_order_func with minimal callback."""
    from vectorbt.portfolio.enums import NoOrder
    from vectorbt.portfolio import nb
    from numba import njit

    n = 50
    close = pd.Series(np.linspace(100, 110, n))

    @njit
    def order_func(c):
        if c.i == 10 and c.position_now == 0:
            return nb.order_nb(
                size=np.float64(1.0),
                size_type=np.int64(2),
                price=np.float64(c.close[c.i, c.col]),
            )
        if c.i == 30 and c.position_now != 0:
            return nb.order_nb(
                size=np.float64(abs(c.position_now)),
                size_type=np.int64(0),
                price=np.float64(c.close[c.i, c.col]),
            )
        return NoOrder

    pf = vbt.Portfolio.from_order_func(
        close=close,
        order_func_nb=order_func,
        init_cash=100000,
        freq="1D",
    )
    assert isinstance(pf, vbt.Portfolio)
    assert pf.trades.count() >= 1
