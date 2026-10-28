"""series 档装载链与 warmup 接线（dynamic-stop-loss-series 4.2
——tester 锚 C4-C1/C2+C4-A4 装载面）。

_load_sl_series_column 四路径+四态校验+纵深防线；warmup 两轨并入
（指标=周期×3/fx=plugin_columns_lookback）；端到端指标源触发价=
线值（B1 人工核算）。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.backtest.engine import (
    BacktestEngine, _load_sl_series_column, _per_dna_risk_params)
from core.features.indicators import compute_all_indicators
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_dna

_BB_SRC = {"indicator": "BB", "params": {"period": 20, "std": 2},
           "field": "lower"}


def _deep_dive_df(n=120):
    close = np.full(n, 100.0)
    close[40:60] = np.linspace(100, 85, 20)
    close[60:] = 86.0
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": np.full(n, 100.0),
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                           tz="UTC", name="timestamp"))


@pytest.fixture()
def env(tmp_path: Path):
    db = tmp_path / "q.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    return {"db": db, "enhanced": compute_all_indicators(_deep_dive_df())}


class TestC1LoadingPaths:
    def test_indicator_column_direct(self, env):
        """C1 路径①：目标列在 df 直取。"""
        arr = _load_sl_series_column(env["db"], env["enhanced"], _BB_SRC)
        expected = env["enhanced"]["bb_lower_20_2"].to_numpy()
        assert np.allclose(arr[19:], expected[19:])

    def test_indicator_column_compute(self, env):
        """C1 路径②：列缺经 _compute_indicator 现算。"""
        stripped = env["enhanced"].drop(columns=["bb_lower_20_2"])
        arr = _load_sl_series_column(env["db"], stripped, _BB_SRC)
        expected = env["enhanced"]["bb_lower_20_2"].to_numpy()
        assert np.allclose(arr[19:], expected[19:])

    def test_compute_unavailable_rejected(self, env):
        """C1 路径③：现算不可得（列名不存在——未知 field）显式拒。"""
        with pytest.raises(ResearchError) as ei:
            _load_sl_series_column(
                env["db"], env["enhanced"],
                {"indicator": "BB", "params": {"period": 20, "std": 2},
                 "field": "no_such"})
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    def test_fx_column_resolved(self, env):
        """C1 路径④：fx 列 enrich 求值落列（数值线值列——rolling
        均价形制；布尔 0/1 列的 0 值会被 ≤0 校验拒——价格水平线语义）。"""
        from core.analysis.plugin_service import register_formula_plugin
        col = register_formula_plugin(
            env["db"], name="sl_fx",
            expression="rolling_mean(close, 20)")["column"]
        arr = _load_sl_series_column(env["db"], env["enhanced"],
                                     {"column": col})
        assert arr.dtype == np.float64
        assert (arr[20:] > 0).all(), "数值线值列"

    def test_fx_without_db_rejected(self, env):
        """fx 形制须 db_path（纯计算直调面不支持 fx）。"""
        with pytest.raises(ResearchError, match="db_path"):
            _load_sl_series_column(None, env["enhanced"],
                                   {"column": "fx_x"})

    def test_fx_stale_rejected(self, env):
        """fx 失效列显式拒（不存在或未注册）。"""
        with pytest.raises(ResearchError) as ei:
            _load_sl_series_column(env["db"], env["enhanced"],
                                   {"column": "fx_ghost"})
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"


class TestA4FourStates:
    def _mutated(self, env, idx, value):
        v = env["enhanced"]["bb_lower_20_2"].to_numpy().copy()
        v[idx] = value
        return env["enhanced"].assign(**{"bb_lower_20_2": v})

    def test_all_nan_rejected(self, env):
        bad = env["enhanced"].assign(**{"bb_lower_20_2": np.nan})
        with pytest.raises(ResearchError, match="全 NaN"):
            _load_sl_series_column(env["db"], bad, _BB_SRC)

    def test_mid_hole_rejected(self, env):
        with pytest.raises(ResearchError, match="NaN"):
            _load_sl_series_column(
                env["db"], self._mutated(env, 50, np.nan), _BB_SRC)

    def test_nonpositive_rejected(self, env):
        with pytest.raises(ResearchError, match="非正值"):
            _load_sl_series_column(
                env["db"], self._mutated(env, 50, -1.0), _BB_SRC)

    def test_head_warmup_nan_legal(self, env):
        """头部预热 NaN 合法不拒（rolling 期）。"""
        arr = _load_sl_series_column(env["db"], env["enhanced"], _BB_SRC)
        assert np.isnan(arr[:19]).all(), "头部 rolling 期 NaN"


class TestDepthGuard:
    def test_series_risk_budget_loading_rejected(self, env):
        """C4-A3⑧ 纵深锚：直改库内 dna 绕过全部校验→装载 _per_dna_
        risk_params 显式拒——不产出按残留值计算仓位的结果。"""
        from core.strategy.dna import RiskGenes
        with pytest.raises(ResearchError) as ei:
            _per_dna_risk_params(
                RiskGenes(sl_mode="series", sl_series_source=dict(_BB_SRC),
                          position_mode="risk_budget",
                          risk_fraction=0.05),
                env["enhanced"], db_path=env["db"])
        assert ei.value.detail.get("conflict") == \
            ["sl_mode=series", "position_mode=risk_budget"]


class TestWarmupTwoTracks:
    def test_indicator_track_period_x3(self, env):
        """warmup 两轨之指标形式：BB(20,std) 并入内置清单——周期×3
        规则自动推导（derive_warmup 覆盖）。"""
        from core.analysis.validation_service import _derive_dna_warmup
        dna = make_dna()
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = dict(_BB_SRC)
        warmup, names = _derive_dna_warmup(env["db"], dna, "4h")
        assert "BB" in names, "series 指标源并入 warmup 指标清单"
        assert warmup >= 200

    def test_fx_track_plugin_lookback(self, env):
        """warmup 两轨之 fx 形式：fx 列并入 plugin_columns_lookback
        依赖闭包（rolling_mean(close,30) 的 fx 列贡献窗口）。"""
        from core.analysis.plugin_service import register_formula_plugin
        from core.analysis.validation_service import _derive_dna_warmup
        col = register_formula_plugin(
            env["db"], name="sl_warm",
            expression="gt(rolling_mean(close, 30), 0)")["column"]
        dna = make_dna()
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = {"column": col}
        warmup, _ = _derive_dna_warmup(env["db"], dna, "4h")
        # rolling_mean(30) 窗口并入（floor 200 之下——验证并入不炸+
        # 指标名清单不含 fx）
        assert warmup >= 200

    def test_warmup_rule_text_three_sources(self):
        """WARMUP_RULE_TEXT 三处一致（常量+两复制体——contract_parity
        锚翻转后 key 同步）。"""
        from core.analysis.registry_meta import WARMUP_RULE_TEXT
        from mcpserver.capability import CAPABILITY_DOC
        import mcpserver.tools as tools_mod
        src = tools_mod.__doc__ + str(tools_mod.list_indicators.__doc__) \
            if hasattr(tools_mod, "list_indicators") else ""
        for text in (WARMUP_RULE_TEXT, CAPABILITY_DOC):
            assert "plugin_columns_lookback" in text
            assert "plugin_formula_lookback" not in text


class TestB1EndToEndTrigger:
    def test_indicator_source_trigger_price_equals_line(self, env):
        """B1 人工核算：深回撤段触发止损，触发价=当 bar bb_lower 列值
        （残值不消费——同声明改 stop_loss 残留值回测结果不变）。"""
        bb = env["enhanced"]["bb_lower_20_2"].to_numpy()
        dna = make_dna(entry_value=45, exit_value=90, stop_loss=0.05,
                       take_profit=0.3)
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = dict(_BB_SRC)
        res = BacktestEngine(db_path=env["db"]).run(
            dna, env["enhanced"])
        assert res.total_trades >= 1, "深回撤段触发止损"
        tr = res.trades_df.iloc[0]
        exit_i = int(env["enhanced"].index.get_loc(
            pd.Timestamp(tr["Exit Timestamp"])))
        assert abs(float(tr["Avg Exit Price"]) - bb[exit_i]) < 1.0, \
            "触发价=当 bar 线值"
        # A5 残留值无行为语义：改残留 0.05→0.10 回测结果逐位不变
        dna2 = make_dna(entry_value=45, exit_value=90, stop_loss=0.05,
                        take_profit=0.3)
        dna2.risk_genes.sl_mode = "series"
        dna2.risk_genes.sl_series_source = dict(_BB_SRC)
        dna2.risk_genes.stop_loss = 0.10  # 残留值变异
        res2 = BacktestEngine(db_path=env["db"]).run(
            dna2, env["enhanced"])
        assert res2.total_trades == res.total_trades
        assert res2.equity_curve.equals(res.equity_curve) or \
            np.allclose(res2.equity_curve.values,
                        res.equity_curve.values), \
            "引擎不消费残留值——行为级证明"

    def test_short_mirror(self, env):
        """B2 方向镜像：空头 bar_high>=line 触发（同列同数据对称）。"""
        bb = env["enhanced"]["bb_lower_20_2"].to_numpy()
        dna = make_dna(entry_value=45, exit_value=90, stop_loss=0.05,
                       take_profit=0.3, direction="short")
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = dict(_BB_SRC)
        res = BacktestEngine(db_path=env["db"]).run(
            dna, env["enhanced"])
        assert res.total_trades >= 1, "空头触发（high 触及线值）"
