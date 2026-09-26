"""regime_split 路由测试（add-regime-split 任务 4.3）。

覆盖：快路径 200 段清单与 gates 披露、参数非法 422 结构化拒绝、
通用 analysis 留痕可查（无专属留痕——分段检查不涉候选谱系）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def split_env(tmp_path: Path):
    """800 bar 1d 数据（ADX/ATR warmup 200 → 请求起点留足前缀）+ 独立 db。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=800, freq="1d", start="2023-06-01", seed=13)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    from core.persistence.db_ext import init_db_ext
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _payload(df, **extra):
    p = {"segmentation": "auto"}
    p.update(extra)
    return {
        "template": "regime_split",
        "params": p,
        "data_range": {"symbol": SYMBOL, "timeframe": "1d",
                       "start": df.index[250].isoformat(),
                       "end": df.index[799].isoformat()},
    }


class TestRegimeSplitRoute:
    def test_fast_path_200_segments(self, api_client, split_env):
        api_client.app.state.db_path = split_env["db"]
        api_client.app.state.data_dir = split_env["data_dir"]
        resp = api_client.post("/api/research/analysis",
                               json=_payload(split_env["df"]))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["segmentation"] == "auto"
        assert len(payload["segments"]) >= 1
        assert {s["type"] for s in payload["segments"]} <= {
            "趋势", "震荡", "高波动"}
        assert payload["gates"]["vol_pct"] == 0.80

    def test_invalid_segmentation_422(self, api_client, split_env):
        api_client.app.state.db_path = split_env["db"]
        api_client.app.state.data_dir = split_env["data_dir"]
        resp = api_client.post(
            "/api/research/analysis",
            json=_payload(split_env["df"], segmentation="sometimes"))
        assert resp.status_code == 422
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_PARAM_INVALID"
        assert body["detail"]["param"] == "segmentation"

    def test_analysis_lineage_written(self, api_client, split_env):
        """通用 analysis 留痕（无专属 entry_type——分段不涉谱系）。"""
        api_client.app.state.db_path = split_env["db"]
        api_client.app.state.data_dir = split_env["data_dir"]
        resp = api_client.post("/api/research/analysis",
                               json=_payload(split_env["df"]))
        assert resp.status_code == 200, resp.text
        logs = rr.list_research_log(split_env["db"], entry_type="analysis")
        assert any(entry["ref_id"] == "regime_split" for entry in logs)
        entry = next(e for e in logs if e["ref_id"] == "regime_split")
        assert json.loads(entry["data_ranges"])[0]["symbol"] == SYMBOL
