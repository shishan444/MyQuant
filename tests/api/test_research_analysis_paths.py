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
    def test_unknown_template_rejected(self, api_client, paths_env):
        """未知模板名：路由白名单前置 422（终验收尾：:295 直测）。"""
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "nope_template", {}))
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_TEMPLATE_UNKNOWN"

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


class TestHeavyPathAndFastPath:
    """通用重组路径与 fast_path 竞态分支（终验消解）。"""

    def test_generic_heavy_success_chain(self, api_client, tmp_path):
        """通用 heavy（>10000 bar 非 screen/sweep/pattern）：202 → 轮询
        succeeded → analysis 日志落库（_job 体全链）。

        db 保持 fixture（executor 单例绑它——照 test_analysis_warmup_route
        :126 形制；只覆盖 data_dir）。
        """
        import time

        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir()
        make_ohlcv(n=10500, freq="4h", start="2020-01-01",
                   seed=5).to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        api_client.app.state.data_dir = data_dir
        db = Path(api_client.app.state.db_path)

        resp = api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": "RSI", "params": {"period": 14},
                       "condition": {"subject": {"indicator": "RSI",
                                                 "params": {"period": 14}},
                                     "action": "lt", "target": 40}},
            "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                           "start": "2020-02-05T00:00:00Z",
                           "end": "2024-11-01T00:00:00Z"}})
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["task_id"]

        t: dict = {}
        for _ in range(60):  # 轮询到终态（404 为入库竞态窗口，重试）
            poll = api_client.get(
                f"/api/research/analysis/tasks/{task_id}")
            if poll.status_code == 200:
                t = poll.json()
                if t["status"] in ("succeeded", "failed"):
                    break
            time.sleep(0.5)
        assert t.get("status") == "succeeded", t.get("reason")
        logs = rr.list_research_log(db, entry_type="analysis")
        assert any(e["ref_id"] == "condition_stats" for e in logs), \
            "通用 heavy 成功后 analysis 日志落库"

    def test_fast_path_race_returns_handle_directly(
            self, api_client, paths_env, monkeypatch):
        """fast_path 竞态分支：executor 快路径吃掉请求——结果即终态
        直返 handle（screen 段与通用段两调用点）。"""
        import api.routes.research.analysis as ana

        _wire(api_client, paths_env)

        class _FakeExecutor:
            def submit(self, name, job, **kw):
                return {"fast_path": True, "task_id": "ant_fast",
                        "status": "succeeded", "result": None}
        monkeypatch.setattr(ana, "get_executor", lambda: _FakeExecutor())

        resp = api_client.post("/api/research/analysis", json=_body(
            "screen_patterns", {"features": "all"}))
        assert resp.status_code == 200, "screen fast_path 直返 handle"

        resp2 = api_client.post("/api/research/analysis", json=_body(
            "pattern_mining", {"as_of": "2024-02-15T00:00:00Z"}))
        assert resp2.status_code == 200, "通用段 fast_path 直返 handle（pattern 恒 heavy）"


class TestParamGuardRouteSmoke:
    """TD-2 路由级 422 冒烟（clear-analysis-tds 2.3——快路径同步通道）：
    guard 在模板函数入口拦截，经 run_analysis 快路径冒泡为 422 附
    legal_range（specs「数值参数非法结构化拒绝」场景的路由级锚）。"""

    def test_correlation_scan_bad_horizon_422(self, api_client, paths_env):
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "correlation_scan",
            {"signal_column": "close", "horizon": "24"}))
        assert resp.status_code == 422, resp.text
        assert "RESEARCH_PARAM_INVALID" in resp.text
        assert "horizon" in resp.text

    def test_distribution_compare_bad_transform_422(
            self, api_client, paths_env):
        _wire(api_client, paths_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "distribution_compare",
            {"column": "close", "transform": "ret",
             "group_a": {"start": "2024-02-10T00:00:00Z",
                          "end": "2024-02-15T00:00:00Z"},
             "group_b": {"start": "2024-02-15T00:00:00Z",
                          "end": "2024-02-20T00:00:00Z"}}))
        assert resp.status_code == 422, resp.text
        assert "RESEARCH_PARAM_INVALID" in resp.text
        assert "returns" in resp.text          # legal_range 枚举清单附出


class TestParamGuardHeavyChannel:
    """TD-2 两通道契约后半（specs「数值参数非法结构化拒绝」）：恒 heavy
    模板（pattern_mining）校验拒绝无同步 422 通道——202 → 轮询 failed，
    reason 前缀 [RESEARCH_PARAM_INVALID]（executor 对 ResearchError
    结构化收尾，轮询方可解析错误码）。

    db 保持 fixture 不覆写：executor 单例绑 lifespan 启动库，覆写
    state.db_path 会使任务写入启动库而轮询查覆写库（404 分裂——照
    test_generic_heavy_success_chain :188 形制，只覆盖 data_dir）。
    """

    def test_pattern_mining_bad_param_failed_reason_carries_code(
            self, api_client, paths_env):
        import time

        api_client.app.state.data_dir = paths_env["data_dir"]
        resp = api_client.post("/api/research/analysis", json=_body(
            "pattern_mining",
            {"as_of": "2024-02-15T00:00:00Z", "n_neighbors": 0}))
        assert resp.status_code == 202, resp.text    # 恒 heavy 不因校验变 422
        task_id = resp.json()["task_id"]

        t: dict = {}
        for _ in range(60):  # 轮询到终态（404 为入库竞态窗口，重试）
            poll = api_client.get(
                f"/api/research/analysis/tasks/{task_id}")
            if poll.status_code == 200:
                t = poll.json()
                if t["status"] in ("succeeded", "failed"):
                    break
            time.sleep(0.5)
        assert t.get("status") == "failed", t
        assert t["reason"].startswith("[RESEARCH_PARAM_INVALID]"), t["reason"]
        assert "n_neighbors" in t["reason"]          # 参数名可解析（错误即文档）


class TestHeavyCountVariantFallback:
    """0.3.3 C-β heavy 计数的变体防御路由级锚：变体 parquet（index 名
    非 timestamp）→ 单列计数探测 KeyError/ValueError → 回退全列装载
    （正确性优先于轻量）。与 precheck 侧 TestProbeVariantFallback 同构
    （CARD-05 随行防御）——此处锚路由 heavy 判定点。
    """

    def test_variant_parquet_counts_via_full_load(self, api_client, paths_env,
                                                  tmp_path):
        """500 bar 变体：回退全列装载计数正确 → 非 heavy 快路径 200
        （探测不回退则裸穿 500——回退存在的区分证据）。"""
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market_variant"   # 避让 paths_env 的 market
        data_dir.mkdir()
        make_ohlcv(n=500, freq="4h", start="2024-01-01",
                   seed=9).rename_axis("not_timestamp").to_parquet(
                       data_dir / f"{SYMBOL}_4h.parquet")
        api_client.app.state.db_path = paths_env["db"]
        api_client.app.state.data_dir = data_dir
        resp = api_client.post("/api/research/analysis", json=_body(
            "correlation_scan", {"signal_column": "close"}))
        assert resp.status_code == 200, resp.text
        assert resp.json()["template"] == "correlation_scan"
