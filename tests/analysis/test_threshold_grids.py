"""条件阈值刻度表特征锚（修复期特征网：记录现状数据）。

threshold_grids.VALUE_GRIDS 是扫描域（screen_patterns）与检查域
（param_sweep）的共享事实源——模块 docstring 明示「MUST 同源，属正确性
要求而非复用便利」。既有守护为两消费者端到端间接覆盖（tests/README
四区登记），本文件补直接特征锚：结构形制 + 代表刻度冻结 + 两消费者
引用同一对象——修复期任何对该表的意外改动（收敛/重排/改值）在此即红。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.threshold_grids import VALUE_GRIDS

# 代表刻度冻结锚（值变更=刻度事实变更，须显式过此测试）
FROZEN_SPOTS = {
    "RSI": {None: {"lt": [20, 25, 30, 35], "gt": [65, 70, 75, 80]}},
    "MFI": {None: {"lt": [20, 25, 30], "gt": [70, 75, 80]}},
    "Williams %R": {None: {"lt": [-90, -80], "gt": [-20, -10]}},
    "CMO": {None: {"lt": [-40, -25], "gt": [25, 40]}},
    "TRIX": {None: {"lt": [0], "gt": [0]}},
    "MACD": {"macd_histogram": {"lt": [0], "gt": [0]}},
    "RVOL": {None: {"gt": [1.5, 2.0, 3.0], "lt": [0.5, 0.8]}},
    "OI_ZScore": {None: {"lt": [-2.0], "gt": [2.0]}},
}


class TestValueGridsShape:
    def test_三层结构_值为非空浮点刻度列表(self) -> None:
        for indicator, selectors in VALUE_GRIDS.items():
            assert isinstance(indicator, str) and indicator
            for selector, actions in selectors.items():
                assert selector is None or isinstance(selector, str)
                assert set(actions) <= {"lt", "gt"}, (
                    f"{indicator}/{selector} 出现非 lt/gt 动作键")
                for action, grid in actions.items():
                    assert isinstance(grid, list) and grid, (
                        f"{indicator}/{selector}/{action} 刻度列表为空")
                    # 现状：int/float 混合（TRIX/MACD 零刻度为 int 字面量）
                    for v in grid:
                        assert isinstance(v, (int, float)) and not isinstance(v, bool), (
                            f"{indicator}/{selector}/{action} 含非数值: {v!r}")

    def test_同动作刻度无重复(self) -> None:
        # 次序现状不统一（Williams %R lt 升序、VROC/CMF lt 为绝对值
        # 递增序），不锚次序；锚无重复——重复刻度会虚增扫描组合数
        for indicator, selectors in VALUE_GRIDS.items():
            for selector, actions in selectors.items():
                for action, grid in actions.items():
                    assert len(grid) == len(set(grid)), (
                        f"{indicator}/{selector}/{action} 刻度重复: {grid}")

    def test_指标清单冻结(self) -> None:
        assert set(VALUE_GRIDS) == set(FROZEN_SPOTS) | {
            "CCI", "ADX", "Aroon", "ROC", "Stochastic",
            "VROC", "CMF", "FundingZScore", "OI_ChangeRate",
            "OIPriceDivergence",
        }

    @pytest.mark.parametrize(
        ("indicator", "expected"),
        [(k, v) for k, v in FROZEN_SPOTS.items()],
        ids=lambda v: str(v),
    )
    def test_代表刻度值冻结(self, indicator: str, expected: dict) -> None:
        assert VALUE_GRIDS[indicator] == expected

    def test_多输出指标按列选择器分档(self) -> None:
        # Aroon / Stochastic / MACD：多输出指标以列选择器区分刻度
        assert set(VALUE_GRIDS["Aroon"]) == {"aroon_up", "aroon_down", "aroon_osc"}
        assert set(VALUE_GRIDS["Stochastic"]) == {"stoch_k", "stoch_d"}
        assert set(VALUE_GRIDS["MACD"]) == {"macd_histogram"}


class TestSingleSourceOfTruth:
    def test_两消费者引用同一对象(self) -> None:
        # import 绑定同一 dict：扫描域与检查域刻度同源的最强锚
        #（任何一侧改为拷贝/重建即破坏同源，此锚即红）。
        # 注意 templates/__init__ 把 screen_patterns 名字绑定为模板
        # 函数（遮蔽模块属性），模块对象须经 sys.modules 取
        import core.analysis.templates  # noqa: F401 触发包初始化载入两模块
        import sys
        sp_mod = sys.modules["core.analysis.templates.screen_patterns"]
        ps_mod = sys.modules["core.analysis.templates.param_sweep"]
        assert sp_mod._VALUE_GRIDS is VALUE_GRIDS
        assert ps_mod.VALUE_GRIDS is VALUE_GRIDS
