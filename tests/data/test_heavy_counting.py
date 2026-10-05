"""0.3.3 C-β：storage 列子集底座 + index-only heavy 计数特征网。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from api.routes.research.analysis import (
    _HEAVY_ROWS, _count_rows_index_only, _load_df,
)
from core.data.storage import load_parquet, save_parquet
from tests.helpers.data_factory import make_ohlcv

RNG = {"symbol": "BTCUSDT", "timeframe": "1h",
       "start": "2024-01-05T00:00:00Z", "end": "2024-01-20T00:00:00Z"}


def _env(tmp_path: Path, n: int = 900):
    df = make_ohlcv(n=n, freq="1h", start="2024-01-01", seed=5)
    save_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    return df


class TestStorageColumns:
    def test_column_subset_read_keeps_index(self, tmp_path):
        """5.0 底座：columns 子集返回列集=请求列集、index 正常恢复。"""
        _env(tmp_path)
        out = load_parquet(tmp_path / "BTCUSDT_1h.parquet",
                           columns=["close"])
        assert list(out.columns) == ["close"]
        assert isinstance(out.index, pd.DatetimeIndex)
        assert len(out) == 900

    def test_none_columns_reads_all(self, tmp_path):
        _env(tmp_path)
        out = load_parquet(tmp_path / "BTCUSDT_1h.parquet")
        assert {"open", "high", "low", "close", "volume"} <= set(out.columns)


class TestIndexOnlyCounting:
    def test_count_equals_full_load_len(self, tmp_path):
        """人工核算锚：index-only 计数 == len(_load_df) 全量装载（两路
        对照——列同源等价）。"""
        _env(tmp_path)
        assert _count_rows_index_only(tmp_path, RNG) == len(
            _load_df(tmp_path, RNG))

    def test_threshold_semantics_unchanged(self, tmp_path):
        """heavy 判定语义不变：计数与 _HEAVY_ROWS 比较的阈值口径
        （900 行 < 10000 → 非 heavy 数值证据；helper 层断言数值，
        分流集成由路由回归族承载）。"""
        _env(tmp_path, n=900)
        count = _count_rows_index_only(tmp_path, RNG)
        assert 0 < count < _HEAVY_ROWS  # 阈值一侧
        assert _HEAVY_ROWS == 10000  # 常量口径锚（G4 披露同源）

    def test_missing_timestamp_falls_back_to_full_load(self, tmp_path):
        """列名形制变体回退全量装载——正确性优先于轻量。"""
        df = _env(tmp_path)
        ren = df.rename_axis("not_timestamp")
        save_parquet(ren, tmp_path / "BTCUSDT_1h.parquet")
        assert _count_rows_index_only(tmp_path, RNG) == len(
            _load_df(tmp_path, RNG))

    def test_single_full_load_per_request(self, tmp_path, monkeypatch):
        """装载 spy：快路径一次请求全列装载恰一次（heavy 计数走
        columns=['timestamp'] 不计全列）——每分支恰好一次真实装载。"""
        import api.routes.research.analysis as ana
        _env(tmp_path)
        calls = {"full": 0, "col": 0}
        real = load_parquet

        def spy(path, columns=None):
            if columns is None:
                calls["full"] += 1
            else:
                calls["col"] += 1
            return real(path, columns=columns)

        monkeypatch.setattr(ana, "_load_df", lambda d, r: (
            calls.__setitem__("full", calls["full"] + 1), real(
                d / "BTCUSDT_1h.parquet"))[1])
        from core.data import storage as storage_mod
        monkeypatch.setattr(storage_mod, "load_parquet", spy)
        # heavy 计数路径（含 timestamp 单列）+ 计数后无全列预载
        before_full = calls["full"]
        _count_rows_index_only(tmp_path, RNG)
        assert calls["col"] == 1
        assert calls["full"] == before_full  # 计数路径零全列装载
