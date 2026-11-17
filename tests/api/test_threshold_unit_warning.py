"""条件阈值单位可疑警告（add-threshold-unit-warning C1~C19）。

覆盖 tasks C1/C2 完成标准八项+判定函数 unit 段（C16）+MCP 披露
（C17）+与 change A 组合（C18）。警告纯观察面：校验链/幂等/指纹/
翻译/验证零改动（C10 hash 对拍锚）。
"""

import inspect
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.registry_meta import is_decimal_ratio_subject


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _ranges() -> list:
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]


def _oi_cond(target=-2.33, action="gt"):
    return {
        "when_conditions": [
            {"subject": {"indicator": "OI_ChangeRate",
                         "params": {"period": 14}, "field": "oi_change_rate"},
             "action": action, "target": target}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "gt", "target": 70}],
    }


def _register(db, conditions, direction=None):
    return insert_hypothesis(
        db, conditions=conditions, statement="单位警告测试",
        data_ranges=_ranges(), direction=direction, strategy_params={"stop_loss": 0.05})


# ── C16/C4：判定函数 unit 段 ─────────────────────────────────

class TestIsDecimalRatioSubject:
    @pytest.mark.parametrize("indicator", [
        "CMF", "OI_ChangeRate", "OIPriceDivergence", "FundingPressure"])
    def test_members_dict_form(self, indicator):
        assert is_decimal_ratio_subject(
            {"indicator": indicator, "params": {"period": 14},
             "field": "x"}) is True

    def test_funding_rate_string_form(self):
        assert is_decimal_ratio_subject("funding_rate") is True

    @pytest.mark.parametrize("indicator", [
        "TRIX", "ROC", "VROC", "RSI", "Stochastic", "OI_ZScore", "CCI",
        "RVOL", "EMA", "MACD"])
    def test_exempt_indicators(self, indicator):
        """豁免各族代表——TRIX 为对质实证纠错点（pandas_ta scalar=100
        百分数形制）必设。"""
        assert is_decimal_ratio_subject(
            {"indicator": indicator, "params": {"period": 14},
             "field": "x"}) is False

    @pytest.mark.parametrize("col", ["close", "volume", "open",
                                     "fx_ma20_deviation_v2", "model_x"])
    def test_exempt_base_and_plugin_columns(self, col):
        assert is_decimal_ratio_subject(col) is False

    def test_other_types_false(self):
        assert is_decimal_ratio_subject(None) is False
        assert is_decimal_ratio_subject(123) is False

    def test_registry_full_checklist_matches_design_c1(self):
        """design 卡 C-1 全量核对表固化（45 指标+8 基础列）：判定
        True 集合恰为清单 4 指标+{funding_rate}——清单属元数据维护，
        增删须以值域证据为准并同步核对表（spec 主条文末句）。"""
        from core.features.registry import INDICATOR_REGISTRY
        true_ind = sorted(
            name for name in INDICATOR_REGISTRY
            if is_decimal_ratio_subject({
                "indicator": name, "params": {"period": 14},
                "field": INDICATOR_REGISTRY[name].output_fields[0]
                if INDICATOR_REGISTRY[name].output_fields else "x"}))
        assert true_ind == ["CMF", "FundingPressure",
                            "OIPriceDivergence", "OI_ChangeRate"]
        from core.analysis.column_ref import BASE_SUBJECT_COLUMNS
        true_base = sorted(c for c in BASE_SUBJECT_COLUMNS
                           if is_decimal_ratio_subject(c))
        assert true_base == ["funding_rate"]


# ── C1~C8：登记链警告行为（服务层直调） ──────────────────────
