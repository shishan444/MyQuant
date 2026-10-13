"""价格比较动作翻译面特征测试（fix-research-c2-price-actions · tester 组）。

对照 change delta Scenario「声明动作翻译可达」推导（不从实现反推）：
- price_above/price_below 加结构化指标 subject 的假设必须可翻译——
  产出 price_above 类型直通映射的草稿（target 占位不进产物、threshold
  不参与），不出现「可统计可登记但翻译被 RESEARCH_NOT_EXPRESSIBLE 拒」
  的表达域缺口；
- target 为任意占位值（数值/数值串/列名）均忽略不报错——与统计面
  占位契约对齐；豁免只及两键（非直通动作列名 target 仍拒）；
- 翻译产物经 SignalGene 序列化（to_json/from_json 往返）与回测面
  evaluate_condition 的 type 消费须保持语义：回测掩码与统计面掩码
  逐位一致（三面一致性）；沙盒回测消费真实 draft 跑通且触发密度与
  统计锚同量级。

既有 tests/validation/test_price_actions.py 覆盖统计面；本文件补翻译
面与下游消费链。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.persistence.db_ext import init_db_ext

BB_UPPER = {"indicator": "BB", "params": {"period": 20, "std": 2},
            "field": "upper"}
BB_LOWER = {"indicator": "BB", "params": {"period": 20, "std": 2},
            "field": "lower"}
EMA_20 = {"indicator": "EMA", "params": {"period": 20}, "field": "ema"}


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """1500 根 4h bar，2023-09-01 起——请求起点 2024-01-01 前
    ~731 bar，满足 EMA 注册表上限推导的 warmup 600 bar。"""
    d = tmp_path / "market"
    d.mkdir()
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-09-01", periods=1500, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 120, 1500))
    pd.DataFrame({"open": close, "high": close + 70, "low": close - 70,
                  "close": close, "volume": rng.uniform(100, 900, 1500),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    return d


def _register(db, when, then, ranges):
    return register_hypothesis(
        db, conditions={"when_conditions": when, "then_conditions": then},
        statement="s", data_ranges=ranges)["hypothesis_id"]


class TestPassthroughProduct:
    """机制层（build_signal_genes 直调，不落库）：直通映射产物形制。"""

    def test_price_above_product_exact(self, db):
        """产物形制：condition 恰为 {"type": "price_above"}——无
        threshold 键、target 不进产物；indicator/params/field/role
        原样传递。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        from core.strategy.dna import SignalRole
        genes, sources = build_signal_genes(
            [{"subject": BB_UPPER, "action": "price_above", "target": 0}],
            [], db)
        assert sources == []
        assert len(genes) == 1
        g = genes[0]
        assert g.condition == {"type": "price_above"}
        assert "threshold" not in g.condition
        assert g.indicator == "BB" and g.params == {"period": 20, "std": 2}
        assert g.role == SignalRole.ENTRY_TRIGGER

    @pytest.mark.parametrize("target", [0, "1.5", "ema_20"],
                             ids=["数值", "数值串", "列名"])
    def test_target_placeholder_forms_equal(self, db, target):
        """占位契约：target 数值/数值串/列名三形产物全等（一律忽略
        不报错、不进产物）。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        genes, _ = build_signal_genes(
            [{"subject": BB_UPPER, "action": "price_above",
              "target": target}], [], db)
        assert genes[0].condition == {"type": "price_above"}

    def test_price_below_exit_role_passthrough(self, db):
        """THEN 组 price_below 同样直通（预期兑现即离场语义；
        when 组须有可表达条件——空 when 是既有独立拒绝面）。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        from core.strategy.dna import SignalRole
        genes, _ = build_signal_genes(
            [{"subject": EMA_20, "action": "gt", "target": 0}],
            [{"subject": BB_LOWER, "action": "price_below",
              "target": "close"}], db)
        exit_genes = [g for g in genes
                      if g.role == SignalRole.EXIT_TRIGGER]
        assert [g.condition for g in exit_genes] == [{"type": "price_below"}]

    def test_non_passthrough_column_target_still_rejected(self, db):
        """豁免边界：非直通动作（gt）的列名 target 仍被拒——
        豁免只及 price_above/price_below 两键（RESEARCH_NOT_EXPRESSIBLE）。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        with pytest.raises(ResearchError) as ei:
            build_signal_genes(
                [{"subject": BB_UPPER, "action": "gt",
                  "target": "ema_20"}], [], db)
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"


class TestTranslateFullChain:
    """链路层：登记 → 翻译 → 序列化往返 → 回测消费 → 沙盒。"""

    RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2023-09-01T00:00:00Z",
               "end": "2023-12-01T00:00:00Z"}]

    def test_translate_draft_and_roundtrip(self, db):
        """Scenario 3 主体：price_above 假设（结构化 subject + 列名
        target 最严形制）翻译成功产 draft（不再 RESEARCH_NOT_EXPRESSIBLE
        ）；dna_json 落库产物与 StrategyDNA.from_json 往返后 condition
        均保持 {"type": "price_above"}（序列化往返一致性）。"""
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.dna import StrategyDNA

        hid = _register(
            db,
            [{"subject": BB_UPPER, "action": "price_above",
              "target": "ema_20"}],
            [{"subject": EMA_20, "action": "price_below", "target": 0}],
            self.RANGES)
        out = translate_hypothesis(db, hypothesis_id=hid)
        assert out["status"] == "draft"

        row = get_strategy(db, out["strategy_id"])
        stored = json.loads(row["dna_json"])
        conds = [g["condition"] for g in stored["signal_genes"]]
        assert conds == [{"type": "price_above"}, {"type": "price_below"}]

        dna = StrategyDNA.from_json(row["dna_json"])
        assert [g.condition for g in dna.signal_genes] == \
            [{"type": "price_above"}, {"type": "price_below"}]

    def test_backtest_mask_parity_with_stat_face(self, data_dir):
        """三面一致性（掩码对照）：同一数据同一列，统计面
        _evaluate_single_condition 掩码 == 回测面 evaluate_condition
        对翻译产物形制 {"type": "price_above"} 的消费掩码——逐位一致。"""
        from core.features.indicators import _compute_indicator
        from core.strategy.executor import evaluate_condition
        from core.validation.engine import _evaluate_single_condition

        d = pd.read_parquet(data_dir / "BTCUSDT_4h.parquet")
        for name, params in (("BB", {"period": 20, "std": 2}),
                             ("EMA", {"period": 20})):
            d = pd.concat(
                [d, pd.DataFrame(_compute_indicator(d, name, params))],
                axis=1)
        stat = _evaluate_single_condition(
            d, {"subject": "bb_upper_20_2", "action": "price_above",
                "target": 0}, None)
        bt = evaluate_condition(d["bb_upper_20_2"], d["close"],
                                {"type": "price_above"}, d)
        assert int(stat.sum()) > 0
        assert bool((stat.fillna(False) == bt.fillna(False)).all())

    def test_sandbox_consumes_draft_density_same_order(self, db, data_dir):
        """沙盒触发密度与统计锚同量级：真实 draft（非手工 DNA）跑通
        沙盒回测；请求段内统计面命中数 ≥ 沙盒交易数（入场触发是交易
        的必要条件，持仓期重叠会合并触发——密度同量级的守恒上界）。"""
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.analysis.validation_service import run_sandbox_backtest
        from core.features.indicators import _compute_indicator
        from core.validation.engine import _evaluate_single_condition

        hid = _register(
            db,
            [{"subject": BB_UPPER, "action": "price_above", "target": 0}],
            [{"subject": EMA_20, "action": "price_below", "target": 0}],
            self.RANGES)
        sid = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]

        start, end = "2024-01-01T00:00:00Z", "2024-03-01T00:00:00Z"
        sb = run_sandbox_backtest(db, data_dir, strategy_id=sid,
                                  symbol="BTCUSDT", timeframe="4h",
                                  start=start, end=end)
        assert sb["metrics"]["total_trades"] > 0, "沙盒须消费 draft 产真实交易"

        d = pd.read_parquet(data_dir / "BTCUSDT_4h.parquet")
        d = pd.concat(
            [d, pd.DataFrame(_compute_indicator(d, "BB",
                                                {"period": 20, "std": 2}))],
            axis=1)
        seg = d[(d.index >= start) & (d.index < end)]
        stat_hits = int(_evaluate_single_condition(
            seg, {"subject": "bb_upper_20_2", "action": "price_above",
                  "target": 0}, None).sum())
        assert stat_hits > 0
        assert sb["metrics"]["total_trades"] <= stat_hits, (
            f"沙盒交易数 {sb['metrics']['total_trades']} 超过段内触发数 "
            f"{stat_hits}——触发密度与统计锚失联")
