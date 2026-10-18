"""护栏分离结构性断言（build-position-registry 任务 4.9——R10）。

R10 契约（design §10+spec「护栏与模型分离」Requirement）：SizingInput
与两参数包均不含 leverage/max_exposure 字段——结构性保证「模型不读
杠杆与护栏参数」（模型想截断也没有输入；护栏 cap=权益×min(leverage,
max_exposure) 住执行层）。字段集精确断言：既不允许 leverage/max_
exposure 混入，也不允许私增字段（字段集==契约全集）。

cap 截断回归由既有 test_ledger cap 用例承载（test_risk_budget_cap_
truncation_with_marker 等——引用声明，不在本文件重复建）；REQ-4-07
import 零引用断言住 tests/paper_trading/test_no_backtest_import.py。
"""
from __future__ import annotations

import dataclasses

import pytest

pytestmark = [pytest.mark.unit]

from core.positioning.interfaces import SizingInput
from core.positioning.models import (
    FixedProportionalParams,
    RiskBudgetParams,
)

_GUARDRAIL_FIELDS = {"leverage", "max_exposure"}


class TestGuardrailFieldIsolation:
    """R10 字段集 inspect 断言（design §3/§10 契约全集）。"""

    @pytest.mark.parametrize(
        ("cls", "expected_fields"),
        [
            pytest.param(
                SizingInput, {"equity", "base_price", "atr_value"},
                id="SizingInput"),
            pytest.param(
                FixedProportionalParams, {"first_size"},
                id="FixedProportionalParams"),
            pytest.param(
                RiskBudgetParams,
                {"risk_fraction", "sl_mode", "stop_loss"},
                id="RiskBudgetParams"),
        ])
    def test_field_sets_equal_contract_no_guardrail_inputs(
            self, cls, expected_fields):
        """三类输入载体字段集==契约全集（精确相等——护栏字段不混入、
        契约字段不缺失、私增字段即红）。"""
        fields = {f.name for f in dataclasses.fields(cls)}
        assert fields == expected_fields, (
            f"{cls.__name__} 字段集 {fields} 偏离契约 {expected_fields}")
        assert fields & _GUARDRAIL_FIELDS == set(), (
            f"{cls.__name__} 不得含护栏输入字段 "
            f"{_GUARDRAIL_FIELDS & fields}")
