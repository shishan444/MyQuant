"""vectorbt 回测引擎（from_order_func 实时风险管理封装）。

职能：把 StrategyDNA 信号在 OHLCV 数据上执行为 BacktestResult——
单策略 run/run_with_portfolio 与全种群 batch_run（信号堆叠 (N, M)
矩阵单次 vbt 调用、逐列即逐个体）两形制。

价值与服务对象：全部回测指标（胜率/净期望/清算判定）的计算权威，
「检查与考试同一量尺」的量尺本体——直接消费方为进化评估链
（evolution/evaluation、evolution/engine）、策略验证链（api/runner、
routes/strategies/verify、compare）与策略手动回测路由链
（routes/strategies/backtest + services/backtest_service）；间接经
backtest_bridge.run_backtest_on_range 服务 validation_service 四
路径（isolated/random_window/sandbox/staged）与 exit_sweep
（0.4.1 R6）。S6 三道门判定数据亦源于此。
ENGINE_SUPPORTED_SL_MODES 消费方=exit_sweep 模式校验（0.4.2
待扩 (pct, atr, trail)）。

职责边界：执行与指标计算（费滑/杠杆放大/资金费/清算等效口径）；
信号生成归 strategy 域（executor 产 SignalSet），清算维持率档位
归 core/market_model（B7.1 单一权威——本模块只引用不持有字面量）。
"""

from __future__ import annotations

import math
import numpy as np
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio.enums import NoOrder
from vectorbt.portfolio import nb as vbt_nb

from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signals, dna_to_signal_set, batch_signal_sets, clear_indicator_cache
# ATR 装载校验显式拒绝用（轻量叶模块——错误码族与 research 域同源；
# engine 不引入 analysis 聚合包，仅此单点）
from core.analysis.errors import ResearchError

logger = logging.getLogger(__name__)

# B7.1：清算口径移交 core/market_model（币安 USDT-M 分层 mmr，权益<
# 仓位名义×档位 mmr 触发）——本模块不再持有维持率字面量（单一权威源）。
from core.market_model import maintenance_threshold as _mm_threshold
from core.market_model.liquidation import tier_arrays as _mm_tier_arrays

# 0.4.1 Q1 裁决（add-research-analysis-tools）：引擎止损真实分派域。
# 语义=引擎已实现的真实分派集；区别于声明域 SL_MODES（core/strategy/
# dna.py——取值域与分派集是两个概念）。消费面纪律：exit_sweep 模式
# 校验只 import 此常量、禁引声明域常量。0.4.2（add-strategy-
# expressiveness）实施 order_func_nb 三分派（tasks 3.1/3.2）后扩展
# 至 ("pct", "atr", "trail")——本扩展与分派实现同任务原子交付（先扩
# 常量后实现=引用式消费方立即放行未实现模式）。
ENGINE_SUPPORTED_SL_MODES: tuple[str, ...] = ("pct", "atr", "trail")

# sl_mode 字符串 → order_func_nb 数值分派码（numba 侧无字符串比较，
# 装配层单源映射；0.4.2 R2）
_SL_MODE_CODE = {"pct": 0.0, "atr": 1.0, "trail": 2.0}


def sl_mode_code(mode: str) -> float:
    """sl_mode → 分派码（未知模式显式拒——review-042 CARD-13）。

    上游 pattern/validator/声明块校验已拦正常路径的非法值；此处 raise
    是库内脏数据防线：dna_json 被直改后 sl_mode 破损时，静默按 pct
    兜底会让 atr/trail 止损语义无声丢失（回测失真不可察）——违反
    「不静默退化」纪律。进化算子只产 pct/atr（SL_MODES 内，trail 不
    在进化面），batch_run 装配层调用无新增拒绝面。

    Raises:
        ValueError: sl_mode 不在合法档位
    """
    try:
        return _SL_MODE_CODE[mode]
    except KeyError:
        raise ValueError(
            f"未知 sl_mode: {mode!r}（合法: {sorted(_SL_MODE_CODE)}）"
            "——引擎装配层不静默按 pct 兜底") from None


def _load_atr_column(enhanced_df: pd.DataFrame, atr_period: int) -> np.ndarray:
    """ATR 取列链（0.4.2 R2，sl_mode="atr" 装载层）。

    动态取列 ``atr_{atr_period}``（不写死 atr_14——pipeline._extract_atr
    同构）；列缺时经 _compute_indicator 现算（validation_service 同款
    先例）；现算不可得（数据不足）显式拒。数值校验（Q-D 装载校验）：
    全 NaN 拒、首个非 NaN 之后无 NaN（中段数据洞拒）、值>0（≤0 拒）
    ——头部预热 NaN（前 atr_period bar 周期性缺失）合法，引擎内 NaN
    bar 该 bar 无 SL 约束（沿交易链 NaN guard 先例）。

    Raises:
        ResearchError: RESEARCH_DATA_INSUFFICIENT（ATR 数据不足族）
    """
    col = f"atr_{atr_period}"
    if col in enhanced_df.columns:
        series = enhanced_df[col]
    else:
        from core.features.indicators import _compute_indicator
        computed = _compute_indicator(enhanced_df, "ATR", {"period": atr_period})
        if col not in computed:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"ATR({atr_period}) 列缺失且现算不可得（数据不足——"
                "区间短于周期或指标计算失败）",
                detail={"field": f"atr_{atr_period}",
                        "atr_period": atr_period})
        series = computed[col]

    arr = pd.Series(series).values.astype(np.float64)
    valid = ~np.isnan(arr)
    n_valid = int(valid.sum())
    if n_valid == 0:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"ATR({atr_period}) 列全 NaN（区间短于周期或数据缺失）",
            detail={"field": col, "atr_period": atr_period})
    first_valid = int(np.argmax(valid))
    tail = arr[first_valid:]
    if np.isnan(tail).any():
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"ATR({atr_period}) 列首个有效值之后存在 NaN（中段数据洞"
            "——止损保护不可静默中断）",
            detail={"field": col, "atr_period": atr_period})
    if (tail <= 0.0).any():
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"ATR({atr_period}) 列含非正值（≤0 的 ATR 值不可作止损锚）",
            detail={"field": col, "atr_period": atr_period})
    return arr


def _per_dna_risk_params(risk_genes, enhanced_df: pd.DataFrame) -> tuple:
    """单个 DNA 的风控参数装配（单路径与批量路径共享单源）。

    返回 (sl_mode_val, atr_col_or_None, exit_fraction, add_fraction,
    max_exposure, max_hold)：
    - sl_mode 经 sl_mode_code 映射分派码；atr 模式装载 ATR 列（装载
      校验在 _load_atr_column），非 atr 模式返回 None——占位数组由
      调用方决定（单路径全 1 向量、批量路径逐列堆叠）
    - 分批三参数缺省物化（0.4.2 R3/A5）：None→0.5/0.1/1.0——「有
      声明用声明、无声明用缺省」全路径统一，引擎内恒具体值
    - max_hold 缺省物化（add-max-hold-exit D-3）：None→0.0=禁用
      （沿 sl_stop>0 才启用形制）；1D 逐列常数参数族（numba 标量
      索引——2D 形式破坏比较语义且与批量路径分叉）

    review-042 CARD-04：_build_portfolio 与 batch_run 原为双份实现
    （批量处注释自证「漏装=该列按缺省执行=与单路径静默分叉」），装配
    语义必须单源；risk_genes 为 RiskGenes dataclass，字段恒存在，
    直接属性访问（原 getattr 防御永不生效反而误导读者）。
    """
    sl_mode_val = sl_mode_code(risk_genes.sl_mode)
    atr_col = (_load_atr_column(enhanced_df, int(risk_genes.atr_period))
               if risk_genes.sl_mode == "atr" else None)
    exit_fraction = (float(risk_genes.exit_fraction)
                     if risk_genes.exit_fraction is not None else 0.5)
    add_fraction = (float(risk_genes.add_fraction)
                    if risk_genes.add_fraction is not None else 0.1)
    max_exposure = (float(risk_genes.max_exposure)
                    if risk_genes.max_exposure is not None else 1.0)
    max_hold = (float(risk_genes.max_hold)
                if risk_genes.max_hold is not None else 0.0)
    return (sl_mode_val, atr_col, exit_fraction, add_fraction,
            max_exposure, max_hold)


@dataclass
class BacktestResult:
    """单次回测运行的结构化结果（指标+权益曲线+逐笔记录）。"""
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float | None  # None=零交易合法缺失（三态语义，见 win_rate_failed 注释）
    total_trades: int
    equity_curve: pd.Series
    trades_df: pd.DataFrame | None = None
    total_funding_cost: float = 0.0
    liquidated: bool = False
    data_bars: int = 0
    trade_win_rate: float | None = None
    # 胜率三态（batch1 D2）：None+win_rate_failed=False=无交易（合法缺失）；
    # 数值=计算成功；None+win_rate_failed=True=计算失败（显式失败，禁止按缺失降级）
    win_rate_failed: bool = False
    trade_returns: np.ndarray | None = None
    bars_per_year: int = 2190
    add_count: int = 0
    reduce_count: int = 0
    metrics_dict: dict | None = None
    degraded_layers: int = 0
    market_annual_return: float = 0.0
    alpha: float = 0.0
    # 基准对照（improve-backtest-benchmark-disclosure）：区间口径。
    # None=无基准数据（早退/无基准序列），不与 0.0 混同——0.0 是
    # 「基准区间持平」的真实值；零费基准（不扣资金费/交易费，从严）
    benchmark_return: Optional[float] = None
    excess_return: Optional[float] = None


@njit
def _apply_funding_loop_nb(adjusted: np.ndarray, position_mask: np.ndarray,
                           cost_rate: float,
                           position_values: np.ndarray | None = None) -> float:
    """funding 资金费扣减内循环（numba njit——逐 bar 就地扣）。

    Args:
        adjusted: 权益值 float64 数组（就地修改）。
        position_mask: 持仓掩码（True=该 bar 持仓中）。
        cost_rate: 单 bar 资金费率。
        position_values: 逐 bar 仓位价值 float64 数组；提供时按
            |position_value|（实际借入额的绝对量——空头仓位价值为负，
            借入的名义敞口与方向无关，取绝对值计费）而非总权益计费。

    Returns:
        总扣费额。
    """
    total_cost = 0.0
    for i in range(1, len(adjusted)):
        if position_mask[i]:
            if position_values is not None:
                # add-direction-channel D-4 修复卡：空头仓位价值为负数，
                # 带符号计费会使 funding 变成给空头的收入（实证
                # short=-3533 vs long=+3881 方向翻转）——借入额是名义
                # 敞口的绝对量，与持仓方向无关（固定正费率保守估计口径）
                base = abs(position_values[i - 1])
            else:
                base = adjusted[i - 1]
            cost = base * cost_rate
            total_cost += cost
            adjusted[i] -= cost
    return total_cost


def _apply_funding_costs(
    equity_curve: pd.Series, leverage: int, timeframe: str,
    trades_df: Optional[pd.DataFrame] = None,
    position_values: Optional[np.ndarray] = None,
) -> tuple[pd.Series, float]:
    """杠杆资金费扣减——仅持仓时段收费。

    费率口径：每 8 小时 0.1% 按 bar 周期比例折算（非 ceil——不足一期
    的零头按比例收，避免多收）；计费基数=借入部分 (leverage-1)/
    leverage。边界条件：leverage≤1 零费用直返。

    Args:
        equity_curve: 逐 bar 组合权益。
        leverage: 杠杆倍数。
        timeframe: bar 周期串（未知周期按 4h 折算）。
        trades_df: 逐笔记录（Entry/Exit Timestamp）；None=全程按持仓
            扣费（legacy 形制），空表=零费用（无持仓即无资金费）。
        position_values: 逐 bar 仓位价值数组；提供时按
            |position_value|（实际借入额的绝对量——空头仓位价值为负，
            借入的名义敞口与方向无关）而非总权益计费。
    """
    if leverage <= 1:
        return equity_curve, 0.0

    RATE_PER_8H = 0.001
    hours_per_bar = {
        "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
        "1h": 1, "4h": 4, "1d": 24, "3d": 72,
    }.get(timeframe, 4)
    # 比例折算而非 ceil——不足一期按零头比例收，避免多收
    periods_per_bar = hours_per_bar / 8.0
    borrowed_ratio = (leverage - 1) / leverage
    cost_rate = RATE_PER_8H * periods_per_bar * borrowed_ratio

    # 持仓掩码：True=持仓中
    position_mask = np.zeros(len(equity_curve), dtype=np.bool_)
    if trades_df is not None and len(trades_df) > 0:
        for _, trade in trades_df.iterrows():
            entry_ts = trade.get("Entry Timestamp")
            exit_ts = trade.get("Exit Timestamp")
            if entry_ts is None:
                continue
            if pd.isna(exit_ts) or exit_ts is None:
                # 未平仓交易：从入场持有至序列末端
                mask_slice = equity_curve.index >= entry_ts
            else:
                mask_slice = (equity_curve.index >= entry_ts) & (equity_curve.index <= exit_ts)
            position_mask |= np.asarray(mask_slice).astype(np.bool_)
    elif trades_df is None:
        # legacy 形制：未传 trades_df，全程按持仓扣费
        position_mask[:] = True
    # （trades_df 给了但为空 → 零资金费——无持仓即无费用）

    adjusted = equity_curve.values.astype(np.float64).copy()

    # 资金费计费用的 position_values 备料
    pv_for_funding = None
    if position_values is not None:
        pv_for_funding = position_values.astype(np.float64)

    total_cost = _apply_funding_loop_nb(adjusted, position_mask, cost_rate, pv_for_funding)
    return pd.Series(adjusted, index=equity_curve.index), total_cost


def _apply_leverage_to_equity(
    equity_curve: pd.Series,
    trades_df: pd.DataFrame | None,
    leverage: float,
    init_cash: float,
) -> pd.Series:
    """权益曲线杠杆放大（后处理）。

    实现意图：vbt 以 position_size 无杠杆运行，本函数把逐笔持仓区间
    的盈亏按杠杆倍数放大——持仓段 leveraged_base + leverage×(eq−
    eq_entry)，持仓间隙的持平段保持 leveraged_base，基准在每笔结算
    点滚动更新。边界：leverage≤1 或无交易时原样返回。
    """
    if leverage <= 1 or trades_df is None or len(trades_df) == 0:
        return equity_curve

    eq_vals = equity_curve.values.astype(np.float64)
    result = np.empty(len(eq_vals), dtype=np.float64)

    # 交易区间排序（entry_idx, exit_idx）
    intervals = []
    for _, trade in trades_df.iterrows():
        entry_ts = trade.get("Entry Timestamp")
        exit_ts = trade.get("Exit Timestamp")
        if entry_ts is None:
            continue
        if pd.isna(exit_ts) or exit_ts is None:
            exit_ts = equity_curve.index[-1]
        mask = (equity_curve.index >= entry_ts) & (equity_curve.index <= exit_ts)
        indices = np.where(mask)[0]
        if len(indices) == 0:
            continue
        intervals.append((int(indices[0]), int(indices[-1])))

    intervals.sort()

    # 逐笔交易区间分段放大
    leveraged_base = float(init_cash)
    prev_end = 0

    for entry_idx, exit_idx in intervals:
        # 该笔之前的空仓段（持平）
        if prev_end < entry_idx:
            result[prev_end:entry_idx] = leveraged_base

        # 持仓段放大
        vbt_entry_eq = eq_vals[entry_idx]
        result[entry_idx:exit_idx + 1] = (
            leveraged_base + leverage * (eq_vals[entry_idx:exit_idx + 1] - vbt_entry_eq)
        )

        leveraged_base = float(result[exit_idx])
        prev_end = exit_idx + 1

    # 尾部剩余空仓段
    if prev_end < len(result):
        result[prev_end:] = leveraged_base

    return pd.Series(result, index=equity_curve.index)


@njit
def pre_sim_func_nb(c):
    """仿真前置状态初始化（每列 entry_price / is_liquidated / peak_price
    / entry_bar 数组）。

    经 vbt 生命周期链作为 order_func_nb 首组参数传入，供逐 bar 回调
    就地读写（入场价记忆、清算标记与 trail 峰值——float 承载因 numba
    状态数组须数值型）。peak_price（0.4.2 R2）：trail 模式持仓期峰值
    （多=最高价/空=最低价），0.0=无持仓（开仓重置成交价、清仓清零）。
    entry_bar（add-max-hold-exit D-3）：入场 bar 序号记忆（与入场价
    同点写入——每次入场独立重计持有根数；0.0=无持仓）。
    """
    n_cols = c.target_shape[1]
    entry_price = np.full(n_cols, 0.0, dtype=np.float64)
    is_liquidated = np.full(n_cols, 0.0, dtype=np.float64)  # 0.0=False，1.0=True
    peak_price = np.full(n_cols, 0.0, dtype=np.float64)
    entry_bar = np.full(n_cols, 0.0, dtype=np.float64)
    return (entry_price, is_liquidated, peak_price, entry_bar)


@njit
def order_func_nb(c, entry_price, is_liquidated, peak_price, entry_bar,
                  entries, exits, adds, reduces,
                  direction_vals, size_pcts, leverages,
                  sl_stops, tp_stops, sl_modes, atr_arr,
                  exit_fractions, add_fractions, max_exposures,
                  max_holds,
                  fee, slippage,
                  high_arr, low_arr, direction_signal,
                  mmr_tier_max, mmr_tier_rate):
    """逐 bar 订单回调（实时 SL/TP 与清算判定；SL 段按 sl_mode 三分派）。

    判定序（关键决策）：清算检查先于 SL/TP——杠杆仓权益击穿维持线
    即强平，优先级高于止盈止损；SL/TP 用 bar HIGH/LOW 触碰判定
    （非收盘价——bar 内可达即触发）。信号在外层已 shift(1) 防
    look-ahead 前视。同 bar 清算线与 trail/SL 线双命中→清算优先单
    事件（DC-10——清算检查在前，先命中先 return）。

    SL 段三模式分派（0.4.2 R2，sl_modes 逐列分派码）：
    - pct（0）：锚入场价比例（存量语义逐位不变——回归锚）。
    - trail（2）：峰值先更新（含当 bar high/low）再判定——多单
      peak=max(peak, bar_high)、sl_level=peak*(1−sl_stop)、bar_low
      触碰全平；空单对称（min(peak, bar_low)、peak*(1+sl_stop)、
      bar_high 触碰）。TP 沿 pct 语义锚入场价（SL 先 TP 后）。
    - atr（1）：sl_level=entry∓sl_stop×ATR、tp_level=entry±tp_stop×ATR
      （倍数语义——atr_arr 为该 bar ATR 值；NaN bar 该 bar 无 SL
      约束，沿交易链 NaN guard 先例——预热期无止损保护）。

    Args（经 vbt 生命周期链传入）:
        entry_price, is_liquidated, peak_price: pre_sim_func_nb 传入
            的可变状态。

    Args（经 order_args 传入）:
        entries, exits, adds, reduces: 2D float64 信号矩阵。
        direction_vals, size_pcts, leverages, sl_stops, tp_stops:
            逐列参数（1D float64）。
        sl_modes: 逐列分派码（1D float64：0=pct/1=atr/2=trail——
            装配层经 sl_mode_code 单源映射）。
        atr_arr: 2D ATR 值矩阵（atr 模式消费；装载层按
            dna.risk_genes.atr_period 动态取列+装载校验）。
        exit_fractions, add_fractions, max_exposures: 分批逐列参数
            （1D float64；装配层物化缺省 0.5/0.1/1.0——A5「有声明用
            声明、无声明用缺省」全路径统一，引擎内不再判 None）。
        max_holds: 时间出场逐列参数（1D float64；装配层物化 None→0.0
            =禁用——沿 sl_stop>0 才启用形制；0.0 以外的值=持有第 N
            根 bar 的出场时点无条件全平，N 为该值取整）。
        fee, slippage: 标量费滑（基础费率——杠杆放大归
            _apply_leverage_to_equity 后处理）。
        high_arr, low_arr: bar 内 SL/TP 判定用 2D 高低价。
        direction_signal: 2D 方向信号（+1=多，−1=空；mixed 模式用）。
    """
    i = c.i
    col = c.col
    # entry_bar 清零纪律（add-max-hold-exit D-3）：时间出场与信号出场
    # 段与 entry_price 同点清零；SL/TP/清算段不清——其消费点（时间
    # 出场分支）守卫含 entry_price>0，且入场时无条件重写，残留旧值
    # 永不生效（行为等价的整洁写法，非正确性依赖）

    # 逐列参数查取
    direction_val = direction_vals[col]
    size_pct = size_pcts[col]
    leverage = leverages[col]
    sl_stop = sl_stops[col]
    tp_stop = tp_stops[col]
    sl_mode = sl_modes[col]
    # 费滑按基础费率收——杠杆放大由 _apply_leverage_to_equity 后处理
    effective_fee = fee
    effective_slippage = slippage

    # 已清算 → 强平余仓；仓位归零后资金充足时可再入场
    if is_liquidated[col] > 0.5:
        if c.position_now != 0.0:
            peak_price[col] = 0.0
            return vbt_nb.order_nb(
                size=np.float64(-c.position_now),
                size_type=np.int64(0),
                fees=effective_fee,
                slippage=effective_slippage,
            )
        # 清算后仓位已平：无入场信号保持空仓；有信号则先查资金
        if entries[i, col] < 0.5:
            return NoOrder
        # 清算后再入场：查资金是否够开仓
        current_price = c.close[i, col]
        min_required = current_price * (1.0 + fee + slippage) * 0.01  # 单股成本 1% 的最低开仓门槛
        if c.value_now < min_required:
            return NoOrder  # 资金不足
        # 复位清算标记，落入下方入场逻辑
        is_liquidated[col] = 0.0
        entry_price[col] = 0.0
        # 落入下方入场信号处理

    current_price = c.close[i, col]

    # 清算检查先于 SL/TP（杠杆仓——序见函数 docstring）
    # B7.1 口径（架构约束修订）：等效换算形态 init_cash×(1−(1−mmr)/L)，
    # mmr 按等效名义 init_cash×L 选档（内联，与 market_model 同源）
    if leverage > 1.0 and c.position_now != 0.0:
        notional_eq = c.init_cash[0] * leverage
        mmr = mmr_tier_rate[len(mmr_tier_rate) - 1]
        for t in range(len(mmr_tier_rate)):
            if notional_eq <= mmr_tier_max[t]:
                mmr = mmr_tier_rate[t]
                break
        maintenance = c.init_cash[0] * (1.0 - (1.0 - mmr) / leverage)
        if c.value_now < maintenance:
            is_liquidated[col] = 1.0
            entry_price[col] = 0.0
            peak_price[col] = 0.0
            return vbt_nb.order_nb(
                size=np.float64(-c.position_now),
                size_type=np.int64(0),
                fees=effective_fee,
                slippage=effective_slippage,
            )

    # SL/TP 判定用 bar HIGH/LOW（触碰即触发——非收盘价口径）
    if c.position_now != 0.0 and entry_price[col] > 0.0:
        bar_high = high_arr[i, col]
        bar_low = low_arr[i, col]
        ep = entry_price[col]

        if sl_mode < 0.5:
            # ── pct 分支（存量语义逐位不变——回归锚）──
            if c.position_now > 0.0:
                # 多头仓
                sl_level = ep * (1.0 - sl_stop) if sl_stop > 0.0 else 0.0
                tp_level = ep * (1.0 + tp_stop) if tp_stop > 0.0 else 0.0
                # SL：bar LOW 触碰止损线
                if sl_stop > 0.0 and bar_low <= sl_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                # TP：bar HIGH 触及止盈线
                if tp_stop > 0.0 and bar_high >= tp_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
            else:
                # 空头仓
                sl_level = ep * (1.0 + sl_stop) if sl_stop > 0.0 else 0.0
                tp_level = ep * (1.0 - tp_stop) if tp_stop > 0.0 else 0.0
                # SL：bar HIGH 触碰止损线
                if sl_stop > 0.0 and bar_high >= sl_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                # TP：bar LOW 触及止盈线
                if tp_stop > 0.0 and bar_low <= tp_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
        elif sl_mode > 1.5:
            # ── trail 分支（峰值回撤锁利，0.4.2 R2/A1 当 bar 口径）──
            # 峰值先更新（含当 bar）再以当 bar 对侧判定触发
            if c.position_now > 0.0:
                peak = peak_price[col]
                if bar_high > peak:
                    peak = bar_high
                peak_price[col] = peak
                sl_level = peak * (1.0 - sl_stop) if sl_stop > 0.0 else 0.0
                # TP 沿 pct 语义锚入场价（trail 与 TP 并存，SL 先 TP 后）
                tp_level = ep * (1.0 + tp_stop) if tp_stop > 0.0 else 0.0
                if sl_stop > 0.0 and bar_low <= sl_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                if tp_stop > 0.0 and bar_high >= tp_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
            else:
                peak = peak_price[col]
                if peak <= 0.0 or bar_low < peak:
                    peak = bar_low
                peak_price[col] = peak
                sl_level = peak * (1.0 + sl_stop) if sl_stop > 0.0 else 0.0
                tp_level = ep * (1.0 - tp_stop) if tp_stop > 0.0 else 0.0
                if sl_stop > 0.0 and bar_high >= sl_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                if tp_stop > 0.0 and bar_low <= tp_level:
                    entry_price[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
        else:
            # ── atr 分支（ATR 倍数语义，0.4.2 R2——消除静默按 pct 误执行）──
            atr_value = atr_arr[i, col]
            if not (atr_value != atr_value):  # NaN guard：预热期该 bar 无 SL 约束
                if c.position_now > 0.0:
                    # 多头：止损=入场−倍数×ATR，止盈=入场+倍数×ATR
                    sl_level = ep - sl_stop * atr_value if sl_stop > 0.0 else 0.0
                    tp_level = ep + tp_stop * atr_value if tp_stop > 0.0 else 0.0
                    if sl_stop > 0.0 and bar_low <= sl_level:
                        entry_price[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
                    if tp_stop > 0.0 and bar_high >= tp_level:
                        entry_price[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
                else:
                    # 空头：止损=入场+倍数×ATR，止盈=入场−倍数×ATR
                    sl_level = ep + sl_stop * atr_value if sl_stop > 0.0 else 0.0
                    tp_level = ep - tp_stop * atr_value if tp_stop > 0.0 else 0.0
                    if sl_stop > 0.0 and bar_high >= sl_level:
                        entry_price[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
                    if tp_stop > 0.0 and bar_low <= tp_level:
                        entry_price[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )

    # 时间出场（max_hold——add-max-hold-exit D-3，参数出场族第三机制）：
    # 持有第 N 根 bar 的出场时点无条件全平（A-1 口径：入场成交 bar 计
    # 第 1 根，判定式 (i−entry_bar+1)≥N）。判定序位于 SL/TP 之后、信
    # 号出场之前——同 bar 双命中时价格触发优先于到期（止损保护优先）；
    # 减仓/加仓不清计数（不动 entry_bar——部分离场不动整仓时间计数）。
    # 0=禁用（装配层 None→0.0 物化，首判即假——存量行为精确等价）
    if max_holds[col] > 0.5 and c.position_now != 0.0 and entry_price[col] > 0.0:
        if (i - entry_bar[col] + 1.0) >= max_holds[col]:
            entry_price[col] = 0.0
            peak_price[col] = 0.0
            entry_bar[col] = 0.0
            return vbt_nb.order_nb(
                size=np.float64(-c.position_now),
                size_type=np.int64(0),
                fees=effective_fee,
                slippage=effective_slippage,
            )

    # 离场信号
    if exits[i, col] > 0.5 and c.position_now != 0.0:
        entry_price[col] = 0.0
        peak_price[col] = 0.0
        entry_bar[col] = 0.0
        return vbt_nb.order_nb(
            size=np.float64(-c.position_now),
            size_type=np.int64(0),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # 入场信号
    if entries[i, col] > 0.5 and c.position_now == 0.0:
        entry_price[col] = current_price
        entry_bar[col] = float(i)  # 持有计数起点（每次入场独立重计）
        peak_price[col] = current_price  # trail 峰值重置为成交价（开仓重锚）
        # mixed 模式：方向由 direction_signal 定交易侧
        dir_sign = direction_signal[i, col]
        if direction_val < 1.5:  # long(0)/short(1) 固定方向
            dir_sign = 1.0  # size 恒正；多空侧由 direction_val 控制
            actual_direction = direction_val
        else:  # mixed(2)——方向来自信号
            if dir_sign > 0.0:
                actual_direction = 0.0  # 多
            else:
                actual_direction = 1.0  # 空
            dir_sign = 1.0  # size 正、方向控制多空
        return vbt_nb.order_nb(
            size=np.float64(size_pct * dir_sign),
            size_type=np.int64(2),  # 百分比仓
            direction=np.int64(int(actual_direction)),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # 减仓信号（部分平仓）——独立比例 exit_fraction（0.4.2 R3/A5：修正
    # 现状复用 position_size 的参数串用——50% 非 30%）
    if reduces[i, col] > 0.5 and c.position_now != 0.0:
        reduce_amount = c.position_now * exit_fractions[col]
        return vbt_nb.order_nb(
            size=np.float64(-reduce_amount),
            size_type=np.int64(0),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # 加仓信号——独立比例 add_fraction（分母=当前权益）+ 总敞口上限恒检查
    if adds[i, col] > 0.5 and c.position_now != 0.0:
        # 敞口上限（0.4.2 R3：阈值=声明值或缺省 1.0——None 装配层物化；
        # 当前仓位名义+本次加仓名义 > 权益×max_exposure → 跳过不炸）
        notional_now = abs(c.position_now) * current_price
        add_value = c.value_now * add_fractions[col]
        if notional_now + add_value > c.value_now * max_exposures[col]:
            return NoOrder
        old_pos = abs(c.position_now)
        old_ep = entry_price[col]
        add_shares = add_value / current_price
        # 入场价更新为加权均价（SL/TP 基准随加仓重锚；trail 峰值保持
        # 连续——持仓期语义，不随部分加仓重置）
        if old_pos + add_shares > 0.0:
            new_ep = (old_ep * old_pos + current_price * add_shares) / (old_pos + add_shares)
            entry_price[col] = new_ep
        # 加仓方向须与现仓同向
        if c.position_now > 0.0:
            add_dir = np.int64(0)   # 多（加多）
        else:
            add_dir = np.int64(1)   # 空（加空）
        return vbt_nb.order_nb(
            size=np.float64(add_shares),
            size_type=np.int64(0),  # 绝对股数（名义=权益×add_fraction 精确语义）
            direction=add_dir,
            fees=effective_fee,
            slippage=effective_slippage,
        )

    return NoOrder


class BacktestEngine:
    """vectorbt 回测引擎（Portfolio.from_order_func 形制）。"""

    def __init__(self, init_cash: float = 100000, fee: float = 0.001,
                 slippage: float = 0.0005):
        self.init_cash = init_cash
        self.fee = fee
        self.slippage = slippage

    def _build_portfolio(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
        signal_set=None,
    ):
        """由 DNA 信号构建 vectorbt Portfolio（from_order_func 形制）。"""
        if signal_set is not None:
            sig_set = signal_set
        else:
            sig_set = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

        # 全信号 shift(1) 防 look-ahead 前视（信号 bar 的下一根才可执行）
        # （fill_value 内联进 shift：首行直接填 False，列全程 bool，
        #   不走 object downcast 弃用路径——cross-entry-consistency spec）
        entries = sig_set.entries.shift(1, fill_value=False).astype(bool)
        exits = sig_set.exits.shift(1, fill_value=False).astype(bool)
        adds = sig_set.adds.shift(1, fill_value=False).astype(bool)
        reduces = sig_set.reduces.shift(1, fill_value=False).astype(bool)

        close = enhanced_df["close"]
        high = enhanced_df["high"]
        low = enhanced_df["low"]

        direction_map = {"long": 0, "short": 1, "mixed": 2}
        direction_val = direction_map.get(dna.risk_genes.direction, 0)
        size_pct = dna.risk_genes.position_size
        leverage = float(dna.risk_genes.leverage)
        sl_stop = float(dna.risk_genes.stop_loss) if dna.risk_genes.stop_loss else 0.0
        tp_stop = float(dna.risk_genes.take_profit) if dna.risk_genes.take_profit else 0.0
        # SL 模式分派+ATR 装载+分批三参数+时间出场（0.4.2 R2/R3、
        # add-max-hold-exit D-3）：per-DNA 装配单源 _per_dna_risk_params
        # （与 batch_run 共享——漏装即与批量路径静默分叉）；非 atr 模式
        # 占位全 1 数组（引擎不消费）
        (sl_mode_val, atr_col, exit_fraction, add_fraction, max_exposure,
         max_hold) = _per_dna_risk_params(dna.risk_genes, enhanced_df)
        if atr_col is None:
            atr_col = np.ones(len(enhanced_df), dtype=np.float64)
        atr_2d = atr_col.reshape(-1, 1)

        # 信号转 2D numpy 数组（numba 兼容）
        entries_2d = entries.values.astype(np.float64).reshape(-1, 1)
        exits_2d = exits.values.astype(np.float64).reshape(-1, 1)
        adds_2d = adds.values.astype(np.float64).reshape(-1, 1)
        reduces_2d = reduces.values.astype(np.float64).reshape(-1, 1)
        high_2d = high.values.astype(np.float64).reshape(-1, 1)
        low_2d = low.values.astype(np.float64).reshape(-1, 1)

        # mixed 模式方向信号（shift(1) 防 look-ahead——与信号同步延迟）
        if sig_set.entry_direction is not None:
            direction_shifted = sig_set.entry_direction.shift(1).fillna(1.0)
            direction_signal_2d = direction_shifted.values.astype(np.float64).reshape(-1, 1)
        else:
            if direction_val >= 1.5:  # mixed 但无方向信号
                logger.warning(
                    "direction='mixed' but no entry_direction signal; "
                    "defaulting to long. strategy_id=%s",
                    getattr(dna, "strategy_id", None),
                )
            direction_signal_2d = np.ones_like(entries_2d)

        pf = vbt.Portfolio.from_order_func(
            close,
            order_func_nb,
            entries_2d,
            exits_2d,
            adds_2d,
            reduces_2d,
            np.array([direction_val], dtype=np.float64),
            np.array([size_pct], dtype=np.float64),
            np.array([leverage], dtype=np.float64),
            np.array([sl_stop], dtype=np.float64),
            np.array([tp_stop], dtype=np.float64),
            np.array([sl_mode_val], dtype=np.float64),
            atr_2d,
            np.array([exit_fraction], dtype=np.float64),
            np.array([add_fraction], dtype=np.float64),
            np.array([max_exposure], dtype=np.float64),
            # 时间出场 1D 单元素（逐列常数参数族——与 sl_stops 等九参数
            # 同形制；2D 形式在 order_func_nb 标量索引处破坏 numba 比较
            # 语义且与批量路径分叉，add-max-hold-exit D-3）
            np.array([max_hold], dtype=np.float64),
            np.float64(self.fee),
            np.float64(self.slippage),
            high_2d,
            low_2d,
            direction_signal_2d,
            _mm_tier_arrays()[0],
            _mm_tier_arrays()[1],
            pre_sim_func_nb=pre_sim_func_nb,
            init_cash=self.init_cash,
            freq=None,
        )

        return pf, int(adds.sum()), int(reduces.sum()), sig_set.degraded_layers

    def _build_result_from_portfolio(
        self,
        portfolio,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        add_count: int = 0,
        reduce_count: int = 0,
        degraded: int = 0,
    ) -> BacktestResult:
        """从已构建的 portfolio 组装 BacktestResult（杠杆放大→清算判定→资金费→指标）。"""
        equity_curve = portfolio.value()
        if isinstance(equity_curve, pd.DataFrame):
            equity_curve = equity_curve.iloc[:, 0]

        # 取 vbt 原始 cash 算 position_value = equity − cash（资金费计费基数）
        position_values_arr = None
        try:
            cash_curve = portfolio.cash()
            if isinstance(cash_curve, pd.DataFrame):
                cash_curve = cash_curve.iloc[:, 0]
            raw_equity = portfolio.value()
            if isinstance(raw_equity, pd.DataFrame):
                raw_equity = raw_equity.iloc[:, 0]
            position_values_arr = (raw_equity.values - cash_curve.values).astype(np.float64)
        except Exception:
            pass

        # 权益曲线杠杆放大
        leverage = float(dna.risk_genes.leverage)
        trades_for_leverage = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None
        equity_curve = _apply_leverage_to_equity(
            equity_curve, trades_for_leverage, leverage, self.init_cash,
        )

        # position_values 同步放大对齐杠杆权益
        if position_values_arr is not None and leverage > 1:
            position_values_arr = position_values_arr * leverage

        # 清算是否发生（在放大后曲线上判——口径与权益同基准）
        liquidated = False
        if leverage > 1 and len(equity_curve) > 0:
            maintenance = _mm_threshold(self.init_cash, leverage)
            if (equity_curve < maintenance).any():
                liquidated = True

        # 杠杆仓资金费（在放大后曲线上收）
        timeframe = dna.execution_genes.timeframe
        trades_for_funding = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None
        equity_curve, total_funding_cost = _apply_funding_costs(
            equity_curve, dna.risk_genes.leverage, timeframe,
            trades_df=trades_for_funding,
            position_values=position_values_arr,
        )

        total_trades_val = portfolio.trades.count()
        if isinstance(total_trades_val, pd.Series):
            total_trades_val = int(total_trades_val.sum())
        total_trades_val = int(total_trades_val) if not pd.isna(total_trades_val) else 0

        # bars_per_year 公共单一权威源（metrics.py，REST batch 同源引用）。
        # 缺省 2190（=4h 档）不变；补全 1m/5m/2h 档——原缺省落错档属修正
        # 非回归（见 improve-backtest-benchmark-disclosure Decision 6）
        from core.scoring.metrics import (
            BARS_PER_YEAR, DEFAULT_BARS_PER_YEAR, compute_metrics)
        bars_per_year = BARS_PER_YEAR.get(timeframe, DEFAULT_BARS_PER_YEAR)

        trade_win_rate = None
        trade_returns = None
        win_rate_failed = False
        if total_trades_val > 0:
            try:
                trade_pnl = portfolio.trades.pnl
                if hasattr(trade_pnl, 'values'):
                    pnl_arr = np.array(trade_pnl.values).flatten()
                else:
                    pnl_arr = np.array(trade_pnl).flatten()

                if len(pnl_arr) > 0:
                    winning = int((pnl_arr > 0).sum())
                    trade_win_rate = winning / len(pnl_arr)
                    trade_returns = pnl_arr / self.init_cash
            except Exception:
                # 三态（D2）：计算失败不得静默降级为「无胜率」——记录原因链并显式标记
                win_rate_failed = True
                logger.error(
                    "win_rate computation failed: total_trades=%s", total_trades_val,
                    exc_info=True,
                )

        metrics = compute_metrics(
            equity_curve, total_trades=total_trades_val,
            bars_per_year=bars_per_year,
            trade_win_rate=trade_win_rate,
            trade_returns=trade_returns,
            benchmark_close=enhanced_df["close"],
        )

        sharpe = metrics["sharpe_ratio"]
        max_dd = metrics["max_drawdown"]
        win_rate = metrics["win_rate"]

        if len(equity_curve) >= 2 and equity_curve.iloc[0] > 0:
            raw_return = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1)
        else:
            raw_return = 0.0

        trades = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None

        return BacktestResult(
            total_return=raw_return,
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_dd),
            win_rate=None if win_rate is None else float(win_rate),
            total_trades=total_trades_val,
            equity_curve=equity_curve,
            trades_df=trades,
            total_funding_cost=total_funding_cost,
            liquidated=liquidated,
            data_bars=len(enhanced_df),
            trade_win_rate=trade_win_rate,
            win_rate_failed=win_rate_failed,
            trade_returns=trade_returns,
            bars_per_year=bars_per_year,
            add_count=add_count,
            reduce_count=reduce_count,
            metrics_dict=metrics,
            degraded_layers=degraded,
            market_annual_return=metrics.get("market_annual_return", 0.0),
            alpha=metrics.get("alpha", 0.0),
            benchmark_return=metrics.get("benchmark_return"),
            excess_return=metrics.get("excess_return"),
        )

    def run(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
        signal_set=None,
    ) -> BacktestResult:
        """单策略 DNA 回测（构建 portfolio → 组装结果）。"""
        build_result = self._build_portfolio(
            dna, enhanced_df,
            dfs_by_timeframe=dfs_by_timeframe,
            signal_set=signal_set,
        )
        if isinstance(build_result, tuple):
            portfolio, add_count, reduce_count, degraded = build_result
        else:
            portfolio = build_result
            add_count, reduce_count, degraded = 0, 0, 0

        return self._build_result_from_portfolio(
            portfolio, dna, enhanced_df, add_count, reduce_count, degraded,
        )

    def run_with_portfolio(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Tuple["BacktestResult", object]:
        """回测并返回 (BacktestResult, vectorbt Portfolio) 二元组（消费方需底层组合时用）。"""
        build_result = self._build_portfolio(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
        if isinstance(build_result, tuple):
            portfolio, add_count, reduce_count, degraded = build_result
        else:
            portfolio = build_result
            add_count, reduce_count, degraded = 0, 0, 0
        result = self._build_result_from_portfolio(
            portfolio, dna, enhanced_df, add_count, reduce_count, degraded,
        )
        return result, portfolio

    def batch_run(
        self,
        individuals: list[StrategyDNA],
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: dict | None = None,
        signal_sets: list | None = None,
    ) -> list[BacktestResult]:
        """全种群批量评估（单次 vbt 调用）。

        实现意图：不逐个 run() N 次，而是把全部个体信号堆叠为 (N, M)
        矩阵跑一次 Portfolio.from_order_func——每列一个体，整个种群
        向量化评估（进化评估链的性能底座）。

        Args:
            individuals: 待评估的 StrategyDNA 清单。
            enhanced_df: 含指标的市场数据。
            dfs_by_timeframe: 多周期数据 dict（可选）。
            signal_sets: 预先算好的信号集（可选——免重算）。

        Returns:
            与 individuals 同序的 BacktestResult 清单。
        """
        M = len(individuals)
        if M == 0:
            return []
        N = len(enhanced_df)

        # 1. 计算信号集
        if signal_sets is not None:
            sig_sets = signal_sets
        else:
            sig_sets = batch_signal_sets(individuals, enhanced_df, dfs_by_timeframe)

        # 2. 信号堆叠为 (N, M) 矩阵
        entries_parts = []
        exits_parts = []
        adds_parts = []
        reduces_parts = []
        direction_signal_parts = []
        add_counts = []
        reduce_counts = []
        degraded_layers_list = []

        for ss in sig_sets:
            entries_parts.append(
                ss.entries.shift(1, fill_value=False).astype(float).values
            )
            exits_parts.append(
                ss.exits.shift(1, fill_value=False).astype(float).values
            )
            adds_parts.append(
                ss.adds.shift(1, fill_value=False).astype(float).values
            )
            reduces_parts.append(
                ss.reduces.shift(1, fill_value=False).astype(float).values
            )
            add_counts.append(int(ss.adds.sum()))
            reduce_counts.append(int(ss.reduces.sum()))
            degraded_layers_list.append(ss.degraded_layers)

            if ss.entry_direction is not None:
                direction_signal_parts.append(
                    ss.entry_direction.shift(1).fillna(1.0).astype(float).values
                )
            else:
                direction_signal_parts.append(np.ones(N))

        entries_matrix = np.column_stack(entries_parts).astype(np.float64)
        exits_matrix = np.column_stack(exits_parts).astype(np.float64)
        adds_matrix = np.column_stack(adds_parts).astype(np.float64)
        reduces_matrix = np.column_stack(reduces_parts).astype(np.float64)
        direction_signal_matrix = np.column_stack(direction_signal_parts).astype(np.float64)

        close = enhanced_df["close"]
        high_2d = np.tile(
            enhanced_df["high"].values.astype(np.float64).reshape(-1, 1), (1, M)
        )
        low_2d = np.tile(
            enhanced_df["low"].values.astype(np.float64).reshape(-1, 1), (1, M)
        )

        # 3. 逐列参数数组
        direction_map = {"long": 0, "short": 1, "mixed": 2}
        direction_vals = np.array(
            [direction_map.get(dna.risk_genes.direction, 0) for dna in individuals],
            dtype=np.float64,
        )
        size_pcts = np.array(
            [float(dna.risk_genes.position_size)
             for dna in individuals],
            dtype=np.float64,
        )
        leverages = np.array(
            [float(dna.risk_genes.leverage) for dna in individuals],
            dtype=np.float64,
        )
        sl_stops = np.array(
            [float(dna.risk_genes.stop_loss) if dna.risk_genes.stop_loss else 0.0
             for dna in individuals],
            dtype=np.float64,
        )
        tp_stops = np.array(
            [float(dna.risk_genes.take_profit) if dna.risk_genes.take_profit else 0.0
             for dna in individuals],
            dtype=np.float64,
        )
        # SL 模式分派+ATR 装载+分批三参数 per-column 装配（0.4.2 R2/R3）：
        # per-DNA 装配单源 _per_dna_risk_params（与 _build_portfolio 共享
        # ——漏装=该列按缺省执行=与单路径静默分叉）；非 atr 模式占位
        # 全 1 列（引擎不消费）
        per_dna = [_per_dna_risk_params(dna.risk_genes, enhanced_df)
                   for dna in individuals]
        sl_mode_vals = np.array([p[0] for p in per_dna], dtype=np.float64)
        atr_matrix = np.column_stack([
            p[1] if p[1] is not None else np.ones(N, dtype=np.float64)
            for p in per_dna]).astype(np.float64)
        exit_fractions = np.array([p[2] for p in per_dna], dtype=np.float64)
        add_fractions = np.array([p[3] for p in per_dna], dtype=np.float64)
        max_exposures = np.array([p[4] for p in per_dna], dtype=np.float64)
        # 时间出场逐列参数（add-max-hold-exit D-3：单源装配追加元——
        # 漏装即该列按 0 执行=与单路径静默分叉，CARD-04 同款防护）
        max_holds = np.array([p[5] for p in per_dna], dtype=np.float64)

        # 4. 单次 vbt 调用——close 平铺 M 列对齐信号矩阵
        close_2d = np.tile(
            close.values.astype(np.float64).reshape(-1, 1), (1, M)
        )
        pf = vbt.Portfolio.from_order_func(
            close_2d,
            order_func_nb,
            entries_matrix,
            exits_matrix,
            adds_matrix,
            reduces_matrix,
            direction_vals,
            size_pcts,
            leverages,
            sl_stops,
            tp_stops,
            sl_mode_vals,
            atr_matrix,
            exit_fractions,
            add_fractions,
            max_exposures,
            max_holds,
            np.float64(self.fee),
            np.float64(self.slippage),
            high_2d,
            low_2d,
            direction_signal_matrix,
            _mm_tier_arrays()[0],
            _mm_tier_arrays()[1],
            pre_sim_func_nb=pre_sim_func_nb,
            init_cash=self.init_cash,
            freq=None,
        )

        # 5. 逐列提取结果
        results = []
        for col_idx in range(M):
            col_pf = pf.iloc[col_idx]
            result = self._build_result_from_portfolio(
                col_pf, individuals[col_idx], enhanced_df,
                add_count=add_counts[col_idx],
                reduce_count=reduce_counts[col_idx],
                degraded=degraded_layers_list[col_idx],
            )
            results.append(result)

        return results
