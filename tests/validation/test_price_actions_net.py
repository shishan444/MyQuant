"""价格比较动作共用求值路径特征网（fix-research-c2-price-actions · tester 组）。

从 change delta 契约推导（不从实现反推）：
- Scenario「未识别动作结构化拒绝」要求拒绝覆盖全部求值入口——契约
  （design §2/tasks C2.2）声称拒绝经共用 _evaluate_conditions
  （rule_engine 进出场、validate_hypothesis WHEN 链）自动获得，本文件
  逐路径证明（拒绝冒泡 + price_above 正向经共用路径产真实信号）；
- Requirement 首句「指标目录声明的每个比较动作在执行层必须有实现」
  ——声明域（INDICATOR_REGISTRY.supported_conditions 并集 + 全谱扫描器
  _PRICE_REL_ACTIONS 第二清单）对拍执行键集（422 detail.legal_values，
  单一事实源）。

已知既有缺口（哨兵锁定，非本分支引入）：eq / lookback_all / wick_touch
三键声明未实现——main 上走「warnings+全 False」静默降级，本分支起
显式 422（缺口显性化而非缺口引入）；处置（补实现或修声明域）上报
team-lead 裁决，哨兵断言锁定缺口集合：扩大即红、修复后红（逼同步
改为全绿断言）。
"""

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError
from core.validation.engine import _evaluate_conditions, _evaluate_single_condition
from tests.helpers.data_factory import make_ohlcv


@pytest.fixture()
def df() -> pd.DataFrame:
    """300 bar 确定性数据，带已知指标列（ema_20 与 close 有交叉）。"""
    d = make_ohlcv(n=300, freq="1h", start="2024-01-01", seed=11)
    d["ema_20"] = d["close"].rolling(20).mean().shift(1)  # 下移使两向都非空
    return d


class TestSharedEvalPathReject:
    """拒绝冒泡：契约声称的「共用路径自动获得」逐路径证明。"""

    def test_conditions_array_unknown_action_bubbles(self, df):
        """条件数组（AND 组合）中间条件未知 action → 整组求值 raise，
        不被组合逻辑吞成部分结果。"""
        conds = [
            {"subject": "ema_20", "action": "gt", "target": 0},
            {"subject": "ema_20", "action": "nope", "target": 1},
        ]
        with pytest.raises(ResearchError) as ei:
            _evaluate_conditions(df, conds, None)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "condition.action"

    def test_or_branch_reject_not_swallowed(self, df):
        """OR 分支同样冒泡：拒绝语义先于组合语义（半真半假的
        「OR 右侧静默全 False」不成立）。"""
        conds = [
            {"subject": "ema_20", "action": "gt", "target": 0},
            {"subject": "ema_20", "action": "nope", "target": 1,
             "logic": "OR"},
        ]
        with pytest.raises(ResearchError):
            _evaluate_conditions(df, conds, None)

    def test_rule_engine_unknown_action_raises(self, tmp_path: Path, df):
        """rule_engine 进出场链：entry 未知 action → ResearchError，
        不再返回 0 交易的「看似正常」RuleResult（旧静默缺陷形态）。"""
        from core.validation.rule_engine import evaluate_rules
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        with pytest.raises(ResearchError) as ei:
            evaluate_rules("BTCUSDT", "1h", "2024-01-05", "2024-01-20",
                           entry_conditions=[
                               {"subject": "ema_20", "action": "stays_above",
                                "target": 0}],
                           exit_conditions=[
                               {"subject": "ema_20", "action": "lt",
                                "target": 0}],
                           data_dir=str(data_dir))
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "price_above" in ei.value.detail["legal_values"]

    def test_rule_engine_price_above_positive(self, tmp_path: Path, df):
        """rule_engine 正向：price_above 经共用路径产真实买入信号
        （buy 与 sell 非空、且逐 bar 落在 close>ema_20 的位置上）。"""
        from core.validation.rule_engine import evaluate_rules
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        result = evaluate_rules(
            "BTCUSDT", "1h", "2024-01-05", "2024-01-20",
            entry_conditions=[
                {"subject": "ema_20", "action": "price_above", "target": 0}],
            exit_conditions=[
                {"subject": "ema_20", "action": "price_below", "target": 0}],
            data_dir=str(data_dir))
        assert result.buy_signals, "price_above 入场须产真实买入信号"
        assert result.sell_signals, "price_below 出场须产真实卖出信号"
        for sig in result.buy_signals:
            ts = pd.Timestamp(sig.time)
            assert df.loc[ts, "close"] > df.loc[ts, "ema_20"]

    def test_validate_hypothesis_when_unknown_raises(self, tmp_path: Path, df):
        """validate_hypothesis（WHEN 链，engine.py _evaluate_conditions
        消费方）：when 未知 action → raise，不静默返回 0 触发。"""
        from core.validation.engine import validate_hypothesis
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        with pytest.raises(ResearchError) as ei:
            validate_hypothesis(
                "BTCUSDT", "1h", "2024-01-05", "2024-01-20",
                when_conditions=[
                    {"subject": "ema_20", "action": "nope", "target": 0}],
                then_conditions=[
                    {"subject": "ema_20", "action": "gt", "target": 0}],
                data_dir=str(data_dir))
        assert ei.value.code == "RESEARCH_PARAM_INVALID"


class TestDeclaredActionsCoverage:
    """Requirement 首句对拍：声明域 ⊆ 执行键集（legal_values 单一事实源）。"""

    @staticmethod
    def _execution_keys() -> set:
        """从受控行为取执行键集：422 拒绝 detail.legal_values（运行时
        action_map 键集——契约指定的单一事实源，非静态抄录）。"""
        probe = pd.DataFrame({"close": [1.0, 2.0], "ema_20": [1.5, 1.5]})
        try:
            _evaluate_single_condition(
                probe, {"subject": "ema_20", "action": "__probe__",
                        "target": 0}, None)
        except ResearchError as e:
            return set(e.detail["legal_values"])
        raise AssertionError("探针动作未触发结构化拒绝——拒绝契约失效")

    @staticmethod
    def _declared_keys() -> set:
        from core.features.registry import INDICATOR_REGISTRY
        from core.analysis.templates.screen_patterns import _PRICE_REL_ACTIONS
        declared = set()
        for defn in INDICATOR_REGISTRY.values():
            declared.update(defn.supported_conditions)
        for actions in _PRICE_REL_ACTIONS.values():
            declared.update(actions)
        return declared

    def test_price_actions_covered_on_all_faces(self):
        """本批次契约主面全绿断言：price_above/price_below 在声明域、
        执行键集、翻译面 _ACTION_MAP 三处齐备（统计与翻译两侧声明域
        一致的增量面）。"""
        from core.analysis.hypothesis_to_strategy import _ACTION_MAP
        declared = self._declared_keys()
        execution = self._execution_keys()
        for action in ("price_above", "price_below"):
            assert action in declared, f"{action} 未在指标目录声明"
            assert action in execution, f"{action} 执行层无实现"
            assert action in _ACTION_MAP, f"{action} 翻译面无映射"

    def test_declared_subset_of_execution_sentinel(self):
        """哨兵（2026-10-13 用户裁决能力完整方向后转全绿断言）：三键
        （eq/lookback_all/wick_touch）已补统计面实现（engine action_map
        对齐回测面语义）——声明域 ⊆ 执行键集恒成立，新增缺口即红。

        历史：本断言曾锁定三键缺口 {eq, lookback_all, wick_touch}
        （补实现前），按设计「缺口收窄后改为空集断言」执行转正。
        """
        missing = self._declared_keys() - self._execution_keys()
        assert missing == set(), (
            f"声明域与执行键集出现新缺口: {sorted(missing)}——"
            "声明了的动作必须有实现（对拍①同契约）")
