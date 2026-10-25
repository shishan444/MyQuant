"""R4 登记面/分析链锚族（fix-mtf-execution-consistency 组 3，T⑥/T⑦ 面）。

- T⑥ 三面同源：执行链合法集==注册表并集==分析链 action_map 含键
  （S6b 主断言在 tests/strategy/test_executor.py——此处分析链分派面
  与翻译链可达面）；
- T⑦.3 嵌套拒绝：inner_action="within_bars_after" 分析链拒绝（白名
  单不含即达）；
- T⑦.4 登记面全通：登记→统计→转化三步全链（登记平铺键 {subject,
  action, target 兼内层阈值, inner_action, window}→转化产物精确嵌套形
  {type, window, inner={type, threshold}}）；
- T⑦.5 披露断言：lookback_any/lookback_all 登记面顺带解锁（window/
  inner_action 键在 ConditionItem 白名单）；
- T①-D 双链对拍：within_bars_after 分析面掩码 vs 执行面信号序列逐位
  相等（含当根事件判别场景——分析链漏 shift(1) 此场景必红）。
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_kernel import (
    _CONDITION_ITEM_KEYS, MtfEvalContext, mask_for_all,
)
from core.analysis.errors import ResearchError
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import StrategyDNA
from core.strategy.executor import (
    EVALUABLE_CONDITION_TYPES, dna_to_signal_set,
)
from core.validation.engine import _LOOKBACK_INNER_ACTIONS


def _df(n: int = 400, seed: int = 61) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0003, 0.01, n)
    close = 60000.0 * np.cumprod(1.0 + rets)
    open_ = np.empty(n)
    open_[0] = 60000.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, 0.005, n)) * close
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread,
                         "close": close,
                         "volume": np.abs(rng.normal(500, 150, n)) + 1.0},
                        index=idx)


class TestT6ThreeFaceSync:
    def test_analysis_action_map_contains_key(self):
        """分析链动作分派表收录（legal_values 自动随键集单源）。"""
        from core.validation import engine as val_engine
        src = open(val_engine.__file__).read()
        assert '"within_bars_after"' in src
        # 分析链 inner 白名单不含 within_bars_after（嵌套防域）
        assert "within_bars_after" not in _LOOKBACK_INNER_ACTIONS

    def test_registry_union_covers_new_action(self):
        """注册表并集覆盖（S6b 主断言的补充面——挂载指标清点）。"""
        from core.features.registry import INDICATOR_REGISTRY
        carriers = [
            name for name, reg in INDICATOR_REGISTRY.items()
            if "within_bars_after" in (reg.supported_conditions or [])]
        assert carriers, "注册表侧无人携带 within_bars_after（S6b 必红）"
        assert "within_bars_after" in EVALUABLE_CONDITION_TYPES


class TestT7RegistrationFace:
    @pytest.fixture()
    def db(self, tmp_path: Path) -> Path:
        d = tmp_path / "q.db"
        init_db_ext(d)
        ensure_research_tables(d)
        return d

    def test_t7_3_nested_action_rejected(self):
        """嵌套拒绝：inner_action="within_bars_after" 分析链拒绝。"""
        df = _df()
        cond = {"subject": {"indicator": "RSI",
                            "params": {"period": 14}},
                "action": "within_bars_after", "target": 30,
                "inner_action": "within_bars_after", "window": 3}
        with pytest.raises(ResearchError) as exc:
            mask_for_all(df, [cond], mtf=None)
        assert exc.value.code == "RESEARCH_PARAM_INVALID"
        assert "inner_action" in str(exc.value.message)

    def test_t7_4_registration_to_translate_full_chain(self, db):
        """登记面全通：登记→统计→转化三步；转化产物精确嵌套形映射
        （登记平铺 {action, target, inner_action, window} → 基因嵌套
        {type, window, inner={type, threshold}}——键形域翻译边界行为锚）。"""
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": {"indicator": "RVOL",
                                 "params": {"period": 20}},
                     "action": "within_bars_after", "target": 1.5,
                     "inner_action": "gt", "window": 5}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 14}},
                     "action": "gt", "target": 65}]},
            statement="R4 登记面全通",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01", "end": "2024-02-01"}]
        )["hypothesis_id"]
        # 统计面可求值（分析链掩码非异常）
        df = _df()
        cond = {"subject": {"indicator": "RVOL", "params": {"period": 20}},
                "action": "within_bars_after", "target": 1.5,
                "inner_action": "gt", "window": 5}
        mask = mask_for_all(df, [cond], mtf=None)
        assert len(mask) == len(df)
        # 转化产物精确嵌套形
        out = translate_hypothesis(db, hypothesis_id=hid)
        from core.persistence.repository.strategy import get_strategy
        row = get_strategy(db, out["strategy_id"])
        genes = json.loads(row["dna_json"])["signal_genes"]
        wba = [g for g in genes
               if g["condition"].get("type") == "within_bars_after"]
        assert len(wba) == 1
        assert wba[0]["condition"]["window"] == 5
        assert wba[0]["condition"]["inner"] == {"type": "gt",
                                                "threshold": 1.5}

    def test_t7_5_lookback_family_unlocked_in_registration(self):
        """披露断言：window/inner_action 键入白名单后 lookback_any/
        all 登记面顺带解锁（行为面扩大属披露）。"""
        assert {"window", "inner_action"} <= _CONDITION_ITEM_KEYS
        from api.schemas import ConditionItem
        fields = ConditionItem.model_fields
        assert "window" in fields and "inner_action" in fields


class TestT1DParityWithinBarsAfter:
    def test_dual_chain_parity_with_current_bar_event(self):
        """T①-D 双链对拍：分析面掩码 vs 执行面信号逐位相等。

        当根事件判别场景：内层事件当根首次成立的位置——分析链若漏
        shift(1) 此场景必红（分析面当根 True、执行面当根 False）。
        """
        df = _df()
        cond_reg = {"subject": {"indicator": "RSI",
                                "params": {"period": 14}},
                    "action": "within_bars_after", "target": 40,
                    "inner_action": "lt", "window": 4}
        # 分析面
        ctx = MtfEvalContext(data_dir=None, symbol="BTCUSDT", base_tf="4h")
        mask = mask_for_all(df, [cond_reg], mtf=ctx)

        # 执行面：等价基因（嵌套形）直造
        from core.strategy.dna import (ExecutionGenes, LogicGenes,
                                       RiskGenes, SignalGene, SignalRole)
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14},
                           SignalRole.ENTRY_TRIGGER, "rsi",
                           {"type": "within_bars_after", "window": 4,
                            "inner": {"type": "lt", "threshold": 40}})],
            execution_genes=ExecutionGenes(timeframe="4h",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"}])
        sig = dna_to_signal_set(dna, df, {"4h": df})

        a = mask.fillna(False).astype(bool)
        e = sig.entries.fillna(False).astype(bool)
        # 当根事件判别：内层 lt(40) 首次成立位 p——两侧同 False
        inner_true = np.flatnonzero(
            (df["rsi_14"] if "rsi_14" in df else _rsi(df)) < 40)
        if len(inner_true):
            p = int(inner_true[0])
            assert e.iloc[p] == False  # noqa: E712 执行面当根排除
        assert int(a.sum()) > 0, "对照侧应有触发"
        pd.testing.assert_series_equal(a, e, check_names=False,
                                       check_freq=False,
                                       obj="T①-D within_bars_after 对拍")


def _rsi(df: pd.DataFrame) -> pd.Series:
    from core.features.indicators import _compute_indicator
    for col, series in _compute_indicator(df, "RSI",
                                          {"period": 14}).items():
        df[col] = series
    return df["rsi_14"]
