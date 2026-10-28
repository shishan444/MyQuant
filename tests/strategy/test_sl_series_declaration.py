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
        """A3②：series+stop_loss 标量键→拒（档位参数互斥——conflict
        数组精准形制，specs Scenario「stop_loss 标量键拒绝」）。"""
        with pytest.raises(ResearchError) as ei:
            _register(db, _BB, extra={"stop_loss": 0.05}, period=17)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["conflict"] == ["sl_mode=series",
                                               "stop_loss"], \
            ei.value.detail
        assert "field" in ei.value.detail and "hint" in ei.value.detail

    def test_series_with_take_profit_legal(self, db):
        """A3⑥：series+take_profit 并存合法（TP 沿 pct 量纲锚入场价
        ——执行面锚见 tests/backtest/test_sl_modes.py TestSeriesMode）。"""
        out = _register(db, _BB, extra={"take_profit": 0.1}, period=27)
        t = translate_hypothesis(db, hypothesis_id=out["hypothesis_id"])
        v = t["risk_genes_disclosure"]["values"]
        assert v["take_profit"] == 0.1

    def test_tp_below_residual_legal_series_only(self):
        """A3⑥ TP>SL 约束 series 档豁免（trail 豁免先例同族）：series
        档 take_profit=0.03 <= stop_loss 残留 0.05 不报错；对照 atr 档
        同关系报错——豁免是档位语义非全局放水。"""
        dna = make_dna()
        dna.risk_genes.sl_mode = "series"
        dna.risk_genes.sl_series_source = dict(_BB)
        dna.risk_genes.take_profit = 0.03
        assert validate_dna(dna).is_valid
        dna_atr = make_dna()
        dna_atr.risk_genes.sl_mode = "atr"
        dna_atr.risk_genes.stop_loss = 2.0
        dna_atr.risk_genes.take_profit = 1.0
        vr = validate_dna(dna_atr)
        assert not vr.is_valid and any(
            "greater than stop loss" in e for e in vr.errors), vr.errors

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
        """两形制互斥：两键同在拒/都缺拒；非 dict 形式拒（闸门①盲区
        :252——src 须为对象）。"""
        with pytest.raises(ResearchError, match="互斥"):
            _register(db, {"indicator": "RSI", "params": {"period": 14},
                           "column": "fx_x"}, period=20)
        with pytest.raises(ResearchError):
            _register(db, {}, period=21)
        with pytest.raises(ResearchError, match="须为对象"):
            _register(db, "not-a-dict", period=28)

    def test_params_non_dict_rejected(self, db):
        """登记面静态校验（闸门①盲区 :292）：params 非 dict 拒。"""
        with pytest.raises(ResearchError, match="params 须为对象"):
            _register(db, {"indicator": "RSI", "params": "bad"}, period=29)


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

    def test_validator_series_source_none_rejected(self):
        """直建面（闸门①盲区 :379）：sl_mode=series 而 sl_series_
        source=None → errors 含 requires sl_series_source（直构脏数据
        的 validate_dna 面防线——登记面同判锚已另有）。"""
        dna = make_dna()
        dna.risk_genes.sl_mode = "series"
        vr = validate_dna(dna)
        assert not vr.is_valid
        assert any("requires sl_series_source" in e for e in vr.errors), \
            vr.errors

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


class TestGate4PureInvariants:
    """阶段四闸门④属性补强：series 相关纯函数不变量（同输入同输出/
    结构稳定/封闭映射——属性级而非单点）。"""

    @pytest.mark.parametrize("mode", SL_MODES)
    def test_sl_mode_code_closed_bijection(self, mode):
        """sl_mode_code 封闭映射：四档全档位可编码、码值唯一可逆
        （名→码单值+码→名无碰撞）、两次调用同值（确定性）。"""
        from core.backtest.engine import sl_mode_code, _SL_MODE_CODE
        c1, c2 = sl_mode_code(mode), sl_mode_code(mode)
        assert c1 == c2, "确定性"
        assert c1 == _SL_MODE_CODE[mode], "与映射表单源一致"
        codes = [sl_mode_code(m) for m in SL_MODES]
        assert len(set(codes)) == len(SL_MODES), "码值无碰撞（双射）"

    def test_to_dict_roundtrip_invariant(self):
        """to_dict/from_dict 往返不变量：非 series 与 series 全字段
        两态，往返后序列化逐键相等（16/17 键基线的属性面）。"""
        for rg in (RiskGenes(stop_loss=0.08, take_profit=0.1,
                             exit_fraction=0.5, max_hold=50),
                   RiskGenes(sl_mode="series",
                             sl_series_source=dict(_BB),
                             take_profit=0.1, position_mode="fixed")):
            once = rg.to_dict()
            twice = RiskGenes.from_dict(once).to_dict()
            assert twice == once, (once, twice)

    def test_signature_idempotent_deterministic(self):
        """gene_signature 幂等+确定性：同 DNA 两次同签名；slsrc 段
        有值才编码（None 恒不出现）。"""
        d = make_dna()
        d.risk_genes.sl_mode = "series"
        d.risk_genes.sl_series_source = dict(_BB)
        assert d.gene_signature == d.gene_signature, "幂等"
        d2 = make_dna()
        assert "slsrc:" not in d2.gene_signature, "无值不编码"
        d3 = make_dna()
        d3.risk_genes.sl_mode = "series"
        d3.risk_genes.sl_series_source = dict(_BB)
        assert d3.gene_signature == d.gene_signature, \
            "同构造确定性（非 series 段逐位同源）"


class TestSerialization:
    # 16 键基线（7 恒写+9 条件——C4-E1 逐键对照，键序=to_dict 输出序）
    _KEYS16 = ["stop_loss", "take_profit", "position_size", "leverage",
               "direction", "sl_mode", "atr_period", "exit_fraction",
               "add_fraction", "max_exposure", "max_hold", "time_exit_utc",
               "position_mode", "risk_fraction", "initial_fraction",
               "max_position_loss"]

    def test_to_dict_superset_form(self):
        """16 键基线：非 series to_dict 不写新键（存量逐字节不变）；
        series 17 键原样回显。"""
        rg = RiskGenes()
        assert "sl_series_source" not in rg.to_dict()
        rg2 = RiskGenes(sl_mode="series", sl_series_source=dict(_BB))
        assert rg2.to_dict()["sl_series_source"] == _BB

    def test_full_field_keyset_baseline_16(self):
        """C4-E1 逐键基线：全字段非 series RiskGenes 的 to_dict 键集
        与键序逐键等于 16 键清单（存量序列化逐字节不变的键级锚）。"""
        rg = RiskGenes(
            stop_loss=0.08, take_profit=0.1, position_size=0.3,
            leverage=2, direction="short", sl_mode="pct", atr_period=20,
            exit_fraction=0.5, add_fraction=0.1, max_exposure=1.0,
            max_hold=100, time_exit_utc=600, position_mode="fixed",
            risk_fraction=0.02, initial_fraction=0.5,
            max_position_loss=1000.0)
        assert list(rg.to_dict()) == self._KEYS16
        assert RiskGenes.from_dict(rg.to_dict()) == rg

    def test_series_keyset_17(self):
        """C4-E1 series 17 键：同全字段+sl_series_source——新键在末位
        （16 键基线逐键不变，第 17 键条件追加）。"""
        kw = dict(stop_loss=0.08, take_profit=0.1, position_size=0.3,
                  leverage=2, direction="short", atr_period=20,
                  exit_fraction=0.5, add_fraction=0.1, max_exposure=1.0,
                  max_hold=100, time_exit_utc=600, position_mode="fixed",
                  risk_fraction=0.02, initial_fraction=0.5,
                  max_position_loss=1000.0)
        rg = RiskGenes(sl_mode="series", sl_series_source=dict(_BB), **kw)
        assert list(rg.to_dict()) == self._KEYS16 + ["sl_series_source"]
        assert RiskGenes.from_dict(rg.to_dict()).to_dict() == rg.to_dict()


class TestFSignatureNoOverwrite:
    """C4-F1 集成扩展·库去重消费方：series 产物不命中 pct 同基因
    既有行的 duplicated 分流——同信号基因两档各自独立成行（高分跨档
    覆写不发生）。"""

    def test_series_high_score_not_overwriting_pct_row(self, db):
        from core.persistence.repository.strategy import (
            get_strategy, save_strategy)
        pct_dna = make_dna()
        series_dna = make_dna()
        series_dna.risk_genes.sl_mode = "series"
        series_dna.risk_genes.sl_series_source = dict(_BB)
        assert pct_dna.gene_signature != series_dna.gene_signature, \
            "slsrc 段生效前置（签名层锚见 TestFSignatureIdentity）"
        save_strategy(db, strategy_id="st_pct", dna_json=pct_dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="pct",
                      status="active", best_score=1.0)
        # 更高分的 series 同信号基因——若同签名会覆写 st_pct 行
        save_strategy(db, strategy_id="st_series",
                      dna_json=series_dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="series",
                      status="active", best_score=2.0)
        row_pct = get_strategy(db, "st_pct")
        row_series = get_strategy(db, "st_series")
        assert row_pct is not None and row_series is not None, "两行并存"
        assert row_pct["best_score"] == 1.0, \
            "pct 行不被高分 series 行覆写（身份独立）"
        # 检出力对照：同 pct DNA 高分再存——同签名保留更优覆写发生
        # （覆写通道健在，series 不命中系签名不同而非通道失效）
        save_strategy(db, strategy_id="st_pct2",
                      dna_json=pct_dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="pct2",
                      status="active", best_score=3.0)
        merged = get_strategy(db, "st_pct2")
        assert merged is not None and merged["best_score"] == 3.0
        assert get_strategy(db, "st_pct") is None, \
            "同签名覆写=旧行 id 被改写（对照证明覆写语义在场）"

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
