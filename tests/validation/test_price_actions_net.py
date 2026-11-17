"""价格比较动作声明域-执行键集对拍特征网（fix-research-c2-price-actions · tester 组）。

从 change delta 契约推导（不从实现反推）：
- Requirement 首句「指标目录声明的每个比较动作在执行层必须有实现」
  ——声明域（INDICATOR_REGISTRY.supported_conditions 并集 + 全谱扫描器
  _PRICE_REL_ACTIONS 第二清单）对拍执行键集（422 detail.legal_values，
  单一事实源）。

已知既有缺口（哨兵锁定，非本分支引入）：eq / lookback_all / wick_touch
三键声明未实现——main 上走「warnings+全 False」静默降级，本分支起
显式 422（缺口显性化而非缺口引入）；处置（补实现或修声明域）上报
team-lead 裁决，哨兵断言锁定缺口集合：扩大即红、修复后红（逼同步
改为全绿断言）。

retire-legacy 10.4 分拣：原 TestSharedEvalPathReject（拒绝冒泡经
_evaluate_conditions / rule_engine / validate_hypothesis 逐路径证明）
已随 core/validation 壳整体退役删除——三个消费方守护对象均不存在；
求值内核 _evaluate_single_condition 存活于 core.analysis.condition_eval
（D-1 纯搬家），本文件对拍面改指该处，断言零改动。
"""

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_eval import _evaluate_single_condition
from core.analysis.errors import ResearchError


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
