"""假设预检器单测（add-hypothesis-precheck tasks 1.1/1.3/1.4 验证锚）。

覆盖（design 测试用例设计节的 unit 子集——组 5 测试资产任务在此基础上
终态化对账）：
- 三态映射：直接比较不取整（4.9 warn / 5 pass / 0 reject_suggestion）；
- 纯计算：确定性 df 手工可核 AND 交集（组合触发数=人工核算）+ 前向
  收益分布/基准对照（specs R1 S1 THEN 项）+ 密度原始值（R2 MUST）；
- 双锚同 fixture（D6：两锚共用阈值与映射，各自按锚判定）；
- 零触发照常构造（min_samples=0——三要素张力解法）；
- 模板壳：to_dict 全键 + validate_range 异构/无数据 422 + 条件结构 422；
- 高周期条件进组合求值（specs R1 S1「含高周期条件」——掩码投影
  相位灵敏确定值，独立核算非实现回抄）；
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
    _probe_directory_ts,
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

    def test_threshold_not_parameterizable(self) -> None:
        """specs R2 MUST：警示阈值内建不可由调用方配置——三个入口
        签名均无阈值形参（阈值进参数面即规格违例）。"""
        import inspect

        for fn in (compute_precheck_report, precheck_hypothesis,
                   run_registration_precheck):
            assert "threshold" not in inspect.signature(fn).parameters, fn


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
        # 可加性不变量：不重叠区间逐段触发数之和 == 合并单区间求值
        merged = compute_precheck_report(
            df, cond, anchor="analysis_segment",
            per_range=[{"start": r1["start"], "end": r2["end"]}],
            warmup_bars=200)
        assert merged["frequency_projection"]["per_range"][0]["n_triggers"] \
            == 3 + 5

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

    def test_forward_returns_and_baseline_hand_counted(self) -> None:
        """specs R1 S1 THEN「前向收益分布、基准对照」+ R2「密度原始值」。

        前向收益口径 A1 冻结：fwd(i)=close[i+N]/close[i]-1（N=4）。
        组合触发位 [2,3,4,9,10,12,13] 的 fwd 值与全统计段基准分别
        人工核算（独立 numpy 直算，非实现回抄）。
        """
        df = _deterministic_df(self.PRICES)
        report = compute_precheck_report(
            df,
            [{"subject": "close", "action": "gt", "target": "95"},
             {"subject": "close", "action": "lt", "target": "110"}],
            anchor="projected", request_start=None, validate_bars=20,
            forward_bars=4, warmup_bars=200)
        cs = report["combination_stats"]
        fr = cs["forward_returns"]
        assert fr["count"] == 7                    # 7 触发位 i+4 均在窗内
        assert fr["mean"] == pytest.approx(-0.0842864658)
        assert fr["median"] == pytest.approx(-0.1428571429)
        assert fr["q25"] == pytest.approx(-0.1469002695)
        assert fr["q75"] == pytest.approx(-0.0451923077)
        assert fr["min"] == pytest.approx(-0.2129629630)
        assert fr["max"] == pytest.approx(0.15)
        # 基准对照=全统计段无条件前向收益（20 bar 中 i+4<20 的 16 个）
        assert cs["baseline"]["count"] == 16
        # 密度原始值披露（specs R2 MUST）
        assert report["frequency_projection"]["density"] == pytest.approx(7 / 20)


# ---------------------------------------------------------------- 双锚

class TestDualAnchorSameFixture:
    """D6 双锚（specs R2「两锚共用同一阈值与同一三态映射」）：同
    fixture 同条件下两锚各自判定——分歧行为（projected=6 pass /
    analysis_segment=3 warn）证明锚独立取值，零触发时两锚同收敛
    reject_suggestion（同映射）。"""

    COND = [{"subject": "close", "action": "gt", "target": "95"}]

    @staticmethod
    def _both(prices):
        df = _deterministic_df(prices)
        projected = compute_precheck_report(
            df, TestDualAnchorSameFixture.COND,
            anchor="projected", request_start=None, validate_bars=40,
            warmup_bars=200)
        end = (df.index[-1] + pd.Timedelta(hours=1)).isoformat()
        segment = compute_precheck_report(
            df, TestDualAnchorSameFixture.COND,
            anchor="analysis_segment",
            per_range=[{"start": df.index[0].isoformat(), "end": end}],
            warmup_bars=200)
        return projected, segment

    def test_same_fixture_divergent_states_shared_threshold(self) -> None:
        """触发 3：外推 (3/20)×40=6 → pass；分析段实际 3 → warn。"""
        projected, segment = self._both([100] * 3 + [50.0] * 17)
        assert projected["frequency_projection"]["projected_trades"] \
            == pytest.approx(6.0)
        assert projected["verdict"] == "pass"
        assert segment["frequency_projection"]["analysis_segment_trades"] == 3
        assert segment["verdict"] == "warn"
        # 共用同一阈值（M2.8）
        assert (projected["frequency_projection"]["threshold"]
                == segment["frequency_projection"]["threshold"] == 5)

    def test_same_fixture_zero_trigger_converges(self) -> None:
        projected, segment = self._both([50.0] * 20)
        assert projected["verdict"] == "reject_suggestion"
        assert segment["verdict"] == "reject_suggestion"


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

    def test_analysis_prefix_insufficient_422(self, env, db_path) -> None:
        """CARD-04 锚定（spec R3 场景「分析段装载不足结构化拒绝」）：
        前缀 400 < warmup 600 → 422 四件套差值声明——结构化通道即
        差值声明；不产出三态结论（未收敛数据上外推是误导）。

        与「拟验证段不足进报告」（test_budget_check_carries_probe，
        validate_margin 负值照报）为 R3 拆分后的两侧锚。
        """
        from core.analysis.registry_meta import WARMUP_RULE_TEXT

        df, data_dir = env
        rng = {"symbol": SYMBOL, "timeframe": "1h",
               "start": df.index[400].isoformat(),
               "end": df.index[-1].isoformat()}
        conds = [{"subject": {"indicator": "EMA",
                              "params": {"period": 50}, "field": "ema"},
                  "action": "gt", "target": 0}]
        with pytest.raises(ResearchError) as ei:
            precheck_hypothesis(db_path, data_dir, rng, dict(rng), conds)
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        assert ei.value.status == 422
        d = ei.value.detail
        assert d["warmup_bars"] == 600            # EMA 上限 200×3
        assert d["available_bars"] == 400         # 数据起点前实际可得
        assert d["data_start"] == df.index[0].isoformat()
        assert d["rule"] == WARMUP_RULE_TEXT      # 推导规则同源披露


# ---------------------------------------------------------------- 探测变体

class TestProbeVariantFallback:
    """CARD-05：探测点列名形制变体回退（_count_rows_index_only 同款
    ——0.3.3 C-β 实踩变体防御随行到预检探测点；正确性优先于轻量）。"""

    @staticmethod
    def _variant_env(tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df = make_ohlcv(n=900, freq="1h", start="2024-01-01", seed=21)
        df.rename_axis("not_timestamp").to_parquet(
            data_dir / f"{SYMBOL}_1h.parquet")
        return df, data_dir

    def test_variant_parquet_falls_back_to_full_load(
            self, tmp_path) -> None:
        """变体（index 名非 timestamp）→ 单列探测抛 KeyError/ValueError
        → 回退全列装载：探测非 None、bar 数正确、全列形制（回退确实
        发生的区分证据——标准形制为单列）。"""
        df, data_dir = self._variant_env(tmp_path)
        ts = _probe_directory_ts(data_dir, SYMBOL, "1h")
        assert ts is not None
        assert len(ts) == 900
        assert len(ts.columns) == 5           # 全列回退非单列

    def test_variant_full_shell_still_reports(
            self, tmp_path, db_path) -> None:
        """变体数据整壳照常：探测回退 → validate_bars 与预算对照
        （消费 .index）不受影响 → 三层报告与 verdict 照常产出。"""
        df, data_dir = self._variant_env(tmp_path)
        rng = {"symbol": SYMBOL, "timeframe": "1h",
               "start": df.index[650].isoformat(),
               "end": df.index[-1].isoformat()}
        out = precheck_hypothesis(
            db_path, data_dir, rng, dict(rng),
            [{"subject": "close", "action": "gt", "target": "0"}])
        assert out["verdict"] == "pass"       # close>0 恒真 → 密度满
        bc = out["payload"]["budget_check"]
        assert bc["data_start_actual"] == df.index[0].isoformat()
        assert bc["analysis_margin"] == 450   # 前缀 650 - warmup 200
        assert bc["validate_margin"] == 450   # 拟验证同起点


# ---------------------------------------------------------------- 持有期

class TestForwardBarsDomain:
    """CARD-01：forward_bars 值域契约（condition_stats 同款形制与时序）。

    env fixture 装载 df=450 行（前缀 200+统计段 250）——上界 449 基于
    含 warmup 前缀的全 df（先例口径，非统计段 249）；修复前字符串/
    float 形落 TypeError 500、True 按 1 静默接受、0/负值产出误导报告。
    """

    def _run(self, env, db_path, forward_bars):
        df, data_dir = env
        start, end = _tail_range(df)
        rng = {"symbol": SYMBOL, "timeframe": "1h", "start": start, "end": end}
        return precheck_hypothesis(
            db_path, data_dir, rng, dict(rng),
            [{"subject": "close", "action": "gt", "target": "0"}],
            forward_bars=forward_bars)

    @pytest.mark.parametrize(
        "bad", ["24", 2.5, 0, -3, True, 500],
        ids=["字符串形", "float形", "零", "负值", "bool形", "超上界"])
    def test_illegal_forward_bars_422(self, env, db_path, bad):
        with pytest.raises(ResearchError) as ei:
            self._run(env, db_path, bad)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        # legal_range 含前缀全 df 口径的精确锚（450 行 → 1..449）
        assert ei.value.detail["legal_range"] == "1..449"

    def test_upper_bound_edge_inclusive_runs(self, env, db_path) -> None:
        """N=len(df)-1=449 恰在界内：尾段前向窗口全 NaN，收益 count=0
        但报告照常产出——与 422 的分界锚。"""
        out = self._run(env, db_path, 449)
        cs = out["payload"]["combination_stats"]
        assert cs["forward_returns"]["count"] == 0
        assert out["verdict"] in ("pass", "warn", "reject_suggestion")


# ---------------------------------------------------------------- 高周期

class TestHtfConditionInCombo:
    """specs R1 S1「多条件 when 形制（含高周期条件）」——timeframe 键
    条件经掩码投影进组合 AND 求值。

    构造（确定性双周期数据，期望独立核算）：1h 300 bar 恒 100（基准
    条件 close>95 恒真）；4h bar k 仅 k∈[50,55) 为 350（close>300 为
    True）。投影语义=1h bar h 取收盘时刻 4(k+1)≤h 的最近已完结 4h
    bar——True 窗 [204h, 224h)。统计段 [210h, 300h)：

    - 正确投影：触发 [210,224) = 14 bar（独立逐 bar 核算）；
    - 相位偏 ±4h（对齐缺陷）分别为 18/10——14 为相位灵敏确定值；
    - 对照：去掉高周期条件（仅 1h 恒真）触发 90——证明高周期条件
      确实参与 AND 交集，非被静默丢弃。
    """

    @staticmethod
    def _htf_env(tmp_path):
        import numpy as np
        idx1 = pd.date_range("2024-01-01", periods=300, freq="1h",
                             tz="UTC", name="timestamp")
        c1 = np.full(300, 100.0)
        df1 = pd.DataFrame({"open": c1, "high": c1 + 1, "low": c1 - 1,
                            "close": c1, "volume": 100.0}, index=idx1)
        idx4 = pd.date_range("2024-01-01", periods=75, freq="4h",
                             tz="UTC", name="timestamp")
        k = np.arange(75)
        c4 = np.where((k >= 50) & (k < 55), 350.0, 100.0)
        df4 = pd.DataFrame({"open": c4, "high": c4 + 1, "low": c4 - 1,
                            "close": c4, "volume": 100.0}, index=idx4)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df1.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
        df4.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        return df1, data_dir

    def test_htf_condition_projects_into_combo(self, tmp_path, db_path) -> None:
        df1, data_dir = self._htf_env(tmp_path)
        rng = {"symbol": SYMBOL, "timeframe": "1h",
               "start": df1.index[210].isoformat(),
               "end": df1.index[-1].isoformat()}
        vr = {"symbol": SYMBOL, "timeframe": "1h",
              "start": df1.index[250].isoformat(),
              "end": (df1.index[-1] + pd.Timedelta(hours=1)).isoformat()}
        out = precheck_hypothesis(
            db_path, data_dir, rng, vr,
            [{"subject": "close", "action": "gt", "target": "95"},
             {"subject": "close", "action": "gt", "target": "300",
              "timeframe": "4h"}])
        cs = out["payload"]["combination_stats"]
        assert cs["n_triggers"] == 14           # 相位灵敏独立核算值
        assert cs["stat_bars"] == 90
        assert cs["trigger_rate"] == pytest.approx(14 / 90)
        # 外推链：密度 14/90 × 拟验证 50 bar ≈ 7.78 → pass
        assert out["payload"]["frequency_projection"]["projected_trades"] \
            == pytest.approx(14 / 90 * 50)
        assert out["verdict"] == "pass"

    def test_without_htf_condition_all_base_bars_trigger(
            self, tmp_path, db_path) -> None:
        """对照锚：同请求仅基准条件（恒真）→ 90 bar 全触发。"""
        df1, data_dir = self._htf_env(tmp_path)
        rng = {"symbol": SYMBOL, "timeframe": "1h",
               "start": df1.index[210].isoformat(),
               "end": df1.index[-1].isoformat()}
        vr = dict(rng, start=df1.index[250].isoformat(),
                  end=(df1.index[-1] + pd.Timedelta(hours=1)).isoformat())
        out = precheck_hypothesis(
            db_path, data_dir, rng, vr,
            [{"subject": "close", "action": "gt", "target": "95"}])
        assert out["payload"]["combination_stats"]["n_triggers"] == 90


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


class TestShellStructureRejection:
    """模板壳入参结构拒绝（盲区消解补锚——壳层三校验分支：
    when 结构/validate_range 必备键/data_range 可达性）。"""

    def _ranges(self, env):
        df, _ = env
        start, end = _tail_range(df)
        rng = {"symbol": SYMBOL, "timeframe": "1h", "start": start, "end": end}
        vr = {"symbol": SYMBOL, "timeframe": "1h",
              "start": start, "end": end}
        return rng, vr

    @pytest.mark.parametrize("bad_conds", ["not-a-list", []],
                             ids=["非列表形", "空列表"])
    def test_when_conditions_structure_422(self, env, db_path, bad_conds):
        rng, vr = self._ranges(env)
        with pytest.raises(ResearchError) as ei:
            precheck_hypothesis(db_path, env[1], rng, vr, bad_conds)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_validate_range_missing_key_422(self, env, db_path):
        rng, vr = self._ranges(env)
        vr.pop("symbol")                     # A2 冻结：四键必备
        with pytest.raises(ResearchError) as ei:
            precheck_hypothesis(db_path, env[1], rng, vr, [
                {"subject": "close", "action": "gt", "target": "0"}])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_data_range_symbol_not_found_422(self, env, db_path):
        rng, vr = self._ranges(env)
        rng["symbol"] = vr["symbol"] = "NOPEUSDT"   # 同 symbol（过一致性
        # 校验）且目录无该 parquet → 目录探测拒绝
        with pytest.raises(ResearchError) as ei:
            precheck_hypothesis(db_path, env[1], rng, vr, [
                {"subject": "close", "action": "gt", "target": "0"}])
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"


class TestUnreachableGuardNotes:
    """不可达判定锚（阶段四步骤 2 盲区消解留档——非用例）。

    precheck_hypothesis.py:293（analysis_segment 锚缺 per_range）与
    :316（未知锚口径）为编程错误防御分支，对模板输入不可达——调用图
    依据：anchor 形参全库仅两处赋值点且均为内部字面量——:448
    anchor="projected"、:508 anchor="analysis_segment" 恒伴随 per_range
    实参；外部输入不触达该形参。删改两分支须先改此调用图事实。
    """
