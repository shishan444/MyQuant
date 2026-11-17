"""ab_compare 模板路由级测试（sandbox-and-report-supplement B4）。

注册负向锚（未进 _TEMPLATES → 422）、run_analysis 集成、三必填键
逐一拒绝（不产生任务记录）、假设关联拦截负向双锚（判定并集不变＋
模板本体未触达——分派层 _HYPOTHESIS_INAPPLICABLE 拦截名单扩枚）。
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError


@pytest.fixture()
def ab_data_dir(tmp_path):
    """三段行情 4h 数据（dimension=none 不消费；供 wave/box 挂载路由
    用例——形制同 ruler 族 fixture）。"""
    d = tmp_path / "data"
    d.mkdir()
    rng = np.random.default_rng(11)
    close = np.concatenate([
        np.linspace(100, 60.2, 200), [63.5],
        np.linspace(63.9, 110, 199), [104.0],
        np.linspace(104, 80, 200)])
    n = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    pd.DataFrame({
        "open": close, "high": close + 0.3, "low": close - 0.3,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(d / "TESTUSDT_4h.parquet")
    return d


@pytest.fixture()
def ab_env(db_path, ab_data_dir):
    """双验证记录库＋TestClient（两条同区间记录 rec_a/rec_b）。"""
    from MyQuant.api.app import create_app
    from fastapi.testclient import TestClient
    from core.analysis.executor import ensure_research_tables
    from core.persistence.db_ext import init_db_ext
    from sqlalchemy import text
    from core.persistence.orm.engine import engine_for

    init_db_ext(db_path)
    ensure_research_tables(db_path)
    with engine_for(db_path).begin() as conn:
        conn.execute(text(
            "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
            " conditions_hash, statement, data_ranges, ranges_hash,"
            " status, created_at, updated_at) VALUES"
            " ('hyp_ab', '[]', 'ch_ab', 's', '[]', 'rh_ab', 'active',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"))
    rng = {"symbol": "TESTUSDT", "timeframe": "4h",
           "start": "2025-01-01", "end": "2025-04-20"}
    for rid, pnls in (("rec_a", (3.0, 2.0, 1.5)),
                      ("rec_b", (1.0, -2.0, 0.5))):
        params = json.dumps({"request_range": rng, "init_cash": 100.0,
                             "fee": 0.001, "slippage": 0.0005})
        with engine_for(db_path).begin() as conn:
            conn.execute(text(
                "INSERT INTO validation_records (record_id, hypothesis_id,"
                " level, segmentation, params, result, verdict, range_hash,"
                " created_at) VALUES (:rid, 'hyp_ab', 'isolated_backtest',"
                " '', :params, '{}', 'pass', 'x',"
                " '2026-01-01T00:00:00Z')"), {"rid": rid,
                                              "params": params})
            for i, pnl in enumerate(pnls):
                conn.execute(text(
                    "INSERT INTO vr_trade_detail (record_id, exec_ref,"
                    " exec_index, range_start, range_end, seq, entry_time,"
                    " exit_time, entry_price, exit_price, size, direction,"
                    " pnl) VALUES (:rid, 'main', 0, :rs, :re, :seq, :et,"
                    " :xt, 100, 101, 1.0, 'Long', :pnl)"),
                    {"rid": rid, "rs": rng["start"], "re": rng["end"],
                     "seq": i,
                     "et": f"2025-02-0{i + 1} 04:00:00+00:00",
                     "xt": f"2025-02-1{i} 04:00:00+00:00", "pnl": pnl})
    app = create_app(db_path=db_path, data_dir=ab_data_dir)
    with TestClient(app) as c:
        yield c, db_path


def _body(params, **extra):
    body = {"template": "ab_compare", "params": params,
            "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                           "start": "2025-01-01", "end": "2025-04-20"}}
    body.update(extra)
    return body


_SOURCES = {"mode": "ab",
            "trade_source_a": {"kind": "validation", "record_id": "rec_a"},
            "trade_source_b": {"kind": "validation", "record_id": "rec_b"}}


class TestAbCompareRoute:
    def test_full_route_two_records(self, ab_env):
        client, _ = ab_env
        resp = client.post("/api/research/analysis",
                           json=_body(dict(_SOURCES)))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["template"] == "ab_compare"
        assert out["sample_size"] == 6
        assert out["payload"]["group_diff"]["direction"] == "a_minus_b"
        assert out["payload"]["dimension"] == "none", "缺省 dimension=none"

    def test_dimension_passthrough_wave(self, ab_env):
        """dimension 透传（wave 挂载经真实路由全链）。"""
        client, _ = ab_env
        resp = client.post("/api/research/analysis", json=_body(
            dict(_SOURCES, dimension="wave")))
        assert resp.status_code == 200, resp.text
        p = resp.json()["payload"]
        assert p["dimension"] == "wave"
        assert "identification_rate" in p["group_a"]["wave"]

    @pytest.mark.parametrize("drop", ["mode", "trade_source_a",
                                      "trade_source_b"])
    def test_missing_required_keys_rejected_each(
            self, ab_env, drop):
        """research-error-channel MODIFIED 条款 scenario：三键各缺其
        一 → 422 点名缺失键，且不产生任务记录（判定并集零留痕）。"""
        client, db_path = ab_env
        params = {k: v for k, v in _SOURCES.items() if k != drop}
        resp = client.post("/api/research/analysis", json=_body(params))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail.get("detail", {}).get("param") == drop, detail
        from core.persistence.repository import research as rr
        logs = rr.list_research_log(db_path)
        assert not [l for l in logs
                    if l.get("entry_type") == "validate"], \
            "缺键拒绝不产生任务记录"

    def test_hypothesis_id_rejected_at_dispatch(
            self, ab_env, monkeypatch):
        """假设关联拦截负向双锚（拦截名单扩枚 ab_compare）：422＋模板
        本体未触达＋判定并集不变。"""
        import sys
        client, db_path = ab_env
        mod = sys.modules["core.analysis.templates.ab_compare"]
        calls = []
        orig = mod.ab_compare
        monkeypatch.setattr(
            mod, "ab_compare",
            lambda *a, **k: calls.append(1) or orig(*a, **k))
        resp = client.post("/api/research/analysis",
                           json=_body(dict(_SOURCES)))
        assert resp.status_code == 200, resp.text
        assert calls, "基线调用触达模板本体"
        n_before = len(calls)
        resp = client.post("/api/research/analysis", json=_body(
            dict(_SOURCES), hypothesis_id="hyp_ab"))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "不适用假设关联" in detail["message"]
        assert len(calls) == n_before, "拦截后模板本体未触达"
        from core.persistence.repository import research as rr
        logs = rr.list_research_log(db_path)
        assert not [l for l in logs if l.get("entry_type") == "validate"], \
            "拦截请求不产生判定留痕"

    def test_unregistered_template_name_rejected(self, ab_env):
        """负向锚（B4）：只挂文件不进 _TEMPLATES → 未知模板 422
        （ab_compare_typo 形制——注册面缺一处的行为面）。"""
        client, _ = ab_env
        resp = client.post("/api/research/analysis", json={
            "template": "ab_compare_typo", "params": dict(_SOURCES),
            "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                           "start": "2025-01-01", "end": "2025-04-20"}})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_TEMPLATE_UNKNOWN"

    def test_inapplicable_list_extended(self):
        """拦截名单扩枚结构锚：_HYPOTHESIS_INAPPLICABLE 含 ab_compare
        （既有三成员零变化）。"""
        from api.routes.research.analysis import _HYPOTHESIS_INAPPLICABLE

        assert "ab_compare" in _HYPOTHESIS_INAPPLICABLE
        assert set(_HYPOTHESIS_INAPPLICABLE) == {
            "wave_inventory", "box_inventory", "ruler_compare",
            "ab_compare"}

    def test_backtest_guidance_422(self, ab_env):
        """kind=backtest（a 侧）→ 422 附防污染指引（与校验链用例并列
        的路由层锚）。"""
        client, _ = ab_env
        sources = dict(_SOURCES,
                       trade_source_a={"kind": "backtest",
                                       "record_id": "rec_a"})
        resp = client.post("/api/research/analysis", json=_body(sources))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "沙盒回测不落库逐笔（防污染纪律）" in detail["message"]
