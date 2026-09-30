"""validation engine 条件求值边界锚（质量阶段 F-2 回归）。

F-2（30818ba）：弱类型入口（run_analysis 的 condition 为任意 dict）
下 SNR 动作数值键坏值经 _cond_number 结构化拒绝
（RESEARCH_PARAM_INVALID，经既有 research_error_to_http 转 422），
替代裸 float()/int() 穿 500；cast 保留原类型语义（int 对 '10.5'
拒绝不隐式截断）。
"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.validation.engine import _evaluate_single_condition
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def df() -> pd.DataFrame:
    return make_ohlcv(n=60, freq="1h", start="2024-01-01", seed=3)


class TestSnrNumericKeyBoundaries:
    """SNR 动作（touch_bounce/role_reversal）数值键坏值四形与好值对照。"""

    @pytest.mark.parametrize("bad", ["abc", None, {"a": 1}],
                             ids=["非数值串", "None", "dict"])
    def test_touch_bounce_proximity坏值_结构化拒绝(self, df, bad):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(df, {
                "subject": "close", "action": "touch_bounce",
                "target": 0, "proximity_pct": bad})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        detail = ei.value.detail or {}
        assert detail["param"] == "condition.proximity_pct"

    def test_role_reversal_lookback截断形_int语义不隐式截断(self, df):
        with pytest.raises(ResearchError) as ei:
            _evaluate_single_condition(df, {
                "subject": "close", "action": "role_reversal",
                "target": 0, "lookback": "10.5"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert (ei.value.detail or {})["param"] == "condition.lookback"

    def test_好值字符串数值_照常求值(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "close", "action": "touch_bounce",
            "target": 0, "proximity_pct": "0.01"})
        assert isinstance(out, pd.Series) and out.dtype == bool

    def test_缺省数值键_走默认照常求值(self, df):
        out = _evaluate_single_condition(df, {
            "subject": "close", "action": "touch_bounce", "target": 0})
        assert isinstance(out, pd.Series) and out.dtype == bool
