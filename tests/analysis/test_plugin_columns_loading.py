"""插件列分析面装载链接入测试（fix-research-c3-plugin-columns-loading · C3.4）。

缺陷一（fr_6f117ce7da0b）：分析模板（condition_stats/event_study/
inspect_cases 等）的装载层不解析插件列——插件列 subject 落「缺列
warnings+全 False」静默降级，0 样本拒绝收场；只有 precheck 支持。
本文件锁修复后的装载链五步（收集→可达性→两维 warmup→求值注入→
存在性校验）与两码分工。

A4 主锚（真实数据 BTCUSDT 4h）：fx_fx_dev20_pct 作 subject 的
condition_stats 触发数与 precheck_hypothesis 同条件一致（两链同源）
——1755。区间用 2024-03-01 起（tasks 锚区间 2024-02-01 在当前数据
集起点 2024-01-01 下前扩 200 bar 差 14 bar 不足——区间平移不伤
「两链一致」锚语义，注明于此）。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]

MARKET_DIR = Path("data/market")

if not (MARKET_DIR / "BTCUSDT_4h.parquet").exists():
    pytest.skip("真实数据 BTCUSDT 4h 不在库（data/market），插件列装载锚跳过",
                allow_module_level=True)

from api.routes.research.analysis import _load_with_condition_refs
from core.analysis.executor import ensure_research_tables
from core.analysis.plugin_service import (
    register_formula_plugin,
    verify_plugin_columns_present,
)
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.precheck_hypothesis import (
    run_registration_precheck,
)
from core.analysis.errors import ResearchError
from core.persistence.db_ext import init_db_ext

RNG = {"symbol": "BTCUSDT", "timeframe": "4h",
       "start": "2024-03-01", "end": "2026-04-01"}
COND = {"subject": "fx_fx_dev20_pct", "action": "lt", "target": -0.01}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("c3")
    p = d / "q.db"
    init_db_ext(p)
    ensure_research_tables(p)
    register_formula_plugin(p, name="fx_dev20_pct",
                            expression="(close - shift(close, 20)) / shift(close, 20)")
    # lookback 260 > derive_warmup 200 floor——插件维并入的判别锚用
    # （20 形预算与 floor 同值 200，砍收集后在数值上不可区分）
    register_formula_plugin(p, name="fx_dev260",
                            expression="shift(close, 260)")
    return p


def _stats(db, conditions, rng=RNG):
    df, rs = _load_with_condition_refs(
        MARKET_DIR, rng, None, conditions=conditions, db_path=db)
    d = condition_stats(df, conditions=conditions, data_ranges=[{**rng}],
                        forward_bars=24, request_start=rs).to_dict()
    return d["payload"]


class TestA4PluginColumnParity:
    """A4 双断言：非零样本+与 precheck 触发数一致（两链同源）。"""

    def test_condition_stats_nonzero_and_precheck_parity(self, db):
        pl = _stats(db, [COND])
        assert pl["hit_count"] > 0  # 修复前此处是 0 样本拒绝
        pre = run_registration_precheck(db, MARKET_DIR, [COND], [RNG])
        assert pre["analysis_segment_trades"] == pl["hit_count"]

    def test_unregistered_column_422(self, db):
        """未注册列名→名字写错码（RESEARCH_PLUGIN_COLUMN_NOT_FOUND）。"""
        bad = {"subject": "fx_not_registered", "action": "lt", "target": 0}
        with pytest.raises(ResearchError) as ei:
            _load_with_condition_refs(
                MARKET_DIR, RNG, None, conditions=[bad], db_path=db)
        assert ei.value.code == "RESEARCH_PLUGIN_COLUMN_NOT_FOUND"


class TestEvalFailurePath:
    """求值失败路径：registry 可达但表达式引用不存在列→「插件坏了」码。"""

    def test_broken_expression_column_unavailable(self, tmp_path):
        """构造法（静态校验拦不住的运行时失败）：fx_outer 依赖
        fx_inner（两者注册序保证静态校验通过），随后直删 fx_inner
        注册条目——fx_outer 求值时依赖列不可得 → enrich 静默
        continue → 存在性校验结构化拒绝。

        2026-10-13 tester 修正：原 hasattr(rr, "_connect") 运行时探测
        私有函数再回退 sqlite3 的两分支（repository research 模块无
        _connect，else 支为死代码）收敛为 sqlite3 直连——同 SQL 同
        语义，消除私有 API 依赖（reviewer3 提示，tester 复核采纳）。"""
        p = tmp_path / "bad.db"
        init_db_ext(p)
        ensure_research_tables(p)
        register_formula_plugin(p, name="inner",
                                expression="shift(close, 1)")
        register_formula_plugin(p, name="outer",
                                expression="fx_inner + 0")
        import sqlite3
        conn = sqlite3.connect(p)
        conn.execute(
            "DELETE FROM signal_registry WHERE column_name='fx_inner'")
        conn.commit()
        conn.close()
        from tests.helpers.data_factory import make_ohlcv
        df = make_ohlcv(n=50, freq="4h", start="2024-01-01", seed=1)
        from core.analysis.plugin_service import enrich_with_plugin_columns
        out = enrich_with_plugin_columns(p, df, {"fx_outer"})
        with pytest.raises(ResearchError) as ei:
            verify_plugin_columns_present(out, {"fx_outer"})
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"


class TestEmptyPluginUnchanged:
    """空插件列请求零行为变化（无插件条件的装载路径逐字节冻结）。"""

    def test_no_plugin_condition_loads_as_before(self, db):
        cond = {"subject": "close", "action": "gt", "target": 50000}
        df, rs = _load_with_condition_refs(
            MARKET_DIR, RNG, None, conditions=[cond], db_path=db)
        assert len(df) > 0
        assert "fx_fx_dev20_pct" not in df.columns  # 不 enrich 无关列


class TestInspectCasesWiring:
    """inspect_cases 接线：插件列 subject 可出案例集+头部收敛充足。"""

    def test_inspect_cases_plugin_subject(self, db):
        from core.analysis.case_inspection import inspect_cases
        res = inspect_cases(
            db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
            selector={"condition": COND}, limit=3, context_bars=4)
        body = res if isinstance(res, dict) else res.to_dict()
        cases = body.get("cases") or body.get("payload", {}).get("cases")
        assert cases, f"插件列条件应出案例集: {sorted(body)[:10]}"
        first = cases[0]
        ind = first.get("indicators") or first.get("context") or {}
        col_vals = None
        for container in (ind, first):
            if isinstance(container, dict) and "fx_fx_dev20_pct" in container:
                col_vals = container["fx_fx_dev20_pct"]
                break
        if col_vals is None:  # 形制兜底：全案例扫描该列出现且非全 NaN
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
        vals = [v for v in (col_vals if isinstance(col_vals, list) else
                            list(col_vals.values()) if isinstance(col_vals, dict)
                            else [col_vals]) if v is not None]
        assert any(v is not None and v == v for v in vals), \
            "插件列头部收敛充足非全 NaN（warmup 两维注入的直接证据）"


class TestRouteFullChain:
    """链路层：TestClient 全链——插件列条件 200+未注册 422 透传。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        import shutil
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
            "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2024-03-01", "end": "2026-04-01"}})

    def test_plugin_condition_200(self, api_client, env):
        resp = self._post(api_client, env, "fx_fx_dev20_pct")
        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()["payload"]["hit_count"] > 0

    def test_unknown_plugin_422(self, api_client, env):
        resp = self._post(api_client, env, "fx_unknown_col")
        assert resp.status_code == 422, resp.text[:300]
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_PLUGIN_COLUMN_NOT_FOUND"


class TestEvalFailureFullChain:
    """求值失败全链（G3）：registry 可达但求值失败经路由面 422
    「插件坏了」码——机制层锚之外的全链证据（design §6 双层验证）。"""

    def test_broken_plugin_422_via_route(self, api_client, tmp_path):
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        import shutil
        shutil.copy(MARKET_DIR / "BTCUSDT_4h.parquet",
                    data_dir / "BTCUSDT_4h.parquet")
        p = tmp_path / "bad.db"
        init_db_ext(p)
        ensure_research_tables(p)
        register_formula_plugin(p, name="inner", expression="shift(close, 1)")
        register_formula_plugin(p, name="outer", expression="fx_inner + 0")
        import sqlite3
        conn = sqlite3.connect(p)
        conn.execute("DELETE FROM signal_registry WHERE column_name='fx_inner'")
        conn.commit()
        conn.close()
        api_client.app.state.db_path = p
        api_client.app.state.data_dir = data_dir
        resp = api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"conditions": [
                {"subject": "fx_outer", "action": "lt", "target": 0}]},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2024-06-01", "end": "2026-04-01"}})
        assert resp.status_code == 422, resp.text[:300]
        assert resp.json()["detail"]["code"] == "RESEARCH_COLUMN_NOT_AVAILABLE"


class TestTwoDimWarmup:
    """两维 warmup（五步之③）：插件维与指标维并集前扩。

    插件列 warmup 实测 200 bar（registry_meta 推导规则）；前缀不足
    走结构化 RESEARCH_WARMUP_INSUFFICIENT（非 500/裸异常）。
    """

    def test_insufficient_prefix_structured(self, db):
        """前缀不足走结构化 RESEARCH_WARMUP_INSUFFICIENT 且预算值含
        插件维（lookback 260 形：start=2024-02-05 前扩 210 bar，夹在
        200 floor 与 260 之间——若插件维收集被砍，预算回落 200 即
        210 够用不抛，本断言即红；2026-10-13 mock 复核证据在案）。"""
        cond = {"subject": "fx_fx_dev260", "action": "lt", "target": 1e12}
        rng = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2024-02-05", "end": "2026-04-01"}
        with pytest.raises(ResearchError) as ei:
            _load_with_condition_refs(
                MARKET_DIR, rng, None, conditions=[cond], db_path=db)
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        assert ei.value.detail["warmup_bars"] == 260

    def test_plugin_and_indicator_dims_union(self, db):
        """subject=插件列 × target=指标列（cross_above ema_50）两维并集：
        预算取两维 max（指标维 600 主导、插件维 200）；装载按预算前扩
        （df 起点前扩 600 bar）且插件列已求值注入（指标列由模板内
        计算，不在装载产物——warmup 预算「只用不算」契约）。"""
        from core.analysis.registry_meta import derive_warmup_full
        plugins = {"fx_fx_dev20_pct"}
        dim_ind = derive_warmup_full(db, ["EMA"], set())
        dim_plg = derive_warmup_full(db, [], plugins)
        dim_both = derive_warmup_full(db, ["EMA"], plugins)
        assert dim_both == max(dim_ind, dim_plg) == 600
        assert dim_plg == 200, "插件维独立预算（fx 表达式 shift 20 推导）"

        cond = {"subject": "fx_fx_dev20_pct",
                "action": "cross_above", "target": "ema_50"}
        rng = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2024-06-01", "end": "2026-04-01"}
        df, request_start = _load_with_condition_refs(
            MARKET_DIR, rng, None, conditions=[cond], db_path=db)
        assert "fx_fx_dev20_pct" in df.columns
        assert df.index.get_loc(request_start) >= dim_both, \
            "装载前扩须覆盖两维并集预算"


class TestInspectCasesP1Regression:
    """P1（reviewer3 通报，tester 已独立复现确证）：插件列 subject +
    indicator 缺省 + 显式 start → case_inspection.py 显式 start 分支
    load_with_warmup(indicator_names=[None]) → derive_warmup 裸
    KeyError('未知指标: None')（500 路径）；start 缺省的全量直装分支
    正常。

    正常路径的 xfail(strict, raises=KeyError) 登记已在
    test_plugin_columns_benefit_surface.py（同源缺陷，不再重复锚）；
    此处只锚该文件未覆盖的结构化拒绝路径：豁免分支修复后，前缀不足
    应走 RESEARCH_WARMUP_INSUFFICIENT 而非裸异常（修复落地后 XPASS
    即红，翻转成正式断言——翻转原子交付纪律）。"""

    def test_explicit_start_insufficient_warmup_structured(self, db):
        """显式 start 前缀不足（2024-01-20 起前扩 ~114 bar < 插件维
        200）应结构化 RESEARCH_WARMUP_INSUFFICIENT，非裸 KeyError。
        （P1 已修——fix/research-c7 一行修 _warm_names 归一，原
        xfail(strict) 锚 XPASS 翻转转正）"""
        from core.analysis.case_inspection import inspect_cases
        with pytest.raises(ResearchError) as ei:
            inspect_cases(
                db, MARKET_DIR, symbol="BTCUSDT", timeframe="4h",
                selector={"condition": COND}, limit=3, context_bars=4,
                start="2024-01-20")
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
