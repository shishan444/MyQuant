"""月矩阵 v2+freshness 透传契约测试（data-management-revamp 组 A2/B2）。

data-integrity 3 Scenario+data-freshness-guardian 2 Scenario+五形态月夹具
+容差边界+档集不硬编码+404/gray 分野+透传/503/属性链缺失。
第二轮补网（审查阶段）：透传 200 分支真空补齐+bars_expected 语义锚
+当月首尾容差/中间缺口分界+长周期缺口形态+months_back 参数契约+空档文件。
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


# ---------------------------------------------------------------------------
# 第二轮补网（审查阶段）。推导起点=specs data-integrity 3 Scenario
# +data-freshness-guardian 2 Scenario+design §5.1 字段/参数/错误语义；
# 实现代码仅用于确定调用形制，不用于决定断言预期。
# ---------------------------------------------------------------------------


def _req_with(state_attrs: dict):
    """构造带指定 app.state 属性的直调 request（freshness 挂点链注入用）。"""
    state = type("St", (), state_attrs)()
    return type("R", (), {"app": type("App", (), {"state": state})()})()


def _cur_month() -> tuple[str, pd.Timestamp]:
    """当月键（UTC）与当月 1 日 0 点——当月用例时间基准现算（不硬编码月份）。"""
    from datetime import datetime, timezone as tz
    n = datetime.now(tz.utc)
    return f"{n.year:04d}-{n.month:02d}", pd.Timestamp(year=n.year, month=n.month, day=1)


def _cell_of(body: dict, month: str, tf: str) -> dict:
    row = [m for m in body["short_period"]["matrix"] if m["month"] == month][0]
    return [c for c in row["cells"] if c["timeframe"] == tf][0]


class TestFreshnessPassthrough:
    """正常透传 Scenario（守护器运行中→零改写）。

    原网 test_passthrough_or_503 在直调形制下 request.app.state 恒无
    paper_scheduler——恒走 503 分支，200 透传分支从未执行（恒真断言）。
    """

    def test_running_guardian_zero_rewrite(self):
        """装配态：响应 kline/derivs 与 guardian.status() 逐键一致（零改写锚）。"""
        from api.routes.integrity_workbench import freshness_status
        payload = {
            "kline": {"last_run": "2026-11-01T08:00:00+00:00",
                      "keys": {"BTCUSDT_4h": "fresh", "BTCUSDT_1h": "stale"}},
            "derivs": {"last_run": None,
                       "symbols": {"BTCUSDT": {"funding": "fresh", "oi": "missing"}}},
        }
        guardian = type("G", (), {"status": lambda self: payload})()
        sched = type("S", (), {"registry": type("R", (), {"guardian": guardian})()})()
        req = _req_with({"paper_scheduler": sched})
        out = freshness_status(request=req)
        assert out.kline == payload["kline"]
        assert out.derivs == payload["derivs"]

    @pytest.mark.parametrize(
        "broken", ["registry 无 guardian", "guardian 无 status 方法"])
    def test_partial_chain_missing_503(self, broken):
        """挂点链中间环缺失（scheduler 在但链断）→503（原网只测 state 首环）。"""
        from api.routes.integrity_workbench import freshness_status
        from fastapi import HTTPException
        if broken == "registry 无 guardian":
            sched = type("S", (), {"registry": type("R", (), {})()})()
        else:
            sched = type("S", (), {
                "registry": type("R", (), {"guardian": type("G", (), {})()})()})()
        req = _req_with({"paper_scheduler": sched})
        with pytest.raises(HTTPException) as ei:
            freshness_status(request=req)
        assert ei.value.status_code == 503


class TestBarsExpectedSemantics:
    """bars_expected 语义锚（design §5.1：bars_actual=实有根数，
    bars_expected=月内应有根数——缺口月差值=缺失根数）。"""

    def test_full_month_expected_equals_actual(self, five_forms):
        c, _ = five_forms
        r = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "BTCUSDT", "months_back": 3})
        cell = _cell_of(r.json(), "2026-09", "4h")
        assert cell["bars_expected"] == cell["bars_actual"] == 180

    def test_gap_month_shortfall_equals_missing_bars(self, five_forms):
        """中间抽 10 根连续 bar：expected-actual=10、连续一段=1 处缺口。"""
        c, _ = five_forms
        r = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "GAPSY", "months_back": 3})
        cell = _cell_of(r.json(), "2026-09", "4h")
        assert cell["bars_expected"] - cell["bars_actual"] == 10
        assert cell["gap_count"] == 1

    def test_expected_never_below_actual_invariant(self, five_forms):
        """不变量：任一格 bars_expected>=bars_actual（expected=应有下界）。"""
        c, _ = five_forms
        for sym in ("BTCUSDT", "GAPSY", "NULLSY", "BADSY", "EMPTY9"):
            r = c.get("/api/data/integrity/monthly-matrix",
                      params={"symbol": sym, "months_back": 3})
            for row in r.json()["short_period"]["matrix"]:
                for cell in row["cells"]:
                    assert cell["bars_expected"] >= cell["bars_actual"], (
                        sym, row["month"], cell["timeframe"])


class TestCurrentMonthTolerance:
    """当月（部分月）首尾容差 vs 中间缺口分界（design §5.1：连续性
    =相邻 bar 间隔==TF，月首尾容差一档——月首缺失/月末未满不计缺口）。"""

    def test_month_head_missing_tolerated_green(self, tmp_path):
        """当月从 5 日起连续（1-4 日缺失=月首容差）→green 非缺口。"""
        mk, m1 = _cur_month()
        idx = pd.date_range(m1 + pd.Timedelta(days=4), periods=30, freq="4h")
        c = _client(tmp_path, {"HEADMISS_4h.parquet": _ohlc(idx)})
        cell = _cell_of(c.get("/api/data/integrity/monthly-matrix",
                              params={"symbol": "HEADMISS", "months_back": 1}).json(),
                        mk, "4h")
        assert cell["status"] == "green"
        assert cell["gap_count"] == 0 and cell["first_gap"] is None

    def test_partial_month_tail_tolerated_green(self, tmp_path):
        """当月 1 日起连续 8 根（月末未满=月末容差）→green 且 expected==actual。"""
        mk, m1 = _cur_month()
        idx = pd.date_range(m1, periods=8, freq="4h")
        c = _client(tmp_path, {"PARTIAL_4h.parquet": _ohlc(idx)})
        cell = _cell_of(c.get("/api/data/integrity/monthly-matrix",
                              params={"symbol": "PARTIAL", "months_back": 1}).json(),
                        mk, "4h")
        assert cell["status"] == "green"
        assert cell["bars_actual"] == 8
        assert cell["bars_expected"] == 8

    def test_current_month_inner_gap_yellow(self, tmp_path):
        """当月中间抽 2 根（首尾齐全）→yellow 必报（容差不覆盖中间）。"""
        mk, m1 = _cur_month()
        idx = pd.date_range(m1, periods=40, freq="4h").delete(range(20, 22))
        c = _client(tmp_path, {"INNER_4h.parquet": _ohlc(idx)})
        cell = _cell_of(c.get("/api/data/integrity/monthly-matrix",
                              params={"symbol": "INNER", "months_back": 1}).json(),
                        mk, "4h")
        assert cell["status"] == "yellow"
        assert cell["gap_count"] >= 1
        assert cell["bars_expected"] - cell["bars_actual"] == 2


class TestLongPeriodGapForm:
    """长周期整体检查·缺口形态（Scenario 3 面——原网只测 green 形态）。"""

    def test_long_period_gap_yellow_full_span(self, tmp_path):
        """1d 全集抽 3 根：yellow+gaps 明细；months_back=1 不裁剪长周期（不做月分桶）。"""
        orig = pd.date_range("2026-05-01", periods=120, freq="1D")
        idx = orig.delete(range(50, 53))
        c = _client(tmp_path, {"LGAP_1d.parquet": _ohlc(idx)})
        b = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "LGAP", "months_back": 1}).json()
        # 当月（2026-11）短周期月窗不含缺口所在的 2026-06——长周期仍整体报
        row = [r for r in b["long_period"] if r["timeframe"] == "1d"][0]
        assert row["status"] == "yellow"
        assert row["gap_count"] == 1
        # 缺口段=缺口前最后一根（原序列 49 位）~ 缺口后第一根（原序列 53 位）
        assert row["gaps"][0] == {"start": str(orig[49]), "end": str(orig[53])}
        assert row["coverage"]["bars"] == 117
        assert row["coverage"]["first_bar"] == str(idx[0])
        assert row["coverage"]["last_bar"] == str(idx[-1])


class TestEmptyFileTimeframe:
    """空 parquet 档（文件存在零行）→该档全月 gray（design §5.1 错误语义：
    部分周期档缺失该档全 gray——数据缺失是事实不是错误）。"""

    def test_empty_file_all_months_gray(self, tmp_path):
        c = _client(tmp_path, {"EMPTYF_4h.parquet": pd.DataFrame()})
        b = c.get("/api/data/integrity/monthly-matrix",
                  params={"symbol": "EMPTYF", "months_back": 2}).json()
        assert "4h" in b["short_period"]["timeframes"]
        assert len(b["short_period"]["matrix"]) == 2
        for row in b["short_period"]["matrix"]:
            cell = [x for x in row["cells"] if x["timeframe"] == "4h"][0]
            assert cell["status"] == "gray"
            assert cell["bars_actual"] == 0 and cell["gap_count"] == 0


class TestMonthsBackContract:
    """months_back 有界查询（design §5.1：可选缺省 6，上限 12）——
    经真实路由校验层（直调函数形制绕过 Query 校验，此处必须走装配路由）。

    TestClient 不进 with：lifespan 不触发（不起 scheduler/guardian 线程，
    规避实测竞写坑），手动补 lifespan 本会注入的 state 键。
    """

    @pytest.fixture()
    def route_client(self, tmp_path):
        idx = pd.date_range("2026-10-01", periods=10, freq="4h")
        _ohlc(idx).to_parquet(tmp_path / "MB_4h.parquet")
        from api.app import create_app
        app = create_app(db_path=tmp_path / "q.db", data_dir=tmp_path)
        app.state.db_path = tmp_path / "q.db"
        app.state.data_dir = tmp_path
        return TestClient(app)

    @pytest.mark.parametrize(
        "mb", [pytest.param(0, id="低于下限 0"),
               pytest.param(13, id="超上限 13"),
               pytest.param("abc", id="非整数")])
    def test_out_of_bounds_422(self, route_client, mb):
        r = route_client.get("/api/data/integrity/monthly-matrix",
                             params={"symbol": "MB", "months_back": mb})
        assert r.status_code == 422

    def test_default_is_6(self, route_client):
        r = route_client.get("/api/data/integrity/monthly-matrix",
                             params={"symbol": "MB"})
        assert r.status_code == 200
        b = r.json()
        assert b["months_back"] == 6
        assert len(b["short_period"]["matrix"]) == 6

    def test_lower_bound_1_single_month(self, route_client):
        r = route_client.get("/api/data/integrity/monthly-matrix",
                             params={"symbol": "MB", "months_back": 1})
        assert r.status_code == 200
        mk, _ = _cur_month()
        assert [m["month"] for m in r.json()["short_period"]["matrix"]] == [mk]
