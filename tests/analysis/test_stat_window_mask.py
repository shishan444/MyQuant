"""stat_window_mask 统计段切回样板锚（CARD-07 收敛守护）。

warmup 前缀「只用不算」的统计段切回样板自四分析模板提取至
condition_kernel（与 _p_value_for 同居）。行为面由四模板既有特征网
覆盖（机械替换逐字等价）；本文件锚定：① 样板函数正牌行为；
② regime_split None 分支引用变副本的输出冻结（等价性论据的行为
证据——段边界/类型/bars 与显式段判读值全经 stat 读出）；
③ 四模板 import 防漂移（对齐 to_utc/safe_symbol 收敛锚形制）。
"""

import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_kernel import stat_window_mask

_IDX = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC",
                     name="timestamp")
_DF = pd.DataFrame({"close": range(100)}, index=_IDX)


class TestStatWindowMaskBehavior:
    def test_None_全True(self) -> None:
        m = stat_window_mask(_DF, None)
        assert m.dtype == bool
        assert len(m) == 100 and m.all()

    def test_UTC字符串_起点含入(self) -> None:
        m = stat_window_mask(_DF, "2024-01-03T02:00:00Z")
        assert int(m.sum()) == 50  # [50, 100) 含起点（>= 语义）

    def test_aware非UTC_与UTC形等价(self) -> None:
        utc = "2024-01-03T02:00:00Z"
        aware = "2024-01-03T08:00:00+06:00"  # 同一绝对时刻
        assert (stat_window_mask(_DF, aware)
                == stat_window_mask(_DF, utc)).all()

    def test_Timestamp直传_与字符串形等价(self) -> None:
        ts = pd.Timestamp("2024-01-03T02:00:00Z")
        assert (stat_window_mask(_DF, ts)
                == stat_window_mask(_DF, "2024-01-03T02:00:00Z")).all()

    def test_掩码直切_与原直切形等价(self) -> None:
        """regime_split 变体形制：df[mask] == df[df.index >= req]。"""
        req = pd.Timestamp("2024-01-03T02:00:00Z")
        cut = _DF[stat_window_mask(_DF, "2024-01-03T02:00:00Z")]
        assert cut.equals(_DF[_DF.index >= req])


class TestRegimeSplitNoneFrozen:
    """CARD-07 等价性证据：None 分支 stat 引用变副本（掩码直切）后
    输出逐字段不变——段边界/类型/bars 与显式段判读值全经 stat 读出。
    冻结值取自 make_two_regime_df 确定性数据（探针 2026-10-01）。"""

    _RANGES = [{"symbol": "TESTUSDT", "timeframe": "1d",
                "start": "2024-01-01", "end": "2025-09-01"}]

    def test_auto段输出冻结(self) -> None:
        from tests.helpers.regime_factory import make_two_regime_df
        from core.analysis.templates.regime_split import regime_split
        p = regime_split(make_two_regime_df(), data_ranges=self._RANGES,
                         timeframe="1d").to_dict()["payload"]
        got = [(s["start"][:10], s["end"][:10], s["type"], s["bars"])
               for s in p["segments"]]
        assert got == [
            ("2024-01-01", "2024-07-11", "趋势", 193),
            ("2024-07-12", "2024-08-08", "高波动", 28),
            ("2024-08-09", "2024-11-25", "趋势", 109),
            ("2024-11-26", "2024-12-19", "高波动", 24),
            ("2024-12-20", "2025-03-08", "趋势", 79),
            ("2025-03-09", "2025-08-12", "震荡", 157),
        ]

    def test_explicit段判读值冻结(self) -> None:
        from tests.helpers.regime_factory import make_two_regime_df
        from core.analysis.templates.regime_split import regime_split
        df = make_two_regime_df()
        p = regime_split(
            df, data_ranges=self._RANGES, timeframe="1d",
            segmentation=[
                {"start": str(df.index[50].date()),
                 "end": str(df.index[250].date())},
                {"start": str(df.index[300].date()),
                 "end": str(df.index[500].date()), "type": "趋势"},
            ]).to_dict()["payload"]
        got = [(s["adx_mean"], s["atr_pct_mean"], s["bars"])
               for s in p["segments"]]
        assert got == [(100.0, 0.72, 201), (54.07, 1.59, 201)]


class TestSingleSourceDrift:
    """四模板必须 import stat_window_mask（内联回潮即红）。"""

    def test_四模板导入防漂移(self) -> None:
        import core.analysis.templates  # noqa: F401 触发包初始化
        mods = [sys.modules[f"core.analysis.templates.{name}"]
                for name in ("condition_stats", "param_sweep",
                             "regime_split", "screen_patterns")]
        for mod in mods:
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
            has = any(
                isinstance(n, ast.ImportFrom)
                and n.module == "core.analysis.condition_kernel"
                and any(a.name == "stat_window_mask" for a in n.names)
                for n in ast.walk(tree))
            assert has, (
                f"{mod.__name__} 未自 condition_kernel 导入 "
                "stat_window_mask——样板内联回潮")
