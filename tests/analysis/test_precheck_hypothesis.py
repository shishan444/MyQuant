"""假设预检器单测（add-hypothesis-precheck tasks 1.1/1.3/1.4 验证锚）。

覆盖（design 测试用例设计节的 unit 子集——组 5 测试资产任务在此基础上
终态化对账）：
- 三态映射：直接比较不取整（4.9 warn / 5 pass / 0 reject_suggestion）；
- 纯计算：确定性 df 手工可核 AND 交集（组合触发数=人工核算）；
- 零触发照常构造（min_samples=0——三要素张力解法）；
- 模板壳：to_dict 全键 + validate_range 异构/无数据 422 + 条件结构 422；
- 装载：跨指标 target 进 warmup（G9）/不可解析 target 422/插件列
  enrich/装载前扩两维注入（前缀 bar 数断言）。

数值容差：触发数整数精确断言；密度/外推 pytest.approx；warmup 整数
精确等。期望全部手工构造，不来自被测实现（构造即期望）。
"""
import subprocess
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.registry_meta import derive_warmup_full
from core.analysis.templates.precheck_hypothesis import (
    _load_precheck_df,
    _verdict_for,
    compute_precheck_report,
    precheck_hypothesis,
    run_registration_precheck,
)
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> Path:
    """registry 库（插件列注册表；空库起步）。"""
    db = tmp_path_factory.mktemp("precheck_reg") / "reg.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}",
         "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


@pytest.fixture()
def env(tmp_path):
    """900 bar 1h 确定性数据目录（请求区间=尾段 650 起——前缀 650 bar
    覆盖全部 warmup 需求：内置 EMA 600 上限与 floor 200）。"""
    df = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=7)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
    return df, data_dir


def _tail_range(df):
    """请求区间=数据尾段（index[650, 900)——250 bar 统计段）。"""
    return df.index[650].isoformat(), df.index[-1].isoformat()


def _deterministic_df(prices, freq="1h"):
    idx = pd.date_range("2024-01-01", periods=len(prices), freq=freq, tz="UTC")
    close = pd.Series(prices, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": 100.0,
    })


# ---------------------------------------------------------------- 三态映射

class TestVerdictMapping:
    """比较规则：直接比较不取整——4.9 不升 pass（保守方向）。"""

    def test_direct_comparison_no_rounding(self) -> None:
        assert _verdict_for(0) == "reject_suggestion"
        assert _verdict_for(0.0) == "reject_suggestion"
        assert _verdict_for(0.9) == "warn"
        assert _verdict_for(4.9) == "warn"    # 4.9 不因取整升为 pass
        assert _verdict_for(5) == "pass"
        assert _verdict_for(5.0) == "pass"
        assert _verdict_for(13) == "pass"


# ---------------------------------------------------------------- 纯计算

class TestComputeProjected:
    """组合统计 + 密度外推（projected 锚，手工可核 AND 交集）。"""

    # 值 ∈ (95, 110) 的位置：2,3,4,9,10,12,13 —— 共 7 处
    PRICES = [80, 90, 100, 105, 108, 112, 115, 90, 85, 104,
              106, 90, 100, 105, 90, 90, 90, 90, 90, 90]

    def _report(self, prices, conds, validate_bars):
        df = _deterministic_df(prices)
        return compute_precheck_report(
            df, conds, anchor="projected", request_start=None,
            validate_bars=validate_bars, warmup_bars=200)

    def test_combo_and_triggers_hand_counted(self) -> None:
        report = self._report(
            self.PRICES,
            [{"subject": "close", "action": "gt", "target": "95"},
             {"subject": "close", "action": "lt", "target": "110"}],
            validate_bars=20)
        cs = report["combination_stats"]
        assert cs["n_triggers"] == 7          # 人工核算 AND 交集
        assert cs["stat_bars"] == 20
        assert cs["trigger_rate"] == pytest.approx(7 / 20)
        fp = report["frequency_projection"]
        assert fp["projected_trades"] == pytest.approx(7.0)  # 密度×20
        assert fp["basis"] == "trigger_density_upper_bound"
        assert "空仓进场抑制" in fp["basis_note"]           # 口径差披露
        assert report["verdict"] == "pass"

    def test_zero_triggers_reject_suggestion(self) -> None:
        report = self._report(
            [50.0] * 20,
            [{"subject": "close", "action": "gt", "target": "95"}],
            validate_bars=20)
        assert report["combination_stats"]["n_triggers"] == 0
        assert report["frequency_projection"]["projected_trades"] == 0.0
        assert report["verdict"] == "reject_suggestion"

    def test_warn_band_by_projection(self) -> None:
        # 密度 2/20 × 拟验证 40 bar = 4 → warn（>0 且 <5）
        prices = [100] * 2 + [50.0] * 18
        report = self._report(
            prices,
            [{"subject": "close", "action": "gt", "target": "95"}],
            validate_bars=40)
        assert report["combination_stats"]["n_triggers"] == 2
        assert report["frequency_projection"]["projected_trades"] == pytest.approx(4.0)
        assert report["verdict"] == "warn"

    def test_combo_le_each_single_invariant(self) -> None:
        """不变量：组合触发数 ≤ 任一单条件（交集数学性质）。"""
        conds = [{"subject": "close", "action": "gt", "target": "95"},
                 {"subject": "close", "action": "lt", "target": "110"}]
        combo = self._report(self.PRICES, conds, 20)["combination_stats"]["n_triggers"]
        for single in ([conds[0]], [conds[1]]):
            n = self._report(self.PRICES, single, 20)["combination_stats"]["n_triggers"]
            assert combo <= n

    def test_analysis_segment_anchor_multi_range_min(self) -> None:
        """挂接锚：逐区间触发数取最小值进锚（最差锚）。"""
        prices = [100] * 3 + [50.0] * 7 + [100] * 5 + [50.0] * 5
        df = _deterministic_df(prices)
        cond = [{"subject": "close", "action": "gt", "target": "95"}]
        r1 = {"start": "2024-01-01T00:00:00+00:00",
              "end": "2024-01-01T10:00:00+00:00"}    # bar 0-9：3 触发
        r2 = {"start": "2024-01-01T10:00:00+00:00",
              "end": "2024-01-01T20:00:00+00:00"}    # bar 10-19：5 触发
        report = compute_precheck_report(
            df, cond, anchor="analysis_segment",
            per_range=[r1, r2], warmup_bars=200)
        fp = report["frequency_projection"]
        assert [x["n_triggers"] for x in fp["per_range"]] == [3, 5]
        assert fp["analysis_segment_trades"] == 3      # 最小值进锚
        assert report["verdict"] == "warn"             # 3 ∈ (0, 5)

    def test_budget_check_carries_probe(self) -> None:
        df = _deterministic_df(self.PRICES)
        report = compute_precheck_report(
            df, [{"subject": "close", "action": "gt", "target": "95"}],
            anchor="projected", request_start=None, validate_bars=20,
            warmup_bars=600,
            budget={"data_start_actual": "2024-01-01T00:00:00+00:00",
                    "analysis_margin": -400, "validate_margin": -350})
        bc = report["budget_check"]
        assert bc["warmup_bars"] == 600
        assert bc["analysis_margin"] == -400           # 负值照报不报错
        assert bc["validate_margin"] == -350
        assert "max(200" in bc["rule"]                  # WARMUP_RULE_TEXT 引用


# ---------------------------------------------------------------- 模板壳

class TestTemplateShell:
    def _run(self, env, db_path, conds, validate_range=None):
        df, data_dir = env
        start, end = _tail_range(df)
        rng = {"symbol": SYMBOL, "timeframe": "1h", "start": start, "end": end}
        vr = validate_range or {
            "symbol": SYMBOL, "timeframe": "1h", "start": start, "end": end}
        return precheck_hypothesis(
            db_path, data_dir, rng, vr, conds)

    def test_to_dict_full_keys(self, env, db_path) -> None:
        out = self._run(env, db_path, [
            {"subject": "close", "action": "gt", "target": "0"}])
        for key in ("template", "sample_size", "confidence", "data_ranges",
                    "payload", "verdict"):
            assert key in out, key
        assert out["template"] == "precheck_hypothesis"
        assert out["sample_size"] == 250            # 统计段 bar 数口径
        assert out["confidence"]["method"] == "trigger_density_projection"
        assert out["confidence"]["basis"] == "trigger_density_upper_bound"
        assert out["verdict"] in ("pass", "warn", "reject_suggestion")
        assert out["payload"]["verdict"] == out["verdict"]  # payload 同步
        # 预算对照四件套在（index-only 探测产物）
        bc = out["payload"]["budget_check"]
        for key in ("data_start_actual", "analysis_margin", "validate_margin"):
            assert key in bc, key

    def test_zero_trigger_builds_without_small_sample_gate(self, env, db_path) -> None:
        """零触发照常构造（min_samples=0——三要素张力解法）。"""
        out = self._run(env, db_path, [
            {"subject": "close", "action": "gt", "target": "1e12"}])
        assert out["payload"]["combination_stats"]["n_triggers"] == 0
        assert out["verdict"] == "reject_suggestion"

    def test_validate_range_symbol_mismatch_422(self, env, db_path) -> None:
        df, _ = env
        with pytest.raises(ResearchError) as ei:
            self._run(env, db_path, [
                {"subject": "close", "action": "gt", "target": "0"}],
                validate_range={"symbol": "ETHUSDT", "timeframe": "1h",
                                "start": df.index[0].isoformat(),
                                "end": df.index[-1].isoformat()})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_validate_range_timeframe_mismatch_422(self, env, db_path) -> None:
        df, _ = env
        with pytest.raises(ResearchError) as ei:
            self._run(env, db_path, [
                {"subject": "close", "action": "gt", "target": "0"}],
                validate_range={"symbol": SYMBOL, "timeframe": "4h",
                                "start": df.index[0].isoformat(),
                                "end": df.index[-1].isoformat()})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_validate_range_no_data_422(self, env, db_path) -> None:
        df, _ = env
        with pytest.raises(ResearchError) as ei:
            self._run(env, db_path, [
                {"subject": "close", "action": "gt", "target": "0"}],
                validate_range={"symbol": SYMBOL, "timeframe": "1h",
                                "start": "2030-01-01T00:00:00+00:00",
                                "end": "2030-02-01T00:00:00+00:00"})
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_condition_structure_422(self, env, db_path) -> None:
        with pytest.raises(ResearchError) as ei:
            self._run(env, db_path, [{"subject": "close", "action": "gt"}])
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"


# ---------------------------------------------------------------- 装载

class TestLoader:
    def test_cross_indicator_target_in_warmup(self, env, db_path) -> None:
        """G9 语义复现：target 侧指标引用并入 warmup（EMA 上限 200×3
        =600——warmup 整数精确等断言）。"""
        df, data_dir = env
        conds = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                              "field": "rsi"},
                  "action": "gt",
                  "target": "ema_120"}]   # 编码列名 target → decode 并入
        start, end = _tail_range(df)
        _, _, warmup = _load_precheck_df(
            db_path, data_dir, SYMBOL, "1h", start, end, conds)
        assert warmup == 600              # max(200, RSI 150, EMA 600)

    def test_base_only_floor_200(self, env, db_path) -> None:
        df, data_dir = env
        conds = [{"subject": "close", "action": "gt", "target": "0"}]
        start, end = _tail_range(df)
        _, _, warmup = _load_precheck_df(
            db_path, data_dir, SYMBOL, "1h", start, end, conds)
        assert warmup == 200              # 纯基础列：floor 保底

    def test_unresolvable_target_422(self, env, db_path) -> None:
        df, data_dir = env
        conds = [{"subject": "close", "action": "gt",
                  "target": "not_a_column_x"}]
        with pytest.raises(ResearchError) as ei:
            _load_precheck_df(
                db_path, data_dir, SYMBOL, "1h",
                df.index[0].isoformat(), df.index[-1].isoformat(), conds)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_plugin_column_enriched_into_df(self, env, db_path) -> None:
        """插件列 enrich：装载后 df 含该列（不 enrich 则 mask 求值列
        缺失即崩）。"""
        df, data_dir = env
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_pre",
            name="fx_pre_test", version=1,
            expression="rolling_mean(close, 5)",
            output_columns='["fx_pre_test"]',
            entry_id="e_pre", column_name="fx_pre_test")
        conds = [{"subject": "fx_pre_test", "action": "gt",
                  "target": "0"}]
        start, end = _tail_range(df)
        loaded, _, warmup = _load_precheck_df(
            db_path, data_dir, SYMBOL, "1h", start, end, conds)
        assert "fx_pre_test" in loaded.columns
        assert warmup == 200              # rolling_mean 5 < floor

    def test_plugin_column_unreachable_422(self, env, db_path) -> None:
        """specs R3 MUST：registry 不可达插件列结构化拒绝（禁止静默降维
        ——plugin_columns_lookback 对不可达列静默贡献 0）。"""
        df, data_dir = env
        conds = [{"subject": "fx_ghost_col", "action": "gt",
                  "target": "0"}]
        with pytest.raises(ResearchError) as ei:
            _load_precheck_df(
                db_path, data_dir, SYMBOL, "1h",
                df.index[0].isoformat(), df.index[-1].isoformat(), conds)
        assert ei.value.code == "RESEARCH_PLUGIN_COLUMN_NOT_FOUND"

    def test_prefix_injection_two_dimension_warmup(self, tmp_path, db_path) -> None:
        """前扩注入断言：插件 lookback 900 进装载（不注入则内部仅内置
        维度 200——数据 920 bar、请求尾部 50 bar 时 870<900 应
        WARMUP_INSUFFICIENT；不注入则 870≥200 通过——负向断言区分两者）。

        data_start 前 870 bar 可得：注入 900 → 差 30 拒绝。"""
        n_all = 920
        df = make_ohlcv(n=n_all, freq="1h", start="2023-01-01", seed=11)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_big", name="fx_big_lb",
            version=1, expression="rolling_mean(close, 900)",
            output_columns='["fx_big_lb"]', entry_id="e_big",
            column_name="fx_big_lb")
        conds = [{"subject": "fx_big_lb", "action": "gt", "target": "0"}]
        start = df.index[n_all - 50].isoformat()
        end = df.index[-1].isoformat()
        with pytest.raises(ResearchError) as ei:
            _load_precheck_df(db_path, data_dir, SYMBOL, "1h",
                              start, end, conds)
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"

    def test_prefix_injection_positive_case(self, tmp_path, db_path) -> None:
        """正向锚：数据充足时前缀实际 bar 数 ≥ 两维 warmup 值（1500 bar
        数据、请求尾部 200 bar → 前缀 1300 ≥ 900）。"""
        n_all = 1500
        df = make_ohlcv(n=n_all, freq="1h", start="2023-01-01", seed=11)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_big2", name="fx_big_lb2",
            version=1, expression="rolling_mean(close, 900)",
            output_columns='["fx_big_lb2"]', entry_id="e_big2",
            column_name="fx_big_lb2")
        conds = [{"subject": "fx_big_lb2", "action": "gt", "target": "0"}]
        start_ts = df.index[n_all - 200]
        loaded, request_start, warmup = _load_precheck_df(
            db_path, data_dir, SYMBOL, "1h",
            start_ts.isoformat(), df.index[-1].isoformat(), conds)
        assert warmup == 900
        prefix_bars = int((loaded.index < request_start).sum())
        assert prefix_bars >= 900          # 前扩注入生效的直接证据


# ---------------------------------------------------------------- 挂接壳

class TestRegistrationPrecheck:
    def test_multi_range_worst_anchor_warning(self, env, db_path) -> None:
        """挂接壳：警告段含三态+最差锚+逐区间明细。"""
        df, data_dir = env
        t0 = df.index[650].isoformat()
        t1 = df.index[775].isoformat()
        t2 = df.index[-1].isoformat()
        warnings = run_registration_precheck(
            db_path, data_dir,
            [{"subject": "close", "action": "gt", "target": "0"}],
            [{"symbol": SYMBOL, "timeframe": "1h", "start": t0, "end": t1},
             {"symbol": SYMBOL, "timeframe": "1h", "start": t1, "end": t2}])
        assert warnings["verdict"] == "pass"       # close>0 恒真 → 密度满
        assert warnings["threshold"] == 5
        assert warnings["basis"] == "trigger_density_upper_bound"
        assert len(warnings["per_range"]) == 2
        assert warnings["analysis_segment_trades"] == min(
            x["n_triggers"] for x in warnings["per_range"])
        assert warnings["warmup_bars"] == 200


# ---------------------------------------------------------------- 5.1 三假设回溯

class TestThreeHypothesisRetrospect:
    """三假设回溯锚（proposal 硬验收）——显式模板路径三态全命中。

    对账口径：考试实测成交笔数 vs 预检触发密度外推存在已披露单向
    口径差（空仓进场抑制，basis: trigger_density_upper_bound），三态
    命中为量级命中（proposal 验收锚说明）。确定性构造：分析段与
    拟验证段各 1000 bar，分析段内放 n 个单 bar 尖峰（close=200，
    基底 100，阈值 150 切分）——密度外推预估笔数恰=n。
    """

    @staticmethod
    def _spike_env(tmp_path, n_spikes):
        import numpy as np
        n_total = 2650
        close = np.full(n_total, 100.0)
        if n_spikes:
            positions = np.linspace(0, 999, n_spikes,
                                    endpoint=False).astype(int)
            close[650 + positions] = 200.0
        idx = pd.date_range("2024-01-01", periods=n_total, freq="1h",
                            tz="UTC", name="timestamp")
        df = pd.DataFrame({
            "open": close, "high": close + 1.0, "low": close - 1.0,
            "close": close, "volume": 100.0,
        }, index=idx)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        return df, data_dir

    _CASES = [
        (3, "warn", "BTC 急跌假设——考试实测成交 ~3 笔"),
        (0, "reject_suggestion", "ETH 双过滤假设——考试实测 0 笔"),
        (13, "pass", "ETH 动量假设——考试实测 ~13 笔"),
    ]

    @pytest.mark.parametrize("n_spikes,expected,case", _CASES, ids=[
        "btc_crash_3", "eth_dblfilter_0", "eth_momentum_13"])
    def test_three_hypothesis_three_states(self, tmp_path, db_path,
                                           n_spikes, expected, case):
        df, data_dir = self._spike_env(tmp_path, n_spikes)
        out = precheck_hypothesis(
            db_path, data_dir,
            {"symbol": SYMBOL, "timeframe": "1h",
             "start": df.index[650].isoformat(),
             "end": df.index[1650].isoformat()},
            {"symbol": SYMBOL, "timeframe": "1h",
             "start": df.index[1650].isoformat(),
             "end": (df.index[-1] + pd.Timedelta(hours=1)).isoformat()},
            [{"subject": "close", "action": "gt", "target": "150"}])
        assert out["verdict"] == expected, case
        # 量级命中口径（proposal 验收锚）：统计段含闭端装载与外推基数的
        # ±1 bar 边界差，5% 容差内视为精确命中构造密度
        assert out["payload"]["frequency_projection"][
            "projected_trades"] == pytest.approx(
                float(n_spikes), rel=0.05)


# ---------------------------------------------------------------- 5.3 warmup 同源

class TestWarmupSourceParity:
    """同条件集两链路 warmup 同值：预检（collect refs →
    derive_warmup_full）vs 隔离回测（translate 等价 DNA →
    _derive_dna_warmup 薄壳）。同值的中间证明=两链路对同条件集产出
    等价引用集（指标名+插件列），计算已收敛到同段代码。"""

    def _register_and_translate(self, db_path, when):
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.db_ext import get_strategy
        from core.strategy.dna import StrategyDNA

        out = register_hypothesis(
            db_path,
            conditions={"when_conditions": when,
                        "then_conditions": [
                            {"subject": {"indicator": "RSI",
                                         "params": {"period": 14},
                                         "field": "rsi"},
                             "action": "gt", "target": 0}]},
            statement="warmup 同源测试假设",
            data_ranges=[{"symbol": SYMBOL, "timeframe": "4h",
                          "start": "2024-01-01T00:00:00+00:00",
                          "end": "2024-02-01T00:00:00+00:00"}])
        tr = translate_hypothesis(db_path, hypothesis_id=out["hypothesis_id"])
        row = get_strategy(db_path, tr["strategy_id"])
        return StrategyDNA.from_json(row["dna_json"])

    def test_builtin_condition_set_same_warmup(self, db_path) -> None:
        from core.analysis.templates.precheck_hypothesis import (
            _collect_precheck_refs,
        )
        from core.analysis.validation_service import _derive_dna_warmup

        when = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "lt", "target": 30},
                {"subject": {"indicator": "EMA", "params": {"period": 50},
                             "field": "ema"},
                 "action": "gt", "target": 0}]
        dna = self._register_and_translate(db_path, when)
        warmup_isolated, indicators = _derive_dna_warmup(db_path, dna)
        names, plugins = _collect_precheck_refs(when)
        warmup_precheck = derive_warmup_full(db_path, names, plugins)
        assert warmup_precheck == warmup_isolated == 600   # EMA 上限 600
        assert set(indicators) == set(names)

    def test_plugin_condition_set_same_warmup(self, db_path) -> None:
        from core.analysis.templates.precheck_hypothesis import (
            _collect_precheck_refs,
        )
        from core.analysis.validation_service import _derive_dna_warmup

        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_src", name="fx_src_col",
            version=1, expression="rolling_mean(close, 900)",
            output_columns='["fx_src_col"]', entry_id="e_src",
            column_name="fx_src_col")
        when = [{"subject": {"indicator": "ATR", "params": {"period": 14},
                             "field": "atr"},
                 "action": "gt", "target": 0},
                {"subject": "fx_src_col", "action": "gt", "target": 0}]
        dna = self._register_and_translate(db_path, when)
        warmup_isolated, _ = _derive_dna_warmup(db_path, dna)
        names, plugins = _collect_precheck_refs(when)
        warmup_precheck = derive_warmup_full(db_path, names, plugins)
        assert plugins == {"fx_src_col"}
        assert warmup_precheck == warmup_isolated == 900   # 插件两维取大
