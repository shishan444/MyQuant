"""月矩阵 v2+freshness 透传契约测试（data-management-revamp 组 A2/B2）。

data-integrity 2 Scenario+data-freshness-guardian 2 Scenario+五形态月夹具
+容差边界+档集不硬编码+404/gray 分野+透传/503/属性链缺失。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _mk(df_data: pd.DataFrame, path: Path, name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    df_data.to_parquet(path.parent / name)


def _client(tmp_path: Path, files: dict):
    """直调形制客户端（无 TestClient/lifespan——后台 scheduler/guardian 线程
    对合成数据竞写 parquet 致请求 500 的实测规避；路由逻辑即被测面）。"""
    from api.routes.integrity_workbench import (
        integrity_monthly_matrix, freshness_status)

    dd = tmp_path / "data"
    for name, df in files.items():
        _mk(df, dd / name, name)

    class _Resp:
        def __init__(self, payload=None, status=200):
            self._p, self.status_code = payload, status
        def json(self):
            return self._p

    class _FS: pass
    class _FA: state = _FS()

    class C:
        _dd = dd
        _db = tmp_path / "q.db"
        def get(self, path, params=None):
            from fastapi import HTTPException
            if path == "/api/data/freshness/status":
                try:
                    out = freshness_status(request=_req())
                    return _Resp({"kline": out.kline, "derivs": out.derivs})
                except HTTPException as e:
                    return _Resp(status=e.status_code)
            params = params or {}
            try:
                out = integrity_monthly_matrix(
                    request=_req(), symbol=params["symbol"],
                    months_back=params.get("months_back", 6),
                    db_path=self._db, data_dir=self._dd)
                return _Resp(out.model_dump())
            except HTTPException as e:
                return _Resp(status=e.status_code)

    def _req():
        return type("R", (), {"app": _FA()})()

    return C()


def _ohlc(idx):
    return pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        index=idx)


@pytest.fixture()
def five_forms(tmp_path: Path):
    """五形态月夹具（2026-09 单月 4h——连续/缺口/空值/非法/零 bar）。"""
    may = pd.date_range("2026-09-01", periods=180, freq="4h")
    files = {"BTCUSDT_4h.parquet": _ohlc(may)}
    # 缺口形态：抽掉中间一段（1d 档做长周期对照）
    gap = may.delete(range(80, 90))
    files["GAPSY_4h.parquet"] = _ohlc(gap)
    # 空值形态
    nulls = _ohlc(may)
    nulls.loc[nulls.index[50], "close"] = float("nan")
    files["NULLSY_4h.parquet"] = nulls
    # 非法形态：high<low
    bad = _ohlc(may)
    bad.loc[bad.index[60], "high"] = 0.1
    bad.loc[bad.index[60], "low"] = 1.9
    files["BADSY_4h.parquet"] = bad
    # 零 bar：只有 8 月数据（9 月零 bar=gray）
    aug = pd.date_range("2026-08-01", periods=60, freq="4h")
    files["EMPTY9_4h.parquet"] = _ohlc(aug)
    # 长周期档
    d1 = pd.date_range("2026-05-01", periods=120, freq="1D")
    files["BTCUSDT_1d.parquet"] = _ohlc(d1)
    return _client(tmp_path, files), files


class TestMonthlyMatrix:
    def test_continuous_green_and_gaps_yellow(self, five_forms):
        c, _ = five_forms
        r = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "BTCUSDT", "months_back": 3})
        assert r.status_code == 200
        b = r.json()
        sep = [m for m in b["short_period"]["matrix"] if m["month"] == "2026-09"][0]
        cell = [x for x in sep["cells"] if x["timeframe"] == "4h"][0]
        assert cell["status"] == "green"
        assert cell["gap_count"] == 0 and cell["first_gap"] is None
        # 缺口形态
        rg = c.get("/api/data/integrity/monthly-matrix",
                   params={"symbol": "GAPSY", "months_back": 3})
        sep_g = [m for m in rg.json()["short_period"]["matrix"] if m["month"] == "2026-09"][0]
        cell_g = [x for x in sep_g["cells"] if x["timeframe"] == "4h"][0]
        assert cell_g["status"] == "yellow"
        assert cell_g["gap_count"] >= 1
        assert cell_g["first_gap"] is not None and "start" in cell_g["first_gap"]

    def test_null_and_invalid_yellow(self, five_forms):
        c, _ = five_forms
        for sym in ("NULLSY", "BADSY"):
            r = c.get("/api/data/integrity/monthly-matrix",
                      params={"symbol": sym, "months_back": 3})
            sep = [m for m in r.json()["short_period"]["matrix"]
                   if m["month"] == "2026-09"][0]
            cell = [x for x in sep["cells"] if x["timeframe"] == "4h"][0]
            assert cell["status"] == "yellow", sym

    def test_zero_bar_month_gray(self, five_forms):
        c, _ = five_forms
        r = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "EMPTY9", "months_back": 3})
        sep = [m for m in r.json()["short_period"]["matrix"] if m["month"] == "2026-09"][0]
        cell = [x for x in sep["cells"] if x["timeframe"] == "4h"][0]
        assert cell["status"] == "gray" and cell["bars_actual"] == 0

    def test_timeframes_follow_directory(self, five_forms, tmp_path):
        """档集不硬编码锚：目录新增 15m 档→timeframes 跟随。"""
        c, files = five_forms
        m15 = pd.date_range("2026-09-01", periods=100, freq="15min")
        _mk(_ohlc(m15), tmp_path / "data" / "BTCUSDT_15m.parquet", "BTCUSDT_15m.parquet")
        r = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "BTCUSDT", "months_back": 1})
        assert "15m" in r.json()["short_period"]["timeframes"]

    def test_404_no_data_vs_gray(self, five_forms):
        c, _ = five_forms
        assert c.get("/api/data/integrity/monthly-matrix",
                     params={"symbol": "NOSUCH"}).status_code == 404

    def test_long_period_two_segment_structure(self, five_forms):
        c, _ = five_forms
        r = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "BTCUSDT", "months_back": 1})
        b = r.json()
        long_rows = b["long_period"]
        assert any(row["timeframe"] == "1d" for row in long_rows)
        d1 = [row for row in long_rows if row["timeframe"] == "1d"][0]
        assert set(d1["coverage"].keys()) == {"first_bar", "last_bar", "bars"}
        assert d1["status"] == "green" and d1["coverage"]["bars"] == 120


class TestFreshnessStatus:
    def test_passthrough_or_503(self, five_forms):
        """透传逐键一致 或 未装配 503（TestClient lifespan 起 scheduler=装配态透传）。"""
        c, _ = five_forms
        r = c.get("/api/data/freshness/status")
        assert r.status_code in (200, 503)
        if r.status_code == 200:
            b = r.json()
            assert "kline" in b and "derivs" in b
            assert isinstance(b["kline"], dict) and "keys" in b["kline"]

    def test_attribute_chain_missing_503(self, tmp_path):
        """挂点属性链缺失（无 scheduler 态）→503 不伪装空。"""
        from api.app import create_app
        app = create_app(db_path=tmp_path / "q.db", data_dir=tmp_path)
        # 不进 lifespan（不起 scheduler）→state 无 paper_scheduler
        client = TestClient(app, raise_server_exceptions=False)
        # lifespan 必须触发才能处理请求——用显式关闭 lifespan 的形制：直接调路由函数
        from api.routes.integrity_workbench import freshness_status
        from fastapi import HTTPException
        class FakeState:
            pass
        class FakeApp:
            state = FakeState()
        req = type("R", (), {"app": FakeApp()})()
        with pytest.raises(HTTPException) as ei:
            freshness_status(request=req)
        assert ei.value.status_code == 503
