"""B10.1 空缺区收口：backtest_service 直测 + discovery 路由层测。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


class TestExitSweepGroupCount:
    """路由 helper _exit_sweep_group_count 直测（fix-fullrun-baseline-4red
    阶段四盲区 :280-281 畸形兜底此前未触达——heavy 判定的防御式输入
    归一：形制异常按 0，参数合法性归模板校验确定序）。"""

    def test_normal_modes_summed(self):
        """正常形制：各组 stops×take_profits 乘积求和。"""
        from api.routes.research.analysis import _exit_sweep_group_count
        params = {"grid": {"modes": [
            {"stops": [0.01, 0.02], "take_profits": [0.03]},
            {"stops": [0.01], "take_profits": [0.04, 0.05, 0.06]},
        ]}}
        assert _exit_sweep_group_count(params) == 2 * 1 + 1 * 3

    @pytest.mark.parametrize("bad", [
        pytest.param({"grid": 42}, id="grid 非对象"),
        pytest.param({"grid": {"modes": {"a": 1}}},
                     id="modes 非列表（迭代出键名）"),
        pytest.param({"grid": {"modes": [42]}}, id="条目非对象"),
        pytest.param({"grid": {"modes": [{"stops": 3,
                                          "take_profits": [1]}]}},
                     id="stops 不可计数"),
    ])
    def test_malformed_grid_returns_zero(self, bad):
        """畸形形制统一按 0（AttributeError/TypeError 双捕获面）。"""
        from api.routes.research.analysis import _exit_sweep_group_count
        assert _exit_sweep_group_count(bad) == 0
