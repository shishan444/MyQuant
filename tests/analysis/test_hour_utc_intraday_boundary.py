"""hour_utc 引用域 12h 放行边界测试（fix-review F13）。

日内上界=12h（_INTRADAY_MINUTES_MAX=720，1d=1440 拒）——12h 恰在
界内须放行（边界含入），1d 拒（已有覆盖）。域判定单源
is_intraday_timeframe 与 resolve_column_ref 域校验两入口同判。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]


class TestIntradayBoundary:
    def test_12h_is_intraday_boundary_inclusive(self):
        """12h=上界含入（≤720 分钟放行）；1d 越界拒。"""
        from core.analysis.column_ref import is_intraday_timeframe
        assert is_intraday_timeframe("12h") is True
        assert is_intraday_timeframe("4h") is True
        assert is_intraday_timeframe("1d") is False
        assert is_intraday_timeframe("1w") is False
        assert is_intraday_timeframe("9h") is False, "未知周期保守 False"

    def test_12h_ref_resolves_1d_rejected(self, tmp_path: Path):
        """resolve 域校验同判：hour_utc@12h 放行、@1d 结构化拒
        （detail 附 ref_tf/domain/hint）。"""
        from core.analysis.column_ref import (
            ColumnRefError, resolve_column_ref,
        )
        cdef = resolve_column_ref("hour_utc", tmp_path / "q.db",
                                  base_tf="4h", ref_tf="12h")
        assert cdef.column_name == "hour_utc"
        with pytest.raises(ColumnRefError) as ei:
            resolve_column_ref("hour_utc", tmp_path / "q.db",
                               base_tf="4h", ref_tf="1d")
        assert ei.value.code == "PARAM_INVALID"
        assert ei.value.detail["ref_tf"] == "1d"
