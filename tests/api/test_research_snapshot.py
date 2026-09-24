"""市场快照路由测试（add-market-snapshot 2.1）。

路由层职责窄：query 直收透传 + 错误结构化透传（422）+ 响应契约
（顶层键、四周期全量、体积 <=2KB）+ 留痕落 research_log。读数正确性
与防前视由 tests/analysis/test_market_snapshot.py 覆盖（本套件构造器
同构，但只求可读数——判读区分归 3.1 验收）。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import research as rr

SYMBOL = "BTCUSDT"


def make_ohlcv(n: int, freq: str, seed: int) -> pd.DataFrame:
    """确定性趋势形 OHLCV（定向漂移+小噪声）。"""
    idx = pd.date_range("2024-01-01", periods=n, freq=freq,
                        tz="UTC", name="timestamp")
    rng = np.random.default_rng(seed)
    closes = 40000 * np.cumprod(1 + 0.004 + rng.normal(0, 0.002, n))
    wiggle = np.abs(rng.normal(0, 0.002, n))
    return pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.001, n)),
        "high": closes * (1 + wiggle),
        "low": closes * (1 - wiggle),
        "close": closes,
        "volume": rng.integers(800, 1200, n).astype(float),
    }, index=idx)


@pytest.fixture()
def snapshot_env(tmp_path: Path):
    """四档足额数据（warmup+90 日窗）+ 已升级库。"""
    data_dir = tmp_path / "snapshot_data"  # 避开 tmp_data_dir fixture 的 data/
    data_dir.mkdir()
    for tf, n, freq in (("3d", 400, "3D"), ("1d", 400, "1D"),
                        ("4h", 2160, "4h"), ("1h", 2200, "1h")):
        make_ohlcv(n, freq, seed=n).to_parquet(
            data_dir / f"{SYMBOL}_{tf}.parquet")
    db = tmp_path / "snapshot.db"
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db)
    # 历史时点：1d/4h 公共网格点（1d 第 360 根、4h 第 2155 根），
    # 前缀远超 warmup 300 bar
    return {"data_dir": data_dir, "db": db,
            "as_of_grid": "2024-12-25T00:00:00Z"}


def _bind(api_client, env):
    api_client.app.state.db_path = env["db"]
    api_client.app.state.data_dir = env["data_dir"]


class TestMarketSnapshotRoute:
    def test_four_timeframes_ok_and_size(self, api_client, snapshot_env):
        """缺省四周期全量：顶层契约键齐备、周期卡五维齐备、体积 <=2KB。"""
        _bind(api_client, snapshot_env)
        resp = api_client.get("/api/research/market-snapshot",
                              params={"symbol": SYMBOL})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"symbol", "as_of", "window_days",
                             "notes", "timeframes"}
        assert list(body["timeframes"]) == ["3d", "1d", "4h", "1h"]
        for tf, card in body["timeframes"].items():
            for dim in ("trend", "momentum", "volatility", "volume",
                        "derivatives", "window_bars", "as_of"):
                assert dim in card, f"{tf} 缺 {dim}"
            assert card["trend"]["adx"] is not None
        assert len(resp.content) <= 2048, \
            f"响应体积超限: {len(resp.content)} bytes"

    def test_timeframes_subset_and_as_of(self, api_client, snapshot_env):
        """逗号分隔子集（去重）+ as_of 历史时点透传（周期卡落实际 bar）。"""
        _bind(api_client, snapshot_env)
        resp = api_client.get("/api/research/market-snapshot", params={
            "symbol": SYMBOL, "timeframes": "1d, 1d,4h",
            "as_of": snapshot_env["as_of_grid"]})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert list(body["timeframes"]) == ["1d", "4h"]
        assert body["as_of"] == "2024-12-25T00:00:00+00:00"
        # 两档均在 4h 网格点上 → 周期卡 as_of 落同一根 bar
        assert body["timeframes"]["1d"]["as_of"] == "2024-12-25T00:00:00+00:00"
        assert body["timeframes"]["4h"]["as_of"] == "2024-12-25T00:00:00+00:00"

    def test_symbol_not_found_422(self, api_client, snapshot_env):
        """symbol 无 parquet → 422 RESEARCH_SYMBOL_NOT_FOUND（族内形制非 404）。"""
        _bind(api_client, snapshot_env)
        resp = api_client.get("/api/research/market-snapshot",
                              params={"symbol": "NOSUCHUSDT"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_timeframes_illegal_rejected(self, api_client, snapshot_env):
        _bind(api_client, snapshot_env)
        resp = api_client.get("/api/research/market-snapshot", params={
            "symbol": SYMBOL, "timeframes": "1d,2d"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"
        assert "2d" in resp.json()["detail"]["message"]

    def test_timeframes_empty_rejected(self, api_client, snapshot_env):
        """空串（或纯逗号）解析为空集 → 422 结构化拒绝。"""
        _bind(api_client, snapshot_env)
        for raw in ("", ",,"):
            resp = api_client.get("/api/research/market-snapshot", params={
                "symbol": SYMBOL, "timeframes": raw})
            assert resp.status_code == 422, raw
            assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    def test_research_log_written(self, api_client, snapshot_env):
        """每次调用落 research_log adhoc 条目（不进假设判定并集）。"""
        _bind(api_client, snapshot_env)
        before = len(rr.list_research_log(snapshot_env["db"]))
        resp = api_client.get("/api/research/market-snapshot", params={
            "symbol": SYMBOL, "timeframes": "1d"})
        assert resp.status_code == 200, resp.text
        entries = [e for e in rr.list_research_log(snapshot_env["db"])
                   if e["entry_type"] == "market_snapshot"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["hypothesis_id"] is None
        assert entry["ref_id"] == SYMBOL
        assert len(rr.list_research_log(snapshot_env["db"])) == before + 1
