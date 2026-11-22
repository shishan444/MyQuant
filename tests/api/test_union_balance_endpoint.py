"""union-balance 薄壳端点正路覆盖（fix-replay-gap-alignment 2.5 裁决③
主案 a——GET /api/research/union-balance 三参单假设精确查询）。

薄壳恢复的端点级验收：六键响应形制（hypothesis_id/symbol/timeframe/
judged/remaining/remaining_bars——与前端 UnionBalanceResponse 逐键
对齐）+404 前置判定+无判定记录时余量=全数据域（账本语义面）。
兼作 query_union_balance 的后端覆盖主锚（原覆盖随 hypothesis-union-
remains 端点测试删除而失锚——本文件承接）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from api.app import create_app
from core.analysis.executor import ensure_research_tables
from core.data.storage import save_parquet
from core.persistence.db_ext import init_db_ext
from core.persistence.orm.engine import engine_for
from tests.helpers.data_factory import make_ohlcv


def _setup(tmp_path, *, with_hypothesis=True):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=300, freq="4h", seed=7, base_price=100.0)
    save_parquet(df, data_dir / "BTCUSDT_4h.parquet")
    db = tmp_path / "ub.db"
    init_db_ext(db)
    if with_hypothesis:
        ensure_research_tables(db)
        from sqlalchemy import text

        with engine_for(db).begin() as conn:
            conn.execute(text(
                "INSERT INTO research_hypotheses (hypothesis_id,"
                " conditions, conditions_hash, statement, data_ranges,"
                " ranges_hash, status, created_at, updated_at) VALUES"
                " ('hyp_ub', '[]', 'ch_ub', 's', '[]', 'rh_ub',"
                " 'active', '2026-01-01T00:00:00Z',"
                " '2026-01-01T00:00:00Z')"))
    app = create_app(db_path=db, data_dir=data_dir)
    app.state.db_path = db
    app.state.data_dir = data_dir
    return TestClient(app, raise_server_exceptions=False), df


class TestUnionBalanceEndpoint:
    def test_happy_path_six_keys_full_remaining(self, tmp_path):
        """六键响应+无判定记录时余量=全数据域（300 bar 半开域）。"""
        client, df = _setup(tmp_path)
        resp = client.get("/api/research/union-balance", params={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "hypothesis_id": "hyp_ub"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {
            "hypothesis_id", "symbol", "timeframe",
            "judged", "remaining", "remaining_bars"}, (
            f"六键形制（与前端逐键对齐——实测键集 {set(body)}）")
        assert body["hypothesis_id"] == "hyp_ub"
        assert body["symbol"] == "BTCUSDT"
        assert body["timeframe"] == "4h"
        assert body["judged"] == []
        # 无判定记录：remaining=单段全数据域（半开 [首 bar, 尾 bar+4h)）
        assert len(body["remaining"]) == 1
        start, end = body["remaining"][0]["start"], body["remaining"][0]["end"]
        assert start == df.index[0].isoformat()
        assert end == (df.index[-1] + __import__("pandas").Timedelta(
            hours=4)).isoformat()
        assert body["remaining_bars"] == 300, (
            f"余量 bar 数=数据域全量（实测 {body['remaining_bars']}）")

    def test_hypothesis_not_found_404(self, tmp_path):
        client, _ = _setup(tmp_path)
        resp = client.get("/api/research/union-balance", params={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "hypothesis_id": "nope"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"

    def test_missing_param_422(self, tmp_path):
        """三参必填（缺 hypothesis_id → 422——三参单假设精确查询契约）。"""
        client, _ = _setup(tmp_path)
        resp = client.get("/api/research/union-balance", params={
            "symbol": "BTCUSDT", "timeframe": "4h"})
        assert resp.status_code == 422

    def test_unknown_timeframe_422(self, tmp_path):
        client, _ = _setup(tmp_path)
        resp = client.get("/api/research/union-balance", params={
            "symbol": "BTCUSDT", "timeframe": "13h",
            "hypothesis_id": "hyp_ub"})
        assert resp.status_code == 422

    def test_no_data_symbol_422(self, tmp_path):
        """数据不可装载拒绝面（load_data_bounds 前置——RESEARCH_SYMBOL_
        NOT_FOUND 缺省 422：假设不存在才 404，品种缺数据属请求域
        不可满足）。"""
        client, _ = _setup(tmp_path)
        resp = client.get("/api/research/union-balance", params={
            "symbol": "ETHUSDT", "timeframe": "4h",
            "hypothesis_id": "hyp_ub"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_judged_middle_slice_remaining_two_segments(self, tmp_path):
        """判定区间中段：remaining=前后两段（merge/差集分支）+
        remaining_bars=两段之和（保守折算语义）。"""
        import pandas as pd

        client, df = _setup(tmp_path, with_hypothesis=False)
        # 假设自带判定区间=数据中段（首+100h 至 首+200h——4h 轴 25 段）
        lo = df.index[0] + pd.Timedelta(hours=100)
        hi = df.index[0] + pd.Timedelta(hours=200)
        ensure_research_tables(client.app.state.db_path)
        from sqlalchemy import text

        with engine_for(client.app.state.db_path).begin() as conn:
            conn.execute(text(
                "INSERT INTO research_hypotheses (hypothesis_id,"
                " conditions, conditions_hash, statement, data_ranges,"
                " ranges_hash, status, created_at, updated_at) VALUES"
                f" ('hyp_mid', '[]', 'ch2', 's',"
                f" '[{{\"symbol\":\"BTCUSDT\",\"timeframe\":\"4h\","
                f"\"start\":\"{lo.isoformat()}\","
                f"\"end\":\"{hi.isoformat()}\"}}]',"
                " 'rh2', 'active', '2026-01-01T00:00:00Z',"
                " '2026-01-01T00:00:00Z')"))
        resp = client.get("/api/research/union-balance", params={
            "symbol": "BTCUSDT", "timeframe": "4h",
            "hypothesis_id": "hyp_mid"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["judged"]) == 1
        segs = body["remaining"]
        assert len(segs) == 2, f"中段判定→前后两段余量（实测 {len(segs)}）"
        import math

        tf_h = 4
        expect_bars = sum(
            math.floor(
                (pd.Timestamp(s["end"]) - pd.Timestamp(s["start"]))
                .total_seconds() / 3600 / tf_h)
            for s in segs)
        assert body["remaining_bars"] == expect_bars, (
            f"保守折算（实测 {body['remaining_bars']} 期望 {expect_bars}）")

    def test_data_bounds_shortcut_branch(self, tmp_path):
        """data_bounds 直传分支（调用方预装载缓存——workbench CARD-02
        形制；单元面直调 service）。"""
        import pandas as pd

        from core.analysis import validation_service as vs

        client, df = _setup(tmp_path)
        db = client.app.state.db_path
        data_dir = client.app.state.data_dir
        bounds = (df.index[0], df.index[-1] + pd.Timedelta(hours=4))
        out = vs.query_union_balance(
            db, data_dir, symbol="BTCUSDT", timeframe="4h",
            hypothesis_id="hyp_ub", data_bounds=bounds)
        assert out["remaining_bars"] == 300
        assert out["judged"] == []
