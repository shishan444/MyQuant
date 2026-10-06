"""错误形制特征网（fix-mcp-error-channel 6.1，0.3.2）。

specs research-error-channel 七场景逐条锚定——结构化（码+message+
detail）、可自愈（hint 参数面内）、通道一致（同步直返/异步可查）。
共同纪律（D11）：失败用例三段断言——状态+code、detail 关键字段、
禁止副作用（任务表无记录）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_kernel import collect_condition_refs
from core.analysis.hypothesis_service import ResearchError
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr

SYMBOL = "BTCUSDT"

# 必填键参数化（D5 口径：6 模板 11 键逐一缺省）
_MISSING_CASES = [
    pytest.param("condition_stats", ["params", "condition"],
                 {"indicator": "RSI"}, id="condition_stats 缺 params"),
    pytest.param("condition_stats", ["indicator", "condition"],
                 {"params": {"period": 14}}, id="condition_stats 缺 indicator"),
    pytest.param("condition_stats", ["indicator", "params"],
                 {"condition": {"subject": "rsi_14", "action": "gt",
                                "target": 70}},
                 id="condition_stats 缺 condition"),
    pytest.param("distribution_compare", ["group_a", "group_b"],
                 {"column": "close"}, id="distribution_compare 缺 group_a"),
    pytest.param("distribution_compare", ["group_b"],
                 {"column": "close", "group_a": "2024-01"},
                 id="distribution_compare 缺 group_b"),
    pytest.param("distribution_compare", ["column"],
                 {"group_a": "2024-01", "group_b": "2024-02"},
                 id="distribution_compare 缺 column"),
    pytest.param("correlation_scan", ["signal_column"], {},
                 id="correlation_scan 缺 signal_column"),
    pytest.param("event_study", ["condition"], {},
                 id="event_study 缺 condition"),
    pytest.param("pattern_mining", ["as_of"], {},
                 id="pattern_mining 缺 as_of"),
    pytest.param("param_sweep", ["condition"], {"indicator": "RSI"},
                 id="param_sweep 缺 condition"),
    pytest.param("param_sweep", ["indicator"],
                 {"condition": {"subject": "rsi_14", "action": "gt",
                                "target": 70}},
                 id="param_sweep 缺 indicator（语义失真修正锚）"),
]


@pytest.fixture()
def error_env(tmp_path: Path):
    """900 bar 4h 合成数据（含趋势段使金叉可触发）+ 独立 db。"""
    rng = np.random.default_rng(42)
    n = 900
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    # 两段趋势拼接（先降后升）——SMA50/sma_200 交叉必然发生
    seg1 = np.linspace(68000, 58000, 550)   # 谷底 bar 550：金叉落统计段内
    seg2 = np.linspace(58000, 72000, n - 550)
    # n=900：sma_200 金叉需 600 bar warmup 前缀 + 统计段余量
    close = np.concatenate([seg1, seg2]) + rng.normal(0, 120, n)
    df = pd.DataFrame({
        "open": close, "high": close + 100, "low": close - 100,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _wire(client, env):
    client.app.state.db_path = env["db"]
    client.app.state.data_dir = env["data_dir"]


def _body(template, params, **range_extra):
    # start 后移使金叉用例 sma_200 的 600 bar warmup 可达（数据 900 bar，
    # 请求起点后移至序列中段以内即可由前缀覆盖）
    rng = {"symbol": SYMBOL, "timeframe": "4h",
           "start": "2024-04-11T00:00:00Z", "end": "2024-05-01T00:00:00Z"}
    rng.update(range_extra)
    return {"template": template, "params": params, "data_range": rng}


class TestCollectRefsUnit:
    """unit①：collect_condition_refs 双语义（卡2）。"""

    def test_strict_golden_cross_refs_include_target(self):
        cond = {"subject": {"indicator": "SMA", "params": {"period": 50},
                            "field": "sma"},
                "action": "cross_above_series", "target": "sma_200"}
        refs = collect_condition_refs(cond, strict=True)
        names = [ind for ind, _ in refs]
        assert names.count("SMA") == 2
        assert any(ind == "SMA" and p.get("period") == 200
                   for ind, p in refs), "target 侧 sma(200) 并入 refs"

    def test_strict_unresolvable_target_rejected(self):
        from core.analysis.hypothesis_service import ResearchError
        cond = {"subject": "rsi_14", "action": "gt",
                "target": "不存在列_x"}
        with pytest.raises(ResearchError) as ei:
            collect_condition_refs(cond, strict=True)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["param"] == "condition.target"

    def test_lenient_unresolvable_target_skipped(self):
        """掩码路径宽容语义不变：不可解析不并入不抛。"""
        cond = {"subject": "rsi_14", "action": "gt",
                "target": "不存在列_x"}
        assert collect_condition_refs(cond) == [
            ("RSI", {"period": 14})]

    def test_numeric_target_passthrough(self):
        cond = {"subject": "rsi_14", "action": "gt", "target": 70}
        assert collect_condition_refs(cond, strict=True) == [
            ("RSI", {"period": 14})]


class TestRequiredKeys:
    """缺键拒绝参数化 + 免疫防过度收紧（specs「模板必填键前置校验」）。"""

    @pytest.mark.parametrize(
        ("template", "_absent", "params"), _MISSING_CASES)
    def test_missing_key_422_with_name(self, api_client, error_env,
                                       template, _absent, params):
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis",
                               json=_body(template, params))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["param"] == _absent[0], "缺失键名入 detail"
        assert "必填" in detail["detail"]["legal_form"]
        # 禁止副作用：任务表无记录（D11，sqlite 直查）
        import sqlite3
        with sqlite3.connect(error_env["db"]) as conn:
            n_tasks = conn.execute(
                "SELECT COUNT(*) FROM analysis_tasks").fetchone()[0]
        assert n_tasks == 0, "缺键拒绝不得产生任务记录"

    def test_regime_split_no_required_keys_still_works(self, api_client,
                                                       error_env):
        """免疫防过度收紧：regime_split 无必填键最小请求照旧。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "regime_split", {}))
        assert resp.status_code == 200, resp.text

    def test_immune_templates_absent_from_required_table(self):
        """免疫项表级锚（D5）：screen_patterns/regime_split 无必填键零
        成本通过——校验表不得把可选项变必填（表级静态断言零副作用，
        screen_patterns 恒 heavy 不做行为级提交）。"""
        from api.routes.research.analysis import _TEMPLATE_REQUIRED_KEYS
        assert "screen_patterns" not in _TEMPLATE_REQUIRED_KEYS
        assert "regime_split" not in _TEMPLATE_REQUIRED_KEYS


class TestCrossIndicatorLoading:
    """金叉装载（独立对照）与不可达 target（specs「跨指标条件列装载」）。"""

    def test_golden_cross_matches_hand_computed(self, api_client, error_env):
        """金叉样本数 == pandas 手算交叉数（修复前恒 0 即 G9 病灶；
        手算为独立参考——不调被测实现生成期望，对照限 warmup 收敛后
        区间段）。"""
        _wire(api_client, error_env)
        cond = {"subject": {"indicator": "SMA", "params": {"period": 50},
                            "field": "sma"},
                "action": "cross_above_series", "target": "sma_200"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "SMA", "params": {"period": 50},
             "condition": cond, "forward_bars": 24, "min_samples": 1}))
        assert resp.status_code == 200, resp.text
        sample_size = resp.json()["sample_size"]

        # 手算：请求区间内 sma50 上穿 sma_200 的次数（+forward_bars 有效）
        df = error_env["df"]
        start_ts = pd.Timestamp("2024-04-11T00:00:00Z")
        end_ts = pd.Timestamp("2024-05-01T00:00:00Z")
        seg = df.loc[(df.index >= start_ts - pd.Timedelta(hours=4 * 700))
                     & (df.index < end_ts)]
        sma50 = seg["close"].rolling(50).mean()
        sma200 = seg["close"].rolling(200).mean()
        cross = (sma50 > sma200) & (sma50.shift() <= sma200.shift())
        in_range = seg.index >= start_ts
        valid = seg.index < (end_ts - pd.Timedelta(hours=4 * 24))
        expected = int((cross & in_range & valid).sum())
        assert sample_size == expected
        assert sample_size > 0, "修复前恒 0——G9 病灶信号必须消除"

    def test_unresolvable_target_422_not_zero(self, api_client, error_env):
        """不可达 target → 422 结构化拒绝（非 n=0 假统计）。"""
        _wire(api_client, error_env)
        cond = {"subject": "rsi_14", "action": "gt", "target": "不存在列_x"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "RSI", "params": {"period": 14},
             "condition": cond}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert detail["detail"]["value"] == "不存在列_x"

    def test_event_study_unresolvable_target_422(self, api_client,
                                                 error_env):
        """分层契约（D20）：事件模板不可解析 target → 422 显式拒绝。"""
        _wire(api_client, error_env)
        cond = {"subject": "rsi_14", "action": "gt", "target": "不存在列_x"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": cond}))
        assert resp.status_code == 422, resp.text
        # event_study 在模板入口即有 available_cols 显式拒绝（既有契约码，
        # 分层契约 D20 满足「显式拒绝非静默 0」——码形制从模板既有入口）
        assert resp.json()["detail"]["code"] == "RESEARCH_COLUMN_NOT_AVAILABLE"

    # ---- tester 建网补强（specs 场景缺口逐条锚定，2026-10-04）----

    def test_fuzzy_substring_target_rejected(self, api_client, error_env):
        """模糊错配路由级锚（卡3c）：非精确列名（曾以去下划线子串
        命中长列名的错配形态）→ 422 结构化拒绝，非「貌似正常的
        统计」（合法假证据防线——unit 层三锚之外的路由链路钉死）。"""
        _wire(api_client, error_env)
        cond = {"subject": "rsi_14", "action": "gt", "target": "closeprice"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "RSI", "params": {"period": 14},
             "condition": cond}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert detail["detail"]["value"] == "closeprice"

    def test_param_sweep_column_target_422(self, api_client, error_env):
        """spec 显式契约锚：param_sweep 对列名类 target 维持模板层
        显式 422 拒绝（sweep 轴须数值网格——显式拒绝无静默风险）。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "param_sweep",
            {"indicator": "SMA",
             "condition": {"subject": {"indicator": "SMA",
                                       "params": {"period": 50},
                                       "field": "sma"},
                           "action": "gt", "target": "sma_200"},
             "sweep": {"period": [10, 20]}}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["param"] == "condition.target"

    def test_param_sweep_warmup_budget_structured_reject(self, api_client,
                                                         error_env):
        """卡3a 预算层锚：param_sweep 条件引用指标（SMA200）前缀不足
        → 结构化 422 拒绝，非 NaN 假统计（warmup 需求并入装载的
        路由分支证据）。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "param_sweep",
            {"indicator": "SMA",
             "condition": {"subject": {"indicator": "SMA",
                                       "params": {"period": 200},
                                       "field": "sma"},
                           "action": "gt", "target": 60000},
             "sweep": {"period": [100, 200]}},
            start="2024-04-25T00:00:00Z"))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_DATA_INSUFFICIENT"

    def test_inspect_cases_cross_indicator_matches_hand(self, api_client,
                                                        error_env):
        """假象型病灶回归锚（卡3b 覆盖清单）：inspect_cases 跨指标
        条件（decode 放行后求值须现算）→ n_matched 与 pandas 手算
        独立对照（修复前「入口放行+求值不算」恒 0）。"""
        _wire(api_client, error_env)
        cond = {"subject": {"indicator": "SMA", "params": {"period": 50},
                            "field": "sma"},
                "action": "cross_above_series", "target": "sma_200"}
        resp = api_client.post("/api/research/inspect-cases", json={
            "symbol": SYMBOL, "timeframe": "4h",
            "selector": {"condition": cond}, "indicator": "SMA",
            "limit": 3,
            "start": "2024-04-11T00:00:00Z", "end": "2024-05-01T00:00:00Z"})
        assert resp.status_code == 200, resp.text
        n_matched = resp.json()["n_matched"]
        # 手算：与金叉用例同区间同条件（独立参考，禁调被测实现）
        df = error_env["df"]
        start_ts = pd.Timestamp("2024-04-11T00:00:00Z")
        end_ts = pd.Timestamp("2024-05-01T00:00:00Z")
        seg = df.loc[(df.index >= start_ts - pd.Timedelta(hours=4 * 700))
                     & (df.index < end_ts)]
        sma50 = seg["close"].rolling(50).mean()
        sma200 = seg["close"].rolling(200).mean()
        cross = (sma50 > sma200) & (sma50.shift() <= sma200.shift())
        expected = int((cross & (seg.index >= start_ts)).sum())
        assert n_matched == expected
        assert n_matched > 0, "假象型病灶（恒 0）必须消除"

    def test_regime_split_cross_indicator_covered(self, api_client,
                                                  error_env):
        """D14 留档断言：regime_split 求值链经 _mask_for（3b 一处修复
        自动覆盖）——跨指标条件在分段统计中真实求值。"""
        _wire(api_client, error_env)
        cond = {"subject": {"indicator": "SMA", "params": {"period": 50},
                            "field": "sma"},
                "action": "cross_above_series", "target": "sma_200"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "regime_split",
            {"indicator": "SMA", "condition": cond, "forward_bars": 8}))
        assert resp.status_code == 200, resp.text
        by_seg = resp.json()["payload"]["condition_by_segment"]
        assert by_seg, "跨指标条件须进入分段求值"
        assert sum(s.get("samples", 0) for s in by_seg) > 0, \
            "跨指标 target 不得静默 0（同病随 3b 治愈）"

    def test_event_study_short_window_events_zero_422(self, api_client,
                                                      error_env):
        """event_study 短区间跨指标（缺陷闭环终态）：请求区间短于
        target lookup 时 sma_200 全 None（CARD-07 收口 float 全 NaN，
        比较安全全 False）→ 事件数 0 如实 → 小样本业务拒绝 422
        （CARD-10 收编）——D20 分层契约「可解析性决定 422、区间
        位置决定事件数」全链落地：非 TypeError 500、非 NaN 序列化
        崩、非静默 0 样本。原现状钉（TypeError→500）随两级收编
        改写消号。"""
        _wire(api_client, error_env)
        cond = {"subject": {"indicator": "SMA", "params": {"period": 50},
                            "field": "sma"},
                "action": "cross_above_series", "target": "sma_200"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": cond, "min_events": 1,
                            "window": 4}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["n_events"] == 0, \
            "事件数如实（区间未收敛 → 0，非静默假统计）"
        assert detail["detail"]["min_events"] == 1

    def test_event_study_full_window_events_vs_hand(self, api_client,
                                                    error_env):
        """event_study 全区间跨指标（CARD-08 后行为）：200 + 事件数
        与 pandas 手算独立对照（对照限 warmup 收敛后区间段——无
        warmup 前缀装载下 sma_200 于区间内 bar 199 起收敛，NaN 段
        交叉公式天然无命中，A4 既有边界）；单事件时 std/p_value 为
        可检性 None（CARD-08 防线——NaN 会在响应序列化 allow_nan=
        False 裸崩，200 本身即无 NaN 出站证据）。"""
        _wire(api_client, error_env)
        cond = {"subject": {"indicator": "SMA", "params": {"period": 50},
                            "field": "sma"},
                "action": "cross_above_series", "target": "sma_200"}
        resp = api_client.post("/api/research/analysis", json=_body(
            "event_study", {"condition": cond, "min_events": 1,
                            "window": 4},
            start="2024-01-01T00:00:00Z", end="2024-06-01T00:00:00Z"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        n_events = body["payload"]["n_events"]
        # 手算：合成数据全量 900 bar 上 sma50 上穿 sma_200（独立参考）
        df = error_env["df"]
        sma50 = df["close"].rolling(50).mean()
        sma200 = df["close"].rolling(200).mean()
        cross = (sma50.shift() < sma200.shift()) & (sma50 >= sma200)
        expected = int(cross.sum())
        assert n_events == expected
        assert n_events > 0, "可解析 target 现算后事件数如实（非静默 0）"
        # CARD-08 可检性 None 语义（单事件无自由度）
        if n_events == 1:
            assert body["confidence"]["p_value"] is None
            assert body["payload"]["terminal_return"]["std"] is None


class TestValueDomainAndAsyncTerminal:
    """阶段四盲区消解：值域拒绝与异步终态全链（coverage 定向补面）。"""

    def test_unknown_template_422(self, api_client, error_env):
        """未知模板结构化拒绝（路由 _TEMPLATES 白名单先行 422）。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "no_such_template", {}))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "RESEARCH_TEMPLATE_UNKNOWN"

    def test_run_template_unknown_template_defensive(self, error_env):
        """_run_template 的 RESEARCH_TEMPLATE_UNKNOWN 兜底分支直调
        （路由白名单后不可达的防御锚——白名单与兜底集合一致性由
        test_errors.py 的 hint 清单对拍另行锁定）。"""
        from api.routes.research.analysis import _run_template
        from api.schemas import AnalysisRunRequest, DataRangeItem
        payload = AnalysisRunRequest(
            template="nope", params={},
            data_range=DataRangeItem(
                symbol=SYMBOL, timeframe="4h",
                start="2024-04-11T00:00:00Z", end="2024-05-01T00:00:00Z"))
        with pytest.raises(ResearchError) as ei:
            _run_template(payload, error_env["data_dir"], error_env["db"])
        assert ei.value.code == "RESEARCH_TEMPLATE_UNKNOWN"

    def test_unknown_indicator_422(self, api_client, error_env):
        """indicator 值域前置拒绝（_require_known_indicator 三消费方
        共用通道——缺键校验之外的值域防线）。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "param_sweep",
            {"indicator": "NOPE",
             "condition": {"subject": "rsi_14", "action": "gt", "target": 70},
             "sweep": {"period": [10]}}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["param"] == "indicator"
        assert "NOPE" in detail["message"]

    def test_param_sweep_success_logs_sweep(self, api_client, error_env):
        """param_sweep 路由级成功 200 + 研究日志 sweep 留痕
        （_log_sweep 路由通道——值阈值类条件走快路径）。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "param_sweep",
            {"indicator": "SMA",
             "condition": {"subject": {"indicator": "SMA",
                                       "params": {"period": 200},
                                       "field": "sma"},
                           "action": "gt", "target": 60000},
             "sweep": {"values": [60000, 62000, 64000]}},
            start="2024-04-11T00:00:00Z", end="2024-05-01T00:00:00Z"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["template"] == "param_sweep"
        # _log_sweep 形制：entry_type="sweep"+ref_id=条件指纹（不双写
        # analysis 日志——第 3/5 步职责边界，_log_sweep docstring）
        logs = rr.list_research_log(error_env["db"], entry_type="sweep")
        assert logs, "sweep 成功留痕进入研究日志（entry_type=sweep）"

    def test_screen_patterns_full_chain_succeeded(self, api_client,
                                                  error_env):
        """screen_patterns 恒 heavy 全链终态（job 体+完成回调留痕）：
        202 → 轮询 succeeded（db 保持 fixture——executor 单例绑它，
        照 test_research_analysis_paths:185 形制）。"""
        import time
        api_client.app.state.data_dir = error_env["data_dir"]
        resp = api_client.post("/api/research/analysis", json=_body(
            "screen_patterns", {}))
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 30.0
        terminal = None
        while time.monotonic() < deadline:
            t = api_client.get(f"/api/research/analysis/tasks/{task_id}")
            if t.status_code == 200:
                tb = t.json()
                if tb["status"] in ("succeeded", "failed"):
                    terminal = tb
                    break
            time.sleep(0.3)
        assert terminal is not None, "30s 内未到终态"
        assert terminal["status"] == "succeeded", terminal.get("reason")

    def test_param_sweep_heavy_path_logs_sweep(self, api_client, tmp_path):
        """param_sweep heavy 路径（>10000 bar）：202 → 终态 succeeded →
        sweep 留痕经 job 完成回调落库（异步 _log_sweep 通道）。"""
        import time
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir()
        make_ohlcv(n=10500, freq="4h", start="2020-01-01",
                   seed=5).to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        api_client.app.state.data_dir = data_dir
        db = Path(api_client.app.state.db_path)
        resp = api_client.post("/api/research/analysis", json={
            "template": "param_sweep",
            "params": {"indicator": "RSI",
                       "condition": {"subject": "rsi_14", "action": "gt",
                                     "target": 70},
                       "sweep": {"values": [60, 70]}},
            "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                           "start": "2020-02-15T00:00:00Z",
                           "end": "2024-11-01T00:00:00Z"}})
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["task_id"]
        deadline = time.monotonic() + 60.0
        terminal = None
        while time.monotonic() < deadline:
            t = api_client.get(f"/api/research/analysis/tasks/{task_id}")
            if t.status_code == 200:
                tb = t.json()
                if tb["status"] in ("succeeded", "failed"):
                    terminal = tb
                    break
            time.sleep(0.3)
        assert terminal is not None, "60s 内未到终态"
        assert terminal["status"] == "succeeded", terminal.get("reason")
        logs = rr.list_research_log(db, entry_type="sweep")
        assert logs, "异步 sweep 留痕进入研究日志"


class TestFailureDirectReturn:
    """同步失败直返三段断言 + 失败日志留痕（specs「同步失败结构化直返」）。"""

    def test_internal_failure_500_structured_with_log(self, api_client,
                                                      error_env,
                                                      monkeypatch):
        """内部失败：500 三段、无伪 task、任务表查无、研究日志含留痕
        （D15/D17）+ message 干净（specs message 通道禁裸异常文本；
        detail.summary 诊断字段允许保留）。"""
        _wire(api_client, error_env)

        def _boom(*a, **kw):
            raise ValueError("内部炸点")

        import api.routes.research.analysis as ana
        monkeypatch.setattr(ana, "_run_template", _boom)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "RSI", "params": {"period": 14},
             "condition": {"subject": "rsi_14", "action": "gt",
                           "target": 70}}))
        assert resp.status_code == 500, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_TEMPLATE_INTERNAL"
        assert detail["detail"]["template"] == "condition_stats"
        assert detail["detail"]["exception_type"] == "ValueError"
        assert "内部炸点" in detail["detail"]["summary"]
        assert "内部炸点" not in detail["message"], "message 通道保持干净"
        # 无伪 task_id：响应体是错误三段非任务 dict
        assert "task_id" not in detail
        # 禁止副作用：任务表查无（D11，sqlite 直查）
        import sqlite3
        with sqlite3.connect(error_env["db"]) as conn:
            n_tasks = conn.execute(
                "SELECT COUNT(*) FROM analysis_tasks").fetchone()[0]
        assert n_tasks == 0, "内部失败不得产生任务记录"
        # 失败留痕（D15）：研究日志含失败码
        logs = rr.list_research_log(error_env["db"], entry_type="analysis")
        assert any("RESEARCH_TEMPLATE_INTERNAL" in e["summary"]
                   for e in logs), "失败留痕进入研究日志"

    def test_business_failure_direct_return(self, api_client, error_env):
        """业务失败（min_samples 高于可触发量）同步直返 422 三段 +
        零任务记录（specs S1「样本量不足」场景——CARD-10 裁决方向 a
        落地：小样本属业务拒绝，422 诱导改参自愈而非 500 诱导报告；
        原现状钉 500 随收编改写消号）。"""
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "RSI", "params": {"period": 14},
             "condition": {"subject": "rsi_14", "action": "gt",
                           "target": 70}, "min_samples": 9999}))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert "小样本" in detail["message"], "失败原因可见"
        assert detail["detail"]["min_samples"] == 9999
        assert "task_id" not in detail
        # 禁止副作用：任务表无记录（D11）
        import sqlite3
        with sqlite3.connect(error_env["db"]) as conn:
            n_tasks = conn.execute(
                "SELECT COUNT(*) FROM analysis_tasks").fetchone()[0]
        assert n_tasks == 0

    def test_log_persistence_failure_never_breaks_success(self, api_client,
                                                          error_env,
                                                          monkeypatch,
                                                          caplog):
        """CARD-02 留痕防线（成功路径）：研究日志写入失败（如库锁）
        降级 warning，不反噬主通道——结果照常 200 返回。"""
        import logging
        import sqlite3
        import api.routes.research.analysis as ana

        def _locked(*a, **kw):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(ana.rr, "append_research_log", _locked)
        _wire(api_client, error_env)
        with caplog.at_level(logging.WARNING, logger="api.routes.research.analysis"):
            resp = api_client.post("/api/research/analysis", json=_body(
                "condition_stats",
                {"indicator": "RSI", "params": {"period": 14},
                 "condition": {"subject": "rsi_14", "action": "gt",
                               "target": 30}, "min_samples": 1}))
        assert resp.status_code == 200, f"留痕失败不得反噬成功路径: {resp.text}"
        assert resp.json()["sample_size"] >= 0
        assert any("分析留痕写入失败" in r.message for r in caplog.records), \
            "降级 warning 须留审计线索（template+failure 可见）"

    def test_log_persistence_failure_never_breaks_error(self, api_client,
                                                        error_env,
                                                        monkeypatch):
        """CARD-02 留痕防线（失败路径）：内部失败叠加上述留痕写库失败
        ——结构化 500 三段照常出站，message 不被 sqlite 异常污染。"""
        import sqlite3
        import api.routes.research.analysis as ana

        def _locked(*a, **kw):
            raise sqlite3.OperationalError("database is locked")

        def _boom(*a, **kw):
            raise ValueError("内部炸点")

        monkeypatch.setattr(ana.rr, "append_research_log", _locked)
        monkeypatch.setattr(ana, "_run_template", _boom)
        _wire(api_client, error_env)
        resp = api_client.post("/api/research/analysis", json=_body(
            "condition_stats",
            {"indicator": "RSI", "params": {"period": 14},
             "condition": {"subject": "rsi_14", "action": "gt",
                           "target": 70}}))
        assert resp.status_code == 500, f"留痕失败不得反噬错误通道: {resp.text}"
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_TEMPLATE_INTERNAL"
        assert detail["detail"]["exception_type"] == "ValueError"
        assert "database is locked" not in detail["message"], \
            "message 通道保持干净（sqlite 降级细节不上浮）"

    def test_async_failure_real_heavy_path_pollable(self, api_client,
                                                    error_env):
        """异步失败可查真实链路（specs「异步任务失败可轮询」S6 的
        WHEN 全程）：pattern_mining 恒 heavy 判定 → 202+task_id →
        轮询终态 failed 且 reason 可读（404 语义只属已消灭的伪 task）。

        db 保持 fixture（executor 单例在 startup 绑它——照
        test_research_analysis_paths.py:185 形制；只覆盖 data_dir）。"""
        import time
        api_client.app.state.data_dir = error_env["data_dir"]
        resp = api_client.post("/api/research/analysis", json=_body(
            "pattern_mining", {"as_of": "2030-01-01T00:00:00Z"}))
        assert resp.status_code == 202, resp.text
        body = resp.json()
        task_id = body["task_id"]
        assert task_id and not task_id.startswith("fast_"), \
            "异步句柄不得为 fast_* 伪形态"
        deadline = time.monotonic() + 15.0
        terminal = None
        while time.monotonic() < deadline:
            t = api_client.get(f"/api/research/analysis/tasks/{task_id}")
            if t.status_code == 200:
                tb = t.json()
                if tb["status"] in ("succeeded", "failed"):
                    terminal = tb
                    break
            time.sleep(0.2)
        assert terminal is not None, "15s 内未到终态"
        assert terminal["status"] == "failed"
        assert "as_of" in (terminal.get("reason") or ""), \
            "失败原因可查（reason 携带模板拒绝语义）"

    def test_async_failure_pollable_regression(self, api_client, error_env):
        """异步失败可查回归锚（specs「异步失败可查性」）：路由层钉
        轮询通道——预置 failed 终态任务行经 GET 取回（异步执行语义由
        tests/analysis/test_executor.py TestAsyncPath 域测试覆盖；404
        语义只属已消灭的伪 task）。"""
        _wire(api_client, error_env)
        task_id = "ant_errchn_async_001"
        rr.save_analysis_task(
            error_env["db"], task_id=task_id, template="pattern_mining",
            params="{}")
        rr.update_analysis_task(
            error_env["db"], task_id=task_id, status="failed",
            reason="[BOOM] 异步失败可查锚")
        resp = api_client.get(f"/api/research/analysis/tasks/{task_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert "BOOM" in (body.get("reason") or "")
        # 已消灭的伪 task 形态：未知 id 轮询 404（结构化），非死循环素材
        resp404 = api_client.get(
            "/api/research/analysis/tasks/fast_condition_stats")
        assert resp404.status_code == 404


class TestDataRangeTimestampValidation:
    """CARD-05 特征网：DataRangeItem 时间串可解析性前置校验——单点
    收敛三个消费模型（analysis.data_range / hypotheses.data_ranges /
    validate-backtest.request_range）。非法串在 schema 层 422（错误码
    +合法形 hint），修复前直达装载层在 pandas 切片比较处抛 TypeError
    裸穿 500。校验在业务前，不触达 db/data——无需 wire。
    """

    def _assert_param_invalid_422(self, resp):
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # pydantic 校验拒绝形制：数组（业务 ResearchError 为 dict 三段）
        assert isinstance(detail, list) and detail
        msgs = " ".join(str(e.get("msg", "")) for e in detail)
        assert "RESEARCH_PARAM_INVALID" in msgs, msgs
        assert "ISO8601" in msgs and "2024-01-01" in msgs  # 合法形 hint 在场

    def test_invalid_timestamp_three_endpoints(self, api_client):
        """三消费端点参数化：任一 DataRangeItem 字段非法串 → 422 且
        msg 含错误码与 hint。"""
        cases = [
            ("/api/research/analysis", {
                "template": "correlation_scan",
                "params": {"signal_column": "close"},
                "data_range": {"symbol": "BTCUSDT", "timeframe": "1h",
                               "start": "not-a-date",
                               "end": "2024-01-20T00:00:00Z"}}),
            ("/api/research/hypotheses", {
                "conditions": {
                    "when_conditions": [{"subject": "close", "action": "gt",
                                         "target": 0}],
                    "then_conditions": [{"subject": "close", "action": "gt",
                                         "target": -1}]},
                "statement": "非法时间串锚",
                "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                                 "start": "2024-01-01T00:00:00Z",
                                 "end": "2024-13-45T00:00:00Z"}]}),
            ("/api/research/validate-backtest", {
                "hypothesis_id": "hyp_any", "strategy_id": "st_any",
                "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                                  "start": "2024-01-01",
                                  "end": "不是时间"}}),
        ]
        for path, body in cases:
            resp = api_client.post(path, json=body)
            self._assert_param_invalid_422(resp)

    def test_naive_form_passes_validation_to_business(self, api_client):
        """合法 naive 形制不被误拒：hypotheses 端点 naive 日期直达
        业务层成功创建（校验器放行 naive——消费端按 UTC 解释）。"""
        resp = api_client.post("/api/research/hypotheses", json={
            "conditions": {
                "when_conditions": [{"subject": "close", "action": "gt",
                                     "target": 0}],
                "then_conditions": [{"subject": "close", "action": "gt",
                                     "target": -1}]},
            "statement": "naive 合法形制锚",
            "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                             "start": "2024-01-01",
                             "end": "2024-03-01"}]})
        assert resp.status_code in (200, 201), resp.text
        assert resp.json().get("hypothesis_id")

    def test_analysis_naive_reaches_business_not_schema(self, api_client):
        """analysis 端点 naive 形制过校验层：响应非 pydantic 数组
        （业务层结果——dict 三段结构或正常 payload）。"""
        resp = api_client.post("/api/research/analysis", json={
            "template": "correlation_scan",
            "params": {"signal_column": "close"},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "1h",
                           "start": "2024-01-01",
                           "end": "2024-01-20"}})
        assert resp.status_code != 422 or not isinstance(
            resp.json()["detail"], list), resp.text
