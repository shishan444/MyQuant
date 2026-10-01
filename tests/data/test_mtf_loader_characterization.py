"""mtf_loader 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住，作为修复期回归锚点——
断言变红即行为变化，须由修复者显式确认是否契约内变更。
openspec 主规格库对本模块无覆盖，阶段四将升级为规格测试。

覆盖缺口（建网时覆盖率 45%）：safe_symbol / find_parquet 别名回退 /
load_and_prepare_df 失败分支与切片边界 / load_mtf_data 全流程 /
衍生品文件合并进单周期与多周期装载。
"""

import pytest

pytestmark = [pytest.mark.unit]

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from core.data.mtf_loader import (
    find_parquet,
    load_and_prepare_df,
    load_mtf_data,
    safe_symbol,
)
from core.data.storage import save_parquet


def make_ohlcv(n: int, start: str = "2024-01-01", freq: str = "1h") -> pd.DataFrame:
    """构造 n 根规则 OHLCV（close 线性递增，便于手工核算）。"""
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = pd.Series(np.arange(1, n + 1, dtype=float) * 10.0, index=idx)
    return pd.DataFrame(
        {
            "open": close - 5.0,
            "high": close + 5.0,
            "low": close - 8.0,
            "close": close,
            "volume": pd.Series(np.arange(1, n + 1, dtype=float) * 100.0, index=idx),
        },
        index=idx,
    )


class TestSafeSymbol:
    """safe_symbol：非字母数字字符剥离，大小写保留。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("BTCUSDT", "BTCUSDT", id="纯字母数字直通"),
            pytest.param("BTC-USDT", "BTCUSDT", id="连字符剥离"),
            pytest.param("BTC/USDT", "BTCUSDT", id="斜杠剥离"),
            pytest.param("BTC USDT", "BTCUSDT", id="空格剥离"),
            pytest.param("btc-usdt", "btcusdt", id="小写保留不转大写"),
        ],
    )
    def test_strips_non_alphanumeric(self, raw: str, expected: str) -> None:
        assert safe_symbol(raw) == expected


class TestFindParquet:
    """find_parquet：主名优先、周期别名回退、未命中 None。"""

    def test_primary_name_hit(self, tmp_path) -> None:
        p = tmp_path / "BTCUSDT_1h.parquet"
        p.write_bytes(b"")
        assert find_parquet(tmp_path, "BTCUSDT", "1h") == p

    def test_alias_60m_backfills_1h(self, tmp_path) -> None:
        p = tmp_path / "BTCUSDT_60m.parquet"
        p.write_bytes(b"")
        assert find_parquet(tmp_path, "BTCUSDT", "1h") == p

    def test_alias_1D_backfills_1d(self, tmp_path) -> None:
        # macOS APFS 大小写不敏感卷上 primary(1d) 与 alias(1D) 是同一文件，
        # 归一大小写断言跨平台成立：1d 请求能定位 1D 命名的可用文件
        p = tmp_path / "BTCUSDT_1D.parquet"
        p.write_bytes(b"")
        found = find_parquet(tmp_path, "BTCUSDT", "1d")
        assert found is not None
        assert found.name.lower() == "btcusdt_1d.parquet"
        assert found.exists()

    def test_primary_wins_over_alias(self, tmp_path) -> None:
        primary = tmp_path / "BTCUSDT_1d.parquet"
        alias = tmp_path / "BTCUSDT_1D.parquet"
        primary.write_bytes(b"")
        alias.write_bytes(b"")
        assert find_parquet(tmp_path, "BTCUSDT", "1d") == primary

    def test_missing_returns_none(self, tmp_path) -> None:
        # 2h 无别名条目：主名不存在即 None
        assert find_parquet(tmp_path, "BTCUSDT", "2h") is None


class TestLoadAndPrepareDf:
    """load_and_prepare_df：缺数据/不足/切片边界/min_bars 门槛。"""

    def test_missing_file_returns_none(self, tmp_path) -> None:
        assert (
            load_and_prepare_df(tmp_path, "BTCUSDT", "1h", compute_indicators=False)
            is None
        )

    def test_below_default_min_bars_returns_none(self, tmp_path) -> None:
        save_parquet(make_ohlcv(10), tmp_path / "BTCUSDT_1h.parquet")
        assert (
            load_and_prepare_df(tmp_path, "BTCUSDT", "1h", compute_indicators=False)
            is None
        )

    def test_custom_min_bars_passes(self, tmp_path) -> None:
        save_parquet(make_ohlcv(10), tmp_path / "BTCUSDT_1h.parquet")
        out = load_and_prepare_df(
            tmp_path, "BTCUSDT", "1h", min_bars=5, compute_indicators=False
        )
        assert len(out) == 10

    def test_raw_passthrough_keeps_ohlcv_columns(self, tmp_path) -> None:
        save_parquet(make_ohlcv(60), tmp_path / "BTCUSDT_1h.parquet")
        out = load_and_prepare_df(tmp_path, "BTCUSDT", "1h", compute_indicators=False)
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]
        assert len(out) == 60
        assert out["close"].iloc[0] == 10.0

    def test_data_start_slice_boundary(self, tmp_path) -> None:
        # 60 根自 01-01 00:00 起 1h 间隔：保留恰 50 根通过，49 根拒绝
        df = make_ohlcv(60)
        save_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
        start_50 = df.index[10]  # 保留 [10, 59] = 50 根
        start_49 = df.index[11]  # 保留 [11, 59] = 49 根
        assert (
            len(
                load_and_prepare_df(
                    tmp_path, "BTCUSDT", "1h",
                    data_start=str(start_50), compute_indicators=False,
                )
            )
            == 50
        )
        assert (
            load_and_prepare_df(
                tmp_path, "BTCUSDT", "1h",
                data_start=str(start_49), compute_indicators=False,
            )
            is None
        )

    def test_data_end_slice_boundary(self, tmp_path) -> None:
        df = make_ohlcv(60)
        save_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
        end_50 = df.index[49]  # 保留 [0, 49] = 50 根
        end_49 = df.index[48]  # 保留 [0, 48] = 49 根
        assert (
            len(
                load_and_prepare_df(
                    tmp_path, "BTCUSDT", "1h",
                    data_end=str(end_50), compute_indicators=False,
                )
            )
            == 50
        )
        assert (
            load_and_prepare_df(
                tmp_path, "BTCUSDT", "1h",
                data_end=str(end_49), compute_indicators=False,
            )
            is None
        )

    def test_indicator_compute_branch_passthrough(self, tmp_path) -> None:
        """compute_indicators=True：合并衍生品后的 df 交指标层，stop_check 透传。"""
        base = make_ohlcv(60)
        save_parquet(base, tmp_path / "BTCUSDT_1h.parquet")
        sentinel = base.copy()
        sentinel["marker"] = 1.0
        stop = MagicMock()
        with patch(
            "core.features.indicators.compute_all_indicators", return_value=sentinel
        ) as mock_compute:
            out = load_and_prepare_df(
                tmp_path, "BTCUSDT", "1h", stop_check=stop, compute_indicators=True
            )
        assert out is sentinel
        mock_compute.assert_called_once()
        called_df = mock_compute.call_args.args[0]
        assert len(called_df) == 60
        assert list(called_df.columns) == ["open", "high", "low", "close", "volume"]
        assert mock_compute.call_args.kwargs.get("stop_check") is stop


class TestLoadAndPrepareDfDerivatives:
    """衍生品 parquet 存在时并入返回帧（ffill 对齐语义）。"""

    def test_oi_file_merged_ffill(self, tmp_path) -> None:
        base = make_ohlcv(60)  # 01-01 00:00 起 1h
        save_parquet(base, tmp_path / "BTCUSDT_1h.parquet")
        oi_idx = pd.date_range("2024-01-01 02:00", periods=30, freq="1h", tz="UTC")
        oi = pd.DataFrame(
            {"open_interest": np.arange(1, 31, dtype=float)}, index=oi_idx
        )
        save_parquet(oi, tmp_path / "BTCUSDT_oi_1h.parquet")

        out = load_and_prepare_df(tmp_path, "BTCUSDT", "1h", compute_indicators=False)

        assert "open_interest" in out.columns
        # OHLCV 首两根（00:00/01:00）早于 OI 首根（02:00）→ NaN
        assert pd.isna(out["open_interest"].iloc[0])
        assert pd.isna(out["open_interest"].iloc[1])
        # 02:00 对齐 OI 首值；OI 结束后持续 ffill
        assert out["open_interest"].iloc[2] == 1.0
        assert out["open_interest"].iloc[31] == 30.0
        assert out["open_interest"].iloc[59] == 30.0

    def test_funding_file_merged_ffill(self, tmp_path) -> None:
        """funding 文件名不带周期段：{symbol}_funding.parquet。"""
        base = make_ohlcv(60)
        save_parquet(base, tmp_path / "BTCUSDT_1h.parquet")
        fr_idx = pd.date_range("2024-01-01", periods=3, freq="8h", tz="UTC")
        fr = pd.DataFrame(
            {"funding_rate": [0.0001, 0.0002, 0.0003]}, index=fr_idx
        )
        save_parquet(fr, tmp_path / "BTCUSDT_funding.parquet")

        out = load_and_prepare_df(tmp_path, "BTCUSDT", "1h", compute_indicators=False)

        assert "funding_rate" in out.columns
        assert out["funding_rate"].iloc[0] == pytest.approx(0.0001)  # 00:00 对齐
        assert out["funding_rate"].iloc[8] == pytest.approx(0.0002)  # 08:00 对齐
        assert out["funding_rate"].iloc[15] == pytest.approx(0.0002)  # 15:00 < 16:00 ffill
        assert out["funding_rate"].iloc[16] == pytest.approx(0.0003)  # 16:00 对齐


class TestLoadMtfData:
    """load_mtf_data：exec 周期直通、附加周期装载与各跳过分支。"""

    def test_exec_tf_passthrough_identity(self, tmp_path) -> None:
        enhanced = make_ohlcv(60)
        out = load_mtf_data(
            tmp_path, "BTCUSDT", "1h", enhanced, {"1h"}, compute_indicators=False
        )
        assert out["1h"] is enhanced  # exec 周期帧原对象直通

    def test_missing_tf_file_skipped(self, tmp_path) -> None:
        enhanced = make_ohlcv(60)
        out = load_mtf_data(
            tmp_path, "BTCUSDT", "1h", enhanced, {"4h"}, compute_indicators=False
        )
        assert set(out.keys()) == {"1h"}

    def test_short_tf_skipped(self, tmp_path) -> None:
        save_parquet(make_ohlcv(30), tmp_path / "BTCUSDT_4h.parquet")
        enhanced = make_ohlcv(60)
        out = load_mtf_data(
            tmp_path, "BTCUSDT", "1h", enhanced, {"4h"}, compute_indicators=False
        )
        assert set(out.keys()) == {"1h"}  # 低于 50 根硬门槛跳过

    def test_slice_below_50_skipped(self, tmp_path) -> None:
        save_parquet(make_ohlcv(60, freq="4h"), tmp_path / "BTCUSDT_4h.parquet")
        enhanced = make_ohlcv(60)
        # data_end 截到 01-02 12:00：保留 10 根 < 50 → 跳过
        out = load_mtf_data(
            tmp_path, "BTCUSDT", "1h", enhanced, {"4h"},
            data_end="2024-01-02 12:00:00+00:00", compute_indicators=False,
        )
        assert set(out.keys()) == {"1h"}

    def test_loaded_tf_raw_columns(self, tmp_path) -> None:
        save_parquet(make_ohlcv(60, freq="4h"), tmp_path / "BTCUSDT_4h.parquet")
        enhanced = make_ohlcv(60)
        out = load_mtf_data(
            tmp_path, "BTCUSDT", "1h", enhanced, {"4h"}, compute_indicators=False
        )
        assert list(out["4h"].columns) == ["open", "high", "low", "close", "volume"]
        assert len(out["4h"]) == 60

    def test_data_start_slice_applies_to_loaded_tf(self, tmp_path) -> None:
        save_parquet(make_ohlcv(60, freq="4h"), tmp_path / "BTCUSDT_4h.parquet")
        enhanced = make_ohlcv(60)
        out = load_mtf_data(
            tmp_path, "BTCUSDT", "1h", enhanced, {"4h"},
            data_start="2024-01-02 16:00:00+00:00", compute_indicators=False,
        )
        # 4h 序列自 01-01 00:00 起 60 根；01-02 16:00 是第 10 根 →
        # 保留恰 50 根（MTF 门槛边界，与 data_end 用例对称）
        assert len(out["4h"]) == 50
        assert out["4h"].index[0] == pd.Timestamp("2024-01-02 16:00:00", tz="UTC")

    def test_load_exception_swallowed(self, tmp_path) -> None:
        """单周期装载异常不外泄：记 warning 后跳过该周期。"""
        save_parquet(make_ohlcv(60), tmp_path / "BTCUSDT_4h.parquet")
        enhanced = make_ohlcv(60)
        with patch(
            "core.data.storage.load_parquet", side_effect=RuntimeError("boom")
        ):
            out = load_mtf_data(
                tmp_path, "BTCUSDT", "1h", enhanced, {"4h"}, compute_indicators=False
            )
        assert set(out.keys()) == {"1h"}

    def test_indicator_compute_branch(self, tmp_path) -> None:
        save_parquet(make_ohlcv(60), tmp_path / "BTCUSDT_4h.parquet")
        enhanced = make_ohlcv(60)
        with patch(
            "core.features.indicators.compute_all_indicators",
            side_effect=lambda df, **kw: df,
        ) as mock_compute:
            out = load_mtf_data(
                tmp_path, "BTCUSDT", "1h", enhanced, {"4h"}, compute_indicators=True
            )
        # 仅附加周期过指标层；enhanced_df（exec 周期）直通不再计算
        assert mock_compute.call_count == 1
        assert len(out["4h"]) == 60
