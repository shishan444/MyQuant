"""单策略回测入口（POST /api/strategies/backtest）。

职能：按 strategy_id 或内联 DNA 装载数据执行单次回测并落库。
服务对象：web 策略页（单策略回测面板）。职责边界：传输与引擎装配，
回测执行在 core.backtest。
"""
from __future__ import annotations

import json
import logging
import uuid

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from core.persistence.repository.backtest_result import (
    get_backtest_result,
    list_backtest_results,
    save_backtest_result,
)
from core.persistence.repository.strategy import (
    count_strategies,
    delete_strategy,
    get_strategy,
    list_strategies,
    save_strategy,
    update_strategy,
)
from core.persistence.repository.verify_session import (
    get_verify_session,
    list_verify_sessions,
    save_verify_session,
    update_verify_session,
)
from ._common import _reject_invalid_or_plugin_dna
from api.deps import get_data_dir, get_db_path
from api.schemas import (
    BacktestRequest,
    BacktestResponse,
    BatchBacktestRequest,
    BatchBacktestResultItem,
    BatchBacktestSummaryItem,
    CompareRequest,
    CompareResponse,
    CompareResultItem,
    DNAModel,
    StrategyCreate,
    StrategyListResponse,
    StrategyMetrics,
    StrategyResponse,
    StrategyUpdate,
    VerifyRequest,
    VerifyResponse,
    VerifyResultItem,
    VerifySummaryItem,
    VerifyPeriodSummary,
    VerifyHistoryItem,
    VerifyHistoryResponse,
    VerifySessionResponse,
    VerifySessionListResponse,
)
from core.backtest import engine as _bt_engine_mod
from core.analysis.errors import ResearchError  # 轻量定义点直取（errors.py 唯一权威）
from core.scoring.scorer import score_strategy
from core.services import backtest_service
from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)


router = APIRouter()


@router.post("/backtest")
def backtest_strategy(
    payload: BacktestRequest,
    db_path: Path = Depends(get_db_path),
    data_dir: Path = Depends(get_data_dir),
) -> BacktestResponse:
    """单策略回测。两种模式：strategy_id 自库取 DNA；
    或 dna+symbol+timeframe 直传 DNA（Lab 页用）。
    """
    # 解析 DNA 与策略元数据
    if payload.strategy_id:
        row = get_strategy(db_path, payload.strategy_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        dna = StrategyDNA.from_json(row["dna_json"])
        # 读库二道校验（recognize-plugin-source-dna T3）：本机直改库文件
        # 可绕过写入时校验（create 通道防线只覆盖正常写入路径），读库
        # 路径复查是防线——威胁模型升级（2026-10 技术债务需求文件
        # A-1/B-1；旧表述「分属不同威胁模型故本分支不动」就此翻案）。
        # 插件源能力边界判据（T4，第二问·乙）：REST 回测通道未接入
        # 插件信号组装，插件源在场即拒（含混合形态）；错误码分立
        # （补丁①）。signal-sources-structure-gate 卡 G 起两段拒绝
        # 表达收敛为 _common helper（判据单源不动，消息逐字节不变）。
        _reject_invalid_or_plugin_dna(dna, library_dna=True, channel="backtest")
        symbol = row["symbol"]
        timeframe = row["timeframe"]
        strategy_id = payload.strategy_id
    elif payload.dna:
        dna = payload.dna.to_dna()
        symbol = payload.symbol or "UNKNOWN"
        timeframe = payload.timeframe or "1d"
        strategy_id = "inline"
        # 内联 DNA 值域校验（CARD-01，position-sizing-params 审查批；
        # 同 review-042 CARD-12 先例——crud create 通道已收口，本通道
        # 直传即跑：NaN/越域/bool 收窄经 Pydantic 全过直进引擎，防线
        # 静默失效三形态（NaN 恒 False 漏开仓/越域 sl 量纲双关/bool
        # 收窄 sl=0 恒不触发）。（原注「strategy_id 分支不动——库内
        # DNA 落库前已经 create 通道校验，分属不同威胁模型」已翻案：
        # 本机直改库可绕过写入时校验，读库复查是防线，见上方
        # strategy_id 分支二道校验，recognize-plugin-source-dna T3）
        # 插件源能力边界（T4 第二问·乙，实施裁量补齐第五入口）：
        # 内联直传同判在场即拒。卡 G 起两段拒绝表达收敛为 _common
        # helper（判据单源不动，消息逐字节不变）。
        _reject_invalid_or_plugin_dna(dna, library_dna=False, channel="backtest")
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either strategy_id or dna",
        )

    # 周期一致性守卫（fix-mtf-execution-consistency R3/D6 挂点②）：
    # 请求周期≠DNA 执行周期 → 结构化拒绝——与验证通道同款错误面
    # （单源 ensure_timeframe_consistent 双挂：桥入口+本端点装载前）。
    # strategy_id 分支 timeframe 取策略行值（自洽调用自然通过，兼防
    # 直改库的行/DNA 周期漂移）；内联分支对显式传参异轴生效。
    from core.analysis.backtest_bridge import ensure_timeframe_consistent
    from core.analysis.errors import ResearchError as _ResearchError
    from api.routes.research.common import research_error_to_http

    try:
        ensure_timeframe_consistent(
            dna, timeframe, strategy_id=strategy_id)
    except _ResearchError as exc:
        raise research_error_to_http(exc) from exc

    # 装载数据集（白名单校验后直拼路径）
    # 命名白名单拒绝式校验（dataset_id 直拼读取路径，对齐 csv_importer 生成侧契约）
    from core.data.validation import validate_import_interval, validate_symbol

    try:
        parts = payload.dataset_id.rsplit("_", 1)
        if len(parts) != 2:
            raise ValueError(f"{payload.dataset_id!r}（须形如 SYMBOL_interval）")
        validate_symbol(parts[0])
        validate_import_interval(parts[1])
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid dataset_id: {exc}",
        ) from exc
    parquet_path = data_dir / f"{payload.dataset_id}.parquet"
    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Dataset {payload.dataset_id} not found",
        )

    from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

    engine = _bt_engine_mod.BacktestEngine(
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
    )

    # mtf_loader 增强数据装载（含全量指标与日期切片）
    enhanced_df = load_and_prepare_df(
        data_dir, symbol, timeframe,
        data_start=payload.data_start,
        data_end=payload.data_end,
    )

    # 回退路径：装载原始数据并逐基因现算指标（legacy 形制——mtf_loader
    # 增强装载不可得时兜底）
    if enhanced_df is None:
        from core.data.storage import load_parquet

        df = load_parquet(parquet_path)
        from core.features.indicators import _compute_indicator
        for gene in dna.signal_genes:
            try:
                indicator_name = gene.indicator
                params = {k: v for k, v in gene.params.items()}
                indicator_df = _compute_indicator(df, indicator_name, params)
                for col in indicator_df.columns:
                    if col not in df.columns:
                        df[col] = indicator_df[col]
            except Exception:
                continue
        enhanced_df = df

    # MTF 装载：DNA 含 layers 或请求带 timeframe_pool 时
    dfs_by_timeframe = None
    needed_tfs = backtest_service.compute_needed_tfs([dna], timeframe)
    if needed_tfs:
        # is_mtf 即装载（不用 len>1 守卫：单层同 tf 策略曾在此必 500——
        # 守卫跳过装载而 executor 要求 is_mtf 必有 dfs_by_timeframe；
        # load_mtf_data 内建 exec tf 帧，单层同 tf 零额外 IO 退化）
        dfs_by_timeframe = load_mtf_data(
            data_dir, symbol, timeframe, enhanced_df,
            needed_tfs, payload.data_start, payload.data_end,
        )
    elif payload.timeframe_pool and len(payload.timeframe_pool) > 1:
        dfs_by_timeframe = load_mtf_data(
            data_dir, symbol, timeframe, enhanced_df,
            set(payload.timeframe_pool), payload.data_start, payload.data_end,
        )

    # 执行器 raise 化后的传播配套（CARD-08）：非法条件类型等经
    # engine.run 冒泡的 ResearchError 转结构化 422，不裸穿 500
    # （与验证端点 validate.py catch 形制对齐——装载/评分链零
    # ResearchError 抛出面，捕获面收口在引擎执行单点）
    try:
        result = engine.run(
            dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)
    except ResearchError as exc:
        from api.routes.research.common import research_error_to_http
        raise research_error_to_http(exc) from exc

    metrics = backtest_service.fallback_metrics(result)
    score_result = score_strategy(
        metrics, template_name=payload.score_template,
        liquidated=result.liquidated,
    )

    # 结果落库
    result_id = str(uuid.uuid4())
    if payload.strategy_id:
        save_backtest_result(
            db_path,
            result_id=result_id,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            data_start=str(enhanced_df.index.min()) if len(enhanced_df) > 0 else "",
            data_end=str(enhanced_df.index.max()) if len(enhanced_df) > 0 else "",
            init_cash=payload.init_cash,
            fee=payload.fee,
            slippage=payload.slippage,
            total_return=result.total_return,
            sharpe_ratio=result.sharpe_ratio,
            max_drawdown=result.max_drawdown,
            win_rate=result.win_rate,
            total_trades=result.total_trades,
            total_score=score_result["total_score"],
            template_name=payload.score_template,
            dimension_scores=json.dumps(score_result.get("dimension_scores", {})),
            run_source="lab",
        )

    # 构建净值曲线
    equity_data = None
    if result.equity_curve is not None and len(result.equity_curve) > 0:
        eq = result.equity_curve
        equity_data = [
            {"timestamp": str(idx), "value": float(val)}
            for idx, val in eq.items()
        ]

    # 由成交构建信号（方向感知：做空开仓=卖、做空平仓=买）
    signals_data = None
    if result.trades_df is not None and len(result.trades_df) > 0:
        signals_data = backtest_service.build_signals_from_trades_df(result.trades_df)

    # 基准对照五键从 metrics_dict 取（dataclass 无 profit_factor 字段，
    # 统一单源）；None=无基准/未计算（None 安全）
    md = result.metrics_dict or {}
    return BacktestResponse(
        result_id=result_id,
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        data_start=str(enhanced_df.index.min()) if len(enhanced_df) > 0 else None,
        data_end=str(enhanced_df.index.max()) if len(enhanced_df) > 0 else None,
        init_cash=payload.init_cash,
        fee=payload.fee,
        slippage=payload.slippage,
        total_return=result.total_return,
        sharpe_ratio=result.sharpe_ratio,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        total_trades=result.total_trades,
        total_score=score_result["total_score"],
        fitness=score_result.get("fitness", 0.0),
        qualified=score_result.get("qualified", False),
        satisfaction=score_result.get("satisfaction"),
        template_name=payload.score_template,
        dimension_scores=score_result.get("dimension_scores"),
        run_source="lab",
        equity_curve=equity_data,
        signals=signals_data,
        total_funding_cost=result.total_funding_cost,
        liquidated=result.liquidated,
        benchmark_return=md.get("benchmark_return"),
        excess_return=md.get("excess_return"),
        alpha=md.get("alpha"),
        market_annual_return=md.get("market_annual_return"),
        profit_factor=md.get("profit_factor"),
    )
