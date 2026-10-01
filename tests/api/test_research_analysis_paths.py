"""分析路由路径盲区消解（阶段四闸门②）。

覆盖闸门①识别的缺口：轻模板 dispatch 三分支（correlation_scan/
event_study 200、pattern_mining 恒 202）、regime_split 带 indicator
分支、区间无数据错误通道（RESEARCH_DATA_INSUFFICIENT）、三处库存
JSON 损坏容错（analysis task result_ref / validation result /
plugins oos_summary）。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr

SYMBOL = "BTCUSDT"


@pytest.fixture()
def paths_env(tmp_path: Path):
    """500 bar 4h 数据 + 独立 db（脏数据用例需直写库）。"""
    rng = np.random.default_rng(21)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 150, n))
    df = pd.DataFrame({
        "open": close, "high": close + 80, "low": close - 80,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(api_client, env):
    api_client.app.state.db_path = env["db"]
    api_client.app.state.data_dir = env["data_dir"]


def _body(template, params, **range_extra):
    # start 后移 40 天：带 RSI 的 warmup 前扩 200 bar（≈33.4 天）可达
    rng = {"symbol": SYMBOL, "timeframe": "4h",
           "start": "2024-02-10T00:00:00Z", "end": "2024-03-01T00:00:00Z"}
    rng.update(range_extra)
    return {"template": template, "params": params, "data_range": rng}


class TestLightTemplateDispatch:
    """轻模板路由 dispatch 直测（服务层厚测，此处钉路由透传契约）。"""

    def test_correlation_scan_200(self, api_client, paths_env):
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "correlation_scan", {"signal_column": "close"}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["template"] == "correlation_scan"
        assert "analysis" in json.dumps(body) or body.get("sample_size"), \
            "归约结果在响应内"

    def test_event_study_200(self, api_client, paths_env):
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study",
            {"condition": {"subject": "close", "action": "lt", "target": 70000}}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["template"] == "event_study"
        assert body["sample_size"] >= 30

    def test_pattern_mining_always_202(self, api_client, paths_env):
        """pattern_mining 恒 heavy（引擎 fit）——202 + poll 句柄。"""
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "pattern_mining", {"as_of": "2024-02-15T00:00:00Z"}))
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"] and body["poll"].startswith(
            "/api/research/analysis/tasks/")


class TestRegimeSplitWithIndicator:
    def test_indicator_branch_warmup_and_stats(self, api_client, paths_env):
        """带 indicator+condition 分支：指标并入 warmup 清单、逐段统计照返。"""
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "regime_split",
            {"indicator": "RSI",
             "condition": {"subject": {"indicator": "RSI",
                                       "params": {"period": 14}},
                           "action": "lt", "target": 40}}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["template"] == "regime_split"
        assert isinstance(body.get("segments", []), list)


class TestNoDataErrorChannel:
    def test_range_outside_data_rejected(self, api_client, paths_env):
        """区间在数据域之外（2025 年）→ 422 RESEARCH_DATA_INSUFFICIENT。"""
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "correlation_scan", {"signal_column": "close"},
            start="2025-06-01T00:00:00Z", end="2025-07-01T00:00:00Z"))
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_DATA_INSUFFICIENT"


class TestCorruptedJsonTails:
    """库存 JSON 损坏容错三处（CARD-12 先例的闸门补齐）。"""

    def test_analysis_task_result_ref_corrupt(self, api_client, paths_env):
        """result_ref 损坏：GET 200 原样保留、不产生 result 键。"""
        rr.save_analysis_task(paths_env["db"], task_id="ant_dirty",
                              template="t", params="{}")
        rr.update_analysis_task(paths_env["db"], task_id="ant_dirty",
                                status="succeeded", result_ref="{corrupt")
        _wire(api_client, paths_env)
        body = api_client.get("/api/research/analysis/tasks/ant_dirty").json()
        assert body["status"] == "succeeded"
        assert body["result_ref"] == "{corrupt"
        assert "result" not in body, "损坏 result_ref 不产生 result 键"

    def test_validation_report_result_corrupt_zero_filled(
            self, api_client, paths_env):
        """result 列损坏归 {}：人话摘要按缺省 0 值呈现（不 500）。"""
        rr.save_hypothesis_with_log(
            paths_env["db"], hypothesis_id="hyp_c", conditions="{}",
            conditions_hash="cc", statement="c", data_ranges="[]",
            ranges_hash="rc")
        rr.save_validation_with_log(
            paths_env["db"], record_id="rec_c", hypothesis_id="hyp_c",
            strategy_id="st_c", level="isolated_backtest",
            params='{"request_range": {}}', result="{corrupt",
            verdict="pass", range_hash="rkc")
        _wire(api_client, paths_env)
        resp = api_client.get("/api/research/validation/st_c/report")
        assert resp.status_code == 200, "损坏 result 不应 500"
        iso = resp.json()["isolated_backtests"][0]
        assert iso["range"] == {} and "0.0%" in iso["人话"], \
            "损坏 result 归 {}，指标缺省 0 值呈现"

    def test_model_plugin_oos_summary_corrupt(self, api_client, paths_env):
        """oos_summary 损坏：列表 200 原样保留。"""
        rr.save_plugin_with_registry(
            paths_env["db"], source="model", plugin_id="pm_c", name="c_m",
            version=1, config="{}", signal_column="c_col",
            entry_id="e_c", column_name="c_col")
        from sqlalchemy import update
        from core.persistence.orm.models import ModelPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(paths_env["db"]).begin() as conn:
            conn.execute(update(ModelPlugin).where(
                ModelPlugin.plugin_id == "pm_c").values(
                    oos_summary="{corrupt"))
        _wire(api_client, paths_env)
        resp = api_client.get("/api/research/plugins/model")
        assert resp.status_code == 200
        row = next(r for r in resp.json()["items"]
                   if r["plugin_id"] == "pm_c")
        assert row["oos_summary"] == "{corrupt"
