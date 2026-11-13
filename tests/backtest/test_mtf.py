"""Tests for multi-timeframe data loading and merging."""

import pytest

pytestmark = [pytest.mark.integration]
import numpy as np
import pandas as pd

from core.validation.mtf import get_timeframe_minutes, merge_to_base

# ---------------------------------------------------------------------------
# get_timeframe_minutes
# ---------------------------------------------------------------------------

class TestGetTimeframeMinutes:
    def test_standard_timeframes(self):
        assert get_timeframe_minutes("1m") == 1
        assert get_timeframe_minutes("5m") == 5
        assert get_timeframe_minutes("15m") == 15
        assert get_timeframe_minutes("30m") == 30
        assert get_timeframe_minutes("1h") == 60
        assert get_timeframe_minutes("4h") == 240
        assert get_timeframe_minutes("1d") == 1440
        assert get_timeframe_minutes("3d") == 4320
        assert get_timeframe_minutes("1w") == 10080

    def test_case_insensitive(self):
        assert get_timeframe_minutes("15M") == 15
        assert get_timeframe_minutes("4H") == 240
        assert get_timeframe_minutes("1D") == 1440

    def test_whitespace_stripped(self):
        assert get_timeframe_minutes(" 4h ") == 240

    def test_fallback_numeric_parsing(self):
        assert get_timeframe_minutes("2h") == 120
        assert get_timeframe_minutes("6h") == 360
        assert get_timeframe_minutes("8h") == 480

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError, match="Unknown timeframe"):
            get_timeframe_minutes("xyz")

# ---------------------------------------------------------------------------
# merge_to_base
# ---------------------------------------------------------------------------

def _make_ohlcv_df(start: str, periods: int, freq: str, close_values=None) -> pd.DataFrame:
    """Helper to build a simple OHLCV DataFrame with DatetimeIndex."""
    idx = pd.date_range(start, periods=periods, freq=freq)
    n = len(idx)
    if close_values is None:
        close_values = np.linspace(100, 110, n)
    data = {
        "open": close_values - 0.5,
        "high": close_values + 1.0,
        "low": close_values - 1.0,
        "close": close_values,
        "volume": np.full(n, 1000.0),
    }
    return pd.DataFrame(data, index=idx)

class TestMergeToBase:
    def test_skip_lower_timeframe(self):
        """Info timeframe lower than base should be skipped."""
        base = _make_ohlcv_df("2024-01-01", 10, "4h")
        info = _make_ohlcv_df("2024-01-01", 40, "1h")
        info["ema_20"] = 50.0

        result = merge_to_base(base, {"1h": info}, base_tf_minutes=240)
        # No columns from 1h should be added since 1h < 4h
        assert "ema_20_1h" not in result.columns

    def test_column_renaming_with_suffix(self):
        """Indicator columns should get timeframe suffix."""
        base = _make_ohlcv_df("2024-01-01", 10, "15min")
        info = _make_ohlcv_df("2024-01-01", 5, "4h")
        info["ema_20"] = np.linspace(50, 55, len(info))

        result = merge_to_base(base, {"4h": info}, base_tf_minutes=15)
        assert "ema_20_4h" in result.columns

    def test_ohlcv_columns_now_merged(self):
        """0.2.14：OHLCV 排除清单移除——close_3d 等高周期基础列同样投影
        （「3d close > 3d EMA50」类条件真生效前提）。"""
        base = _make_ohlcv_df("2024-01-01", 40, "15min")
        info = _make_ohlcv_df("2024-01-01", 5, "4h")
        info["ema_20"] = 50.0

        result = merge_to_base(base, {"4h": info}, base_tf_minutes=15)
        for col in ["open_4h", "high_4h", "low_4h", "close_4h", "volume_4h",
                    "ema_20_4h"]:
            assert col in result.columns

    def test_forward_fill_behavior(self):
        """0.2.14 asof_completed：4h bar k 的值在其收盘时刻（开盘+4h）
        起 backward 对齐可见——逐段确定。"""
        base = _make_ohlcv_df("2024-01-01", 33, "15min")   # 覆盖到 08:00
        # 4h TF: 4 candles covering the same period
        info = _make_ohlcv_df("2024-01-01", 4, "4h")
        info["ema_20"] = [10.0, 20.0, 30.0, 40.0]

        result = merge_to_base(base, {"4h": info}, base_tf_minutes=15)
        col = result["ema_20_4h"]
        # 00:00-03:45（16 根）：首根 4h 形成期——NaN
        assert col.iloc[:16].isna().all()
        # 04:00-07:45（16 根）：bar0 收盘可见=10
        assert (col.iloc[16:32] == 10.0).all()
        # 08:00 起：bar1 收盘可见=20
        assert col.iloc[32] == 20.0

    def test_no_look_ahead_bias(self):
        """0.2.14 真前视锚（替换旧 +1 基准 bar 位移的宽松断言）：
        4h bar（00:00 开盘、04:00 收盘）的值在 04:00 前不可见（NaN），
        04:00 起可见；改掉形成中 bar 的值不影响其收盘前输出（差分）。"""
        base = _make_ohlcv_df("2024-01-01", 33, "15min")   # 00:00..08:00
        info = _make_ohlcv_df("2024-01-01", 1, "4h")
        info["ema_20"] = [99.0]

        result = merge_to_base(base, {"4h": info}, base_tf_minutes=15)
        col = result["ema_20_4h"]
        # 形成期（00:00-03:45）不可见
        assert col.iloc[:16].isna().all()
        # 收盘后（04:00 起）可见
        assert (col.iloc[16:] == 99.0).all()

        # 差分锚：形成中 bar 值改掉，收盘前输出不变
        info2 = info.copy()
        info2["ema_20"] = [-999.0]
        result2 = merge_to_base(base, {"4h": info2}, base_tf_minutes=15)
        pd.testing.assert_series_equal(
            result["ema_20_4h"].iloc[:16],
            result2["ema_20_4h"].iloc[:16], check_names=False)

    def test_multiple_timeframes(self):
        """Multiple higher TFs should all be merged."""
        base = _make_ohlcv_df("2024-01-01", 20, "15min")
        info_4h = _make_ohlcv_df("2024-01-01", 5, "4h")
        info_4h["ema_20"] = 50.0
        info_1d = _make_ohlcv_df("2024-01-01", 2, "1D")
        info_1d["rsi_14"] = 60.0

        result = merge_to_base(
            base,
            {"4h": info_4h, "1d": info_1d},
            base_tf_minutes=15,
        )
        assert "ema_20_4h" in result.columns
        assert "rsi_14_1d" in result.columns

    def test_empty_info_df_skipped(self):
        """Empty info DataFrames should be skipped without error."""
        base = _make_ohlcv_df("2024-01-01", 5, "15min")
        info = pd.DataFrame(columns=["ema_20"])

        result = merge_to_base(base, {"4h": info}, base_tf_minutes=15)
        assert "ema_20_4h" not in result.columns

    def test_info_with_no_indicator_columns(self):
        """0.2.14：纯 OHLCV info 不再跳过——高周期基础列同样投影
        （旧排除清单移除）。"""
        base = _make_ohlcv_df("2024-01-01", 5, "15min")
        info = _make_ohlcv_df("2024-01-01", 2, "4h")

        result = merge_to_base(base, {"4h": info}, base_tf_minutes=15)
        # OHLCV 后缀列全量投影（5 基础 + 5 后缀）
        assert len(result.columns) == len(base.columns) + 5
        assert "close_4h" in result.columns


# ---------------------------------------------------------------------------
# R-3 enrich_with_mtf（fix-silent-degradation）：两引擎共享段
# ---------------------------------------------------------------------------

class TestMtfDataExceptionsSingleSource:
    """R-3：MTF 数据类异常清单单源契约（值断言＋全工程唯一书写）。"""

    def test_value_equals_contract(self):
        from core.validation.mtf import MTF_DATA_EXCEPTIONS
        # 值断言（非仅存在断言）：清单恰为三数据类异常
        assert MTF_DATA_EXCEPTIONS == (OSError, ValueError, KeyError)

    def test_single_definition_project_wide(self):
        """生产域（core/api/scripts）仅 core/validation/mtf.py 一处顶层赋值。"""
        import re
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        hits = []
        for src_dir in ("core", "api", "scripts"):
            for path in (repo_root / src_dir).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if re.search(r"^MTF_DATA_EXCEPTIONS\s*=", text, re.MULTILINE):
                    hits.append(str(path))
        assert hits == [str(repo_root / "core" / "validation" / "mtf.py")], (
            f"异常清单须单源于 mtf.py，检测到第二处书写: {hits}")


class TestEnrichWithMtf:
    """R-3：enrich_with_mtf 函数级三面（正常合并/数据类回退/非数据类上抛）。"""

    @staticmethod
    def _write_pair(tmp_path):
        """建 15m 基准与 4h 高周期 parquet（走真实装载链）。"""
        base = _make_ohlcv_df("2024-01-01", 48, "15min")
        base.index.name = "timestamp"
        base.to_parquet(tmp_path / "TESTUSDT_15m.parquet")
        high = _make_ohlcv_df("2024-01-01", 6, "4h")
        high.index.name = "timestamp"
        high.to_parquet(tmp_path / "TESTUSDT_4h.parquet")
        return base

    def test_normal_merge(self, tmp_path):
        """正常面：高周期在库 → 合并帧含带后缀高周期列，warnings 空。"""
        from core.validation.mtf import enrich_with_mtf

        base = self._write_pair(tmp_path)
        out, warnings = enrich_with_mtf(
            base, "TESTUSDT", "15m", ["4h"], "2024-01-01", "2024-01-02", str(tmp_path))
        assert any(c.endswith("_4h") for c in out.columns)
        assert warnings == []

    @pytest.mark.parametrize("exc", [
        OSError("disk error"), ValueError("bad value"), KeyError("missing col"),
    ], ids=["OSError", "ValueError", "KeyError"])
    def test_data_exception_fallback(self, tmp_path, monkeypatch, caplog, exc):
        """数据类回退面：返回原帧＋固定文案 warning；异常原文只进日志。"""
        import logging

        import core.validation.mtf as mtf_mod
        from core.validation.mtf import enrich_with_mtf

        base = self._write_pair(tmp_path)

        def _boom(*args, **kwargs):
            raise exc

        monkeypatch.setattr(mtf_mod, "load_mtf_data", _boom)
        with caplog.at_level(logging.WARNING, logger="core.validation.mtf"):
            out, warnings = enrich_with_mtf(
                base, "TESTUSDT", "15m", ["4h"], "2024-01-01", "2024-01-02", str(tmp_path))
        assert out is base  # 原帧回退（同一对象）
        assert warnings == [
            "MTF data loading failed, falling back to single timeframe"]
        assert str(exc) not in "".join(warnings)  # 异常原文不回传
        assert any("MTF 数据装载失败" in r.message for r in caplog.records)

    @pytest.mark.parametrize("exc", [TypeError, AttributeError, RuntimeError],
                             ids=["TypeError", "AttributeError", "RuntimeError"])
    def test_non_data_exception_raises(self, tmp_path, monkeypatch, exc):
        """非数据类上抛面：装载代码自身缺陷类异常不被吞为回退。"""
        import core.validation.mtf as mtf_mod
        from core.validation.mtf import enrich_with_mtf

        base = self._write_pair(tmp_path)

        def _boom(*args, **kwargs):
            raise exc("code bug")

        monkeypatch.setattr(mtf_mod, "load_mtf_data", _boom)
        with pytest.raises(exc):
            enrich_with_mtf(
                base, "TESTUSDT", "15m", ["4h"], "2024-01-01", "2024-01-02", str(tmp_path))

    def test_empty_referenced_tfs(self, tmp_path):
        """空清单回归：空装载 → 原帧返回零 warning。"""
        from core.validation.mtf import enrich_with_mtf

        base = self._write_pair(tmp_path)
        out, warnings = enrich_with_mtf(
            base, "TESTUSDT", "15m", [], "2024-01-01", "2024-01-02", str(tmp_path))
        assert out is base
        assert warnings == []
