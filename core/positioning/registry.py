"""模型注册表（声明式静态注册+准入校验+只读查询）。

职能：仓位模型注册表本体——模型 ID→RegistryEntry（实现+四件元
数据：可展开性声明/手算锚/对拍网格/条目版本）。注册为代码内声明
式静态注册：模块导入期依次 _register 登记两模型，进程内不可变
（REJ-09 拒热插拔配置系统、REJ-06 拒运行中热改——无任何运行时
注册/注销/改写公开入口，公开面仅 get_model/list_models）。

注册准入校验（防裸条目入库）：缺可展开性声明、手算锚或对拍网格
任一件（以及 ID 空/重复、version<1、environments 与可展开性声明
不一致）在注册时即拒——import 期失败，错误文案列出所缺元数据项。
条目版本化纪律：锚/网格/可展开性声明变更须 bump version。

消费方：resolve/adapter（模型 ID 查条目可展开性）、纸盘 ledger
（取条目 implementation 纯 Python 消费）、tests/positioning/
（准入校验与锚/网格的数据驱动断言面）。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.positioning.interfaces import PositionModel, PositioningError
from core.positioning.models import (
    AnchorCase,
    ParityCase,
    FixedProportionalModel,
    MODEL_FIXED_PROPORTIONAL,
    MODEL_RISK_BUDGET,
    FIXED_PROPORTIONAL_ANCHORS,
    FIXED_PROPORTIONAL_PARITY_GRID,
    RiskBudgetModel,
    RISK_BUDGET_ANCHORS,
    RISK_BUDGET_PARITY_GRID,
)


@dataclass(frozen=True)
class RegistryEntry:
    """注册条目（四件元数据齐备的模型登记单元）。

    Attributes:
        model_id: 模型 ID（D-5 常量——"fixed-proportional"|
            "risk_budget"）。
        version: 条目版本（初始 1；锚/网格/可展开性声明变更须 bump）。
        implementation: 三方法纯 Python 实现。
        expandable: 可展开性声明（能否展开为回测装配参数）。
        environments: 支持环境域（expandable=True→含 "backtest"；
            False→仅逐根环境 ("paper","live")）。
        anchors: 手算锚（输入+期望输出的具体数值样例）。
        parity_grid: 双实现对拍网格（数据网格内嵌+断言器外驻
            tests——见 models.py 模块 docstring 承载分工）。
    """

    model_id: str
    version: int
    implementation: PositionModel
    expandable: bool
    environments: tuple[str, ...]
    anchors: tuple[AnchorCase, ...]
    parity_grid: tuple[ParityCase, ...]


def _validate_entry(entry: RegistryEntry,
                    registered_ids: tuple[str, ...] = ()) -> None:
    """注册准入校验（纯函数，测试直接调）。

    七项校验：model_id 非空且唯一（registered_ids 传入在册集判重）/
    version≥1/implementation 具备三方法/expandable 布尔/environments
    非空且与 expandable 一致/anchors 非空/parity_grid 非空。任一不
    满足 raise PositioningError，文案列出所缺元数据项（spec Scenario
    「裸条目注册被拒」）。

    Args:
        entry: 待校验条目。
        registered_ids: 已在册模型 ID 集（重复检测；独立调用缺省空）。

    Raises:
        PositioningError: 任一准入项不满足。
    """
    missing: list[str] = []
    if not entry.model_id:
        missing.append("model_id（非空）")
    if entry.model_id in registered_ids:
        missing.append(f"model_id 唯一（与已注册重复: "
                       f"{entry.model_id!r}）")
    if not isinstance(entry.version, int) or entry.version < 1:
        missing.append("version（≥1）")
    lacks_methods = [
        m for m in ("size_open", "size_add", "size_reduce")
        if not callable(getattr(entry.implementation, m, None))]
    if lacks_methods:
        missing.append(
            f"implementation 三方法（缺 {lacks_methods}）")
    if not isinstance(entry.expandable, bool):
        missing.append("expandable（布尔声明）")
    if not entry.environments:
        missing.append("environments（非空）")
    elif entry.expandable and "backtest" not in entry.environments:
        missing.append('environments 与 expandable 一致（expandable='
                       'True 须含 "backtest"）')
    elif not entry.expandable and "backtest" in entry.environments:
        missing.append('environments 与 expandable 一致（expandable='
                       'False 不得含 "backtest"）')
    if not entry.anchors:
        missing.append("anchors（手算锚非空）")
    if not entry.parity_grid:
        missing.append("parity_grid（对拍网格非空）")
    if missing:
        raise PositioningError(
            f"注册条目准入校验失败（model_id={entry.model_id!r}）"
            f"——缺: {'；'.join(missing)}")


# 注册表本体（模块级私有——声明式静态注册，无运行时改写入口）
_REGISTRY: dict[str, RegistryEntry] = {}


def _register(entry: RegistryEntry) -> None:
    """导入期登记单点（校验通过才入表；重复 ID 即拒）。"""
    _validate_entry(entry, tuple(_REGISTRY))
    _REGISTRY[entry.model_id] = entry


def get_model(model_id: str) -> RegistryEntry:
    """查注册条目（只读）。

    Args:
        model_id: 模型 ID。

    Returns:
        RegistryEntry: 在册条目。

    Raises:
        PositioningError: 查无此 ID（防御直构——正常路径两消费方
            只消费 resolve 产出的在册 ID）。
    """
    try:
        return _REGISTRY[model_id]
    except KeyError:
        raise PositioningError(
            f"未知仓位模型 ID: {model_id!r}（在册: {list_models()}）"
        ) from None


def list_models() -> list[str]:
    """在册模型 ID 列表（注册序——只读快照）。"""
    return list(_REGISTRY)


# 导入期静态登记（两模型条目：可展开、三环境域、版本 1）
_register(RegistryEntry(
    model_id=MODEL_FIXED_PROPORTIONAL,
    version=1,
    implementation=FixedProportionalModel(),
    expandable=True,
    environments=("backtest", "paper", "live"),
    anchors=FIXED_PROPORTIONAL_ANCHORS,
    parity_grid=FIXED_PROPORTIONAL_PARITY_GRID,
))
_register(RegistryEntry(
    model_id=MODEL_RISK_BUDGET,
    version=1,
    implementation=RiskBudgetModel(),
    expandable=True,
    environments=("backtest", "paper", "live"),
    anchors=RISK_BUDGET_ANCHORS,
    parity_grid=RISK_BUDGET_PARITY_GRID,
))
