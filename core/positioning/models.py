"""仓位模型实现与条目元数据（一号 fixed-proportional/二号 risk_budget）。

职能：两模型纯 Python 三方法实现（现状语义的注册化，不是新行为）+
锚数据与对拍网格数据（注册条目的元数据件）。身份常量（模型 ID——
D-5 形制）与参数包（resolve 产出）同住本模块。

一号 fixed-proportional：fixed 档定仓现状语义（size_open 输出目标
保证金=权益×首笔生效比例）。二号 risk_budget：risk_budget 档现状
收编（size_open 输出目标数量=权益×risk_fraction÷止损距离——距离
三档分派语义自 ledger._stop_distance 移驻：pct/trail=基准价×
stop_loss 比例域（D-6 终态）、atr=stop_loss×该 bar ATR）。两模型
互不继承（注册面并列独立条目）；size_add/size_reduce 两模型同式
各自实现（spec「首仓制」裁定：risk_budget 档的加仓减仓不走风险
预算法）。

拒绝语义（第二逻辑源禁令）：二号拒绝四源以返回值 0.0 表达不抛
异常——stop_loss≤0、base_price≤0 或 equity≤0、atr 模式下 ATR
None/NaN/≤0；risk_fraction 物化 0（直构防御值）时 qty 恒 0，数值
上等同拒绝。一号无拒绝分支（现状 fixed 档无防御）。输出裸值不含
cap 截断（护栏住执行层——leverage/max_exposure 不进本模块任何
输入，结构性分离）。

对拍谓词承载分工：本模块内嵌的是数据网格（案例+期望值+断言域
标注）；「纯 Python vs njit」的等值断言器住 tests/positioning/
test_parity.py，njit 侧执行器（引擎 risk_budget_qty）由测试注入
——positioning 零上层依赖由此成立。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.positioning.interfaces import SizingInput

# 模型 ID 常量（D-5：沿用 position_mode 值域字面，零翻译）
MODEL_FIXED_PROPORTIONAL = "fixed-proportional"
MODEL_RISK_BUDGET = "risk_budget"


# ── 参数包（resolve 产出，frozen——物化后参数不可变） ──────────


@dataclass(frozen=True)
class FixedProportionalParams:
    """一号参数包（首笔生效比例——initial_fraction 物化>0 优先）。"""

    first_size: float


@dataclass(frozen=True)
class RiskBudgetParams:
    """二号参数包（risk_fraction 物化 None→0.0——正常路径装载校验
    已强制声明，0 为直构防御值）。

    Attributes:
        risk_fraction: 每笔风险预算比例（预算额=权益×本值）。
        sl_mode: 距离分派档位 "pct"|"atr"|"trail"。
        stop_loss: pct/trail=比例域、atr=ATR 倍数。
    """

    risk_fraction: float
    sl_mode: str
    stop_loss: float


# ── 一号实现 ────────────────────────────────────────────────


class FixedProportionalModel:
    """一号：固定比例定仓（output_form="margin"）。

    三方法均为现状两环境同式的注册化：size_open=equity×first_size
    （引擎侧 effective_first_size×权益经 vbt percent、纸盘侧
    margin=equity×first_size）；size_add=equity_now×add_fraction
    （滚动基数——分母为加仓时点权益）；size_reduce=exit_fraction
    直传（比例语义）。
    """

    output_form = "margin"

    def size_open(self, inp: SizingInput,
                  params: FixedProportionalParams) -> float:
        """开仓目标保证金=当时权益×首笔生效比例。"""
        return inp.equity * params.first_size

    def size_add(self, equity_now: float, add_fraction: float) -> float:
        """加仓目标保证金=加仓时点权益×add_fraction（滚动基数）。"""
        return equity_now * add_fraction

    def size_reduce(self, exit_fraction: float) -> float:
        """减仓比例直传（无量纲）。"""
        return exit_fraction


# ── 二号实现 ────────────────────────────────────────────────


class RiskBudgetModel:
    """二号：风险预算定仓（output_form="quantity"，现状收编）。

    主式 qty=equity×risk_fraction÷距离（每笔风险额恒等式
    qty×dist=equity×risk_fraction，未触 cap 时成立）；距离三档分派
    自 ledger._stop_distance 移驻。输出裸值不含 cap 截断（护栏住
    执行层）。
    """

    output_form = "quantity"

    def size_open(self, inp: SizingInput,
                  params: RiskBudgetParams) -> float:
        """开仓目标数量（拒绝四源→0.0，逐源对齐现状防御链）。"""
        if (params.stop_loss <= 0.0 or inp.base_price <= 0.0
                or inp.equity <= 0.0):
            return 0.0
        if params.sl_mode == "atr":
            atr = inp.atr_value
            if atr is None or atr != atr or atr <= 0.0:
                return 0.0
            dist = params.stop_loss * atr
        else:
            # pct/trail 同为比例域（基准价×stop_loss——D-6 终态）
            dist = inp.base_price * params.stop_loss
        return inp.equity * params.risk_fraction / dist

    def size_add(self, equity_now: float, add_fraction: float) -> float:
        """加仓目标保证金=加仓时点权益×add_fraction（与一号同式各自
        实现——首仓制）。"""
        return equity_now * add_fraction

    def size_reduce(self, exit_fraction: float) -> float:
        """减仓比例直传（与一号同式各自实现）。"""
        return exit_fraction


# ── 条目元数据载体（锚案例/对拍案例——与数据就近） ─────────────


@dataclass(frozen=True)
class AnchorCase:
    """手算锚案例（具体数值样例的标准答案——期望值为字面常量，
    禁用实现生成）。

    Attributes:
        name: 锚名（kebab/snake 语义标识）。
        inputs: 输入（权益/基准价/ATR/参数包字段）。
        method: 方法名 "size_open"|"size_add"|"size_reduce"。
        expected: 期望输出（手算字面值）。
    """

    name: str
    inputs: dict
    method: str
    expected: float


@dataclass(frozen=True)
class ParityCase:
    """双实现对拍网格案例（数据网格内嵌条目+断言器外驻 tests）。

    inputs 键集（二号）：equity/base_price/atr_value/sl_mode/
    stop_loss/risk_fraction/stop_dist/leverage/max_exposure——
    stop_dist/leverage/max_exposure 为 njit 侧直调输入（纯 Python
    侧不消费）；一号键集按方法自然缩减。

    Attributes:
        name: 案例名。
        inputs: 两实现各自消费的输入（见上）。
        zone: 断言域——"equal"=两实现等值断言/"reject"=拒绝等值
            断言/"cap"=截断行为各自断言（等值断言由执行层对拍承载）。
        pure_expected: 纯 Python 侧手算期望（裸值）。
    """

    name: str
    inputs: dict
    zone: str
    pure_expected: float


# ── 一号锚与网格（四锚=spec 四 Scenario 一一对应） ─────────────

FIXED_PROPORTIONAL_ANCHORS: tuple[AnchorCase, ...] = (
    AnchorCase(  # 首仓缺省档
        name="first_open_default",
        inputs={"equity": 10_000.0, "first_size": 0.3},
        method="size_open", expected=3_000.0),
    AnchorCase(  # 首笔声明替换（initial_fraction=0.2）
        name="first_open_declared",
        inputs={"equity": 10_000.0, "first_size": 0.2},
        method="size_open", expected=2_000.0),
    AnchorCase(  # 加仓滚动基数
        name="add_rolling_base",
        inputs={"equity_now": 12_000.0, "add_fraction": 0.1},
        method="size_add", expected=1_200.0),
    AnchorCase(  # 减仓比例直传
        name="reduce_fraction",
        inputs={"exit_fraction": 0.3},
        method="size_reduce", expected=0.3),
)

# 一号无 njit 数值对拍面（其回测消费形制=vbt percent 装配路径，数值
# 等值由受控等价 test_fixed_qty_and_margin_equal 承载）；网格按准入
# 校验最低形制在位（equal 域手算锚数据）。
FIXED_PROPORTIONAL_PARITY_GRID: tuple[ParityCase, ...] = (
    ParityCase(
        name="open_margin_default",
        inputs={"equity": 10_000.0, "first_size": 0.3,
                "method": "size_open"},
        zone="equal", pure_expected=3_000.0),
    ParityCase(
        name="add_margin_rolling",
        inputs={"equity_now": 12_000.0, "add_fraction": 0.1,
                "method": "size_add"},
        zone="equal", pure_expected=1_200.0),
)

# ── 二号锚与网格 ────────────────────────────────────────────

RISK_BUDGET_ANCHORS: tuple[AnchorCase, ...] = (
    AnchorCase(  # 恒等式锚：500×2=100000×0.01=1000
        name="identity_untruncated",
        inputs={"equity": 100_000.0, "risk_fraction": 0.01,
                "base_price": 100.0, "sl_mode": "pct",
                "stop_loss": 0.02},
        method="size_open", expected=500.0),
    AnchorCase(  # 裸值锚（护栏分离）：名义 200000 超 cap 也不截断
        name="bare_value_over_cap",
        inputs={"equity": 100_000.0, "risk_fraction": 0.01,
                "base_price": 100.0, "sl_mode": "pct",
                "stop_loss": 0.005},
        method="size_open", expected=2_000.0),
)

RISK_BUDGET_PARITY_GRID: tuple[ParityCase, ...] = (
    # equal 域（名义<cap——纯 Python size_open == 引擎 risk_budget_qty
    # 直调 qty，多组含三档距离）
    ParityCase(
        name="pct_basic",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "sl_mode": "pct", "stop_loss": 0.02,
                "risk_fraction": 0.01, "stop_dist": 2.0,
                "leverage": 1.0, "max_exposure": 1.0},
        zone="equal", pure_expected=500.0),
    ParityCase(
        name="pct_lev2",
        inputs={"equity": 50_000.0, "base_price": 50.0,
                "sl_mode": "pct", "stop_loss": 0.04,
                "risk_fraction": 0.02, "stop_dist": 2.0,
                "leverage": 2.0, "max_exposure": 1.0},
        zone="equal", pure_expected=500.0),
    ParityCase(
        name="atr_distance",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "atr_value": 2.5, "sl_mode": "atr", "stop_loss": 2.0,
                "risk_fraction": 0.02, "stop_dist": 5.0,
                "leverage": 1.0, "max_exposure": 1.0},
        zone="equal", pure_expected=400.0),
    ParityCase(
        name="trail_distance",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "sl_mode": "trail", "stop_loss": 0.05,
                "risk_fraction": 0.01, "stop_dist": 5.0,
                "leverage": 3.0, "max_exposure": 1.0},
        zone="equal", pure_expected=200.0),
    # reject 域（距离不可定义/价格/权益非正——纯 Python 0.0 ==
    # risk_budget_qty (0.0, 0.0)）
    ParityCase(
        name="reject_no_stop",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "sl_mode": "pct", "stop_loss": 0.0,
                "risk_fraction": 0.01, "stop_dist": 0.0,
                "leverage": 1.0, "max_exposure": 1.0},
        zone="reject", pure_expected=0.0),
    ParityCase(
        name="reject_bad_price",
        inputs={"equity": 100_000.0, "base_price": 0.0,
                "sl_mode": "pct", "stop_loss": 0.02,
                "risk_fraction": 0.01, "stop_dist": 0.0,
                "leverage": 1.0, "max_exposure": 1.0},
        zone="reject", pure_expected=0.0),
    ParityCase(
        name="reject_no_equity",
        inputs={"equity": 0.0, "base_price": 100.0,
                "sl_mode": "pct", "stop_loss": 0.02,
                "risk_fraction": 0.01, "stop_dist": 2.0,
                "leverage": 1.0, "max_exposure": 1.0},
        zone="reject", pure_expected=0.0),
    ParityCase(
        name="reject_atr_invalid",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "atr_value": None, "sl_mode": "atr", "stop_loss": 2.0,
                "risk_fraction": 0.02, "stop_dist": float("nan"),
                "leverage": 1.0, "max_exposure": 1.0},
        zone="reject", pure_expected=0.0),
    # cap 域（名义>cap——纯 Python 裸值不截断断言+risk_budget_qty
    # (cap/price, capped=1.0) 在位断言，两侧行为各自断言）
    ParityCase(
        name="cap_bare_2000",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "sl_mode": "pct", "stop_loss": 0.005,
                "risk_fraction": 0.01, "stop_dist": 0.5,
                "leverage": 1.0, "max_exposure": 1.0},
        zone="cap", pure_expected=2_000.0),
    ParityCase(
        name="cap_ledger_anchor",
        inputs={"equity": 100_000.0, "base_price": 100.0,
                "sl_mode": "pct", "stop_loss": 0.01,
                "risk_fraction": 0.5, "stop_dist": 1.0,
                "leverage": 10.0, "max_exposure": 1.0},
        zone="cap", pure_expected=50_000.0),
)
