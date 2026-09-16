"""清算判定单一权威源 · 区分性测试（batch1 D1 任务 2.3；B7.1 重锚定）。

B7.0 口径卡冻结：维持率由 0.9/L 简化近似改为币安 USDT-M 分层档位，
基准由全额 init_cash 改为仓位名义价值。守护不变量不变——单一权威源、
逐 bar 与事后同源；数值断言按新口径重锚定（守护不变量非历史数值）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活

import inspect
import re

import pytest

from core.market_model import (
    BinanceMmrTiers,
    maintenance_margin_rate,
    maintenance_threshold,
    tier_arrays,
)


def test_tier_selection_at_boundaries():
    """id=档位边界正确选择（MODIFIED spec 场景）"""
    assert maintenance_margin_rate(30_000) == 0.004
    assert maintenance_margin_rate(50_000) == 0.004   # 边界含
    assert maintenance_margin_rate(50_001) == 0.005
    assert maintenance_margin_rate(1_000_000) == 0.010
    assert maintenance_margin_rate(1e12) == 0.100      # 超界取最高档


def test_threshold_formula_tiered():
    """id=清算线=等效换算形态 init_cash×(1−(1−mmr)/L)，mmr 按名义选档"""
    # L=5, init_cash=100k → 名义 500k → mmr 1% → 80.2k（旧简化口径 82k）
    assert maintenance_threshold(100_000, 5) == pytest.approx(80_200)
    # L=10 → 名义 1M → mmr 1% → 90.1k（旧简化 91k——分层后略宽松）
    assert maintenance_threshold(100_000, 10) == pytest.approx(90_100)


def test_single_source_no_mmr_literal_in_engine():
    """id=常量单一定义（engine 内无维持率字面量，档位表唯一源在 market_model）"""
    import core.backtest.engine as eng
    src = inspect.getsource(eng)
    code_lines = [
        ln for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    offenders = [
        ln for ln in code_lines
        if re.search(r"=\s*0\.9\b", ln) or re.search(r"0\.00[45]\b", ln)
    ]
    assert offenders == [], f"engine 内发现维持率字面量: {offenders}"


def test_both_consumers_reference_market_model():
    """id=两处消费点均引用 market_model（单一权威源）"""
    import core.backtest.engine as eng
    src = inspect.getsource(eng)
    assert "mmr_tier_max, mmr_tier_rate" in src          # njit 逐 bar 内联选档
    assert "_mm_tier_arrays()[0]" in src and "_mm_tier_arrays()[1]" in src
    assert "_mm_threshold(" in src                        # 事后检测引用唯一函数


def test_tier_arrays_match_python_tiers():
    """id=njit 数组与 Python 档位表一致（两形制同源）"""
    maxs, rates = tier_arrays()
    assert list(rates) == [t.mmr for t in BinanceMmrTiers]
    assert float(maxs[-1]) == float("inf")
