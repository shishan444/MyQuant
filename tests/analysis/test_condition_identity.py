"""条件指纹规范化测试（add-screen-patterns 任务 1.2）。"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_identity import canonical_condition_hash, legacy_compatible_hash


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


# ---------------------------------------------------------------- 0.2.13 演进锚

class TestLegacyCompatibleHash:
    """F1 影子：字符串逐字节=旧算法；结构化经 encode 还原=旧算法。"""

    def test_string_subject_identical_to_legacy_algorithm(self):
        cond = {"subject": "rsi_14", "action": "lt", "target": 30}
        # 旧算法直算（subject str 原样）与 legacy_compatible_hash 逐字节一致
        import hashlib
        from core.analysis.condition_identity import _SEP, _normalize_target
        parts = ["rsi_14", "lt", _normalize_target(30), "4h"]
        manual = hashlib.sha256(_SEP.join(parts).encode()).hexdigest()[:16]
        assert legacy_compatible_hash(cond, "4h") == manual
        assert canonical_condition_hash(cond, "4h") == manual  # 字符串两算法同指纹

    def test_structured_subject_shadow_equals_legacy_on_encoded_form(self):
        legacy = {"subject": "rsi_14", "action": "lt", "target": 30}
        structured = {"subject": {"indicator": "RSI",
                                  "params": {"period": 14}, "field": "rsi"},
                      "action": "lt", "target": 30}
        assert (legacy_compatible_hash(structured, "4h")
                == legacy_compatible_hash(legacy, "4h"))
        assert (canonical_condition_hash(structured, "4h")
                != canonical_condition_hash(legacy, "4h"))  # 新算法区分形制


class TestEffectiveTimeframe:
    """0.2.14 有效周期归一（B4）：condition 自带 timeframe 键优先，
    否则任务周期——显式基准=缺省同指纹；无键条件与 0.2.13 逐字节连续。"""

    def test_无timeframe键_指纹与0213逐字节连续(self):
        """冻结值字面量锚（0.2.13 终态指纹——函数内归一对无键条件零影响）。"""
        assert canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "1h"
        ) == "caa63f6430826b34"

    def test_显式基准周期_与缺省同指纹(self):
        a = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "1h")
        b = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25,
             "timeframe": "1h"}, "1h")
        assert a == b

    def test_高周期键_指纹以键为准而非任务周期(self):
        a = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25,
             "timeframe": "4h"}, "1h")
        b = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "4h")
        assert a == b          # 同有效周期同指纹（跨入口一致）
        c = canonical_condition_hash(
            {"subject": "rsi_14", "action": "lt", "target": 25}, "1h")
        assert a != c          # 高周期≠基准（同名列跨周期不算精确复用）

    def test_字符串subject两算法一致保持(self):
        cond = {"subject": "rsi_14", "action": "lt", "target": 25,
                "timeframe": "4h"}
        assert (legacy_compatible_hash(cond, "1h")
                == legacy_compatible_hash(
                    {"subject": "rsi_14", "action": "lt", "target": 25}, "4h"))


class TestGapClosureIdentity:
    def test_bool_target_归一为b标记(self) -> None:
        from core.analysis.condition_identity import _normalize_target
        assert _normalize_target(True) == "b:1"
        assert _normalize_target(False) == "b:0"
