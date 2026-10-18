"""注册表准入与不可变测试（build-position-registry 任务 4.1——R1+R8）。

R1 契约（design §4+spec「模型注册表与注册准入」Requirement）：
- _validate_entry 七项准入校验参数化拒六类不完整条目（缺锚/空网格/
  缺 expandable/environments 与 expandable 不一致/version<1/重复
  model_id），错误文案列出所缺元数据项名（spec Scenario「裸条目
  注册被拒」）；
- 公开面白名单锁定（design §2 十符号——模块无 register/注销/改写
  入口，REJ-09 拒热插拔+REJ-06 拒运行中热改；spec Scenario「注册表
  运行时不可变」=无此类 API 面）；
- 两模型在册不变量（version=1/三环境域/expandable=True/三方法齐备
  +output_form 声明）与 get_model 未知 ID 防御（PositioningError）。

R8 契约（design §8+spec「不可展开模型回测侧明确拒绝」Scenario）：
直构 expandable=False 条目（RegistryEntry 可构造——校验在注册不在
构造）经取装配参数路径（expand_for_backtest 真实链）明确拒绝——
PositioningError 文案含 model_id 与支持环境域 ("paper","live")，
不产出装配参数、不静默回退；同条目纸盘语境可用（resolve 正常返回+
纯 Python size_open 可调——纸盘不查可展开性，D-4）；当前两在册
模型 expandable=True（position_mode 全取值 expand 不 raise）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

import core.positioning as pkg
from core.positioning import (
    MODEL_FIXED_PROPORTIONAL,
    MODEL_RISK_BUDGET,
    PositioningError,
    RegistryEntry,
    expand_for_backtest,
    get_model,
    list_models,
    resolve,
)
from core.positioning.interfaces import SizingInput
from core.positioning.models import (
    AnchorCase,
    ParityCase,
    RiskBudgetModel,
    RiskBudgetParams,
)
from core.positioning.registry import _REGISTRY, _validate_entry

# design §2 白名单（公开面 inspect 断言的契约基准）
_WHITELIST = {
    "PositionModel", "SizingInput", "PositioningError", "RegistryEntry",
    "get_model", "list_models", "resolve", "expand_for_backtest",
    "MODEL_FIXED_PROPORTIONAL", "MODEL_RISK_BUDGET",
}
# 包结构自然暴露的子模块属性（import 机制的父包属性——非注册/改写
# 入口；resolve 子模块名被同名公开函数覆盖不单独出现）
_SUBMODULE_ATTRS = {"adapter", "interfaces", "models", "registry"}


class _StubModel:
    """最小三方法实现（直构条目的 implementation 载体）。"""

    output_form = "margin"

    def size_open(self, inp, params) -> float:
        return 1.0

    def size_add(self, equity_now: float, add_fraction: float) -> float:
        return 1.0

    def size_reduce(self, exit_fraction: float) -> float:
        return 1.0


def make_entry(**overrides) -> RegistryEntry:
    """直构注册条目（合法基线+覆写——七项校验的测试输入面）。"""
    base = dict(
        model_id="test-model",
        version=1,
        implementation=_StubModel(),
        expandable=True,
        environments=("backtest", "paper", "live"),
        anchors=(AnchorCase(
            name="stub_open", inputs={"equity": 1.0},
            method="size_open", expected=1.0),),
        parity_grid=(ParityCase(
            name="stub_parity", inputs={}, zone="equal",
            pure_expected=1.0),),
    )
    base.update(overrides)
    return RegistryEntry(**base)


class TestValidateEntry:
    """R1 准入校验：六类不完整条目拒绝（文案含所缺项名）+完整条目放行。"""

    @pytest.mark.parametrize(
        ("overrides", "registered_ids", "expected_keywords"),
        [
            pytest.param(
                {"anchors": ()}, (),
                ["anchors"],
                id="缺手算锚"),
            pytest.param(
                {"parity_grid": ()}, (),
                ["parity_grid"],
                id="空对拍网格"),
            pytest.param(
                {"expandable": None}, (),
                ["expandable"],
                id="缺可展开性声明"),
            pytest.param(
                {"environments": ("paper", "live")}, (),
                ["environments", "expandable"],
                id="expandable-True 但不含 backtest"),
            pytest.param(
                {"expandable": False,
                 "environments": ("backtest", "paper", "live")}, (),
                ["environments", "expandable"],
                id="expandable-False 但含 backtest"),
            pytest.param(
                {"version": 0}, (),
                ["version"],
                id="version 小于 1"),
            pytest.param(
                {}, ("test-model",),
                ["唯一"],
                id="重复 model_id"),
        ])
    def test_rejects_incomplete_entry_with_named_missing_items(
            self, overrides, registered_ids, expected_keywords):
        """六类不完整条目注册即拒，错误文案列出所缺元数据项名。"""
        with pytest.raises(PositioningError) as excinfo:
            _validate_entry(make_entry(**overrides), registered_ids)
        for kw in expected_keywords:
            assert kw in str(excinfo.value), (
                f"错误文案须含所缺项名 {kw!r}，实际: {excinfo.value}")

    def test_accepts_complete_entry(self):
        """对照：七项齐备的合法条目校验放行（不 raise）。"""
        _validate_entry(make_entry())  # 不抛异常即通过

    def test_entry_constructible_without_validation(self):
        """RegistryEntry 可构造（校验在注册不在构造）——直构缺锚条目
        本身不 raise，防线在 _validate_entry/_register。"""
        bare = RegistryEntry(
            model_id="bare", version=1, implementation=_StubModel(),
            expandable=True, environments=("backtest", "paper", "live"),
            anchors=(), parity_grid=())
        assert bare.model_id == "bare"


class TestPublicSurface:
    """R1 公开面白名单（design §2——无注册/注销/改写入口）。"""

    def test_whitelist_locked_no_mutation_api(self):
        """模块符号集==白名单+子模块属性：白名单十符号全在场，任何
        register/注销/改写类符号出现即集合相等破坏。"""
        public = {n for n in dir(pkg) if not n.startswith("_")}
        assert public == _WHITELIST | _SUBMODULE_ATTRS, (
            f"公开面偏离白名单: 多出 {public - _WHITELIST - _SUBMODULE_ATTRS}"
            f"，缺失 {_WHITELIST - public}")
        # 显式负向断言（失败信息可读——REJ-09/REJ-06）
        for forbidden in ("register", "unregister", "register_model",
                          "unregister_model", "set_model", "remove_model",
                          "update_entry"):
            assert not hasattr(pkg, forbidden), (
                f"公开面不得出现注册/改写入口 {forbidden!r}")
        assert sorted(pkg.__all__) == sorted(_WHITELIST)


class TestRegisteredModels:
    """R1 两模型在册不变量+get_model 未知 ID 防御。"""

    def test_two_models_invariants(self):
        """在册序（导入期登记序）+version=1+三环境域+expandable=True
        +三方法齐备+output_form 声明（一号 margin/二号 quantity）。"""
        assert list_models() == [
            MODEL_FIXED_PROPORTIONAL, MODEL_RISK_BUDGET]
        fixed = get_model(MODEL_FIXED_PROPORTIONAL)
        budget = get_model(MODEL_RISK_BUDGET)
        for entry in (fixed, budget):
            assert entry.version == 1
            assert entry.environments == ("backtest", "paper", "live")
            assert entry.expandable is True
            for method in ("size_open", "size_add", "size_reduce"):
                assert callable(getattr(entry.implementation, method))
            assert entry.anchors, "手算锚须非空"
            assert entry.parity_grid, "对拍网格须非空"
        assert fixed.implementation.output_form == "margin"
        assert budget.implementation.output_form == "quantity"

    def test_get_model_unknown_id_raises(self):
        """查无此 ID→PositioningError（防御直构——正常路径两消费方
        只消费 resolve 产出的在册 ID）。"""
        with pytest.raises(PositioningError) as excinfo:
            get_model("no-such-model")
        assert "no-such-model" in str(excinfo.value)


class TestNonExpandableEntry:
    """R8 不可展开拒绝+纸盘可跑（design §8——D-4 结构化拒绝）。"""

    def test_backtest_expansion_rejected_paper_context_runnable(
            self, monkeypatch):
        """直构 expandable=False 条目：取装配参数路径（expand_for_
        backtest 真实链）raise PositioningError——文案含 model_id 与
        支持环境域 ("paper","live")；同条目 resolve 正常返回+纯
        Python size_open 可调（纸盘语境不查可展开性）。

        经 monkeypatch.setitem 将直构条目挂到 risk_budget 槽位（唯一
        能被 resolve 产出的在册 ID 槽）——取装配路径完整经过
        resolve→get_model→可展开性检查，测试后自动恢复。
        """
        paper_only = make_entry(
            model_id=MODEL_RISK_BUDGET,
            implementation=RiskBudgetModel(),
            expandable=False,
            environments=("paper", "live"))
        monkeypatch.setitem(_REGISTRY, MODEL_RISK_BUDGET, paper_only)
        # 回测侧：取装配参数明确拒绝（结构化错误，非静默回退）
        with pytest.raises(PositioningError) as excinfo:
            expand_for_backtest("risk_budget", 0.3, risk_fraction=0.01)
        msg = str(excinfo.value)
        assert MODEL_RISK_BUDGET in msg, f"文案须含 model_id: {msg}"
        assert "paper" in msg and "live" in msg, (
            f"文案须含支持环境域 paper/live: {msg}")
        # 纸盘侧：resolve 正常返回（不查可展开性）+纯 Python 可调
        resolved = resolve("risk_budget", 0.3, risk_fraction=0.01,
                           sl_mode="pct", stop_loss=0.02)
        assert resolved.model_id == MODEL_RISK_BUDGET
        qty = paper_only.implementation.size_open(
            SizingInput(100_000.0, 100.0, None),
            RiskBudgetParams(risk_fraction=0.01, sl_mode="pct",
                             stop_loss=0.02))
        assert qty == pytest.approx(500.0)  # 手算 100000×0.01÷2

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            pytest.param(None, (0.0, 0.0, 0.3), id="None 落一号"),
            pytest.param("fixed", (0.0, 0.0, 0.3), id="fixed 落一号"),
            pytest.param("risk_budget", (1.0, 0.0, 0.3), id="risk_budget"),
            pytest.param("garbage-mode", (0.0, 0.0, 0.3),
                         id="非法值防御忽略落一号"),
        ])
    def test_current_models_expand_all_mode_values(self, mode, expected):
        """当前两在册模型 expandable=True——position_mode 全取值
        （None/"fixed"/"risk_budget"/非法值防御忽略）expand 不 raise
        且产出三元组。"""
        exp = expand_for_backtest(mode, 0.3, None, None)
        assert (exp.position_mode_code, exp.risk_fraction,
                exp.effective_first_size) == expected
