"""清算判定单一权威源 · 区分性测试（batch1 D1，任务 2.3）。

验证: (a) 阈值函数为 0.9/L 口径（清算价差 d = 0.9/L，非 0.9/L^2）;
      (b) 模块内无第二个维持率字面量（除常量定义处）;
      (c) 逐 bar 判定与事后检测消费同一函数。
"""
from __future__ import annotations

import inspect
import re

import pytest

from core.backtest.engine import (
    MAINTENANCE_MARGIN_FRACTION,
    maintenance_threshold,
)


def test_threshold_matches_0_9_over_L_formula():
    """id=口径形态 0.9/L 而非 0.9/L^2"""
    # L=10: 维持线 = init_cash * (1 - 0.9/10) = 0.91 * init_cash（价差 9%）
    assert maintenance_threshold(100_000.0, 10.0) == pytest.approx(100_000.0 * (1 - 0.9 / 10.0))
    # L=2: 维持线 = 0.55 * init_cash（价差 45%）
    assert maintenance_threshold(100_000.0, 2.0) == pytest.approx(55_000.0)
    # 若为旧笔误口径 0.9/L^2，L=10 时应为 0.991*init_cash——显式排除
    assert maintenance_threshold(100_000.0, 10.0) != pytest.approx(100_000.0 * (1 - 0.9 / 100.0))


def test_constant_single_source_no_second_literal():
    """id=常量单一定义（模块内除常量定义处无 0.9 维持率字面量）"""
    import core.backtest.engine as eng
    src = inspect.getsource(eng)
    # 移除常量定义行与注释行后，不应再有裸 0.9 参与清算表达
    code_lines = [
        ln for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
        and "MAINTENANCE_MARGIN_FRACTION = 0.9" not in ln
    ]
    code = "\n".join(code_lines)
    offenders = [ln for ln in code_lines if re.search(r"0\.9\s*/", ln)]
    assert offenders == [], f"发现绕过常量的维持率字面量: {offenders}"


def test_both_consumers_reference_single_function():
    """id=两处消费点均引用唯一函数"""
    import core.backtest.engine as eng
    src = inspect.getsource(eng)
    assert 'maintenance = maintenance_threshold(c.init_cash[0], leverage)' in src, "njit 逐 bar 判定未引用唯一函数"
    assert 'maintenance = maintenance_threshold(self.init_cash, leverage)' in src, "事后检测未引用唯一函数"
    assert MAINTENANCE_MARGIN_FRACTION == 0.9
