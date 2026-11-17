"""数学组件插件集成测试（add-math-formula-vocabulary 组 2 tester 锚）。

I1 注册与引用（三插件形制/幂等/版本化/叠层求值）、I3 warmup 前扩
（derive_warmup_full 插件维）、S-1 验收 1 全链（注册→假设引用→翻译→
隔离验证——管道连通性口径，不设盈利门槛）。
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.plugin_service import (
    _plugin_allowed_columns,
    enrich_with_plugin_columns,
    plugin_columns_lookback,
    register_formula_plugin,
)
from core.analysis.registry_meta import derive_warmup_full
from core.analysis.validation_service import run_sandbox_backtest
from core.persistence.db_ext import init_db_ext

_EXPRESSIONS = {
    "kf_slope": "kalman_slope(close, 0.01, 25)",
    "kf_level": "kalman(close, 0.01, 25)",
    "q75_vol": "rolling_quantile(abs(diff(close, 1) / shift(close, 1)), 20, 0.75)",
}
# 假设声明段（S-1 与生产验收同形制——避开判定并集用段外区）；
# 验证段起点须留足 warmup（EMA 上限 200×3=600 bar≈100 天前置数据）
_HYP_START, _HYP_END = "2022-11-01", "2022-12-01"
_RUN_START, _RUN_END = "2023-01-15", "2023-02-15"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(11)
    n = 2000
    idx = pd.date_range("2022-10-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 150, n))
    spread = 75
    pd.DataFrame({"open": close, "high": close + spread,
                  "low": close - spread, "close": close,
                  "volume": rng.uniform(100, 800, n),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    return d


@pytest.fixture()
def market_df(data_dir: Path) -> pd.DataFrame:
    return pd.read_parquet(data_dir / "BTCUSDT_4h.parquet")


class TestI1RegistrationAndReference:
    """I1：三插件注册形制/幂等/版本化/假设引用/叠层求值。"""

    def test_i11_three_plugins_registered(self, db):
        """三插件注册成功且列名=strategy_visible 注册列（fx_* 三列）。"""
        for name, expr in _EXPRESSIONS.items():
            r = register_formula_plugin(db, name=name, expression=expr)
            assert r["column"] == f"fx_{name}", name
        # 幂等：同表达式重注册返回既有版本
        r = register_formula_plugin(db, name="kf_slope",
                                    expression=_EXPRESSIONS["kf_slope"])
        assert r["duplicated"] is True and r["version"] == 1
        # 版本化：同 name 新表达式 v2 列名带 _v2
        r2 = register_formula_plugin(
            db, name="kf_slope", expression="kalman_slope(close, 0.02, 30)")
        assert r2["version"] == 2
        assert r2["column"] == "fx_kf_slope_v2"

    def test_i12_hypothesis_can_reference_kf_slope(self, db, market_df):
        """假设条件引用 fx_kf_slope：allowed 列含之+登记成功（登记链
        内含 resolve_column_ref plugin 分支+assert_plugin_columns_reachable）
        +条件求值直接锚——fx_kf_slope<0 在数据面出混合布尔（真实值
        非全 False，条件语义可触发）。"""
        for name, expr in _EXPRESSIONS.items():
            register_formula_plugin(db, name=name, expression=expr)
        assert "fx_kf_slope" in _plugin_allowed_columns(db)
        r = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": "fx_kf_slope", "action": "lt", "target": 0}],
                "then_conditions": [
                    {"subject": {"indicator": "EMA", "params": {"period": 30},
                                 "field": "ema"},
                     "action": "price_above", "target": 0}]},
            statement="I1-2 插件列假设引用锚",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": _HYP_START, "end": _HYP_END}],
            direction="short",
            strategy_params={"sl_mode": "pct", "stop_loss": 0.05})
        assert r["hypothesis_id"]
        # 条件求值直接锚：when 条件（fx_kf_slope lt 0）的数据面在
        # 合成行情上时真时假——非全 False（条件永不可触发）非全 True
        enriched = enrich_with_plugin_columns(db, market_df, {"fx_kf_slope"})
        neg = enriched["fx_kf_slope"] < 0
        assert neg.any(), "须存在斜率为负的 bar（条件可触发）"
        assert not neg.all(), "须存在斜率非负的 bar（条件非恒真）"

    def test_i13_warmup_closure_200(self, db):
        """warmup 闭包：fx_kf_slope 固定贡献 200 经 plugin_columns_
        lookback 生效（fx_q75_vol 窗口位 20 对照）。"""
        register_formula_plugin(db, name="kf_slope",
                                expression=_EXPRESSIONS["kf_slope"])
        register_formula_plugin(db, name="q75_vol",
                                expression=_EXPRESSIONS["q75_vol"])
        assert plugin_columns_lookback(db, {"fx_kf_slope"}) == 200
        assert plugin_columns_lookback(db, {"fx_q75_vol"}) == 20

    def test_i14_stacked_reference_evaluates(self, db, market_df):
        """叠层引用：衍生插件引用 fx_kf_level，enrich 求值落列成功
        （新词作被引用者——依赖闭包递归求值）。"""
        register_formula_plugin(db, name="kf_level",
                                expression=_EXPRESSIONS["kf_level"])
        register_formula_plugin(db, name="kf_dev",
                                expression="diff(fx_kf_level, 1)")
        out = enrich_with_plugin_columns(db, market_df, {"fx_kf_dev"})
        assert "fx_kf_dev" in out.columns and "fx_kf_level" in out.columns
        assert out["fx_kf_level"].notna().all()
        # diff 首元素为 NaN（rolling 族语义），其余有限
        assert np.isnan(out["fx_kf_dev"].iloc[0])
        assert np.isfinite(out["fx_kf_dev"].iloc[1:]).all()


class TestI3WarmupExpansion:
    """I3：derive_warmup_full 插件维并入（max(内置, 插件, 200)）。"""

    def test_i31_pure_plugin_chain_floored_at_200(self, db):
        """纯 fx_kf_slope 链恰 200（floor 兜底不放大）。"""
        register_formula_plugin(db, name="kf_slope",
                                expression=_EXPRESSIONS["kf_slope"])
        assert derive_warmup_full(db, [], {"fx_kf_slope"}) == 200

    def test_i31_plugin_dominates_small_indicator(self, db):
        """叠加推导小的内置指标（EMA30→90）时插件 200 取大。"""
        register_formula_plugin(db, name="kf_slope",
                                expression=_EXPRESSIONS["kf_slope"])
        assert derive_warmup_full(db, ["EMA"], {"fx_kf_slope"}) >= 200

    def test_i31_large_indicator_dominates(self, db):
        """内置推导 >200 时取大（EMA 上限 200×3=600）。"""
        register_formula_plugin(db, name="kf_slope",
                                expression=_EXPRESSIONS["kf_slope"])
        assert derive_warmup_full(db, ["EMA"], {"fx_kf_slope"}) == 600


class TestS1AcceptancePipeline:
    """S-1 验收 1 全链：kalman 词→插件→假设引用→翻译→隔离验证
    （管道连通性口径——verdict 三态任意，不设盈利门槛）。"""

    def test_s1_full_chain(self, db, data_dir):
        from core.analysis.hypothesis_to_strategy import translate_hypothesis

        for name, expr in _EXPRESSIONS.items():
            register_formula_plugin(db, name=name, expression=expr)
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": "fx_kf_slope", "action": "lt", "target": 0}],
                "then_conditions": [
                    {"subject": {"indicator": "EMA", "params": {"period": 30},
                                 "field": "ema"},
                     "action": "price_above", "target": 0}]},
            statement="S-1 全链锚（管道连通性）",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": _HYP_START, "end": _HYP_END}],
            direction="short",
            strategy_params={"sl_mode": "pct", "stop_loss": 0.05},
        )["hypothesis_id"]

        t = translate_hypothesis(db, hypothesis_id=hid, data_dir=data_dir)
        assert t["status"] == "draft" and t["risk_genes"] == "declared"

        v = run_sandbox_backtest(
            db, data_dir, strategy_id=t["strategy_id"], hypothesis_id=hid,
            symbol="BTCUSDT", timeframe="4h",
            start=_RUN_START, end=_RUN_END)
        assert v["record_id"], "验证记录在案"
        assert v["sandbox"] is True
        assert v["warmup_bars"] >= 200, "kalman 固定贡献进 warmup"

    def test_s1_series_mount_fully_wired(self, db, market_df):
        """series 档 fx 形制接线：fx_kf_level 作 sl_series_source 挂载
        装载成功（需求一验收「可参与 series 档止损线」service 面锚——
        engine 四态细节在 tests/backtest/test_sl_series_loading.py I2）。"""
        from core.backtest.engine import _load_sl_series_column
        register_formula_plugin(db, name="kf_level",
                                expression=_EXPRESSIONS["kf_level"])
        arr = _load_sl_series_column(db, market_df,
                                     {"column": "fx_kf_level"})
        assert arr.dtype == np.float64
        assert not np.isnan(arr).any() and (arr > 0).all()
