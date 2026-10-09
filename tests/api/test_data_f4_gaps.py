"""Data 路由缺口特征网（修复期安全网，现状记录语义，F4 在案缺口）。

补 api/routes/data.py 合并视角未覆盖分支：
- preview / ohlcv 的 parquet_path 失效回退（data_dir fallback）与 404
- available-sources：data_dir 不存在、空数据集、读异常的时间回退
- ohlcv by symbol / chart-indicators 的空数据集 404
- chart-indicators B1.5：指标计算失败时该层缺失（可区分「未开启」与「计算失败」）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_ohlcv


def _write_parquet(data_dir: Path, name: str, df=None) -> Path:
    path = data_dir / f"{name}.parquet"
    (df if df is not None else make_ohlcv(30, "4h")).to_parquet(path)
    return path


def _seed_dataset(db_path: Path, dataset_id: str, parquet_path: str) -> None:
    from core.persistence.repository.dataset_meta import save_dataset_meta
    save_dataset_meta(
        db_path, dataset_id=dataset_id, symbol="BTCUSDT", interval="4h",
        parquet_path=parquet_path, row_count=30,
        time_start="2024-01-01", time_end="2024-01-06",
        source="csv_import",
    )


class TestPreviewFallbacks:
    def test_preview_parquet_missing_on_disk_404(
        self, api_client: TestClient,
    ) -> None:
        """meta 指向的 parquet 不存在且 data_dir 无同名文件 → 404。"""
        db_path = api_client.app.state.db_path
        _seed_dataset(db_path, "ds-gone", "/nonexistent/path.parquet")
        response = api_client.get("/api/data/datasets/ds-gone/preview")
        assert response.status_code == 404
        assert response.json()["detail"] == "Parquet file not found on disk"

    def test_preview_falls_back_to_data_dir(
        self, api_client: TestClient,
    ) -> None:
        """meta 路径失效但 data_dir/{dataset_id}.parquet 存在时回退成功。"""
        db_path = api_client.app.state.db_path
        data_dir = api_client.app.state.data_dir
        _write_parquet(data_dir, "ds-fb")
        _seed_dataset(db_path, "ds-fb", "/nonexistent/path.parquet")
        response = api_client.get(
            "/api/data/datasets/ds-fb/preview", params={"limit": 3},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dataset_id"] == "ds-fb"
        assert data["total_rows"] == 30
        assert len(data["rows"]) == 3
        assert "timestamp" in data["rows"][0]

    def test_preview_unknown_dataset_404(self, api_client: TestClient) -> None:
        response = api_client.get("/api/data/datasets/ghost/preview")
        assert response.status_code == 404


class TestOhlcvFallbacks:
    def test_ohlcv_parquet_missing_on_disk_404(
        self, api_client: TestClient,
    ) -> None:
        """dataset 维度 ohlcv：parquet 失效且无回退 → 404。"""
        db_path = api_client.app.state.db_path
        _seed_dataset(db_path, "ds-oh-gone", "/nonexistent/p.parquet")
        response = api_client.get("/api/data/datasets/ds-oh-gone/ohlcv")
        assert response.status_code == 404
        assert response.json()["detail"] == "Parquet file not found on disk"

    def test_ohlcv_falls_back_to_data_dir(self, api_client: TestClient) -> None:
        """dataset 维度 ohlcv：data_dir 回退成功并返回 OHLCV 行。"""
        db_path = api_client.app.state.db_path
        data_dir = api_client.app.state.data_dir
        _write_parquet(data_dir, "ds-oh-fb")
        _seed_dataset(db_path, "ds-oh-fb", "/nonexistent/p.parquet")
        response = api_client.get(
            "/api/data/datasets/ds-oh-fb/ohlcv", params={"limit": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 5
        first = data["data"][0]
        for key in ("open", "high", "low", "close", "volume"):
            assert key in first

    def test_ohlcv_by_symbol_empty_dataset_404(
        self, api_client: TestClient,
    ) -> None:
        """symbol 维度 ohlcv：parquet 存在但零行 → 404 Empty dataset。"""
        import pandas as pd
        data_dir = api_client.app.state.data_dir
        empty = pd.DataFrame(
            {"open": [], "high": [], "low": [], "close": [], "volume": []},
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        _write_parquet(data_dir, "EMPTYUSDT_4h", empty)
        response = api_client.get("/api/data/ohlcv/EMPTYUSDT/4h")
        assert response.status_code == 404
        assert response.json()["detail"] == "Empty dataset"


class TestAvailableSources:
    def test_missing_data_dir_returns_empty(self, tmp_path: Path) -> None:
        """data_dir 不存在时回空列表（直接函数调用）。"""
        from api.routes.data import get_available_sources
        result = get_available_sources(tmp_path / "never-created")
        assert result.sources == []

    def test_empty_parquet_skipped(self, tmp_path: Path) -> None:
        """零行 parquet 不进 sources。"""
        import pandas as pd
        from api.routes.data import get_available_sources
        data_dir = tmp_path / "d"
        data_dir.mkdir()
        empty = pd.DataFrame(
            {"open": [], "high": [], "low": [], "close": [], "volume": []},
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        empty.to_parquet(data_dir / "EMPTYUSDT_4h.parquet")
        make_ohlcv(10, "4h").to_parquet(data_dir / "BTCUSDT_4h.parquet")
        result = get_available_sources(data_dir)
        syms = [s.symbol for s in result.sources]
        assert "EMPTYUSDT" not in syms
        assert "BTCUSDT" in syms

    def test_corrupt_parquet_time_fields_none(self, tmp_path: Path) -> None:
        """parquet 读取异常时时间字段回 None 但条目保留。"""
        from api.routes.data import get_available_sources
        data_dir = tmp_path / "d2"
        data_dir.mkdir()
        (data_dir / "BTCUSDT_4h.parquet").write_bytes(b"not a parquet")
        result = get_available_sources(data_dir)
        assert len(result.sources) == 1
        src = result.sources[0]
        assert src.symbol == "BTCUSDT"
        assert src.time_start is None
        assert src.time_end is None


class TestChartIndicatorsGaps:
    def test_chart_indicators_empty_dataset_404(
        self, api_client: TestClient,
    ) -> None:
        """symbol 维度图表指标：空数据集 → 404 Empty dataset。"""
        import pandas as pd
        data_dir = api_client.app.state.data_dir
        empty = pd.DataFrame(
            {"open": [], "high": [], "low": [], "close": [], "volume": []},
            index=pd.DatetimeIndex([], name="timestamp"),
        )
        _write_parquet(data_dir, "EMPTYUSDT_4h", empty)
        response = api_client.get("/api/data/chart-indicators/EMPTYUSDT/4h")
        assert response.status_code == 404
        assert response.json()["detail"] == "Empty dataset"

    def test_indicator_compute_failure_layer_missing(
        self, api_client: TestClient,
    ) -> None:
        """B1.5：指标计算抛异常时对应层缺失、其余层照常（失败可观测不 500）。

        全指标开启 + 全部计算失败：ema 空字典、boll/rsi/rvol/vwma/macd/kdj
        均为 None，端点仍 200。
        """
        from unittest.mock import patch

        def _boom(*a, **kw):
            raise RuntimeError("indicator boom")

        with patch("core.features.indicators._compute_indicator", side_effect=_boom):
            response = api_client.get(
                "/api/data/chart-indicators/BTCUSDT/4h",
                params={
                    "ema_periods": "20",
                    "boll_enabled": True, "rsi_enabled": True,
                    "rvol_enabled": True, "vwma_enabled": True,
                    "macd_enabled": True, "kdj_enabled": True,
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["ema"] == {}
        assert data["boll"] is None
        assert data["rsi"] is None
        assert data["rvol"] is None
        assert data["vwma"] is None
        assert data["macd"] is None
        assert data["kdj"] is None

    def test_indicator_partial_failure_keeps_others(
        self, api_client: TestClient,
    ) -> None:
        """部分指标失败只缺失该层：RSI 失败而 EMA 正常时 ema 有值 rsi 为 None。"""
        from unittest.mock import patch
        from core.features.indicators import _compute_indicator as real_impl

        # conftest 默认 1 行数据不够 EMA(20) 计算——写入足量数据
        _write_parquet(api_client.app.state.data_dir, "BTCUSDT_4h",
                       make_ohlcv(60, "4h"))

        def _fail_rsi(df, name, params):
            if name == "RSI":
                raise RuntimeError("rsi boom")
            return real_impl(df, name, params)

        with patch(
            "core.features.indicators._compute_indicator", side_effect=_fail_rsi,
        ):
            response = api_client.get(
                "/api/data/chart-indicators/BTCUSDT/4h",
                params={"ema_periods": "20", "rsi_enabled": True},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["ema"]["20"], "EMA 层应正常计算"
        assert data["rsi"] is None
