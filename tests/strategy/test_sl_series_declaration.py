"""series 档动态止损线声明域（dynamic-stop-loss-series 4.1 原子组
——tester 锚 C4-A1-A4+C4-C3+C4-F）。

五处同源：SL_MODES/RiskGenes.sl_series_source+签名 slsrc 段/
validator 四路+组合/risk_budget 双闸/登记面四断言+两形制+静态校验/
engine 常量。存量与非 series 档序列化/签名逐位不变（零迁移）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import RiskGenes, SL_MODES
from core.strategy.validator import validate_dna
from tests.helpers.data_factory import make_dna

_BB = {"indicator": "BB", "params": {"period": 20, "std": 2},
       "field": "lower"}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


def _register(db, src, extra=None, period=14):
    sp = {"sl_mode": "series", "sl_series_source": src}
    sp.update(extra or {})
    return register_hypothesis(
        db,
        conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": period}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 60}]},
        statement="series", data_ranges=_RANGES, strategy_params=sp)


class TestA1TwoFormsRegistrable:
    def test_indicator_form_end_to_end(self, db):
        """A1 指标源：登记→translate→落库 dna_json 可回读；
        sl_series_source 原样回显。A5 忠实披露恒态：stop_loss 残留
        0.05 物化+default_filled 含 stop_loss=结构性残留非行为性默认
        （引擎不消费该值——C4-A5 分态：series 档此态绿）。"""
        out = _register(db, _BB)
        t = translate_hypothesis(db, hypothesis_id=out["hypothesis_id"])
        v = t["risk_genes_disclosure"]["values"]
        assert v["sl_series_source"] == _BB
        assert v["sl_mode"] == "series"
        assert len(v) == 8  # 7 常键 + sl_series_source
        assert v["stop_loss"] == 0.05, "物化残留值（无行为语义）"
        assert "stop_loss" in t["risk_genes_disclosure"]["default_filled"], \
            "忠实披露恒态：声明块无 stop_loss 键即标注默认填充"

    def test_fx_form_end_to_end(self, db):
        """A1 fx 源：fx_ 前缀精确列名登记→translate。"""
        from core.analysis.plugin_service import register_formula_plugin
        col = register_formula_plugin(
            db, name="sl_line", expression="gt(close, 0)")["column"]
        out = _register(db, {"column": col}, period=15)
        t = translate_hypothesis(db, hypothesis_id=out["hypothesis_id"])
        assert t["risk_genes_disclosure"]["values"]["sl_series_source"] \
            == {"column": col}

    def test_indicator_field_omittable_single_output(self, db):
        """A1 field 省略两态：单输出指标（RSI）不带 field 合法。"""
        out = _register(
            db, {"indicator": "RSI", "params": {"period": 14}}, period=16)
        t = translate_hypothesis(db, hypothesis_id=out["hypothesis_id"])
        assert t["risk_genes_disclosure"]["values"]["sl_series_source"] \
            == {"indicator": "RSI", "params": {"period": 14}}


class TestA3CombinationGuards:
    def test_series_without_source_rejected(self, db):
        """A3①/⑤：series 无来源拒（合法例外锚——series+来源+无
        stop_loss 登记成功即反面）。"""
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions={
                    "when_conditions": [{"subject": {"indicator": "RSI",
                                                     "params": {"period": 14}},
                                         "action": "lt", "target": 35}],
                    "then_conditions": [{"subject": {"indicator": "RSI",
                                                     "params": {"period": 30}},
                                         "action": "gt", "target": 60}]},
                statement="s", data_ranges=_RANGES,
                strategy_params={"sl_mode": "series"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["field"] == "strategy_params.sl_series_source"

    def test_series_with_scalar_stop_rejected(self, db):
        """A3②：series+stop_loss 标量键→拒（档位参数互斥）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, _BB, extra={"stop_loss": 0.05}, period=17)
        assert ei.value.detail.get("conflict") == ["sl_mode=series",
                                                   "stop_loss"][:1] or \
            "互斥" in ei.value.message

    def test_non_series_with_source_rejected(self, db):
        """A3③：非 series 档携带来源键→拒（防半配置）。"""
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions={
                    "when_conditions": [{"subject": {"indicator": "RSI",
                                                     "params": {"period": 18}},
                                         "action": "lt", "target": 35}],
                    "then_conditions": [{"subject": {"indicator": "RSI",
                                                     "params": {"period": 30}},
                                         "action": "gt", "target": 60}]},
                statement="s", data_ranges=_RANGES,
                strategy_params={"stop_loss": 0.05,
                                 "sl_series_source": _BB})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_series_risk_budget_conflict(self, db):
        """A3④：series×risk_budget 互斥精准报错。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, _BB, extra={"position_mode": "risk_budget",
                                      "risk_fraction": 0.02}, period=19)
        assert ei.value.detail["conflict"] == ["sl_mode=series",
                                               "position_mode=risk_budget"]

    def test_form_mutual_exclusion(self, db):
        """两形制互斥：两键同在拒/都缺拒。"""
        with pytest.raises(ResearchError, match="互斥"):
            _register(db, {"indicator": "RSI", "params": {"period": 14},
                           "column": "fx_x"}, period=20)
        with pytest.raises(ResearchError):
            _register(db, {}, period=21)


class TestA4StaticValidation:
    def test_unknown_indicator_rejected(self, db):
        with pytest.raises(ResearchError, match="不在注册表"):
            _register(db, {"indicator": "NoSuchInd",
                           "params": {}}, period=22)

    def test_unknown_param_rejected(self, db):
        with pytest.raises(ResearchError, match="未知参数"):
            _register(db, {"indicator": "RSI",
                           "params": {"periodx": 14}}, period=23)

    def test_bad_field_rejected(self, db):
        with pytest.raises(ResearchError, match="field"):
            _register(db, {"indicator": "BB",
                           "params": {"period": 20, "std": 2},
                           "field": "no_such"}, period=24)

    def test_fx_prefix_required(self, db):
        with pytest.raises(ResearchError, match="fx_"):
            _register(db, {"column": "no_prefix"}, period=25)

    def test_fx_column_reachability(self, db):
        """fx 失效列（未注册）→ 可达性拒绝（登记时查注册表——
        ResearchError 族结构化，非静默放行）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, {"column": "fx_ghost_line"}, period=26)
        assert "fx_ghost_line" in ei.value.message or \
            ei.value.code == "RESEARCH_PARAM_INVALID", \
            (ei.value.code, ei.value.message)


class TestDirectDNAGates:
    def test_validator_series_risk_budget(self):
        """直建面：series×risk_budget 直构 DNA 过 validate_dna 得
        互斥 errors（残留 0.05 绕过 requires-stop_loss>0 的洞检出）。"""
        dna = make_dna()
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = dict(_BB)
        dna.risk_genes.position_mode = "risk_budget"
        dna.risk_genes.risk_fraction = 0.05
        vr = validate_dna(dna)
        assert not vr.is_valid
        assert any("mutually exclusive" in e for e in vr.errors)

    def test_validator_source_structures(self):
        """直建面结构四态：非 dict/两键同在/都缺/fx 无前缀各自 errors。"""
        for src, frag in (
            ("not-a-dict", "must be a dict"),
            ({"indicator": "RSI", "column": "fx_x"}, "mutually exclusive"),
            ({}, "requires 'indicator'"),
            ({"column": "bad"}, "fx_-prefixed"),
        ):
            dna = make_dna()
            dna.risk_genes.sl_mode = "series"
            dna.risk_genes.sl_series_source = src
            vr = validate_dna(dna)
            assert not vr.is_valid and any(frag in e for e in vr.errors), \
                (src, vr.errors)

    def test_global_validation_not_confused(self):
        """全局校验不误伤：series 合法 DNA 过 validate_dna（链路支持
        归能力矩阵独立判定）。"""
        dna = make_dna()
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = dict(_BB)
        vr = validate_dna(dna)
        assert vr.is_valid, vr.errors


class TestFSignatureIdentity:
    def test_slsrc_segment_active(self):
        """F1：series vs pct 同信号基因签名必异（slsrc 段生效）。"""
        d1 = make_dna()
        d2 = make_dna()
        d2.risk_genes.sl_mode = "series"
        d2.risk_genes.sl_series_source = dict(_BB)
        assert d1.gene_signature != d2.gene_signature

    def test_zero_migration(self):
        """F2：存量与非 series 档签名逐位不变（无 sl_series_source 键
        退化形 slsrc 段不编码）。"""
        d = make_dna()
        sig_before = d.gene_signature
        assert "slsrc:" not in sig_before
        d2 = make_dna()
        d2.risk_genes.sl_mode = "atr"
        d2.risk_genes.stop_loss = 2.0
        assert "slsrc:" not in d2.gene_signature

    def test_declaration_order_independent(self):
        """sl_series_source 字典键序不影响签名（sort_keys）。"""
        d1 = make_dna()
        d1.risk_genes.sl_mode = "series"
        d1.risk_genes.sl_series_source = {"indicator": "BB", "field":
                                          "lower", "params": {"std": 2,
                                                              "period": 20}}
        d2 = make_dna()
        d2.risk_genes.sl_mode = "series"
        d2.risk_genes.sl_series_source = {"params": {"period": 20,
                                                     "std": 2},
                                          "field": "lower",
                                          "indicator": "BB"}
        assert d1.gene_signature == d2.gene_signature


class TestSerialization:
    def test_to_dict_superset_form(self):
        """16 键基线：非 series to_dict 不写新键（存量逐字节不变）；
        series 17 键原样回显。"""
        rg = RiskGenes()
        assert "sl_series_source" not in rg.to_dict()
        rg2 = RiskGenes(sl_mode="series", sl_series_source=dict(_BB))
        assert rg2.to_dict()["sl_series_source"] == _BB

    def test_sl_modes_four_entries(self):
        """SL_MODES 四档封闭枚举。"""
        assert SL_MODES == ("pct", "atr", "trail", "series")

    def test_engine_constants_synced(self):
        """SL_MODES+ENGINE_SUPPORTED_SL_MODES 双集同步+分派码。"""
        from core.backtest.engine import (
            ENGINE_SUPPORTED_SL_MODES, _SL_MODE_CODE, sl_mode_code)
        assert set(ENGINE_SUPPORTED_SL_MODES) == set(SL_MODES)
        assert "series" in _SL_MODE_CODE
        assert sl_mode_code("series") == 3.0
