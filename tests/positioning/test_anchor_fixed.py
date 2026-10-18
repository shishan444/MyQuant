"""一号模型手算锚测试（build-position-registry 任务 4.2——R2）。

R2 契约（design §5+spec「一号模型 fixed-proportional 行为规格」四
Scenario）：条目四锚逐例断言——锚期望值（条目 anchors 读入）与字面
常量对照（3000/2000/1200/0.3，禁用实现生成期望——锚数据本身先对
手算标准答案锁定），再以实现复现锚（输入→输出==锚期望）；确定性
（同输入两次调用逐位一致+一号 size_open 不读 base_price——spec
「同输入同输出的确定性」Scenario）。

字面常量推导（人工核算）：
- 首仓缺省档 10000×0.3=3000；首笔声明替换 10000×0.2=2000；
- 加仓滚动基数 12000×0.1=1200（分母=加仓时点权益）；
- 减仓比例 exit_fraction=0.3 直传 0.3。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.positioning import MODEL_FIXED_PROPORTIONAL, get_model
from core.positioning.interfaces import SizingInput
from core.positioning.models import (
    FIXED_PROPORTIONAL_ANCHORS,
    AnchorCase,
    FixedProportionalModel,
    FixedProportionalParams,
)

# 锚名→手算字面期望（spec 四 Scenario 一一对应——独立于条目数据推导）
_LITERAL_EXPECTED = {
    "first_open_default": 3_000.0,
    "first_open_declared": 2_000.0,
    "add_rolling_base": 1_200.0,
    "reduce_fraction": 0.3,
}


def _invoke(model: FixedProportionalModel, anchor: AnchorCase) -> float:
    """锚输入→三方法分派调用（SizingInput 必填位给契约内占位值）。"""
    if anchor.method == "size_open":
        inp = SizingInput(
            equity=anchor.inputs["equity"], base_price=1.0,
            atr_value=None)  # 一号不读基准价/ATR（契约面）
        return model.size_open(
            inp, FixedProportionalParams(
                first_size=anchor.inputs["first_size"]))
    if anchor.method == "size_add":
        return model.size_add(anchor.inputs["equity_now"],
                              anchor.inputs["add_fraction"])
    return model.size_reduce(anchor.inputs["exit_fraction"])


class TestFixedProportionalAnchors:
    """R2 四锚数据驱动（条目锚↔字面常量↔实现输出三层锁定）。"""

    @pytest.mark.parametrize(
        "anchor", FIXED_PROPORTIONAL_ANCHORS,
        ids=[a.name for a in FIXED_PROPORTIONAL_ANCHORS])
    def test_anchor_matches_literal_and_implementation(self, anchor):
        """锚期望==手算字面常量（数据完整性）+实现复现锚期望。"""
        assert anchor.expected == _LITERAL_EXPECTED[anchor.name], (
            f"锚 {anchor.name} 期望值偏离手算标准答案 "
            f"{_LITERAL_EXPECTED[anchor.name]}")
        model = get_model(MODEL_FIXED_PROPORTIONAL).implementation
        assert _invoke(model, anchor) == pytest.approx(
            anchor.expected, rel=1e-12)

    def test_entry_anchors_cover_all_four_scenarios(self):
        """覆盖完备性：四 Scenario 锚全在场（缺一即覆盖缺口）。"""
        assert {a.name for a in FIXED_PROPORTIONAL_ANCHORS} == set(
            _LITERAL_EXPECTED)

    def test_zero_equity_natural_zero_open(self):
        """equity=0 时一号输出 0（design §3 SizingInput 契约：equity
        必填、≤0 时一号输出 0——0×first_size 的自然积，非拒绝分支；
        与二号 0 权益返回 0.0 拒绝的语义区分锚）。"""
        model = get_model(MODEL_FIXED_PROPORTIONAL).implementation
        qty = model.size_open(
            SizingInput(equity=0.0, base_price=100.0),
            FixedProportionalParams(first_size=0.3))
        assert qty == 0.0


class TestFixedProportionalDeterminism:
    """R2 确定性（spec「同输入同输出的确定性」Scenario）。"""

    def test_same_input_same_output_bitwise_and_base_price_not_read(self):
        """同输入两次调用逐位一致（==，无随机/时钟/外部状态）；一号
        size_open 不读 base_price——换基准价输出逐位不变（契约面实证：
        基准价仅二号距离与执行层换算消费）。"""
        model = get_model(MODEL_FIXED_PROPORTIONAL).implementation
        params = FixedProportionalParams(first_size=0.3)
        inp_a = SizingInput(equity=10_000.0, base_price=100.0)
        first = model.size_open(inp_a, params)
        second = model.size_open(inp_a, params)
        assert first == second  # 逐位（同 double 运算链）
        inp_other_price = SizingInput(equity=10_000.0, base_price=7_777.0)
        assert model.size_open(inp_other_price, params) == first
        # size_add/size_reduce 同判（确定性覆盖三方法）
        assert model.size_add(12_000.0, 0.1) == model.size_add(
            12_000.0, 0.1) == 1_200.0
        assert model.size_reduce(0.3) == model.size_reduce(0.3) == 0.3
