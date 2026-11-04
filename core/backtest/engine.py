"""vectorbt 回测引擎（from_order_func 实时风险管理封装）。

职能：把 StrategyDNA 信号在 OHLCV 数据上执行为 BacktestResult——
单策略 run/run_with_portfolio 与全种群 batch_run（信号堆叠 (N, M)
矩阵单次 vbt 调用、逐列即逐个体）两形制。

价值与服务对象：全部回测指标（胜率/净期望/清算判定）的计算权威，
「检查与考试同一量尺」的量尺本体——直接消费方为进化评估链
（evolution/evaluation、evolution/engine）、策略对比回测链（compare）
与策略手动回测路由链
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
# 定仓装配三元组物化单源（build-position-registry：resolve 单点的
# 回测展开适配——公共层包，零引擎侧物化分派）
from core.positioning import expand_for_backtest

# 0.4.1 Q1 裁决（add-research-analysis-tools）：引擎止损真实分派域。
# 语义=引擎已实现的真实分派集；区别于声明域 SL_MODES（core/strategy/
# dna.py——取值域与分派集是两个概念）。消费面纪律：exit_sweep 模式
# 校验只 import 此常量、禁引声明域常量。0.4.2（add-strategy-
# expressiveness）实施 order_func_nb 三分派（tasks 3.1/3.2）后扩展
# 至 ("pct", "atr", "trail")——本扩展与分派实现同任务原子交付（先扩
# 常量后实现=引用式消费方立即放行未实现模式）。
ENGINE_SUPPORTED_SL_MODES: tuple[str, ...] = ("pct", "atr", "trail",
                                              "series")

# sl_mode 字符串 → order_func_nb 数值分派码（numba 侧无字符串比较，
# 装配层单源映射；0.4.2 R2）
_SL_MODE_CODE = {"pct": 0.0, "atr": 1.0, "trail": 2.0, "series": 3.0}


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


def _bar_ts_minutes_and_tf(index, tf: str) -> tuple:
    """bar 开盘时刻（UTC epoch 分钟数组）与执行周期分钟数装配单点
    （run/batch_run 两入口共用——time_exit 时刻出场覆盖判定的两输入；
    epoch 分钟取整——bar 时刻为整点）。非时序索引（合成测试形制）
    ts 数组填 0——仅被 time_exit 时刻分支消费，无声明路径哨兵 -1
    短路不读取（零影响）。"""
    try:
        _ts = pd.DatetimeIndex(index).astype("int64") // 1_000_000_000
        bar_ts = (_ts / 60.0).to_numpy(dtype=np.float64)
    except (TypeError, ValueError):
        bar_ts = np.zeros(len(index), dtype=np.float64)
    from core.analysis.warmup_loader import TF_MINUTES
    return bar_ts, float(TF_MINUTES.get(tf, 240))


@njit
def risk_budget_qty(value_now, price, stop_dist, risk_fraction,
                    leverage, max_exposure):
    """风险预算定仓公式单点（complete-research-tooling 任务 4）。

    入场数量 qty=当时权益×risk_fraction÷实际生效止损距离（每笔风险
    预算恒等：qty×stop_dist=value_now×risk_fraction）；名义敞口上限
    cap=当时权益×min(leverage, max_exposure)（与 max_exposure 同域
    取严）——超限截断 qty=cap/price 并标记 capped。
    入场/重放计数双消费方共用本函数（python 侧调用 njit 函数合法，
    公式单点防两处漂移）。距离/价格/权益非正 → (0, 0)（调用方回退
    fixed 百分比路径——防御直构 DNA 的残缺组合）。
    """
    if stop_dist <= 0.0 or price <= 0.0 or value_now <= 0.0 \
            or stop_dist != stop_dist:  # NaN 防御（atr 预热期）
        return 0.0, 0.0
    qty = value_now * risk_fraction / stop_dist
    cap = value_now * min(leverage, max_exposure)
    capped = 0.0
    if qty * price > cap:
        qty = cap / price
        capped = 1.0
    return qty, capped


def _load_sl_series_column(
    db_path, enhanced_df: pd.DataFrame, source: dict,
) -> np.ndarray:
    """series 档止损线列装载（dynamic-stop-loss-series 4.2——沿
    _load_atr_column 同构四态校验）。

    指标形制 {indicator, params, field?}：目标列名经 registry 命名
    规则构造（多输出列含 field、单输出省 field 用命名展开——与
    encode_column_ref 同源的列名推导）；df 直取/缺列经
    _compute_indicator 现算（depends_on 闭包同点受益）/现算不可得
    显式拒。fx 形制 {column}：enrich_with_plugin_columns 求值落列+
    verify（失效列显式拒）。

    装载校验四态（A4 哲学不可静默）：全 NaN 拒/首个非 NaN 后中段
    洞拒/值≤0 拒（价格水平线语义）/头部预热 NaN 合法（fx lookback
    期/指标 rolling 期）。

    Raises:
        ResearchError: RESEARCH_DATA_INSUFFICIENT（数据不足族——
            线列缺失/失效/形态不可用）。
    """
    if not isinstance(source, dict):
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            "sl_series_source 形式非法（须为指标形制或 fx 形制 dict）",
            detail={"field": "sl_series_source", "source": source})

    if "indicator" in source:
        from core.features.registry import INDICATOR_REGISTRY
        ind = source["indicator"]
        reg = INDICATOR_REGISTRY.get(ind)
        if reg is None:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"sl_series_source 指标不在注册表: {ind!r}",
                detail={"field": "sl_series_source.indicator"})
        params = dict(source.get("params") or {})
        # 列名推导：encode_column_ref（column_ref.py 权威——与
        # test_column_ref roundtrip 锚同源；core.backtest→
        # core.analysis.column_ref 为新增 import 边，方向与
        # plugin_service 消费同族无环）
        from core.analysis.column_ref import encode_column_ref
        field = source.get("field")
        if field is None:
            outputs = list(reg.output_fields)
            if len(outputs) > 1:
                raise ResearchError(
                    "RESEARCH_DATA_INSUFFICIENT",
                    f"sl_series_source 指标 {ind} 为多输出指标"
                    f"（{outputs}），field 必填",
                    detail={"field": "sl_series_source.field",
                            "output_fields": outputs})
        col = encode_column_ref(ind, params, field)
        if col in enhanced_df.columns:
            series = enhanced_df[col]
        else:
            from core.features.indicators import _compute_indicator
            computed = _compute_indicator(enhanced_df, ind, params)
            if col not in computed:
                raise ResearchError(
                    "RESEARCH_DATA_INSUFFICIENT",
                    f"series 止损线列 {col} 缺失且现算不可得"
                    "（数据不足或指标计算失败）",
                    detail={"field": col, "indicator": ind,
                            "params": params})
            series = computed[col]
        label = col
    elif "column" in source:
        col = str(source["column"])
        # fx 前缀校验（纵深防线第三面——登记面/validator 均有而装载
        # 面缺位：无前缀列名静默拿行情基础列当止损线不可察）；错误码
        # 沿 series×risk_budget 装载纵深先例（PARAM_INVALID——形制
        # 错误非数据不足）
        if not col.startswith("fx_"):
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"series 止损线 fx 形式列名须 fx_ 前缀: {col!r}",
                detail={"field": col,
                        "hint": "fx 形式=fx_ 前缀精确插件列名"})
        if db_path is None:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"series 止损线 fx 形制须 db_path（插件注册表查询——"
                f"引擎构造时未提供，fx 列 {col} 不可装载）",
                detail={"field": col,
                        "hint": "BacktestEngine(db_path=...) 构造传入"
                                "（桥层/路由已接；纯计算直调面用指标"
                                "形制）"})
        from core.analysis.plugin_service import (
            enrich_with_plugin_columns)
        try:
            work = enrich_with_plugin_columns(db_path, enhanced_df,
                                              {col})
        except ResearchError:
            raise
        except Exception as exc:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"series 止损线插件列求值失败: {col}（{exc}）",
                detail={"field": col}) from exc
        if col not in work.columns:
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"series 止损线插件列失效（不存在或未注册）: {col}",
                detail={"field": col})
        series = work[col]
        label = col
    else:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            "sl_series_source 来源必填（indicator 或 column 键）",
            detail={"field": "sl_series_source"})

    arr = pd.Series(series).values.astype(np.float64)
    valid = ~np.isnan(arr)
    if int(valid.sum()) == 0:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"series 止损线列 {label} 全 NaN（区间短于周期或数据缺失）",
            detail={"field": label})
    first_valid = int(np.argmax(valid))
    tail = arr[first_valid:]
    if np.isnan(tail).any():
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"series 止损线列 {label} 首个有效值之后存在 NaN"
            "（中段数据洞——止损保护不可静默中断）",
            detail={"field": label})
    if (tail <= 0.0).any():
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"series 止损线列 {label} 含非正值（≤0 的线值不可作"
            "价格水平线锚）",
            detail={"field": label})
    return arr


def _per_dna_risk_params(risk_genes, enhanced_df: pd.DataFrame,
                         db_path=None) -> tuple:
    """单个 DNA 的风控参数装配（单路径与批量路径共享单源）。

    返回 (sl_mode_val, atr_col_or_None, exit_fraction, add_fraction,
    max_exposure, max_hold, time_exit, position_mode_code,
    risk_fraction, effective_first_size, max_position_loss)：
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
    # series 档：线值列装载入 atr_col 槽位（njit 签名零变化——注释
    # 语义扩展：atr 档=ATR 值列/series 档=线值列/其余 None）；纵深
    # 防线：series×risk_budget 直构脏数据装载即拒（堵绕过 validate_
    # dna 的库内改写路径——不产出按残留值计算仓位的结果）
    if risk_genes.sl_mode == "series":
        if risk_genes.position_mode == "risk_budget":
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                "series×risk_budget 组合装载拒绝（纵深防线——series "
                "线值非标量，risk_budget 仓位分母不可定义）",
                detail={"conflict": ["sl_mode=series",
                                     "position_mode=risk_budget"]})
        atr_col = _load_sl_series_column(
            db_path, enhanced_df, risk_genes.sl_series_source or {})
    else:
        atr_col = (_load_atr_column(
            enhanced_df, int(risk_genes.atr_period))
            if risk_genes.sl_mode == "atr" else None)
    exit_fraction = (float(risk_genes.exit_fraction)
                     if risk_genes.exit_fraction is not None else 0.5)
    add_fraction = (float(risk_genes.add_fraction)
                    if risk_genes.add_fraction is not None else 0.1)
    max_exposure = (float(risk_genes.max_exposure)
                    if risk_genes.max_exposure is not None else 1.0)
    max_hold = (float(risk_genes.max_hold)
                if risk_genes.max_hold is not None else 0.0)
    # 固定时刻出场（任务 6）：None→-1.0=禁用哨兵（区别于 max_hold
    # 的 0.0——time_exit 的 0 是 0:00 UTC 合法时刻值）
    time_exit = (float(risk_genes.time_exit_utc)
                 if risk_genes.time_exit_utc is not None
                 else -1.0)
    # 动态定仓（complete-research-tooling 任务 4 + add-position-sizing-
    # params 2.1 + build-position-registry）：定仓装配三元组物化单源移驻
    # core/positioning（expand_for_backtest——resolve 单点的回测展开适配
    # 器；None/"fixed"→code 0.0 百分比固定路径、"risk_budget"→1.0 风险
    # 预算档，rf None→0.0 防御直构，efs=initial_fraction 物化>0 优先否则
    # position_size——全档物化语义见 adapter.py）。三元组与改造前装配逐
    # 位等值（R6 四组基准锁定）；两构建入口从本元组取值的接线不变；
    # risk_budget 档消费端不读 size_pct（互斥由装载校验承担，直构防御=
    # 忽略，不设第二判断源）
    exp = expand_for_backtest(risk_genes.position_mode,
                              risk_genes.position_size,
                              risk_genes.initial_fraction,
                              risk_genes.risk_fraction)
    # 持仓金额防线（add-position-sizing-params 2.2）：None→0.0=禁用
    # 哨兵（值域 >0 使 0 天然无歧义——max_hold 同款形制）；1D 逐列常数
    # 参数族。funding_acc 旁路费率不入本元组（永续口径费率经
    # _funding_rates_for 单点时序供给——调用方装配，与事后层同源）
    max_position_loss = (float(risk_genes.max_position_loss)
                         if risk_genes.max_position_loss is not None
                         else 0.0)
    return (sl_mode_val, atr_col, exit_fraction, add_fraction,
            max_exposure, max_hold, time_exit,
            exp.position_mode_code, exp.risk_fraction,
            exp.effective_first_size, max_position_loss)


@dataclass
class BacktestResult:
    """单次回测运行的结构化结果（指标+权益曲线+逐笔记录）。"""
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float | None  # None=零交易合法缺失（三态语义，见 win_rate_failed 注释）
    total_trades: int
    equity_curve: pd.Series
    # series 档止损价序列（hypothesis-workbench 组 2：sl_mode=series 时
    # 输出装载期线值列（索引=bt 区间），其余档 None——沿 equity_curve 先例）
    sl_series: "pd.Series | None" = None
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
    # 信号计数披露（fix-mtf-execution-consistency C3）：请求区间口径
    # 四键 {entries, exits, adds, reduces}——「条件全假零交易」与
    # 「装配异常」在回测输出可区分（三态判读：①entries==0=条件未
    # 触发；②entries>0 且 total_trades==0=执行段无成交；③正常）。
    # None=未经 run() 求值路径（批量/直组 portfolio 形制）——纯增量。
    signal_counts: dict | None = None
    # 基准对照（improve-backtest-benchmark-disclosure）：区间口径。
    # None=无基准数据（早退/无基准序列），不与 0.0 混同——0.0 是
    # 「基准区间持平」的真实值；零费基准（不扣资金费/交易费，从严）
    benchmark_return: Optional[float] = None
    excess_return: Optional[float] = None
    # 永续资金费披露五键（卡 C 3.4）：{mode:"perpetual", total_cost,
    # total_received, coverage, caliber_note}——付出/收入分列两桶
    # （空头收入额与负费率多头收入并入 received）；coverage=费率供给
    # 元数据 {status, covered, gaps}（缺失区间 kind 两态分列）；
    # caliber_note=跨环境口径差固定文案（回测连续折算 vs 纸盘/live
    # 8h 结算点）。纯增量键。
    funding: dict | None = None
    # ── 信号留痕通道产物（add-vr-signal-trace——纯增量，缺省 None）──
    # trace_buf：事件码矩阵（n_bars×1，0.0=无事件；resolver 消费）
    trace_buf: "np.ndarray | None" = None
    # trail_series：trail 档逐 bar 止损线真值（engine.run 组装时自
    # trail_buf[:,0] 转换——沿 sl_series 先例；非 trail 档 None；
    # 禁事后重算——重算须复刻全部生命周期规则任一漏即线形漂移）
    trail_series: "pd.Series | None" = None
    # swallowed_rows：求值层吞没行（SignalSet.swallowed 透传——
    # [{bar_time, reason}] 形制；resolver 合并进留痕行）
    swallowed_rows: list | None = None
    # value_curve_raw：原始组合价值曲线（portfolio.value() 放大/资金
    # 费调整前——resolver capped 重放的单源：期望下单量的 value_now
    # 对齐基准，与 _position_sizing_disclosure 同款对齐假设）
    value_curve_raw: "pd.Series | None" = None
    # mtf_gated_bars：MTF 决策门控拦截 bar 计（SignalSet 透传——
    # null=DNA 无门控面与 0=门控全过严格区分；记录级聚合在入口层）
    mtf_gated_bars: "int | None" = None


@njit
def _apply_funding_loop_nb(adjusted: np.ndarray, position_mask: np.ndarray,
                           rates: np.ndarray,
                           position_values: np.ndarray | None = None):
    """永续资金费扣减内循环（numba njit——逐 bar 就地扣，卡 C 3.2）。

    计费语义：cost[t]=|position_value[t−1]|×rates[t]×direction——费率
    符号进乘积、方向由仓位符号单源推出（正费率多头付/空头收=权益
    增加；负费率反向；direction 由 position_values 符号得出，无独立
    方向数组）。rates 为 _funding_rates_for 产出的逐 bar 已折算费率
    （rate_ffill×hours_per_bar/8——A3 连续折算形制）。

    Args:
        adjusted: 权益值 float64 数组（就地修改）。
        position_mask: 持仓掩码（True=该 bar 持仓中）。
        rates: 逐 bar 资金费率 float64 数组（已含 hours_frac 折算）。
        position_values: 逐 bar 仓位价值 float64 数组；提供时计费基数=
            |position_value|（名义敞口绝对量——1x 同样计费，无
            借贷比例折减）而非总权益；缺省按权益×多头方向计。

    Returns:
        (总付出, 总收入)——多空互付后分列两桶（空头正费率收入、
        负费率多头收入并入收入桶）；净支出=总付出−总收入。
    """
    total_paid = 0.0
    total_received = 0.0
    for i in range(1, len(adjusted)):
        if position_mask[i] and rates[i] != 0.0:
            if position_values is not None:
                pv = position_values[i - 1]
                base = abs(pv)
                if pv > 0.0:
                    direction = 1.0
                elif pv < 0.0:
                    direction = -1.0
                else:
                    direction = 0.0
            else:
                base = adjusted[i - 1]
                direction = 1.0
            cost = base * rates[i] * direction
            if cost > 0.0:
                total_paid += cost
            elif cost < 0.0:
                total_received += -cost
            adjusted[i] -= cost
    return total_paid, total_received


def _funding_rates_for(df: pd.DataFrame, timeframe: str):
    """永续资金费率时序供给（卡 C 3.1 单点装配——双消费点同源）。

    df 含 funding_rate 列（装载链 reindex+ffill 供给——8h±1ms 偏移
    已被 ffill 吸收，引擎不做二次归并）→ rates=列值 NaN→0 后乘
    hours_per_bar/8 连续折算（逐 bar 单 bar 有效费率）；**段外置零**：
    funding parquet 覆盖边界之外一律 0——含 ffill 外推尾段（外推值
    非真实费率不计费），覆盖边界权威源=df.attrs[
    "derivatives_coverage"].funding。df 不含列（裸 df 调用路径）→
    rates 全零+status="not_loaded"（无时序供给零费）。

    hours_per_bar 八周期映射单源（_funding_cost_rate 退役迁移至此）。

    Returns:
        (rates, meta)：meta={status, covered, gaps}——gaps 为缺失
        区间清单 [{start, end, kind}]，kind∈{outside_coverage（覆盖
        边界外——含 ffill 外推）/hole（覆盖段内数据洞——质量问题）}；
        NaN→0 零费段可披露可判读（零费可以，静默不行——A4）。
    """
    n = len(df)
    hours_per_bar = {
        "1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
        "1h": 1, "4h": 4, "1d": 24, "3d": 72,
    }.get(timeframe, 4)
    hours_frac = hours_per_bar / 8.0

    if "funding_rate" not in df.columns:
        return (np.zeros(n, dtype=np.float64),
                {"status": "not_loaded", "covered": None, "gaps": []})

    raw = df["funding_rate"].to_numpy(dtype=np.float64)
    nan_mask = np.isnan(raw)
    rates = raw.copy()
    rates[nan_mask] = 0.0

    idx = df.index
    cov = None
    try:
        cov = (df.attrs.get("derivatives_coverage") or {}).get("funding")
    except Exception:  # noqa: BLE001 —— attrs 形态异常按不可得处理
        cov = None

    gaps: list[dict] = []

    def _push_gap(start, end, kind):
        # 相邻同 kind 段合并（ISO 串边界相等即相邻）
        if gaps and gaps[-1]["kind"] == kind and gaps[-1]["end"] == start:
            gaps[-1]["end"] = end
        else:
            gaps.append({"start": start, "end": end, "kind": kind})

    def _segments(mask: np.ndarray):
        """布尔掩码的连续 True 段 [(首 idx 位置, 末 idx 位置)]。"""
        segs = []
        pos = np.flatnonzero(mask)
        if len(pos) == 0:
            return segs
        start = pos[0]
        for k in range(1, len(pos)):
            if pos[k] != pos[k - 1] + 1:
                segs.append((start, pos[k - 1]))
                start = pos[k]
        segs.append((start, pos[-1]))
        return segs

    if cov and cov.get("available_start") and cov.get("available_end"):
        lo = pd.Timestamp(cov["available_start"])
        hi = pd.Timestamp(cov["available_end"])
        before = np.asarray(idx < lo)
        after = np.asarray(idx > hi)
        # 段外置零（覆盖前无源+ffill 外推尾段——非真实费率不计费）
        rates[before] = 0.0
        rates[after] = 0.0
        for a, b in _segments(before | after):
            _push_gap(idx[a].isoformat(), idx[b].isoformat(),
                      "outside_coverage")
        # 段内洞：covered 区间内的原始 NaN 段（零费+披露分列）
        for a, b in _segments(nan_mask & ~before & ~after):
            _push_gap(idx[a].isoformat(), idx[b].isoformat(), "hole")
        status = "loaded"
        covered = [cov["available_start"], cov["available_end"]]
    else:
        # 覆盖边界不可得（裸列直调/探测失败降级）：按在场列值计费，
        # status 标注可判读（不静默零费也不静默计费）
        status = "coverage_unknown"
        covered = None
        for a, b in _segments(nan_mask):
            _push_gap(idx[a].isoformat(), idx[b].isoformat(), "hole")

    rates *= hours_frac
    return rates, {"status": status, "covered": covered, "gaps": gaps}


# 资金费口径差披露固定文案（卡 C 3.4——跨环境语义差在披露面可见）
_FUNDING_CALIBER_NOTE = (
    "永续合约资金费口径：回测为逐 bar 连续折算（每 bar 计费=名义敞口×"
    "当 bar 费率×hours_per_bar/8，时变费率取 funding_rate 列 ffill 值，"
    "多空互付——正费率多头付出/空头收入）；纸盘/live 为 8h 结算点口径"
    "（逐结算点对账），跨环境存在口径差；覆盖边界外（含 ffill 外推）"
    "零费，缺失区间见 coverage.gaps 分列披露")


def _apply_funding_costs(
    equity_curve: pd.Series,
    timeframe: str,
    trades_df: Optional[pd.DataFrame] = None,
    position_values: Optional[np.ndarray] = None,
    rates: Optional[np.ndarray] = None,
) -> tuple[pd.Series, float, float]:
    """永续资金费扣减——仅持仓时段收费（卡 C 3.2/3.3）。

    计费基数=|position_value[t−1]|（名义敞口绝对值，1x 同样计费——
    无 leverage≤1 短路与借贷比例折减）；费率符号进乘积、
    方向由仓位符号单源推出（多空互付）。rates 为 _funding_rates_for
    单点产出的逐 bar 已折算费率数组（缺省全零=无时序供给零费——
    裸 df 调用路径）。

    Args:
        equity_curve: 逐 bar 组合权益。
        timeframe: bar 周期串（hours_frac 折算在 rates 装配层完成，
            此处仅透传语义——rates 已含折算）。
        trades_df: 逐笔记录（Entry/Exit Timestamp）；空表=零费用
            （无持仓即无资金费）；None=全程按持仓扣费（legacy 形制）。
        position_values: 逐 bar 仓位价值数组（计费基数）。
        rates: 逐 bar 已折算费率数组（_funding_rates_for 产出）。

    Returns:
        (调整后权益曲线, 总付出, 总收入)——净支出=总付出−总收入。
    """
    if rates is None:
        rates = np.zeros(len(equity_curve), dtype=np.float64)

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

    total_paid, total_received = _apply_funding_loop_nb(
        adjusted, position_mask, rates, pv_for_funding)
    return (pd.Series(adjusted, index=equity_curve.index),
            total_paid, total_received)


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
    / entry_bar / funding_acc 数组）。

    经 vbt 生命周期链作为 order_func_nb 首组参数传入，供逐 bar 回调
    就地读写（入场价记忆、清算标记与 trail 峰值——float 承载因 numba
    状态数组须数值型）。peak_price（0.4.2 R2）：trail 模式持仓期峰值
    （多=最高价/空=最低价），0.0=无持仓（开仓重置成交价、清仓清零）。
    entry_bar（add-max-hold-exit D-3）：入场 bar 序号记忆（与入场价
    同点写入——每次入场独立重计持有根数；0.0=无持仓）。
    funding_acc（add-position-sizing-params 2.2）：当前持仓累计资金费
    （金额防线判定的资金费分量），与 entry_price 同生命周期（开仓归零、
    出场清零）。不落库不进报告不进 metrics、不触碰 vbt 可见状态（只读
    position_now/close 写自身数组）——对外可见资金费事实仍只有事后层
    total_funding_cost 与调整后权益曲线一处。
    """
    n_cols = c.target_shape[1]
    entry_price = np.full(n_cols, 0.0, dtype=np.float64)
    is_liquidated = np.full(n_cols, 0.0, dtype=np.float64)  # 0.0=False，1.0=True
    peak_price = np.full(n_cols, 0.0, dtype=np.float64)
    entry_bar = np.full(n_cols, 0.0, dtype=np.float64)
    funding_acc = np.full(n_cols, 0.0, dtype=np.float64)
    return (entry_price, is_liquidated, peak_price, entry_bar, funding_acc)


@njit
def order_func_nb(c, entry_price, is_liquidated, peak_price, entry_bar,
                  funding_acc,
                  entries, exits, adds, reduces,
                  direction_vals, size_pcts, leverages,
                  sl_stops, tp_stops, sl_modes, atr_arr,
                  exit_fractions, add_fractions, max_exposures,
                  max_holds, time_exits,
                  max_position_losses, funding_rates,
                  position_modes, risk_fractions,
                  fee, slippage,
                  high_arr, low_arr, direction_signal,
                  bar_ts_minutes, tf_minutes,
                  mmr_tier_max, mmr_tier_rate,
                  trace_buf, trail_buf):
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
        entry_price, is_liquidated, peak_price, entry_bar, funding_acc:
            pre_sim_func_nb 传入的可变状态。

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
        max_position_losses: 持仓金额防线逐列参数（1D float64；装配层
            物化 None→0.0=禁用哨兵——0.0 使首判即假，存量零开销）。
        funding_rates: funding_acc 旁路累计的逐 bar 费率矩阵
            （2D float64 bars×cols；装配层经 _funding_rates_for 单点
            产出逐列同源复制——与事后层同一费率源不漂移；已含
            hours_frac 折算与段外置零）。
        fee, slippage: 标量费滑（基础费率——杠杆放大归
            _apply_leverage_to_equity 后处理）。
        high_arr, low_arr: bar 内 SL/TP 判定用 2D 高低价。
        direction_signal: 2D 方向信号（+1=多，−1=空；mixed 模式用）。
        trace_buf: 信号留痕缓冲（add-vr-signal-trace——纯写入通道：
            每事件一格 trace_buf[i,col]=事件码〔蓝族 10-13/金族 20-25
            /灰族 30-34〕，0.0=无事件；装配段构造经 order_args 传入
            〔vbt 生命周期链：pre_sim 返回值+order_args 按序填形参——
            数组须由 Python 侧构造持有引用，仿真后结果提取可达〕；
            本函数内仅赋值零读回、不参与任何判定（REJ-01 纯写入）
        trail_buf: trail 档止损线逐 bar 真值缓冲（NaN=无值；trail
            分支峰值更新点后写当 bar sl_level，非 trail 档不写——
            结果经 BacktestResult.trail_series 单源透出，禁事后重算）
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

    # funding_acc 当 bar 旁路累计（永续口径——卡 C 3.3，先于一切出场
    # return：出场 bar 也计费）。计费=名义敞口×当 bar 费率×方向
    # （abs(position_now)×close[i−1]×leverage=名义敞口；费率符号进
    # 乘积、方向由仓位符号单源推出——空头正费率=收入即负累计，与
    # 事后层 _apply_funding_loop_nb 同式同源）。三重短路：未声明防线
    # （首判即假——存量路径 funding_acc 恒零零开销）、i==0（无滞后
    # bar）、当 bar 费率 0。纯读 position_now/close，写自身数组——
    # 不触碰 vbt 可见状态
    if max_position_losses[col] > 0.0 and i > 0 and c.position_now != 0.0:
        _fr = funding_rates[i, col]
        if _fr != 0.0:
            _qty = c.position_now
            _fdir = 1.0 if _qty > 0.0 else -1.0
            funding_acc[col] += (abs(_qty) * c.close[i - 1, col]
                                 * leverage * _fr * _fdir)

    # 已清算 → 强平余仓；仓位归零后资金充足时可再入场
    if is_liquidated[col] > 0.5:
        if c.position_now != 0.0:
            peak_price[col] = 0.0
            trace_buf[i, col] = 22.0  # 留痕：清算余仓强平
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
            trace_buf[i, col] = 31.0  # 留痕：资金不足（清算后再入场）
            return NoOrder  # 资金不足
        # 复位清算标记，落入下方入场逻辑
        is_liquidated[col] = 0.0
        entry_price[col] = 0.0
        funding_acc[col] = 0.0
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
            funding_acc[col] = 0.0
            peak_price[col] = 0.0
            trace_buf[i, col] = 22.0  # 留痕：清算触发强平
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
                    trace_buf[i, col] = 20.0  # 留痕：SL 出场（pct·多）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                # TP：bar HIGH 触及止盈线
                if tp_stop > 0.0 and bar_high >= tp_level:
                    trace_buf[i, col] = 21.0  # 留痕：TP 出场（pct·多）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
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
                    trace_buf[i, col] = 20.0  # 留痕：SL 出场（pct·空）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                # TP：bar LOW 触及止盈线
                if tp_stop > 0.0 and bar_low <= tp_level:
                    trace_buf[i, col] = 21.0  # 留痕：TP 出场（pct·空）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
        elif sl_mode > 2.5:
            # ── series 分支（dynamic-stop-loss-series：止损线=预计算
            # 价格列——atr_arr 槽位复用承载线值列〔njit 签名零变化；
            # 装载链 _load_sl_series_column 填充，见 4.2〕。触发守卫=
            # 持仓在+line 有效（NaN guard：预热/缺失 bar 无 SL 约束，
            # 沿 atr 同哲学）；不读 sl_stop>0——stop_loss 标量残留值
            # MUST NOT 消费〔物化默认 0.05 非行为值〕；触发价=线值
            # 〔D2 intrabar 先例〕：多头 bar_low<=line / 空头
            # bar_high>=line 同列镜像〔方向由持仓侧解释——D1〕；TP
            # 沿 pct 语义锚入场价〔A1〕）──
            line = atr_arr[i, col]
            if line == line:  # NaN guard：线值无效 bar 无 SL 约束
                if c.position_now > 0.0:
                    if bar_low <= line:
                        trace_buf[i, col] = 20.0  # 留痕：SL 出场（series·多）
                        entry_price[col] = 0.0
                        funding_acc[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
                else:
                    if bar_high >= line:
                        trace_buf[i, col] = 20.0  # 留痕：SL 出场（series·空）
                        entry_price[col] = 0.0
                        funding_acc[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
            # TP 沿 pct 语义锚入场价（与动态线 SL 并存生效）
            if c.position_now > 0.0:
                tp_level = ep * (1.0 + tp_stop) if tp_stop > 0.0 else 0.0
                if tp_stop > 0.0 and bar_high >= tp_level:
                    trace_buf[i, col] = 21.0  # 留痕：TP 出场（series·多）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
            else:
                tp_level = ep * (1.0 - tp_stop) if tp_stop > 0.0 else 0.0
                if tp_stop > 0.0 and bar_low <= tp_level:
                    trace_buf[i, col] = 21.0  # 留痕：TP 出场（series·空）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
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
                # 留痕：trail 档逐 bar 止损线真值（峰值更新后当 bar 线
                # ——含触发 bar；sl_stop=0 防御不写防与 NaN 无值态混淆）
                if sl_stop > 0.0:
                    trail_buf[i, col] = sl_level
                # TP 沿 pct 语义锚入场价（trail 与 TP 并存，SL 先 TP 后）
                tp_level = ep * (1.0 + tp_stop) if tp_stop > 0.0 else 0.0
                if sl_stop > 0.0 and bar_low <= sl_level:
                    trace_buf[i, col] = 20.0  # 留痕：SL 出场（trail·多）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                if tp_stop > 0.0 and bar_high >= tp_level:
                    trace_buf[i, col] = 21.0  # 留痕：TP 出场（trail·多）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
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
                # 留痕：trail 档逐 bar 止损线真值（空头对称）
                if sl_stop > 0.0:
                    trail_buf[i, col] = sl_level
                tp_level = ep * (1.0 - tp_stop) if tp_stop > 0.0 else 0.0
                if sl_stop > 0.0 and bar_high >= sl_level:
                    trace_buf[i, col] = 20.0  # 留痕：SL 出场（trail·空）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
                    peak_price[col] = 0.0
                    return vbt_nb.order_nb(
                        size=np.float64(-c.position_now),
                        size_type=np.int64(0),
                        fees=effective_fee,
                        slippage=effective_slippage,
                    )
                if tp_stop > 0.0 and bar_low <= tp_level:
                    trace_buf[i, col] = 21.0  # 留痕：TP 出场（trail·空）
                    entry_price[col] = 0.0
                    funding_acc[col] = 0.0
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
                        trace_buf[i, col] = 20.0  # 留痕：SL 出场（atr·多）
                        entry_price[col] = 0.0
                        funding_acc[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
                    if tp_stop > 0.0 and bar_high >= tp_level:
                        trace_buf[i, col] = 21.0  # 留痕：TP 出场（atr·多）
                        entry_price[col] = 0.0
                        funding_acc[col] = 0.0
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
                        trace_buf[i, col] = 20.0  # 留痕：SL 出场（atr·空）
                        entry_price[col] = 0.0
                        funding_acc[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )
                    if tp_stop > 0.0 and bar_low <= tp_level:
                        trace_buf[i, col] = 21.0  # 留痕：TP 出场（atr·空）
                        entry_price[col] = 0.0
                        funding_acc[col] = 0.0
                        peak_price[col] = 0.0
                        return vbt_nb.order_nb(
                            size=np.float64(-c.position_now),
                            size_type=np.int64(0),
                            fees=effective_fee,
                            slippage=effective_slippage,
                        )

    # 持仓金额防线（add-position-sizing-params 2.2，参数出场族第五
    # 机制）：单一合并持仓总浮亏（价格浮亏+该持仓累计资金费净支出）
    # 按当根收盘价判定，≥声明金额全平。判定序：SL/TP 之后、time_exit
    # 之前（价格触发优先——与止损同根双触发归止损：SL 段在前先 return，
    # 位次与纸盘判定序一致）；与 time_exit/max_hold 同形制（判定段
    # return 全平单、当根 close 成交）。公式带符号天然双向：空头
    # position_now<0、价涨 (close−entry)>0，乘积为负取负号后为正=浮亏
    # 态；价跌时乘积为正、取负号为负=盈利态不触发——无方向分支。
    # 0.0=禁用哨兵（装配层 None 物化，首判即假——存量零开销）
    if max_position_losses[col] > 0.0 and c.position_now != 0.0 \
            and entry_price[col] > 0.0:
        loss = -(c.position_now * (current_price - entry_price[col])) \
            + funding_acc[col]
        if loss >= max_position_losses[col]:
            trace_buf[i, col] = 23.0  # 留痕：持仓金额防线出场
            entry_price[col] = 0.0
            funding_acc[col] = 0.0
            peak_price[col] = 0.0
            entry_bar[col] = 0.0
            return vbt_nb.order_nb(
                size=np.float64(-c.position_now),
                size_type=np.int64(0),
                fees=effective_fee,
                slippage=effective_slippage,
            )

    # 固定时刻出场（add-session-structure-expression 任务 6，参数
    # 出场族第四机制）：当根 bar 时间区间 [open_ts, open_ts+tf) 覆盖
    # 当日 UTC 分钟时刻即无条件全平（左闭右开——区间右端时刻归下根
    # bar）。覆盖判定：r=(t_exit−bar 当日分钟) mod 1440 < tf_minutes
    # （t_exit 每日重复出现，取不早于 bar 开盘的下一次出现判定是否
    # 落在本 bar 区间内）；tf≥1440（日线及以上）每 bar 覆盖全天恒
    # 触发≡max_hold=1 退化（spec 记录在案）。判定序：SL/TP 之后、
    # max_hold 之前（价格触发优先于时刻到期）。-1.0=禁用哨兵
    # （None 物化；0.0 是 0:00 合法值——首判 t_exit>=0 即假等价性：
    # 哨兵 -1.0 使存量（无声明）路径零行为变化）
    if time_exits[col] >= 0.0 and c.position_now != 0.0:
        r = (time_exits[col] - (bar_ts_minutes[i] % 1440.0)) % 1440.0
        if r < tf_minutes:
            trace_buf[i, col] = 24.0  # 留痕：固定时刻出场
            entry_price[col] = 0.0
            funding_acc[col] = 0.0
            peak_price[col] = 0.0
            entry_bar[col] = 0.0
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
            trace_buf[i, col] = 25.0  # 留痕：max_hold 时间出场
            entry_price[col] = 0.0
            funding_acc[col] = 0.0
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
        trace_buf[i, col] = 13.0  # 留痕：信号离场
        entry_price[col] = 0.0
        funding_acc[col] = 0.0
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
        # funding_acc 开仓归零（新持仓的资金费从零累计——出场清零之外
        # 的第二重锚点，防上一持仓残留值泄入新持仓判定）
        funding_acc[col] = 0.0
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
        # 动态定仓（任务 4）：fixed 档（含缺省 0.0）百分比路径逐字节
        # 不变；risk_budget 档数量仓——止损距离按 sl_mode 三档分派
        # （pct/trail 同为价格×比例〔trail=峰值回撤比例〕，仅 atr=
        # 比例×当根 ATR——与下方分支及内层 D-6 注释一致），公式单点
        # risk_budget_qty；距离不可定义（无止损/NaN）回退 fixed
        # 百分比路径（防御直构 DNA）
        order_size = np.float64(size_pct * dir_sign)
        order_size_type = np.int64(2)  # 百分比仓
        if position_modes[col] > 0.5:
            if sl_mode > 2.5:
                # series×risk_budget 纵深防线（引擎侧）：装载层
                # _per_dna_risk_params 已拒（4.2），此处不可达兜底——
                # 不产出按残留值 0.05 计算 risk_budget 仓位的结果
                # （C4-A3 ⑧/⑨反面锚检出对象），回退 fixed 百分比仓
                return vbt_nb.order_nb(
                    size=np.float64(size_pct * dir_sign),
                    size_type=np.int64(2),
                    direction=np.int64(int(actual_direction)),
                    fees=effective_fee,
                    slippage=effective_slippage,
                )
            elif sl_mode < 0.5 or sl_mode > 1.5:
                # pct(0)/trail(2) 的 stop_loss 同为比例域（trail=峰值
                # 回撤比例——止损距离口径=入场价×比例，D-6 修复：此前
                # trail 误并入 ATR 倍数分支，而装配层对非 atr 模式 ATR
                # 占位全 1，stop_dist=比例值×1 数值错档致 qty 虚高顶格）
                stop_dist = current_price * sl_stop
            else:
                stop_dist = sl_stop * atr_arr[i, col]
            qty, capped = risk_budget_qty(
                c.value_now, current_price, stop_dist,
                risk_fractions[col], leverage, max_exposures[col])
            if qty > 0.0:
                order_size = np.float64(qty * dir_sign)
                order_size_type = np.int64(0)  # 数量仓
                trace_buf[i, col] = 10.0  # 留痕：入场（数量仓）
            else:
                # 留痕：风险预算降级（qty≤0 回退百分比仓发单——Q1 对照
                # 实测裁决：outcome 由 resolver 与成交对照定，非恒拒绝）
                trace_buf[i, col] = 32.0
        else:
            trace_buf[i, col] = 10.0  # 留痕：入场（百分比仓）
        return vbt_nb.order_nb(
            size=order_size,
            size_type=order_size_type,
            direction=np.int64(int(actual_direction)),
            fees=effective_fee,
            slippage=effective_slippage,
        )

    # 减仓信号（部分平仓）——独立比例 exit_fraction（0.4.2 R3/A5：修正
    # 现状复用 position_size 的参数串用——50% 非 30%）
    if reduces[i, col] > 0.5 and c.position_now != 0.0:
        reduce_amount = c.position_now * exit_fractions[col]
        trace_buf[i, col] = 12.0  # 留痕：减仓信号
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
            trace_buf[i, col] = 30.0  # 留痕：敞口上限拒绝
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
        trace_buf[i, col] = 11.0  # 留痕：加仓信号
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
                 slippage: float = 0.0005, db_path=None):
        self.init_cash = init_cash
        self.fee = fee
        self.slippage = slippage
        # series 档 fx 形制装载依赖（dynamic-stop-loss-series 4.2）：
        # 插件注册表查询经 db_path；None=不支持 fx 形制（装载显式拒
        # ——桥层/路由传入，纯计算直调面仅指标形制可用）
        self.db_path = db_path

    def _position_sizing_disclosure(
        self, dna, portfolio, enhanced_df,
    ) -> dict:
        """仓位口径披露七子键（complete-research-tooling 任务 4；
        F-B4-1 补 replay_status）。

        {mode, risk_fraction, leverage_cap, replay_status,
        orders_capped, avg_entry_size, max_entry_size}——mode/
        risk_fraction 为声明面。replay_status（F-B4-1 失败不虚标）：
        统计三键的可信度状态，恒带两态——"ok"=值可信（fixed 档声明
        面确定 / risk_budget 重放成功含零交易）；"failed"=重放异常，
        orders_capped 与 avg/max_entry_size 置 None（此前失败路径
        吞错返回部分 out 使 orders_capped 恒 0——虚标「无截断」）。

        声明续注
        面；leverage_cap=min(leverage, max_exposure)（与引擎截断 cap
        同源）；orders_capped 与 entry size 统计为 risk_budget 档
        确定性重放（与 numba 回调共用 risk_budget_qty 公式单点——
        python 侧调用 njit 函数，两处公式不漂移）。ATR 距离按
        _load_atr_column 单点自装载（与 _build_portfolio 同源——
        装配层重载一次，重放成本可忽略）。

        重放对齐假设（诚实披露）：value_now=订单 bar 前一 bar 期末
        组合价值（portfolio.value() 曲线，首 bar 退化 init_cash）
        ——numba 侧 c.value_now 即订单决策时点的当前组合价值。
        """
        rg = dna.risk_genes
        mode = rg.position_mode or "fixed"
        leverage_cap = min(
            float(rg.leverage),
            float(rg.max_exposure) if rg.max_exposure is not None else 1.0)
        out = {
            "mode": mode,
            "risk_fraction": rg.risk_fraction,
            "leverage_cap": leverage_cap,
            "replay_status": "ok",
            "orders_capped": 0,
            "avg_entry_size": None,
            "max_entry_size": None,
        }
        if mode != "risk_budget" or rg.risk_fraction is None:
            return out  # fixed 档：声明面即全部（size 恒定=position_size）
        try:
            trades = portfolio.trades.records_readable
            if trades is None or len(trades) == 0:
                return out
            value_curve = portfolio.value()
            if isinstance(value_curve, pd.DataFrame):
                value_curve = value_curve.iloc[:, 0]
            val_arr = value_curve.values
            idx_positions = enhanced_df.index.get_indexer(
                pd.DatetimeIndex(trades["Entry Timestamp"]))
            sl_stop = float(rg.stop_loss) if rg.stop_loss else 0.0
            atr_col = (
                _load_atr_column(enhanced_df, int(rg.atr_period))
                if rg.sl_mode != "pct" else None)
            capped = 0
            sizes = []
            for k in range(len(trades)):
                pos = int(idx_positions[k])
                price = float(trades["Avg Entry Price"].iloc[k])
                size = abs(float(trades["Size"].iloc[k]))
                sizes.append(size)
                if pos <= 0:
                    value_now = float(self.init_cash)
                else:
                    value_now = float(val_arr[pos - 1])
                if rg.sl_mode in ("pct", "trail"):
                    # pct/trail 的 stop_loss 同为比例域（与引擎回调入场
                    # 分派同步，D-6——trail 此前误走 ATR 倍数分支）
                    stop_dist = price * sl_stop
                else:
                    if atr_col is None or pos >= len(atr_col):
                        continue  # 防御——atr 装载失败理论不可达
                    stop_dist = sl_stop * float(atr_col[pos])
                _, hit = risk_budget_qty(
                    value_now, price, stop_dist, float(rg.risk_fraction),
                    float(rg.leverage),
                    # 第 6 参按形参语义传有效 max_exposure（None→1.0
                    # 显式化）——此前传已 min 合并的 leverage_cap，幂等
                    # 恒等但签名语义误导（K6）
                    float(rg.max_exposure) if rg.max_exposure is not None
                    else 1.0)
                capped += int(hit)
            out["orders_capped"] = capped
            out["avg_entry_size"] = (
                float(np.mean(sizes)) if sizes else None)
            out["max_entry_size"] = (
                float(np.max(sizes)) if sizes else None)
        except Exception:
            logger.warning(
                "position_sizing disclosure replay failed",
                exc_info=True,
            )
            # 失败不虚标（F-B4-1）：三统计键不可信置 None + 状态显式
            # 披露——禁止以初值 0 假装「无截断」
            out["replay_status"] = "failed"
            out["orders_capped"] = None
            out["avg_entry_size"] = None
            out["max_entry_size"] = None
        return out

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
        leverage = float(dna.risk_genes.leverage)
        sl_stop = float(dna.risk_genes.stop_loss) if dna.risk_genes.stop_loss else 0.0
        tp_stop = float(dna.risk_genes.take_profit) if dna.risk_genes.take_profit else 0.0
        # SL 模式分派+ATR 装载+分批三参数+时间出场（0.4.2 R2/R3、
        # add-max-hold-exit D-3）：per-DNA 装配单源 _per_dna_risk_params
        # （与 batch_run 共享——漏装即与批量路径静默分叉）；非 atr 模式
        # 占位全 1 数组（引擎不消费）
        (sl_mode_val, atr_col, exit_fraction, add_fraction, max_exposure,
         max_hold, time_exit, position_mode_code, risk_fraction,
         effective_first_size, max_position_loss,
         ) = _per_dna_risk_params(
            dna.risk_genes, enhanced_df, db_path=self.db_path)
        # 首笔生效比例（add-position-sizing-params 2.1）：size_pct 赋值
        # 源从装配元组取（effective_first_size=声明 initial_fraction 否
        # 则 position_size）——物化分派单点，两入口同批接线
        size_pct = effective_first_size
        # 永续资金费率时序供给（卡 C 3.1 单点——与事后层同一函数同值，
        # 不产生第二费率源）：逐 bar 已折算费率（含段外置零），旁路按
        # bar×列矩阵传入
        funding_rates_1d, _funding_meta = _funding_rates_for(
            enhanced_df, dna.execution_genes.timeframe)
        # bar 开盘时刻与执行周期分钟（任务 6 两输入——run/batch 共用
        # 装配单点 _bar_ts_minutes_and_tf，语义与注释见该 helper）
        bar_ts_minutes_arr, tf_minutes_val = _bar_ts_minutes_and_tf(
            enhanced_df.index, dna.execution_genes.timeframe)
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

        # 信号留痕缓冲（add-vr-signal-trace）：装配段构造+order_args
        # 尾传（数组 Python 侧持有引用——仿真后结果提取可达；vbt 生命
        # 周期链按序填形参）；实例属性透传沿 _last_sl_col 先例（组装段
        # 消费后清空防跨 run 泄漏）。trail_buf NaN=无值（非 trail 档
        # 不写）；trace_buf 0.0=无事件
        n_bars_sim = len(enhanced_df)
        trace_buf_sim = np.zeros((n_bars_sim, 1), dtype=np.float64)
        trail_buf_sim = np.full((n_bars_sim, 1), np.nan, dtype=np.float64)
        self._last_trace_buf = trace_buf_sim
        self._last_trail_buf = trail_buf_sim
        self._last_swallowed = getattr(sig_set, "swallowed", None)
        self._last_mtf_gated_bars = getattr(sig_set, "mtf_gated_bars", None)

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
            np.array([time_exit], dtype=np.float64),
            # 金额防线两参数（add-position-sizing-params 2.2——逐列常数
            # 参数族，time_exit 同款；费率为 _funding_rates_for 单点产出
            # 的逐 bar×列矩阵——卡 C 3.3）
            np.array([max_position_loss], dtype=np.float64),
            funding_rates_1d.reshape(-1, 1),
            # 动态定仓两参数（任务 4——逐列常数参数族，time_exit 同款）
            np.array([position_mode_code], dtype=np.float64),
            np.array([risk_fraction], dtype=np.float64),
            np.float64(self.fee),
            np.float64(self.slippage),
            high_2d,
            low_2d,
            direction_signal_2d,
            # bar 开盘 UTC 分钟绝对数（任务 6 时刻覆盖判定）+每 bar
            # 周期分钟数（执行周期——timeframe_minutes 单源派生）
            bar_ts_minutes_arr,
            np.float64(tf_minutes_val),
            _mm_tier_arrays()[0],
            _mm_tier_arrays()[1],
            trace_buf_sim,
            trail_buf_sim,
            pre_sim_func_nb=pre_sim_func_nb,
            init_cash=self.init_cash,
            freq=None,
        )

        # series 档线值列经实例属性透传（hypothesis-workbench 组 2：
        # 四元组签名零改动——三个消费面 run/run_with_portfolio/测试直调
        # 均解包四元组；_last_sl_col=模块内契约，组装段消费后清空防跨
        # run 泄漏）
        self._last_sl_col = (
            atr_col if (sl_mode_val == sl_mode_code("series")
                        and atr_col is not None) else None)
        return pf, int(adds.sum()), int(reduces.sum()), sig_set.degraded_layers

    def _build_result_from_portfolio(
        self,
        portfolio,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        add_count: int = 0,
        reduce_count: int = 0,
        degraded: int = 0,
        signal_counts: dict | None = None,
    ) -> BacktestResult:
        """从已构建的 portfolio 组装 BacktestResult（杠杆放大→清算判定→资金费→指标）。"""
        equity_curve = portfolio.value()
        if isinstance(equity_curve, pd.DataFrame):
            equity_curve = equity_curve.iloc[:, 0]
        # 原始组合价值曲线（放大/资金费调整前——留痕 resolver capped
        # 重放的单源，add-vr-signal-trace）
        value_curve_raw = equity_curve

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

        # 永续资金费（在放大后曲线上收——卡 C：时序费率供给与旁路装配
        # 同一 _funding_rates_for 单点，run/batch 共用本组装点不漂移）
        timeframe = dna.execution_genes.timeframe
        funding_rates_1d, funding_meta = _funding_rates_for(
            enhanced_df, timeframe)
        trades_for_funding = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None
        equity_curve, funding_paid, funding_received = _apply_funding_costs(
            equity_curve, timeframe,
            trades_df=trades_for_funding,
            position_values=position_values_arr,
            rates=funding_rates_1d,
        )
        # 旧字段语义=净支出（总付出−总收入——空头净收入为负）；REST
        # 响应既有消费方零适配，明细两桶见 funding 披露
        total_funding_cost = funding_paid - funding_received
        funding_disclosure = {
            "mode": "perpetual",
            "total_cost": funding_paid,
            "total_received": funding_received,
            "coverage": funding_meta,
            "caliber_note": _FUNDING_CALIBER_NOTE,
        }

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

        # 仓位口径披露（complete-research-tooling 任务 4——纯增量键）：
        # mode/risk_fraction/leverage_cap 声明面 + orders_capped 截断计数
        # （risk_budget 档确定性重放——与 numba 回调共用 risk_budget_qty
        # 公式单点；value_now 对齐=订单 bar 前一 bar 期末组合价值，首
        # bar 退化 init_cash——披露对齐假设）+ entry size 统计（数量仓
        # 口径；fixed 档 size 恒定只报 mode/参数面）
        position_sizing_disclosure = self._position_sizing_disclosure(
            dna, portfolio, enhanced_df)
        metrics["position_sizing"] = position_sizing_disclosure

        if len(equity_curve) >= 2 and equity_curve.iloc[0] > 0:
            raw_return = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1)
        else:
            raw_return = 0.0

        trades = portfolio.trades.records_readable if hasattr(portfolio.trades, "records_readable") else None

        # series 档止损线序列透出（hypothesis-workbench 组 2）：atr_col
        # 槽位在 series 档承载装载期线值列（_per_dna_risk_params :339 注释）；
        # 索引=调用方传入的 enhanced_df（bridge=切回执行段/REST=请求段——
        # 不恒含 warmup 前缀，由调用方装载链决定，审查 CARD-02 注释改实）
        _sl_col = getattr(self, "_last_sl_col", None)
        self._last_sl_col = None  # 消费即清（防跨 run 泄漏）
        sl_series_out = (
            pd.Series(_sl_col, index=enhanced_df.index, name="sl_price")
            if _sl_col is not None
            else None)

        # ── 信号留痕通道产物接线（add-vr-signal-trace——实例属性消费
        # 即清，沿 _last_sl_col 先例；batch_run 装配段已清 None 故批量
        # 结果天然不带留痕产物〔缓冲照写不落库〕）──
        _trace_buf = getattr(self, "_last_trace_buf", None)
        _trail_buf = getattr(self, "_last_trail_buf", None)
        _swallowed = getattr(self, "_last_swallowed", None)
        _mtf_gated = getattr(self, "_last_mtf_gated_bars", None)
        self._last_trace_buf = None
        self._last_trail_buf = None
        self._last_swallowed = None
        self._last_mtf_gated_bars = None
        trail_series_out = (
            pd.Series(_trail_buf[:, 0], index=enhanced_df.index,
                      name="trail_price")
            if (_trail_buf is not None
                and not bool(np.isnan(_trail_buf).all()))
            else None)

        return BacktestResult(
            total_return=raw_return,
            sharpe_ratio=float(sharpe),
            max_drawdown=float(max_dd),
            win_rate=None if win_rate is None else float(win_rate),
            total_trades=total_trades_val,
            equity_curve=equity_curve,
            sl_series=sl_series_out,
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
            signal_counts=signal_counts,
            funding=funding_disclosure,
            trace_buf=_trace_buf,
            trail_series=trail_series_out,
            swallowed_rows=_swallowed,
            value_curve_raw=value_curve_raw,
            mtf_gated_bars=_mtf_gated,
        )

    def run(
        self,
        dna: StrategyDNA,
        enhanced_df: pd.DataFrame,
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
        signal_set=None,
    ) -> BacktestResult:
        """单策略 DNA 回测（构建 portfolio → 组装结果）。"""
        # C3 信号计数：signal_set 缺省时求值上移到 run（一次求值两
        # 消费——计数与 portfolio 构建同源不重算；显式传入（插件源
        # 组装链）直接计数）
        if signal_set is None:
            signal_set = dna_to_signal_set(
                dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
        sig_counts = {
            "entries": int(signal_set.entries.fillna(False).sum()),
            "exits": int(signal_set.exits.fillna(False).sum()),
            "adds": int(signal_set.adds.fillna(False).sum()),
            "reduces": int(signal_set.reduces.fillna(False).sum()),
        }
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
            signal_counts=sig_counts,
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
        per_dna = [_per_dna_risk_params(dna.risk_genes, enhanced_df,
                                        db_path=self.db_path)
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
        time_exits = np.array([p[6] for p in per_dna], dtype=np.float64)
        # 动态定仓两参数（任务 4——单源装配追加元同款防护）
        position_modes = np.array([p[7] for p in per_dna], dtype=np.float64)
        risk_fractions = np.array([p[8] for p in per_dna], dtype=np.float64)
        # 首笔生效比例（add-position-sizing-params 2.1）：size_pcts 赋值
        # 源从装配元组取（p[9]=effective_first_size——声明 initial_
        # fraction 否则 position_size）。与 _build_portfolio 同批接线：
        # 漏接本入口的失效形态是进化种群首笔静默按 position_size 执行
        # 的静默分叉（不报错）
        size_pcts = np.array([p[9] for p in per_dna], dtype=np.float64)
        # 金额防线（add-position-sizing-params 2.2）：None→0.0 禁用哨兵
        # 逐列参数族
        max_position_losses = np.array(
            [p[10] for p in per_dna], dtype=np.float64)
        # bar 开盘时刻与执行周期分钟（任务 6——批量种群共享同一执行
        # 周期（batch_run 语义：全部个体同一 enhanced_df/timeframe）；
        # run/batch 共用装配单点 _bar_ts_minutes_and_tf）
        exec_tf = individuals[0].execution_genes.timeframe \
            if individuals else "4h"
        bar_ts_minutes_arr, tf_minutes_val = _bar_ts_minutes_and_tf(
            enhanced_df.index, exec_tf)
        # 永续费率旁路矩阵（卡 C 3.3）：_funding_rates_for 单点产出逐列
        # 同源复制（多 DNA 同 df 费率列逐元素相等——与事后层同一费率源）
        funding_rates_1d, _funding_meta = _funding_rates_for(
            enhanced_df, exec_tf)
        funding_rates_2d = np.tile(
            funding_rates_1d.reshape(-1, 1), (1, M))
        # 信号留痕缓冲（add-vr-signal-trace）：批量路径缓冲照写（numba
        # 同函数不可分支）但结果不解析不落库（batch 无验证记录语境）——
        # 实例属性显式清空防上一 run() 残留泄入本批量循环的组装段
        trace_buf_batch = np.zeros((N, M), dtype=np.float64)
        trail_buf_batch = np.full((N, M), np.nan, dtype=np.float64)
        self._last_trace_buf = None
        self._last_trail_buf = None
        self._last_swallowed = None
        self._last_mtf_gated_bars = None

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
            time_exits,
            max_position_losses,
            funding_rates_2d,
            position_modes,
            risk_fractions,
            np.float64(self.fee),
            np.float64(self.slippage),
            high_2d,
            low_2d,
            direction_signal_matrix,
            bar_ts_minutes_arr,
            np.float64(tf_minutes_val),
            _mm_tier_arrays()[0],
            _mm_tier_arrays()[1],
            trace_buf_batch,
            trail_buf_batch,
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
