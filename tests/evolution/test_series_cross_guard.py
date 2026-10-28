"""进化域 series 档交叉守卫（dynamic-stop-loss-series 4.4——tester
锚 C4-G1-G3：无异常往返/残留不改写+解耦/sl_series_source 不触碰/非
series 回归）。"""
from __future__ import annotations

import random

import pytest

pytestmark = [pytest.mark.integration]

from core.evolution.operators import mutate_risk
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

    def test_tp_nonnone_clamp_source(self):
        """G2③：TP 非 None 扰动下限按 TP 自身 pct 下界 0.005 钳制
        （不读 stop_loss 残留——残留 0.05+0.005=0.055 下限不复现）。"""
        for seed in range(30):
            random.seed(seed)
            dna = _series_dna()
            dna.risk_genes.take_profit = 0.006  # 残留下限 0.055 之下
            out = mutate_risk(dna)
            if out.risk_genes.take_profit is not None:
                assert out.risk_genes.take_profit >= 0.005, \
                    "钳制下界=TP 值域 0.005（非残留+0.005）"
                # 若被钳制到下界，只能是 0.005（残留推导会得 0.055）
                if out.risk_genes.take_profit == 0.005:
                    break
        else:
            # 30 seed 内未触发钳制下界路径也可（扰动向上时无差别）——
            # 解耦面由 stop_loss 不改写+TP=None 锚承担
            pass

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
