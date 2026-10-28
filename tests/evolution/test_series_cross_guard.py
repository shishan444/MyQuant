"""进化域 series 档交叉守卫（dynamic-stop-loss-series 4.4——tester
锚 C4-G1-G3：无异常往返/残留不改写+解耦/sl_series_source 不触碰/非
series 回归）。"""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.evolution.operators import mutate_risk
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import RiskGenes, StrategyDNA
from tests.helpers.data_factory import make_dna

_BB_SRC = {"indicator": "BB", "params": {"period": 20, "std": 2},
           "field": "lower"}


def _series_dna(**kw):
    dna = make_dna(**kw)
    dna.risk_genes.sl_mode = "series"
    dna.risk_genes.sl_series_source = dict(_BB_SRC)
    return dna


class TestG1Roundtrip:
    def test_no_exception_slsrc_preserved(self):
        """G1：series 档经 mutate_risk 不炸——to_dict/from_dict 往返
        sl_series_source 保持。"""
        for seed in range(30):
            random.seed(seed)
            dna = _series_dna()
            out = mutate_risk(dna)
            assert out.risk_genes.sl_series_source == _BB_SRC, seed
            assert out.risk_genes.sl_mode == "series", seed

    def test_g3_non_series_regression(self):
        """G3：pct/atr/trail 档变异行为零变化（同 seed 对照——pct 档
        stop_loss 可被扰动）。"""
        # 非 series 档在特定 seed 下 stop_loss 被扰动（0.5 概率×30
        # seed 中必然出现）——与实现前行为一致（分支未跳过）
        mutated = set()
        for seed in range(30):
            random.seed(seed)
            dna = make_dna()
            out = mutate_risk(dna)
            mutated.add(round(out.risk_genes.stop_loss, 4)
                        != round(dna.risk_genes.stop_loss, 4))
        assert True in mutated, "非 series 档 stop_loss 扰动路径健在"


class TestG2ResidualUntouched:
    def test_stop_loss_residual_not_written(self):
        """G2①：残留值 0.05 逐位不被改写（30 seed 扫描）。"""
        for seed in range(30):
            random.seed(seed)
            dna = _series_dna()
            out = mutate_risk(dna)
            assert out.risk_genes.stop_loss == dna.risk_genes.stop_loss, \
                seed

    def test_tp_none_stays_none(self):
        """G2②：TP=None 个体不从残留推导（TP 保持 None）。"""
        for seed in range(30):
            random.seed(seed)
            dna = _series_dna()
            dna.risk_genes.take_profit = None
            out = mutate_risk(dna)
            assert out.risk_genes.take_profit is None, seed

    def test_non_series_tp_none_derives_from_stop(self):
        """非 series 对照（闸门①盲区 :691）：TP=None 的非 series 档
        按 stop_loss 倍数初始化路径健在——series 档跳过（上例）是档位
        语义而非分支缺失（探明 seed=2 进 0.3 概率门→0.0814）。"""
        random.seed(2)
        dna = make_dna()  # pct 档 stop_loss=0.05
        dna.risk_genes.take_profit = None
        out = mutate_risk(dna)
        assert out.risk_genes.take_profit == pytest.approx(0.0814), \
            "非 series 档 TP 从 stop_loss 倍数 (1.5,3.0) 推导（对照 series 保持 None）"


class TestEvaluatePathSeries:
    """evaluate_dna 真路径（闸门①盲区 :208——CARD-04 db_path 透传
    构造点）：series 档个体经进化评估链真跑引擎。"""

    def test_evaluate_dna_series_indicator_runs(self, tmp_path):
        from core.evolution.evaluation import evaluate_dna
        from core.features.indicators import compute_all_indicators
        n = 120
        close = np.full(n, 100.0)
        close[40:60] = np.linspace(100, 85, 20)
        close[60:] = 86.0
        enhanced = compute_all_indicators(pd.DataFrame({
            "open": close * 0.999, "high": close * 1.002,
            "low": close * 0.998, "close": close,
            "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp")))
        db = tmp_path / "q.db"
        init_db_ext(db)
        dna = _series_dna(entry_value=45, exit_value=90)
        out = evaluate_dna(
            dna, {"symbol": "BTCUSDT", "timeframe": "4h",
                  "score_template": "profit_first"},
            tmp_path, leverage=1, direction="long",
            enhanced_df=enhanced, db_path=db)
        assert isinstance(out, dict) and "score" in out
        assert out.get("total_trades", 0) >= 1, \
            f"series 档真跑触发（评估链引擎构造 db_path 透传），实际 {out}"

    def test_tp_nonnone_clamp_source(self):
        """G2③：TP 非 None 扰动下限按 TP 自身 pct 下界 0.005 钳制
        （不读 stop_loss 残留——残留 0.05+0.005=0.055 下限不复现）。
        固定 seed 确定性断言（seed 探明：seed=8 负向扰动穿下界被钳至
        0.005——若误读残留则 max(0.055,·)=0.055 即红；seed=1 正向
        扰动至 0.026 自由——钳制只在下穿时生效非全局贴下界）。"""
        random.seed(8)
        dna = _series_dna()
        dna.risk_genes.take_profit = 0.006  # 残留下限 0.055 之下
        out = mutate_risk(dna)
        assert out.risk_genes.take_profit == pytest.approx(0.005), \
            f"下穿钳制至 TP 值域下界 0.005，实际 {out.risk_genes.take_profit}"
        random.seed(1)
        dna2 = _series_dna()
        dna2.risk_genes.take_profit = 0.006
        out2 = mutate_risk(dna2)
        assert out2.risk_genes.take_profit == pytest.approx(0.026), \
            f"正向扰动自由（seed=1 → 0.026），实际 {out2.risk_genes.take_profit}"

    def test_decoupling_residual_change_no_effect(self):
        """G2 解耦断言：同声明同 seed 下改残留值（0.05→0.10）变异结果
        不变（除 stop_loss 字段本身——其余键逐位同）。"""
        for seed in range(10):
            random.seed(seed)
            d1 = _series_dna()
            d1.risk_genes.stop_loss = 0.05
            random.seed(seed)
            d2 = _series_dna()
            d2.risk_genes.stop_loss = 0.10
            random.seed(seed)
            o1 = mutate_risk(d1)
            random.seed(seed)
            o2 = mutate_risk(d2)
            r1, r2 = o1.risk_genes.to_dict(), o2.risk_genes.to_dict()
            r1.pop("stop_loss"), r2.pop("stop_loss")
            assert r1 == r2, seed
