"""模板列前置守卫特征测试（修复期特征网：记录现状行为）。

column_guard 是模板域公共守卫（require_column / require_indicator），
此前 tests 全域零行为覆盖（行覆盖 100% 系 def 语句被执行的假象）。
本文件锚定其现状契约：不可用列/未知指标 → ResearchError 结构化拒绝，
detail 附可用清单（错误即文档）。
"""

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.column_guard import (
    require_column,
    require_indicator,
)


def _df() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC",
                        name="timestamp")
    return pd.DataFrame(
        {"open": [1.0] * 5, "close": [2.0] * 5, "volume": [10] * 5},
        index=idx)


class TestRequireColumn:
    def test_列存在_通过不抛(self) -> None:
        require_column(_df(), "close", template="condition_stats")

    def test_列缺失_结构化拒绝附可用列清单(self) -> None:
        with pytest.raises(ResearchError) as ei:
            require_column(_df(), "rsi_14", template="condition_stats")

        err = ei.value
        assert err.code == "RESEARCH_COLUMN_NOT_AVAILABLE"
        assert err.status == 422
        assert err.detail["column"] == "rsi_14"
        # 可用列清单 = df 实际列（索引名不计入 columns）
        assert sorted(err.detail["available_columns"]) == ["close", "open", "volume"]
        # 指引：指标条件走 condition_stats（支持指标参数计算）
        assert "condition_stats" in err.detail["note"]

    def test_拒绝信息含模板名(self) -> None:
        with pytest.raises(ResearchError, match="regime_split"):
            require_column(_df(), "nope", template="regime_split")


class TestRequireIndicator:
    def test_注册表内指标_通过不抛(self) -> None:
        registry = {"RSI": {}, "EMA": {}}
        require_indicator("RSI", registry)

    def test_未知指标_结构化拒绝附排序清单(self) -> None:
        registry = {"EMA": {}, "RSI": {}, "ADX": {}}
        with pytest.raises(ResearchError) as ei:
            require_indicator("NOPE", registry)

        err = ei.value
        assert err.code == "RESEARCH_INDICATOR_UNKNOWN"
        assert err.status == 422
        assert err.detail["indicator"] == "NOPE"
        # 清单排序稳定（错误即文档的可读性契约）
        assert err.detail["available_indicators"] == ["ADX", "EMA", "RSI"]

    def test_空注册表_拒绝附空清单(self) -> None:
        with pytest.raises(ResearchError) as ei:
            require_indicator("RSI", {})

        assert ei.value.detail["available_indicators"] == []
