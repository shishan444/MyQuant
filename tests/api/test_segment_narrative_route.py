"""区间事件摘要路由级测试（0.4.1 R3 任务 3.2/3.3——双上限装载前侧、
双入口可达、留痕两态）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.isolation import judged_ranges_union
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def smooth_env(tmp_path: Path):
    """低频波动趋势数据（缓趋势+周期 240 bar 正弦）：5000 bar 区间
    事件密度 ~370<500——bar 上限与事件上限两守护各得其所互不遮蔽
    （零噪声纯趋势在 Stochastic/MACD 上产生 epsilon 伪交叉，不采用）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    n = 7000
    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC",
                        name="timestamp")
    import numpy as np
    t = np.arange(n)
    close = 100 + 0.004 * t + 5.0 * np.sin(2 * np.pi * t / 240)
    df = pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.full(n, 100.0),
    }, index=idx)
    df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


@pytest.fixture()
def sn_env(tmp_path: Path):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=5200, freq="1h", start="2023-01-01", seed=9)
    df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(client, env):
    client.app.state.db_path = env["db"]
    client.app.state.data_dir = env["data_dir"]


def _body(start, end, **params):
    return {
        "template": "segment_narrative",
        "params": params,
        "data_range": {"symbol": SYMBOL, "timeframe": "1h",
                       "start": start, "end": end},
    }


class TestSegmentNarrativeRoute:
    def test_unknown_template_lists_new_name(self, api_client, sn_env):
        """未知模板错误消息的可用清单含新名（四处形制之路由可达）。"""
        _wire(api_client, sn_env)
        resp = api_client.post("/api/research/analysis", json={
            "template": "no_such", "params": {},
            "data_range": {"symbol": SYMBOL, "timeframe": "1h",
                           "start": "2023-02-01", "end": "2023-03-01"}})
        assert resp.status_code == 422
        assert "segment_narrative" in resp.json()["detail"]["message"]

    @pytest.mark.parametrize(
        ("n_bars", "ok"),
        [(4999, True), (5000, True), (5001, False)],
        ids=["4999 过", "5000 边界过", "5001 拒"])
    def test_range_bar_cap_boundary(self, api_client, smooth_env, n_bars, ok):
        """双上限之一（装载前）：区间 bar 两侧确定样例（闭合计数下
        end 落点即边界 bar；平滑数据事件密度低——不与事件上限耦合）。"""
        _wire(api_client, smooth_env)
        df = smooth_env["df"]
        start = df.index[700]
        end = df.index[700 + n_bars - 1]  # 闭端：区间恰含 n_bars 根
        resp = api_client.post("/api/research/analysis",
                               json=_body(start.isoformat(),
                                          end.isoformat()))
        if ok:
            assert resp.status_code == 200, resp.text
        else:
            assert resp.status_code == 422, resp.text
            detail = resp.json()["detail"]
            assert detail["code"] == "RESEARCH_PARAM_INVALID"
            assert detail["detail"]["limit"] == 5000

    def test_warmup_insufficient_rejected(self, api_client, sn_env):
        """warmup 不足拒绝（detail 四键族——load_with_warmup 透传）。"""
        _wire(api_client, sn_env)
        df = sn_env["df"]
        resp = api_client.post("/api/research/analysis", json=_body(
            df.index[0].isoformat(), df.index[50].isoformat()))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_WARMUP_INSUFFICIENT"
        for key in ("warmup_bars", "available_bars", "data_start", "rule"):
            assert key in detail["detail"]

    def test_log_trail_two_states(self, api_client, sn_env):
        """留痕两态：不挂假设=analysis 条目 hypothesis_id 空；挂假设=
        区间进该假设判定并集（沿既有语义）。"""
        import sqlite3
        from core.analysis.hypothesis_service import register_hypothesis
        _wire(api_client, sn_env)
        rng = {"start": "2023-02-01T00:00:00Z", "end": "2023-03-01T00:00:00Z"}

        resp = api_client.post("/api/research/analysis", json=_body(
            rng["start"], rng["end"], indicators=["OBV"]))
        assert resp.status_code == 200, resp.text
        with sqlite3.connect(sn_env["db"]) as conn:
            rows = conn.execute(
                "SELECT entry_type, ref_id, hypothesis_id FROM research_log "
                "WHERE ref_id = 'segment_narrative'").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "analysis"
        assert rows[0][2] is None  # 不挂假设

        cond = {"subject": {"indicator": "RSI", "params": {"period": 14},
                            "field": "rsi"}, "action": "gt", "target": 55}
        reg = register_hypothesis(
            sn_env["db"],
            conditions={"when_conditions": [cond],
                        "then_conditions": [dict(cond)]},
            statement="t", data_ranges=[{
                "symbol": SYMBOL, "timeframe": "1h",
                "start": "2023-06-01T00:00:00Z",
                "end": "2023-07-01T00:00:00Z"}])
        hyp_id = reg["hypothesis_id"]
        resp = api_client.post("/api/research/analysis", json={
            "template": "segment_narrative",
            "params": {"indicators": ["OBV"]},
            "data_range": {"symbol": SYMBOL, "timeframe": "1h",
                           "start": rng["start"], "end": rng["end"]},
            "hypothesis_id": hyp_id})
        assert resp.status_code == 200, resp.text
        union = judged_ranges_union(sn_env["db"], hyp_id)
        assert any(r["start"] == pd.Timestamp(rng["start"]).isoformat()
                   for r in union), "挂假设区间进判定并集"


class TestIndicatorsPrevalidation:
    """CARD-02 特征测试：indicators 装载前校验——非法形制 422
    RESEARCH_PARAM_INVALID（原 derive_warmup 对未注册名 KeyError
    裸穿 500）；校验先于装载，与模板内同源同码（validate_indicators
    单一事实源）。"""

    @pytest.mark.parametrize(
        "indicators",
        [["NOPE"], [], "EMA"],
        ids=["未注册名拒", "空列表拒", "字符串非列表拒"])
    def test_invalid_indicators_rejected_before_load(self, api_client,
                                                     sn_env, indicators):
        _wire(api_client, sn_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "2023-02-01", "2023-03-01", indicators=indicators))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["param"] == "indicators"

    def test_valid_indicators_pass(self, api_client, sn_env):
        """合法点名对照：在册指标装载前校验通过 → 200。"""
        _wire(api_client, sn_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "2023-02-01", "2023-03-01", indicators=["EMA"]))
        assert resp.status_code == 200, resp.text
