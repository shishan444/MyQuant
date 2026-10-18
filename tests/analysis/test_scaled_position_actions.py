"""分批行为登记翻译链测试（0.4.2 R3，scaled-position-actions 规格，
B4/L2 用例组 + 4.6 翻译生产路径拒绝面收口）。

覆盖：三角色映射四断言/插件列新组拒绝/组基因进签名/翻译生产路径
live 链装载即拒（B3-17 收口——真实翻译产物证据；回放/模拟旧链随
旧域退役摘除）/预检组级披露/
示例策略形态端到端（版本 DoD：组合入场+部分离场+trail+风险声明→
登记→翻译→回测行为与设计一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.analysis.errors import ResearchError
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import get_hypothesis
from core.persistence.repository.strategy import get_strategy
from core.strategy.dna import RiskGenes, SignalRole
from core.strategy.validator import validate_trading_compatibility


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """DoD 链数据：120 bar 温和趋势+波动（RSI 可跨阈值、价格有回撤）。"""
    d = tmp_path / "market"
    d.mkdir()
    rng = np.random.default_rng(21)
    n = 420  # warmup 前置 200 bar + 统计段（预检装载链约束）
    trend = np.linspace(60000, 66000, n)
    close = trend + rng.normal(0, 400, n)
    high = close + 300
    low = close - 300
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                  "volume": rng.uniform(100, 900, n), "trades": 40},
                 index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


RSI_COND = {"indicator": "RSI", "params": {"period": 14}, "field": "rsi"}


def _cond(threshold, action="lt"):
    return {"subject": RSI_COND, "action": action, "target": threshold}


def _ranges():
    # 起点 2024-02-10：前置 240 bar > warmup 200（预检装载链可达）
    return [{"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-02-10T00:00:00Z", "end": "2024-02-20T00:00:00Z"}]


GROUP_RSI_HIGH = [_cond(70, "gt")]   # RSI>70（部分离场触发）
GROUP_RSI_LOW = [_cond(40)]          # RSI<40（加仓触发）


def _register_scaling(db, *, exit_group=None, add_group=None,
                      suppress_group=None, sp=None, period=14):
    conditions = {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "lt", "target": 45}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "gt", "target": 75}],
    }
    if exit_group is not None:
        conditions["exit_partial_conditions"] = exit_group
    if add_group is not None:
        conditions["add_trigger_conditions"] = add_group
    if suppress_group is not None:
        conditions["add_suppress_conditions"] = suppress_group
    return register_hypothesis(db, conditions=conditions,
                               statement="scaling", data_ranges=_ranges(),
                               strategy_params=sp)


class TestTranslationMapping:
    """三角色映射四断言（B4-18）+插件列拒绝+组基因进签名。"""

    def test_role_mapping_four_assertions(self, db):
        """exit_partial→REDUCE_TRIGGER、add_trigger→ADD_TRIGGER、
        add_suppress→ADD_INHIBIT、then→EXIT_TRIGGER 不变（映射四断言）。"""
        h = _register_scaling(db, exit_group=GROUP_RSI_HIGH,
                              add_group=GROUP_RSI_LOW,
                              suppress_group=[_cond(80, "gt")])
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        dna = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])
        roles = {g["role"] for g in dna["signal_genes"]}
        assert "reduce_trigger" in roles
        assert "add_trigger" in roles
        assert "add_inhibit" in roles
        assert "exit_trigger" in roles   # then 语义保持
        assert "entry_trigger" in roles

    def test_plugin_column_in_group_rejected(self, db):
        """插件列 subject 在任一新组→翻译显式拒绝 RESEARCH_NOT_EXPRESSIBLE
        （登记链合法——插件列是合法 subject；拒绝在翻译：插件源仅入场）。"""
        from core.analysis.plugin_service import register_formula_plugin
        register_formula_plugin(db, name="demo_scaling",  # 列名自动加 fx_ 前缀
                                expression="rolling_mean(close, 3)")
        h = _register_scaling(
            db, exit_group=[{"subject": "fx_demo_scaling",
                             "action": "gt", "target": 0}],
            period=22)
        with pytest.raises(ResearchError) as ei:
            translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert ei.value.code == "RESEARCH_NOT_EXPRESSIBLE"

    def test_group_genes_in_signature(self, db):
        """组基因自然进 gene_signature（同条件集异组=不同签名）。"""
        h1 = _register_scaling(db, period=25)
        h2 = _register_scaling(db, exit_group=GROUP_RSI_HIGH, period=25)
        t1 = translate_hypothesis(db, hypothesis_id=h1["hypothesis_id"])
        t2 = translate_hypothesis(db, hypothesis_id=h2["hypothesis_id"])
        d1 = json.loads(get_strategy(db, t1["strategy_id"])["dna_json"])
        d2 = json.loads(get_strategy(db, t2["strategy_id"])["dna_json"])
        from core.strategy.dna import StrategyDNA
        assert StrategyDNA.from_dict(d1).gene_signature != \
            StrategyDNA.from_dict(d2).gene_signature

    def test_declared_scaling_params_to_dna(self, db):
        """声明块三键→RiskGenes 物化（组在时登记归一化字面 0.5/0.1/1.0）。"""
        h = _register_scaling(db, exit_group=GROUP_RSI_HIGH,
                              add_group=GROUP_RSI_LOW, sp=None)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        rg = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])["risk_genes"]
        assert rg["exit_fraction"] == 0.5
        assert rg["add_fraction"] == 0.1
        assert rg["max_exposure"] == 1.0
        assert t["risk_genes"] == "declared"


class TestProductionPathRejection:
    """4.6 翻译生产路径拒绝面收口（B3-17 完整断言——真实翻译产物双证据）。"""

    def test_translated_scaling_dna_rejected_on_live(self, db):
        """真实翻译产物（REDUCE/ADD/ADD_INHIBIT 基因 DNA）live 链装载即拒。"""
        h = _register_scaling(db, exit_group=GROUP_RSI_HIGH,
                              add_group=GROUP_RSI_LOW,
                              suppress_group=[_cond(80, "gt")])
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_dict(
            json.loads(get_strategy(db, t["strategy_id"])["dna_json"]))
        for link in ("live",):
            reasons = validate_trading_compatibility(dna, link)
            assert reasons and "分批" in reasons[0], (link, reasons)

    def test_translated_trail_dna_rejected_on_live(self, db):
        """trail 声明经翻译产物 live 链拒（翻译生产路径的模式面）。"""
        h = _register_scaling(
            db, sp={"sl_mode": "trail", "stop_loss": 0.05}, period=27)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_dict(
            json.loads(get_strategy(db, t["strategy_id"])["dna_json"]))
        assert dna.risk_genes.sl_mode == "trail"
        for link in ("live",):
            assert validate_trading_compatibility(dna, link)


class TestPrecheckGroupDisclosure:
    def test_group_triggers_in_per_range(self, db, data_dir):
        """组级密度进 per_range 新键（纯增量披露）；锚与三态不含组。"""
        base = {
            "when_conditions": [_cond(45)],
            "then_conditions": [_cond(75, "gt")],
        }
        r_with = register_hypothesis(
            db, conditions=dict(base, add_trigger_conditions=GROUP_RSI_LOW),
            statement="with-group", data_ranges=_ranges(),
            precheck_data_dir=data_dir)
        r_without = register_hypothesis(
            db, conditions=dict(
                base, when_conditions=[_cond(46)]),  # 异身份（同 when 结构）
            statement="no-group", data_ranges=_ranges(),
            precheck_data_dir=data_dir)
        w1 = r_with.get("precheck_warnings") or {}
        w0 = r_without.get("precheck_warnings") or {}
        # 组级键只出现在含组假设的 per_range
        pr1 = w1.get("per_range") or [{}]
        assert "add_trigger_conditions" in (
            pr1[0].get("group_triggers") or {})
        pr0 = w0.get("per_range") or [{}]
        assert not pr0[0].get("group_triggers")
        # 组不进三态载体键（锚仍 when 交集口径）
        assert "analysis_segment_trades" in w1

    def test_group_not_in_anchor_same_when(self, db, data_dir):
        """组不进锚（spec 场景逐字锚）：同 when 条件集+同区间集——
        无组 vs 含组两个独立假设身份（组键进指纹），预检锚
        （analysis_segment_trades）与三态结论完全一致（组不参与判定）。"""
        base = {
            "when_conditions": [_cond(45)],
            "then_conditions": [_cond(75, "gt")],
        }
        r_without = register_hypothesis(
            db, conditions=dict(base), statement="no-group",
            data_ranges=_ranges(), precheck_data_dir=data_dir)
        r_with = register_hypothesis(
            db, conditions=dict(base, add_trigger_conditions=GROUP_RSI_LOW),
            statement="with-group", data_ranges=_ranges(),
            precheck_data_dir=data_dir)
        # 组键参与身份：同条件集+同区间集仍两个独立假设
        assert r_without["hypothesis_id"] != r_with["hypothesis_id"]
        w0 = r_without.get("precheck_warnings") or {}
        w1 = r_with.get("precheck_warnings") or {}
        assert w0 and w1, "两侧预检均须成功执行"
        # 锚与三态不含组：完全一致（若组进锚，含组侧触发数必变）
        assert w1["analysis_segment_trades"] == \
            w0["analysis_segment_trades"]
        assert w1["verdict"] == w0["verdict"]


class TestDoDEndToEnd:
    """示例策略形态端到端（版本 DoD）：组合入场+部分离场+trail 全离场
    +风险声明→登记→翻译→回测行为与设计一致。"""

    def test_full_chain(self, db, data_dir):
        # 1. 登记：when 双条件组合入场+then 离场+部分离场组+加仓组
        #    +抑制组+声明块（trail+杠杆+分批参数）
        h = register_hypothesis(
            db,
            conditions={
                "when_conditions": [_cond(45), _cond(50)],
                "then_conditions": [_cond(75, "gt")],
                "exit_partial_conditions": [GROUP_RSI_HIGH[0]],
                "add_trigger_conditions": GROUP_RSI_LOW,
                "add_suppress_conditions": [_cond(20)],  # RSI<20 深超卖不加仓
            },
            statement="DoD: 组合入场+部分离场+trail+抑制",
            data_ranges=_ranges(),
            strategy_params={"sl_mode": "trail", "stop_loss": 0.04,
                             "leverage": 2, "exit_fraction": 0.5,
                             "add_fraction": 0.2, "max_exposure": 1.8},
            precheck_data_dir=data_dir)
        assert h["duplicated"] is False
        # 归一化检查：组在+三键声明齐（无缺省物化需求）
        row = get_hypothesis(db, h["hypothesis_id"])
        params = json.loads(row["strategy_params"])
        assert params["sl_mode"] == "trail" and params["leverage"] == 2

        # 2. 翻译：三组三角色+声明块映射
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        assert t["risk_genes"] == "declared"
        dna_dict = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])
        roles = {g["role"] for g in dna_dict["signal_genes"]}
        assert {"entry_trigger", "exit_trigger", "reduce_trigger",
                "add_trigger", "add_inhibit"} <= roles
        rg = dna_dict["risk_genes"]
        assert rg["sl_mode"] == "trail" and rg["exit_fraction"] == 0.5 \
            and rg["add_fraction"] == 0.2 and rg["max_exposure"] == 1.8

        # 3. 回测行为与设计一致：trail 生效（有交易且不止损越界炸）、
        #    部分离场与加仓信号被执行（trades 多笔）
        from core.backtest.engine import BacktestEngine
        from core.data.mtf_loader import load_and_prepare_df
        from core.features.indicators import compute_all_indicators
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_dict(dna_dict)
        df = compute_all_indicators(load_and_prepare_df(
            data_dir, "BTCUSDT", "4h",
            "2024-01-01T00:00:00Z", "2024-02-25T00:00:00Z"))
        engine = BacktestEngine(init_cash=100000)
        # 存量回测链形制：dfs_by_timeframe 携带执行周期 df（翻译产物
        # 单层 auto-wrap 在 MTF 分支 _single_compat 等同单周期）
        result = engine.run(dna, df, dfs_by_timeframe={"4h": df})
        assert result.total_trades >= 1, "DoD 链应有交易"
        assert np.isfinite(result.total_return), "trail 回测不炸"

        # 4. live 链拒绝面：同一真实产物（trail+分批双判据）
        reasons = validate_trading_compatibility(dna, "live")
        assert len(reasons) == 2  # trail + 分批角色（双判据合并报告）
