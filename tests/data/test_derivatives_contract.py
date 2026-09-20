"""数据前置形制契约测试（harden-research-test-coverage 1.4 / C1）。

防"全绿但数据瞎了"：derivatives_merger 的优雅 no-op 设计意味着
`{SYMBOL}_oi_{tf}.parquet` 命名或 open_interest/funding_rate 列名被
破坏时指标静默消失而非报错——形制契约是唯一防线。

双层（D4）：
- 形制层：构造数据落盘 → _try_merge_derivatives 真调用 → 非 NaN；
  人为破坏命名/列名时红。
- 真数据抽样层：对 data/market 的 parquet 断言命名/列/Index 契约 +
  双品种衍生品指标非 NaN；数据缺失时显式 skip（不假绿）。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

MARKET_DIR = Path("data/market")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
KLINE_TFS = ("15m", "30m", "1h", "4h", "1d", "3d")
OI_TFS = ("5m", "15m", "30m", "1h", "4h", "1d", "3d")
DERIV_INDICATOR_COLS = ("oi_change_rate_14", "oi_zscore_20", "funding_zscore_30",
                        "oi_price_divergence_14", "funding_pressure_8")


def _make_ohlcv(n=300, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 900, n), "trades": 50,
    }, index=idx)


class TestContractFormLayer:
    """形制层：merger 对正确形制合并出非 NaN；破坏形制可被测试捕获。"""

    def test_correct_form_merges_non_nan(self, tmp_path: Path):
        df = _make_ohlcv()
        oi_idx = df.index[::6]
        oi = pd.DataFrame({"open_interest": np.linspace(1000, 2000, len(oi_idx))},
                          index=pd.DatetimeIndex(oi_idx, name="timestamp"))
        fr_idx = df.index[::24]
        fr = pd.DataFrame({"funding_rate": np.linspace(0.0001, 0.0005, len(fr_idx))},
                          index=pd.DatetimeIndex(fr_idx, name="timestamp"))
        # 落盘形制：{SYMBOL}_oi_{tf} / {SYMBOL}_funding
        oi.to_parquet(tmp_path / "BTCUSDT_oi_4h.parquet")
        fr.to_parquet(tmp_path / "BTCUSDT_funding.parquet")

        from core.data.mtf_loader import _try_merge_derivatives
        merged = _try_merge_derivatives(tmp_path, "BTCUSDT", "4h", df)
        assert merged["open_interest"].notna().sum() > 0, "OI 未合并"
        assert merged["funding_rate"].notna().sum() > 0, "funding 未合并"

    def test_wrong_filename_silently_ignored_detected_by_contract(self, tmp_path: Path):
        """形制破坏场景（红向证明）：错误命名使 merger 优雅 no-op——
        契约测试正是要抓住这种静默失明。这里验证该行为本身（ documenting
        the trap），真数据层用命名断言兜底。"""
        df = _make_ohlcv()
        oi_idx = df.index[::6]
        oi = pd.DataFrame({"open_interest": np.full(len(oi_idx), 1000.0)},
                          index=pd.DatetimeIndex(oi_idx, name="timestamp"))
        # 错误命名（- 而非 _）
        oi.to_parquet(tmp_path / "BTCUSDT-oi-4h.parquet")

        from core.data.mtf_loader import _try_merge_derivatives
        merged = _try_merge_derivatives(tmp_path, "BTCUSDT", "4h", df)
        assert "open_interest" not in merged.columns, (
            "merger 应优雅 no-op（错名不合并）——真数据层命名契约防此失明")

    def test_wrong_column_name_breaks_indicator(self, tmp_path: Path):
        """列名破坏：open_interest 改名 → 指标静默消失（no-op）。"""
        df = _make_ohlcv()
        oi_idx = df.index[::6]
        oi = pd.DataFrame({"oi_value": np.full(len(oi_idx), 1000.0)},  # 错列名
                          index=pd.DatetimeIndex(oi_idx, name="timestamp"))
        oi.to_parquet(tmp_path / "BTCUSDT_oi_4h.parquet")

        from core.data.mtf_loader import _try_merge_derivatives
        merged = _try_merge_derivatives(tmp_path, "BTCUSDT", "4h", df)
        assert "open_interest" not in merged.columns


class TestRealDataContract:
    """真数据抽样层：data/market 的 28 parquet 命名/列/Index 契约 +
    双品种衍生品指标全链路非 NaN（缺文件显式 skip）。"""

    def test_kline_files_and_columns(self):
        missing = [f"{s}_{tf}.parquet" for s in SYMBOLS for tf in KLINE_TFS
                   if not (MARKET_DIR / f"{s}_{tf}.parquet").exists()]
        if missing:
            pytest.skip(f"数据文件缺失（不假绿）: {missing}")
        for s in SYMBOLS:
            for tf in KLINE_TFS:
                df = pd.read_parquet(MARKET_DIR / f"{s}_{tf}.parquet")
                assert isinstance(df.index, pd.DatetimeIndex)
                assert str(df.index.tz) == "UTC"
                for col in ("open", "high", "low", "close", "volume"):
                    assert col in df.columns, f"{s}_{tf} 缺列 {col}"
                assert df.index.is_monotonic_increasing

    def test_derivatives_files_form(self):
        oi_missing = [f"{s}_oi_{tf}" for s in SYMBOLS for tf in OI_TFS
                      if not (MARKET_DIR / f"{s}_oi_{tf}.parquet").exists()]
        fr_missing = [f"{s}_funding" for s in SYMBOLS
                      if not (MARKET_DIR / f"{s}_funding.parquet").exists()]
        if oi_missing or fr_missing:
            pytest.skip(f"衍生品文件缺失（不假绿）: oi={oi_missing} fr={fr_missing}")
        for s in SYMBOLS:
            for tf in OI_TFS:
                oi = pd.read_parquet(MARKET_DIR / f"{s}_oi_{tf}.parquet")
                assert "open_interest" in oi.columns, f"{s}_oi_{tf} 列名契约破坏"
                assert str(oi.index.tz) == "UTC"
            fr = pd.read_parquet(MARKET_DIR / f"{s}_funding.parquet")
            assert "funding_rate" in fr.columns, f"{s}_funding 列名契约破坏"
            assert str(fr.index.tz) == "UTC"

    def test_derivatives_indicators_non_nan_full_chain(self):
        """0.1 终验固化：双品种 5 衍生品指标经 load→merge→compute 非 NaN。"""
        kline_missing = [f"{s}_1h.parquet" for s in SYMBOLS
                         if not (MARKET_DIR / f"{s}_1h.parquet").exists()]
        if kline_missing:
            pytest.skip(f"K 线缺失（不假绿）: {kline_missing}")
        from core.data.mtf_loader import load_and_prepare_df
        for s in SYMBOLS:
            df = load_and_prepare_df(MARKET_DIR, s, "1h")
            if df is None:
                pytest.skip(f"{s} 1h 数据不足 min_bars")
            for col in DERIV_INDICATOR_COLS:
                if col not in df.columns:
                    pytest.fail(f"{s} 衍生品指标列缺失: {col}（形制破坏的静默失明）")
                n_valid = df[col].notna().sum()
                assert n_valid > 0, f"{s}.{col} 全 NaN——衍生品数据链路失明"
