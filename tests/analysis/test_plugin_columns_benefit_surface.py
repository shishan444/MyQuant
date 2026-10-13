"""插件列装载特征测试网补充（fix-research-c3 · C3.4 tester 组）。

与 test_plugin_columns_loading.py（A4 主锚+机制五步）互补，本文件承载
design §2-§6 的受益面与接线特征：

- 受益面四分支（condition_stats 旧键/param_sweep/counter_profile/
  event_study conditions 形制）经 _load_refs 自动获得插件列支持各一；
- event_study 旧键接线：长区间走通 + 短区间「无前扩自然 NaN vs
  conditions 前扩收敛」行为差异锚（键形制冻结锚的观测面）；
- 两码分工链路级：NOT_FOUND detail 附缺失列 / NOT_AVAILABLE 经完整
 装载链（非直调 verify）/ db_path 缺失不静默；
- 空插件请求逐字节冻结强锚：与 load_with_warmup 直调 assert_frame_equal；
- warmup 两维数值锚：插件 lookback>200 抬升（idx=lookback）与
  <200 不抬升（idx=200）+ shift 语义精确值；
- inspect_cases 接线补全：显式 start 走通 / 插件维度前缀不足
  WARMUP_INSUFFICIENT / 豁免与 subject 边界的非插件对照；
- 登记链回归：precheck 与装载链同码同 detail（两入口单一判定面）
  + warmup 同源；
- 零留痕（422 拒绝不写 research_log）+ MCP 工具层透传（A8）+
  R5 披露快照锚。

数据集锚（真实 BTCUSDT 4h，2024-01-01 起 5954 行）：2024-03-01 前
360 bar、2024-11-01 前 730 bar；EMA 注册表上限 period=200 →
derive_warmup(["EMA"])=600（受益面带顶层 indicator 的分支区间起点
须 ≥2024-11-01）。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]

MARKET_DIR = Path("data/market")

if not (MARKET_DIR / "BTCUSDT_4h.parquet").exists():
    pytest.skip("真实数据 BTCUSDT 4h 不在库（data/market），插件列特征网跳过",
                allow_module_level=True)

from api.routes.research.analysis import _load_with_condition_refs
from core.analysis.case_inspection import inspect_cases
from core.analysis.errors import ResearchError
from core.analysis.executor import ensure_research_tables
from core.analysis.plugin_service import register_formula_plugin
from core.analysis.registry_meta import derive_warmup_full
from core.analysis.templates.precheck_hypothesis import (
    run_registration_precheck,
)
from core.analysis.warmup_loader import load_with_warmup
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import count_research_log

COND = {"subject": "fx_fx_dev20_pct", "action": "lt", "target": -0.01}
# 受益面带顶层 indicator 的分支：EMA warmup 600 bar，起点 2024-11-01
# （前 730 bar 充足）
RNG_IND = {"symbol": "BTCUSDT", "timeframe": "4h",
           "start": "2024-11-01", "end": "2026-04-01"}
# conditions 形制（无顶层 indicator，warmup 预算 200/插件维）
RNG = {"symbol": "BTCUSDT", "timeframe": "4h",
       "start": "2024-03-01", "end": "2026-04-01"}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("c3net")
    p = d / "q.db"
    init_db_ext(p)
    ensure_research_tables(p)
    # dev20：lookback 20（<200 floor，不抬升锚）；dev260：260（>200，
    # 抬升锚）；dev700：700（inspect_cases 插件维度前缀不足归因锚）
    register_formula_plugin(p, name="fx_dev20_pct",
                            expression="(close - shift(close, 20)) / shift(close, 20)")
    register_formula_plugin(p, name="fx_dev260",
                            expression="shift(close, 260)")
    register_formula_plugin(p, name="fx_dev700",
                            expression="shift(close, 700)")
    return p


class TestBenefitSurface:
    """受益面四分支（design §2）：经 _load_refs 的模板分支一次获得。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        import shutil
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        shutil.copy(MARKET_DIR / "BTCUSDT_4h.parquet",
                    data_dir / "BTCUSDT_4h.parquet")
        p = tmp_path / "q.db"
        init_db_ext(p)
        ensure_research_tables(p)
        register_formula_plugin(p, name="fx_dev20_pct",
                                expression="(close - shift(close, 20)) / shift(close, 20)")
        return {"data_dir": data_dir, "db": p}

    def _post(self, api_client, env, template, params, rng):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        return api_client.post("/api/research/analysis", json={
            "template": template, "params": params, "data_range": rng})

    def test_condition_stats_legacy_key_plugin_condition(self, api_client, env):
        """condition_stats 旧键形制（analysis.py 装载分支）：插件列
        subject 的 condition 求值出非零样本（修复前 0 样本静默）。"""
        r = self._post(api_client, env, "condition_stats", {
            "indicator": "EMA", "params": {"period": 20},
            "condition": COND}, RNG_IND)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["payload"]["hit_count"] > 0

    def test_param_sweep_plugin_condition(self, api_client, env):
        """param_sweep（:338 装载分支）：插件列条件参与扫描统计——
        刻度表不覆盖插件 subject（既有形制：显式 values），points
        逐点位携带非零样本（装载+求值双通的观测面）。"""
        r = self._post(api_client, env, "param_sweep", {
            "indicator": "EMA", "condition": COND,
            "sweep": {"axis": "target", "values": [0.0, -0.01, -0.05]}},
            RNG_IND)
        assert r.status_code == 200, r.text[:300]
        points = r.json()["payload"]["points"]
        assert len(points) == 3
        counts = [p["n"] if "n" in p else p.get("samples")
                  for p in points]
        assert all(c and c > 0 for c in counts), points

    def test_counter_profile_plugin_conditions(self, api_client, env):
        """counter_profile（:403 装载分支）：插件列 conditions 求值
        出非空分组（extra_names=feature_matrix 并入同一装载）。"""
        r = self._post(api_client, env, "counter_profile", {
            "conditions": [COND], "direction": "long"}, RNG_IND)
        assert r.status_code == 200, r.text[:300]
        payload = r.json()["payload"]
        groups = payload["groups"]
        assert groups, "插件列条件应产出分组"

    def test_event_study_conditions_key_plugin(self, api_client, env):
        """event_study conditions 形制（:499 前扩装载分支）：插件列
        条件出非零事件（warmup_bars 披露键存在——前扩形制标志）。"""
        r = self._post(api_client, env, "event_study",
                       {"conditions": [COND]}, RNG)
        assert r.status_code == 200, r.text[:300]
        payload = r.json()["payload"]
        assert payload["n_events"] > 0
        assert "warmup_bars" in payload


class TestEventStudyLegacyKeyWiring:
    """event_study 旧键接线（design §3-①：普通装载后 enrich，无前扩）。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        import shutil
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        shutil.copy(MARKET_DIR / "BTCUSDT_4h.parquet",
                    data_dir / "BTCUSDT_4h.parquet")
        p = tmp_path / "q.db"
        init_db_ext(p)
        ensure_research_tables(p)
        register_formula_plugin(p, name="fx_dev20_pct",
                                expression="(close - shift(close, 20)) / shift(close, 20)")
        register_formula_plugin(p, name="fx_dev400",
                                expression="shift(close, 400)")
        return {"data_dir": data_dir, "db": p}

    def _post(self, api_client, env, params, rng):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        return api_client.post("/api/research/analysis", json={
            "template": "event_study", "params": params,
            "data_range": rng})

    def test_legacy_key_long_range_events(self, api_client, env):
        """长区间：旧键形制插件列条件走通——enrich 后 require_column
        放行+条件求值出事件。"""
        r = self._post(api_client, env, {"condition": COND}, RNG)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["payload"]["n_events"] > 0

    def test_legacy_key_no_prefix_vs_conditions_prefix(self, api_client, env):
        """键形制冻结锚的行为差异（design §3-①「插件列头部收敛不足
        自然 NaN」）：短区间（84 bar < 插件 lookback 400）同条件对照
        ——旧键无前扩→插件列区间内全 NaN→0 事件小样本拒绝；
        conditions 形制前扩收敛→出事件。"""
        short = {"symbol": "BTCUSDT", "timeframe": "4h",
                 "start": "2024-06-01", "end": "2024-06-15"}
        cond400 = {"subject": "fx_fx_dev400", "action": "lt",
                   "target": 1e12}
        legacy = self._post(api_client, env, {"condition": cond400}, short)
        assert legacy.status_code == 422
        body = legacy.json()["detail"]
        assert body["code"] == "RESEARCH_PARAM_INVALID"
        assert body["detail"]["n_events"] == 0, \
            "旧键无前扩：插件列全 NaN 应投影为 0 事件"

        prefixed = self._post(
            api_client, env, {"conditions": [cond400]}, short)
        assert prefixed.status_code == 200, prefixed.text[:300]
        assert prefixed.json()["payload"]["n_events"] > 0, \
            "conditions 形式前扩：插件列收敛应出事件"


class TestTwoCodeDivisionChainLevel:
    """两码分工链路级（design §1-⑤/§4）——经完整装载链而非直调。"""

    def test_not_found_detail_names_missing_columns(self, db):
        """「名字写错」码 detail.plugin_columns 附缺失列清单（契约
        detail 承诺，非仅 code）。"""
        bad = {"subject": "fx_typo_col", "action": "lt", "target": 0}
        with pytest.raises(ResearchError) as ei:
            _load_with_condition_refs(MARKET_DIR, RNG, None,
                                      conditions=[bad], db_path=db)
        assert ei.value.code == "RESEARCH_PLUGIN_COLUMN_NOT_FOUND"
        assert ei.value.detail["plugin_columns"] == ["fx_typo_col"]

    def test_eval_failed_through_full_chain(self, tmp_path):
        """「插件坏了」码经完整装载链：outer 可达（静态过）但依赖列
        已删→enrich 静默缺列→⑤存在性校验结构化拒绝——五步顺序接线
        的链路证据（test_plugin_columns_loading 仅直调 verify）。"""
        import sqlite3
        p = tmp_path / "bad.db"
        init_db_ext(p)
        ensure_research_tables(p)
        register_formula_plugin(p, name="inner",
                                expression="shift(close, 1)")
        register_formula_plugin(p, name="outer",
                                expression="fx_inner + 0")
        conn = sqlite3.connect(p)
        conn.execute("DELETE FROM signal_registry WHERE column_name='fx_inner'")
        conn.commit()
        conn.close()
        cond = {"subject": "fx_outer", "action": "gt", "target": 0}
        with pytest.raises(ResearchError) as ei:
            _load_with_condition_refs(MARKET_DIR, RNG, None,
                                      conditions=[cond], db_path=p)
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"
        assert ei.value.detail["missing_columns"] == ["fx_outer"]
        assert ei.value.detail["reason"] == "plugin_eval_failed"

    def test_missing_db_path_not_silent(self, db):
        """插件列条件而 db_path 缺失（接线不变量）：结构化拒绝而非
        静默跳过插件维度（design §1「可达性校验/求值注入需读库」）。"""
        with pytest.raises(ResearchError) as ei:
            _load_with_condition_refs(MARKET_DIR, RNG, None,
                                      conditions=[COND], db_path=None)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "db_path"


class TestEmptyPluginByteIdentical:
    """空插件列请求逐字节不变（design §1「逐字节冻结」）：无插件
    条件时与原装载路径（load_with_warmup 直调）完全一致。"""

    BASE_COND = {"subject": "close", "action": "gt", "target": 50000}

    def test_conditions_form_identical_to_plain_load(self, db):
        df1, rs1 = _load_with_condition_refs(
            MARKET_DIR, RNG, None, conditions=[self.BASE_COND], db_path=db)
        df2, rs2 = load_with_warmup(
            MARKET_DIR, "BTCUSDT", "4h",
            RNG["start"], RNG["end"], [])
        pd.testing.assert_frame_equal(df1, df2)
        assert rs1 == rs2

    def test_single_condition_form_identical_to_plain_load(self, db):
        """单条件形制（indicator+condition，docstring「逐字节不变」
        承诺面）：warm_names 含 EMA（600 bar 预算）同对照。"""
        df1, rs1 = _load_with_condition_refs(
            MARKET_DIR, RNG_IND, "EMA", condition=self.BASE_COND, db_path=db)
        df2, rs2 = load_with_warmup(
            MARKET_DIR, "BTCUSDT", "4h",
            RNG_IND["start"], RNG_IND["end"], ["EMA"])
        pd.testing.assert_frame_equal(df1, df2)
        assert rs1 == rs2


class TestWarmupTwoDimNumericAnchor:
    """warmup 两维数值锚（design §1-③）：插件 lookback 对前扩的
    抬升/不抬升可直接在装载产物上观测。"""

    def _load(self, db, subject):
        cond = {"subject": subject, "action": "lt", "target": 1e12}
        df, rs = _load_with_condition_refs(
            MARKET_DIR, RNG, None, conditions=[cond], db_path=db)
        idx = df.index.get_loc(rs)
        return df, idx

    def test_plugin_lookback_over_floor_lifts_prefix(self, db):
        """lookback 260 > 200 floor：前扩抬升至 260——request_start
        位于第 260 bar（数据起点起），且 shift(close,260) 语义值
        == 260 bar 前的 close（恰收敛数值锚；未接两维时 idx=200、
        iloc 越界取尾值，断言必败）。"""
        df, idx = self._load(db, "fx_fx_dev260")
        assert idx == 260
        assert df["fx_fx_dev260"].iloc[260] == pytest.approx(
            df["close"].iloc[0])
        assert pd.notna(df["fx_fx_dev260"].iloc[260])

    def test_plugin_lookback_under_floor_keeps_200(self, db):
        """lookback 20 < 200 floor：不抬升（前扩恰 200）+ 语义值
        == 20 bar 前的 close。"""
        df, idx = self._load(db, "fx_fx_dev20_pct")
        assert idx == 200
        expected = ((df["close"].iloc[200] - df["close"].iloc[180])
                    / df["close"].iloc[180])
        assert df["fx_fx_dev20_pct"].iloc[200] == pytest.approx(expected)


class TestInspectCasesWiringExtra:
    """inspect_cases 接线补全（design §3-②）。"""

    def test_explicit_start_with_indicator_converged(self, db):
        """显式 start + 插件列条件（indicator 显式传）：load_with_warmup
        路径走通，案例携带插件列值且非全 NaN（头部收敛）。"""
        res = inspect_cases(db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
                            selector={"condition": COND}, limit=2,
                            context_bars=4, indicator="EMA",
                            start="2024-11-01", end="2025-02-01")
        body = res if isinstance(res, dict) else res.to_dict()
        cases = body.get("cases") or body.get("payload", {}).get("cases")
        assert cases, f"显式 start 插件列条件应出案例集: {sorted(body)[:10]}"

        def _find(o):
            if isinstance(o, dict):
                if "fx_fx_dev20_pct" in o:
                    return o["fx_fx_dev20_pct"]
                for v in o.values():
                    r = _find(v)
                    if r is not None:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = _find(v)
                    if r is not None:
                        return r
            return None

        col_vals = _find(body)
        assert col_vals is not None, "案例应携带插件列值"
        vals = (col_vals if isinstance(col_vals, list)
                else list(col_vals.values()) if isinstance(col_vals, dict)
                else [col_vals])
        assert any(v is not None and v == v for v in vals), \
            "显式 start 装载后插件列应收敛非全 NaN"

    def test_explicit_start_plugin_dim_insufficient(self, db):
        """插件维度前缀不足归因锚：start=2024-04-12（前 612 bar）
        满足 EMA 600 但不足插件 700 → RESEARCH_WARMUP_INSUFFICIENT
        且 warmup_bars=700（两维取 max 的直接证据）。"""
        with pytest.raises(ResearchError) as ei:
            inspect_cases(db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
                          selector={"condition": {
                              "subject": "fx_fx_dev700", "action": "lt",
                              "target": 1e12}},
                          limit=2, context_bars=4, indicator="EMA",
                          start="2024-04-12", end="2024-06-01")
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        assert ei.value.detail["warmup_bars"] == 700

    def test_non_plugin_subject_still_requires_indicator(self, db):
        """豁免边界对照：非插件字符串 subject（基础列 close）不传
        indicator → 必填拒绝保持（豁免只对插件列）。"""
        with pytest.raises(ResearchError) as ei:
            inspect_cases(db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
                          selector={"condition": {
                              "subject": "close", "action": "gt",
                              "target": 50000}},
                          limit=2, context_bars=4)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "indicator"

    def test_non_plugin_unknown_subject_boundary_kept(self, db):
        """subject 边界对照：合法列引用（基础列 volume）但不属于
        indicator 输出列 → RESEARCH_COLUMN_NOT_AVAILABLE 保持
        （插件列放行未放宽一般边界；纯乱名列名在更早的
        ColumnRef 解析层即 RESEARCH_CONDITION_INVALID，非本锚面）。"""
        with pytest.raises(ResearchError) as ei:
            inspect_cases(db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
                          selector={"condition": {
                              "subject": "volume", "action": "gt",
                              "target": 1}},
                          limit=2, context_bars=4, indicator="EMA")
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"

    def test_explicit_start_plugin_exempt_expected_ok(self, db):
        """插件列 subject 豁免 + 显式 start：契约预期正常出案例集。
        （P1 已修——fix/research-c7 装载分支复用 _warm_names 归一，
        原 xfail(strict) 锚 XPASS 翻转转正）"""
        inspect_cases(db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
                      selector={"condition": COND}, limit=2, context_bars=4,
                      start="2024-11-01", end="2025-02-01")


class TestRegistryChainRegression:
    """登记链回归（design §4「precheck 提升零变化」）：两入口同一
    判定面 + warmup 同源（既有 precheck 测试集之外的对照锚）。"""

    def test_same_bad_column_same_code_and_detail(self, db):
        """同一坏列名：precheck 与装载链接线同码 RESEARCH_PLUGIN_
        COLUMN_NOT_FOUND 且 detail.plugin_columns 一致（共享助手=
        单一事实源；提升共享为搬位置不改逻辑的行为对照）。"""
        bad = {"subject": "fx_typo_col", "action": "lt", "target": 0}
        with pytest.raises(ResearchError) as e_load:
            _load_with_condition_refs(MARKET_DIR, RNG, None,
                                      conditions=[bad], db_path=db)
        with pytest.raises(ResearchError) as e_pre:
            run_registration_precheck(db, MARKET_DIR, [bad], [RNG])
        assert e_pre.value.code == e_load.value.code
        assert (e_pre.value.detail["plugin_columns"]
                == e_load.value.detail["plugin_columns"]
                == ["fx_typo_col"])

    def test_precheck_warmup_same_source_as_full(self, db):
        """precheck 披露 warmup_bars 与 derive_warmup_full 同参同值
        （两维同源的字面证据；dev20 lookback 20<floor → 200）。"""
        pre = run_registration_precheck(db, MARKET_DIR, [COND], [RNG])
        assert pre["warmup_bars"] == derive_warmup_full(
            db, [], {"fx_fx_dev20_pct"}) == 200


class TestZeroSideEffects:
    """零留痕（design §4：拒绝发生在装载段=零统计副作用、零留痕，
    analysis 留痕成功后才写）。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        import shutil
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        shutil.copy(MARKET_DIR / "BTCUSDT_4h.parquet",
                    data_dir / "BTCUSDT_4h.parquet")
        p = tmp_path / "q.db"
        init_db_ext(p)
        ensure_research_tables(p)
        register_formula_plugin(p, name="fx_dev20_pct",
                                expression="(close - shift(close, 20)) / shift(close, 20)")
        return {"data_dir": data_dir, "db": p}

    def _post(self, api_client, env, subject):
        api_client.app.state.db_path = env["db"]
        api_client.app.state.data_dir = env["data_dir"]
        return api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"conditions": [
                {"subject": subject, "action": "lt", "target": -0.01}]},
            "data_range": RNG})

    def test_rejected_request_writes_no_log(self, api_client, env):
        """对照锚：成功请求写 analysis 留痕（count 增加），未注册
        插件列 422 拒绝后 count 不变（零留痕承诺）。"""
        before = count_research_log(env["db"])
        ok = self._post(api_client, env, "fx_fx_dev20_pct")
        assert ok.status_code == 200, ok.text[:300]
        after_ok = count_research_log(env["db"])
        assert after_ok > before, "成功请求应写 analysis 留痕（对照前提）"

        rejected = self._post(api_client, env, "fx_unknown_col")
        assert rejected.status_code == 422
        assert count_research_log(env["db"]) == after_ok, \
            "装载段拒绝应零留痕"


class TestMcpToolPassthrough:
    """MCP 工具层透传（design §6「A8 静默点证据打到 MCP 工具层」）：
    修复前的静默 0 样本在工具层现为结构化错误可见。"""

    def _run_tool(self, monkeypatch, code, message, detail):
        from mcpserver.api_client import ApiError
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        def _raise(path, json=None):
            raise ApiError(422, code, message, detail)

        mcp, api = build_server(profile="research", base_url="http://x")
        monkeypatch.setattr(api, "post", _raise)
        return run_async(mcp.call_tool(
            "run_analysis",
            {"template": "condition_stats",
             "params": {"conditions": [
                 {"subject": "fx_unknown_col", "action": "lt",
                  "target": 0}]},
             "symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-03-01", "end": "2026-04-01"}))

    def test_not_found_visible_at_tool_layer(self, monkeypatch):
        out = self._run_tool(
            monkeypatch, "RESEARCH_PLUGIN_COLUMN_NOT_FOUND",
            "条件引用的插件列不可达: ['fx_unknown_col']",
            {"plugin_columns": ["fx_unknown_col"],
             "hint": "可用列经 GET /api/research/signals 查询"})
        err = out.structured_content["result"]["error"]
        assert err["code"] == "RESEARCH_PLUGIN_COLUMN_NOT_FOUND"
        assert err["detail"]["plugin_columns"] == ["fx_unknown_col"]
        # 顶层 hint 走工程码注册表场景文案（指向插件列查询），非
        # detail.hint 复制——透传形制：code+detail 原样，hint 注册表源
        assert "list_signal_columns" in err["hint"]

    def test_not_available_visible_at_tool_layer(self, monkeypatch):
        out = self._run_tool(
            monkeypatch, "RESEARCH_COLUMN_NOT_AVAILABLE",
            "插件列求值失败（registry 可达但求值不出列）: ['fx_outer']",
            {"missing_columns": ["fx_outer"],
             "reason": "plugin_eval_failed",
             "hint": "插件表达式求值失败——核对表达式引用列与数据装载"})
        err = out.structured_content["result"]["error"]
        assert err["code"] == "RESEARCH_COLUMN_NOT_AVAILABLE"
        assert err["detail"]["missing_columns"] == ["fx_outer"]
        assert err["detail"]["reason"] == "plugin_eval_failed"


class TestCapabilityDisclosure:
    """R5 披露快照锚（tasks C3.5：披露与实际能力一致）。"""

    def test_capability_doc_discloses_plugin_columns(self):
        from mcpserver.capability import CAPABILITY_DOC
        assert "分析模板插件列条件" in CAPABILITY_DOC
        for code in ("RESEARCH_PLUGIN_COLUMN_NOT_FOUND",
                     "RESEARCH_COLUMN_NOT_AVAILABLE"):
            assert code in CAPABILITY_DOC
        for tpl in ("condition_stats", "event_study", "inspect_cases",
                    "param_sweep", "counter_profile"):
            assert tpl in CAPABILITY_DOC
