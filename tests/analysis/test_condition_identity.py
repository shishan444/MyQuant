"""条件指纹规范化测试（add-screen-patterns 任务 1.2）。"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_identity import canonical_condition_hash


class TestCanonicalConditionHash:
    def test_numeric_normalization_int_vs_float(self):
        """25 与 25.0 同指纹（数值同值同形）。"""
        a = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "1h")
        b = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25.0}, "1h")
        assert a == b

    def test_distinct_values_distinct(self):
        a = canonical_condition_hash(
            {"subject": "rsi_ concat_guard", "action": "lt", "target": 25}, "1h")
        # 分隔符防拼接歧义：("rsi", "25lt") 之类拼接碰撞不可能构造等价
        b = canonical_condition_hash(
            {"subject": "rsi", "action": "lt", "target": 25}, "1h")
        assert a != b
        c = canonical_condition_hash(
            {"subject": "rsi", "action": "lt", "target": 30}, "1h")
        assert b != c

    def test_timeframe_participates(self):
        """同条件跨周期不同指纹（数据不同，不算精确复用）。"""
        a = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "1h")
        b = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "4h")
        assert a != b

    def test_action_distinct(self):
        """近义动作不合并（lt 与 le 语义不同即不同身份）。"""
        a = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "1h")
        b = canonical_condition_hash(
            {"subject": "rsi_14", "action": "le", "target": 25}, "1h")
        assert a != b

    def test_string_target_column_name(self):
        """target 为列名（交叉类条件）原样参与，不与数值混淆。"""
        a = canonical_condition_hash(
            {"subject": "ema_12", "action": "cross_above", "target": "ema_26"}, "1h")
        b = canonical_condition_hash(
            {"subject": "ema_12", "action": "cross_above", "target": 26}, "1h")
        assert a != b

    def test_whitespace_stripped(self):
        """subject/target 首尾空白不影响身份。"""
        a = canonical_condition_hash(
            {"subject": " rsi_14 ", "action": "lt", "target": 25}, "1h")
        b = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": "25"}, "1h")
        # "25" 字符串归一为数值形——与 25 同指纹
        assert a == b

    def test_missing_key_rejected(self):
        with pytest.raises(ValueError):
            canonical_condition_hash({"subject": "rsi_14", "action": "lt"}, "1h")

    def test_stable_across_calls(self):
        """同输入跨调用稳定（可落审计清单比对）。"""
        c = {"subject": "adx_14", "action": "gt", "target": 25}
        assert canonical_condition_hash(c, "1d") == canonical_condition_hash(c, "1d")
