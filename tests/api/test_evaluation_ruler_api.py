"""评测尺子模板族路由级测试（add-evaluation-ruler 任务 3.4/4.3/5.4/5.5）。

注册同步负向锚（未进 _TEMPLATES → 422 RESEARCH_TEMPLATE_UNKNOWN）、
run_analysis 集成用例、D5 假设关联拦截负向双锚（判定并集不变＋模板
本体未触达——断言点在分派层）。
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError


@pytest.fixture()
def ruler_data_dir(tmp_path):
    """三段行情 4h 数据（确定性构造——zigzag 确认依赖单根达门槛
    跳变：谷后单根 +5.8%、峰后单根 −5.8%，两次转折产生一条已确认浪
    谷 60.2 → 峰 110）。"""
    d = tmp_path / "data"
    d.mkdir()
    rng = np.random.default_rng(11)
    close = np.concatenate([
        np.linspace(100, 60.2, 200),   # 缓跌：ext 跟随新低
        [63.5],                        # 单根跳变 ≥60.2×1.05 → 确认谷
        np.linspace(63.9, 110, 199),   # 上涨：ext 跟随新高至 110
        [104.0],                       # 单根回落 110/104−1≥5% → 回认峰
        np.linspace(104, 80, 200),     # 下跌（末端未确认）
    ])
    n = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    pd.DataFrame({
        "open": close, "high": close + 0.3, "low": close - 0.3,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(d / "TESTUSDT_4h.parquet")
    return d


@pytest.fixture()
def ruler_client(db_path, ruler_data_dir):
    from MyQuant.api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(db_path=db_path, data_dir=ruler_data_dir)
    with TestClient(app) as c:
        yield c


def _body(template, params, **range_kw):
    r = {"symbol": "TESTUSDT", "timeframe": "4h",
         "start": "2025-01-01", "end": "2025-04-01"}
    r.update(range_kw)
    return {"template": template, "params": params, "data_range": r}


class TestBoxInventoryRoute:
    def test_box_registered_and_runs_with_frozen_default(self, ruler_client):
        """box 走 run_analysis：1h 冻结默认 (120,48)——4h 轴无冻结默认
        422（该轴必填），改显式参数后可跑（截断末段成箱）。"""
        resp = ruler_client.post("/api/research/analysis", json=_body(
            "box_inventory", {}))
        assert resp.status_code == 422
        assert "无冻结默认" in resp.json()["detail"]["message"]
        resp = ruler_client.post("/api/research/analysis", json=_body(
            "box_inventory", {"window": 3, "min_len": 2}))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["template"] == "box_inventory"
        assert out["sample_size"] == len(out["payload"]["boxes"])

    def test_box_hypothesis_id_rejected(self, ruler_client):
        resp = ruler_client.post("/api/research/analysis", json={
            "template": "box_inventory",
            "params": {"window": 3, "min_len": 2},
            "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                           "start": "2025-01-01", "end": "2025-04-01"},
            "hypothesis_id": "hyp_probe"})
        assert resp.status_code == 422
        assert "不适用假设关联" in resp.json()["detail"]["message"]


class TestWaveInventoryRoute:
    def test_registered_in_templates_and_runs(self, ruler_client):
        resp = ruler_client.post("/api/research/analysis",
                                 json=_body("wave_inventory", {}))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["template"] == "wave_inventory"
        assert out["sample_size"] == len(out["payload"]["waves"])

    def test_unregistered_template_name_rejected(self, ruler_client):
        """负向锚（任务 3.4）：只挂文件不进 _TEMPLATES → 未知模板 422。"""
        resp = ruler_client.post(
            "/api/research/analysis",
            json=_body("wave_inventory_typo", {}))
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_TEMPLATE_UNKNOWN"

    def test_threshold_and_warmup_passthrough(self, ruler_client):
        resp = ruler_client.post("/api/research/analysis", json=_body(
            "wave_inventory",
            {"threshold": 0.05, "warmup_bars": 0}))
        assert resp.status_code == 200
        be = resp.json()["payload"]["boundary_effect"]
        assert be["warmup_bars_requested"] == 0

    def test_hypothesis_id_rejected_at_dispatch(
            self, ruler_client, db_path, monkeypatch):
        """D5 拦截负向双锚：422＋模板本体未触达＋判定并集不变。"""
        import sys
        wi = sys.modules["core.analysis.templates.wave_inventory"]
        calls = []
        orig = wi.wave_inventory
        monkeypatch.setattr(
            wi, "wave_inventory",
            lambda *a, **k: calls.append(1) or orig(*a, **k))
        resp = ruler_client.post("/api/research/analysis", json=_body(
            "wave_inventory", {}, ))
        # 先证基线可达（模板本体被调），再带 hypothesis_id 证拦截
        assert resp.status_code == 200
        assert calls, "基线调用触达模板本体"
        n_before = len(calls)
        resp = ruler_client.post("/api/research/analysis", json={
            "template": "wave_inventory", "params": {},
            "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                           "start": "2025-01-01", "end": "2025-04-01"},
            "hypothesis_id": "hyp_probe"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "不适用假设关联" in detail["message"]
        assert len(calls) == n_before, "拦截后模板本体未触达"
        # 判定并集不变：无 validate 留痕、无验证记录
        from core.persistence.repository import research as rr
        logs = rr.list_research_log(db_path)
        assert not [l for l in logs if l.get("entry_type") == "validate"], \
            "拦截请求不产生判定留痕"


class TestRulerCompareRoute:
    @pytest.fixture()
    def vr_db(self, db_path):
        from core.analysis.executor import ensure_research_tables
        from core.persistence.db_ext import init_db_ext
        from sqlalchemy import text
        from core.persistence.orm.engine import engine_for

        init_db_ext(db_path)
        ensure_research_tables(db_path)
        with engine_for(db_path).begin() as conn:
            conn.execute(text(
                "INSERT INTO research_hypotheses (hypothesis_id,"
                " conditions, conditions_hash, statement, data_ranges,"
                " ranges_hash, status, created_at, updated_at) VALUES"
                " ('hyp_rr', '[]', 'ch_rr', 's', '[]', 'rh_rr',"
                " 'active', '2026-01-01T00:00:00Z',"
                " '2026-01-01T00:00:00Z')"))
        rng = {"symbol": "TESTUSDT", "timeframe": "4h",
               "start": "2025-01-01", "end": "2025-04-20"}
        rows = [
            ("2025-02-05 04:00:00+00:00", "2025-02-12 04:00:00+00:00",
             "Long", 62.0, 80.0, 3.0),
            ("2025-02-20 04:00:00+00:00", "2025-02-25 04:00:00+00:00",
             "Long", 70.0, 95.0, 2.0),
            ("2025-03-01 04:00:00+00:00", "2025-03-05 04:00:00+00:00",
             "Short", 100.0, 90.0, 1.5),
        ]
        import json as _json
        params = _json.dumps({"request_range": rng, "init_cash": 100.0,
                              "fee": 0.001, "slippage": 0.0005})
        with engine_for(db_path).begin() as conn:
            conn.execute(text(
                "INSERT INTO validation_records (record_id,"
                " hypothesis_id, level, segmentation, params, result,"
                " verdict, range_hash, created_at) VALUES"
                " ('rec_rr', 'hyp_rr', 'isolated_backtest', '',"
                " :params, '{}', 'pass', 'x', '2026-01-01T00:00:00Z')"),
                {"params": params})
            for i, (et, xt, d, ep, xp, pnl) in enumerate(rows):
                conn.execute(text(
                    "INSERT INTO vr_trade_detail (record_id, exec_ref,"
                    " exec_index, range_start, range_end, seq,"
                    " entry_time, exit_time, entry_price, exit_price,"
                    " size, direction, pnl) VALUES"
                    " ('rec_rr', 'main', 0, :rs, :re, :seq, :et, :xt,"
                    " :ep, :xp, 1.0, :dir, :pnl)"),
                    {"rs": rng["start"], "re": rng["end"], "seq": i,
                     "et": et, "xt": xt, "ep": ep, "xp": xp, "dir": d,
                     "pnl": pnl})
        return db_path

    @pytest.fixture()
    def cmp_client(self, vr_db, ruler_data_dir):
        from MyQuant.api.app import create_app
        from fastapi.testclient import TestClient

        app = create_app(db_path=vr_db, data_dir=ruler_data_dir)
        with TestClient(app) as c:
            yield c

    def _body(self, params, **extra):
        body = {"template": "ruler_compare", "params": params,
                "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                               "start": "2025-01-01",
                               "end": "2025-04-20"}}
        body.update(extra)
        return body

    def test_wave_mode_full_route(self, cmp_client):
        resp = cmp_client.post("/api/research/analysis", json=self._body(
            {"mode": "wave",
             "trade_source": {"kind": "validation",
                              "record_id": "rec_rr"}}))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["template"] == "ruler_compare"
        assert out["payload"]["mode"] == "wave"
        assert out["sample_size"] == 3

    def test_box_mode_full_route(self, cmp_client):
        resp = cmp_client.post("/api/research/analysis", json=self._body(
            {"mode": "box", "edge_window": 24,
             "trade_source": {"kind": "validation",
                              "record_id": "rec_rr"}}))
        assert resp.status_code == 200, resp.text
        assert resp.json()["payload"]["box"]["n_trades"] == 3

    @pytest.mark.parametrize("params", [
        {},
        {"mode": "wave"},
        {"trade_source": {"kind": "validation", "record_id": "rec_rr"}},
    ])
    def test_missing_required_keys_rejected(self, cmp_client, params,
                                            db_path):
        """research-error-channel MODIFIED 条款 scenario：缺必填键 422
        且不产生任务记录（判定并集零留痕）。"""
        resp = cmp_client.post("/api/research/analysis", json=self._body(
            params))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail.get("detail", {}).get("param") in (
            "mode", "trade_source", "mode|trade_source"), detail
        from core.persistence.repository import research as rr
        logs = rr.list_research_log(db_path)
        assert not [l for l in logs
                    if l.get("entry_type") == "validate"], \
            "缺键拒绝不产生任务记录"

    def test_backtest_guidance_422(self, cmp_client):
        """kind=backtest → 422 附防污染指引文案（与 5.1 拒绝面并列）。"""
        resp = cmp_client.post("/api/research/analysis", json=self._body(
            {"mode": "wave",
             "trade_source": {"kind": "backtest",
                              "record_id": "rec_rr"}}))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "沙盒回测不落库逐笔（防污染纪律）" in detail["message"]
        assert "隔离验证记录 record_id" in detail["message"]

    def test_hypothesis_id_rejected(self, cmp_client):
        resp = cmp_client.post("/api/research/analysis", json=self._body(
            {"mode": "wave",
             "trade_source": {"kind": "validation",
                              "record_id": "rec_rr"}},
            hypothesis_id="hyp_rr"))
        assert resp.status_code == 422
        assert "不适用假设关联" in resp.json()["detail"]["message"]

    def test_unknown_template_still_rejected(self, cmp_client):
        resp = cmp_client.post("/api/research/analysis", json=self._body(
            {"mode": "wave"}))
        # ruler_compare 已注册——未知模板名走另一断言（保留族级负向锚）
        resp2 = cmp_client.post("/api/research/analysis", json={
            "template": "ruler_compare_x", "params": {},
            "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                           "start": "2025-01-01", "end": "2025-04-20"}})
        assert resp2.status_code == 422
        assert resp2.json()["detail"]["code"] == "RESEARCH_TEMPLATE_UNKNOWN"

    @pytest.mark.parametrize("tpl, params", [
        ("wave_inventory", {}),
        ("box_inventory", {"window": 3, "min_len": 2}),
    ])
    def test_insufficient_data_rejected(self, ruler_client, tpl, params):
        """路由层数据不足拒绝＋分派段 None 分支不可达判定锚（阶段四
        盲区消解）。调用图依据：run_analysis 端点预检查（rows<50 时
        _load_df 复现 DATA_INSUFFICIENT 先抛，analysis.py:944-948）先于
        _run_template 分派段装载——预检查保证请求区间 ≥50 行后，分派段
        装载切片 [load_start, end) ⊇ 请求区间恒有数据，wave/box 分派段
        的 df None raise（:386/:435）经公开路由不可达（防御纵深冗余层）。
        message 锚=原始请求区间（无 warmup 前扩）——若未来预检查退役、
        分派段拦截顶上，message 变前扩区间即红，逼同步更新本判定。"""
        resp = ruler_client.post("/api/research/analysis", json=_body(
            tpl, params, start="2023-01-01", end="2023-02-01"))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_DATA_INSUFFICIENT"
        assert "[2023-01-01, 2023-02-01)" in detail["message"], \
            "拒绝出自预检查（原始区间无前扩）——分派段 None 分支不可达锚"


class TestConditionStatsSignificanceRoute:
    """condition_stats significance 经真实路由的集成面（阶段五层级
    补全——ai-research-tools MODIFIED 增量此前只有模板直调层）。

    独立长数据 fixture：EMA warmup 按注册表上限推导为 600 bar（保守
    取向），公共 ruler_data_dir（601 根）起点前扩不足——造 900 根。"""

    @pytest.fixture()
    def stats_env(self, db_path, tmp_path):
        from MyQuant.api.app import create_app
        from fastapi.testclient import TestClient

        d = tmp_path / "data"
        d.mkdir()
        rng_ = np.random.default_rng(5)
        n = 900
        close = 100 + np.cumsum(rng_.normal(0, 0.5, n))
        idx = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC",
                            name="timestamp")
        pd.DataFrame({
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": rng_.uniform(100, 800, n),
        }, index=idx).to_parquet(d / "TESTUSDT_4h.parquet")
        app = create_app(db_path=db_path, data_dir=d)
        with TestClient(app) as c:
            # 低分位阈值：区间内命中约九成（防随机游走窗口性零命中）
            yield c, float(np.quantile(close, 0.1))

    def _stats_body(self, params, target):
        return {"template": "condition_stats",
                "params": {**params, "condition": {
                    "subject": "close", "action": "gt", "target": target}},
                "data_range": {"symbol": "TESTUSDT", "timeframe": "4h",
                               "start": "2025-04-20", "end": "2025-05-20"}}

    def test_bootstrap_ci_via_route(self, stats_env):
        """params.significance 经 /api/research/analysis 全链 → 200 且
        显著性段 method=bootstrap_ci＋CI 附着。"""
        client, target = stats_env
        resp = client.post("/api/research/analysis", json=self._stats_body(
            {"indicator": "EMA", "params": {"period": 10},
             "significance": {"method": "bootstrap_ci", "B": 500}}, target))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        fs = out["confidence"]["forward_significance"]
        assert fs["method"] == "bootstrap_ci"
        assert fs["boot_diff_ci"] is not None and len(
            fs["boot_diff_ci"]) == 2
        assert fs["B"] == 500

    def test_default_t_test_via_route(self, stats_env):
        """不携带 significance 经路由 → 缺省 t 检验（存量路径集成锚）。"""
        client, target = stats_env
        resp = client.post("/api/research/analysis", json=self._stats_body(
            {"indicator": "EMA", "params": {"period": 10}}, target))
        assert resp.status_code == 200, resp.text
        fs = resp.json()["confidence"]["forward_significance"]
        assert fs["method"] == "t_test_vs_baseline"
