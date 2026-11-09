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

from core.analysis.hypothesis_service import ResearchError


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
