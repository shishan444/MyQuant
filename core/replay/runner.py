"""回放驱动编排（ReplayRunner）——组装执行核心+分段驱动+结果汇总。

change add-replay-engine 任务 2.2：以 PaperTaskExecutor 为执行核心
（不调 run()/subscribe_feeds——直驱 process_frames 回放注入通道），
按 ReplayFeeder 分段纪律驱动（段间刷新辅帧引用），落库行为=执行行为
的一部分（A2：_persist_bar 单 bar 原子事务复用，目标库由调用方指定，
P1=验证用临时库）。装载链 1-11 全部在 pt_task 行创建之前完成（任一
步失败抛 ReplayError、行不建——「载入即拒」纪律）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from core.analysis.errors import ResearchError
from core.analysis.warmup_loader import TF_MINUTES
from core.persistence.repository import paper as paper_repo
from core.persistence.repository.strategy import get_strategy
from core.replay.feeder import ReplayFeeder
from core.strategy.dna import StrategyDNA
from core.strategy.validator import (
    has_plugin_sources,
    validate_dna,
    validate_trading_compatibility,
)


class ReplayError(Exception):
    """回放装载失败（载入即拒——pt_task 行不建）。

    code 沿研究链码值域（RESEARCH_*/STRATEGY_NOT_FOUND/
    TRADING_INCOMPATIBLE——P2 接 MCP 时映射结构化错误通道）。
    """

    def __init__(self, code: str, message: str,
                 detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass(frozen=True)
class ReplayResult:
    """回放结果汇总（summary 快照组+引用组）。

    引用组（db_path+task_id）经 paper_repo 既有查询通道可取全部明细
    （pt_fill/pt_signal/pt_equity/pt_funding——P2 落库与 P3 展示的
    取数面）；stop_reason 承载终态原因（depleted/stop_condition/
    user_stop/None=区间耗尽——后续记录与呈现的输入）。
    """

    task_id: str
    db_path: Path
    strategy_id: str
    symbol: str
    timeframe: str
    start: str
    end: str
    status: str
    stop_reason: Optional[str]
    bars_processed: int
    last_bar_time: Optional[str]
    win_count: int
    loss_count: int
    total_trades: int
    total_pnl: float
    final_balance: float
    final_equity: float
    halted_at: Optional[str]
    first_met_at: Optional[str]


class ReplayRunner:
    """回放驱动编排：装载校验→组装执行器→分段驱动→终态→结果汇总。

    语义参数不可覆写（direction/timeframe/信号基因/止损模式以 DNA
    为准）；fee/slippage/leverage/initial_cash 是任务覆写面（与纸盘
    挂载同则）。P1 单进程同步调用；P2 并发组经进程池每路独立实例。
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def run(
        self,
        db_path: Path,
        strategy_id: str,
        start: str,
        end: str,
        *,
        fee: float = 0.001,
        slippage: float = 0.0005,
        initial_cash: float = 100_000.0,
        leverage: int = 1,
        window_bars: Optional[int] = None,
        stop_loss_pct: Optional[float] = None,
        halt_drawdown_pct: Optional[float] = None,
        task_id: Optional[str] = None,
    ) -> ReplayResult:
        """跑一次完整回放（装载失败即抛 ReplayError，行不建）。"""
        db_path = Path(db_path)
        # ── 装载链 1-5（策略域校验；6-10 归 feeder.load()）──
        strategy_row = get_strategy(db_path, strategy_id)
        if strategy_row is None:
            raise ReplayError(
                "STRATEGY_NOT_FOUND", f"策略不存在: {strategy_id}")
        try:
            dna = StrategyDNA.from_json(strategy_row["dna_json"])
        except Exception as exc:  # noqa: BLE001 —— 坏值统一拒绝
            raise ReplayError(
                "RESEARCH_STORED_DATA_INVALID",
                f"策略 DNA 快照解析失败: {exc}") from exc
        result = validate_dna(dna)
        if not result.is_valid:
            raise ReplayError(
                "INVALID_DNA", "DNA 值域校验未过",
                detail="; ".join(result.errors))
        # paper 链能力矩阵（回放走纸盘执行链——series 档/插件源等
        # 纸盘不支持形态装载即拒，与 prepare 同判据单源；显式拒绝
        # 不静默降级）
        if has_plugin_sources(dna):
            raise ReplayError(
                "TRADING_INCOMPATIBLE",
                "执行环境不支持插件信号源（回放走纸盘执行链）")
        compat = validate_trading_compatibility(dna, "paper")
        if compat:
            raise ReplayError(
                "TRADING_INCOMPATIBLE", "策略与回放执行链不兼容",
                detail="; ".join(compat))
        symbol = dna.execution_genes.symbol
        timeframe = dna.execution_genes.timeframe
        feeder = ReplayFeeder(
            self.data_dir, db_path, dna, symbol, timeframe, start, end)
        # 装载链 4：数据可行性（find_parquet 公共入口，不自拼路径）
        if not feeder.parquet_exists(timeframe):
            raise ReplayError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"执行周期数据不存在: {symbol} {timeframe}")
        try:
            feeder.load()  # 装载链 6-10（前缀不足等在此显式拒绝）
        except ResearchError as exc:
            raise ReplayError(
                getattr(exc, "code", "RESEARCH_DATA_INSUFFICIENT"),
                str(exc)) from exc
        # 装载链 11：MIN_NOTIONAL 只读模块级表现状（MUST NOT 发起
        # 网络拉取——表值属环境输入；app 进程=lifespan 已装载值，
        # 裸进程=内置副本缺省或 env 注入后本地装配）
        # ── 装载完成，建行驱动（A2：落库=执行行为一部分）──
        research_status = paper_repo.resolve_research_status(
            db_path, strategy_row)
        tid = task_id or uuid.uuid4().hex[:12]
        paper_repo.create_task(
            db_path,
            strategy_id=strategy_id,
            dna_snapshot_json=strategy_row["dna_json"],
            symbol=symbol, timeframe=timeframe,
            research_status_snapshot=research_status,
            initial_cash=initial_cash, fee=fee, slippage=slippage,
            leverage=leverage, window_bars=window_bars,
            stop_loss_pct=stop_loss_pct,
            halt_drawdown_pct=halt_drawdown_pct, task_id=tid)
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry

        # registry 形参必填但回放不订阅真实 feed：空注册表在场
        # （不 acquire 零网络；funding 查询走注入优先永不经
        # registry.get——供数契约五件全在 feeder/注入面）
        registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        task_row = paper_repo.get_task(db_path, tid)
        # start_after=请求起点：warmup 前缀 bar 不感知不计数（指标
        # 收敛用，不驱动交易判定——与回测「前铺段无判定」语义一致；
        # 不传则回退 created_at=现在，历史区间全被跳过）
        executor = PaperTaskExecutor(
            db_path, task_row, registry=registry,
            start_after=feeder.main_start.isoformat(),
            funding_lookup=feeder.funding_lookup)
        if not executor.prepare():
            # prepare 在回放装载面已前置校验（1-5+6-10），此处 False
            # 属意外（数据在装载后驱动前被外部改变等）——如实转错误
            raise ReplayError(
                "REPLAY_PREPARE_FAILED", "执行器装载校验意外未过")
        paper_repo.update_task(db_path, tid, status="running")
        # ── 分段驱动（前缀切片形制：每段 process_frames 收到「含全
        # 部历史的前缀帧」+from_idx=段起点——信号窗口完整、循环自然
        # 止于段边界〔range(from_idx, len(前缀))〕；段边界刷新辅帧
        # 引用即重建可见性，与全帧单段特例同构）──
        main = feeder.main
        for seg_start, aux_frames in feeder.segments():
            executor._aux_frames.update(aux_frames)
            prefix = main.iloc[: self._segment_end(
                feeder, seg_start, aux_frames) + 1]
            executor.process_frames(prefix, seg_start)
            if executor._concluded_status is not None:
                break
        if executor._concluded_status is None:
            executor._finalize("completed")  # 区间自然耗尽
        return self._collect(db_path, tid, strategy_id, start, end,
                             symbol, timeframe)

    def _segment_end(self, feeder: ReplayFeeder, seg_start: int,
                     aux_frames: Dict[str, Any]) -> int:
        """段末主 bar 下标（该段可见集下的最后一根——下一边界-1 或
        帧尾）。"""
        # 段末=下一可见集变化点-1；实现上以「本段辅帧切片可见集在
        # 后续段的更大前缀中首次变化」推导成本高——直接采用保守式：
        # 段末=主帧中第一个「收盘时刻 > 该段辅帧切片末根收盘时刻」
        # 的前一根，保证段内每根的可见集恒等于切片集（含等号契约
        # 下切片末根闭合时刻 ≤ 段内每根收盘时刻）。
        main = feeder.main
        tf_delta = pd.Timedelta(
            minutes=TF_MINUTES.get(feeder.timeframe, 240))
        if not aux_frames:
            return len(main) - 1  # 单周期=全区间单段
        seg_close_bound = None
        for tf, aux_df in aux_frames.items():
            if aux_df.empty:
                continue
            last_aux_close = aux_df.index[-1] + pd.Timedelta(
                minutes=TF_MINUTES.get(tf, 240))
            seg_close_bound = (
                last_aux_close if seg_close_bound is None
                else max(seg_close_bound, last_aux_close))
        if seg_close_bound is None:
            return len(main) - 1
        close_times = main.index + tf_delta
        later = (close_times > seg_close_bound).nonzero()[0]
        end = int(later[0]) - 1 if len(later) else len(main) - 1
        # 下限钳段首：辅切片末根收盘早于主帧首根收盘时（warmup 前缀
        # 首段）推导值为 -1——至少处理段首一根（warmup bar 由
        # start_after 边界跳过判定，空帧会使下游投影取 index[-1] 炸）
        return max(seg_start, end)

    def _collect(self, db_path, tid, strategy_id, start, end, symbol,
                 timeframe) -> ReplayResult:
        """终态收割（task 行统计组+引用组——A2 落库行为即结果源）。"""
        row = paper_repo.get_task(db_path, tid)
        return ReplayResult(
            task_id=tid, db_path=Path(db_path),
            strategy_id=strategy_id, symbol=symbol,
            timeframe=timeframe, start=start, end=end,
            status=row["status"],
            stop_reason=row.get("stop_reason"),
            bars_processed=int(row.get("bars_processed") or 0),
            last_bar_time=row.get("last_bar_time"),
            win_count=int(row.get("win_count") or 0),
            loss_count=int(row.get("loss_count") or 0),
            total_trades=int(row.get("total_trades") or 0),
            total_pnl=float(row.get("total_pnl") or 0.0),
            final_balance=float(row.get("balance") or 0.0),
            final_equity=float(row.get("balance") or 0.0)
            + float(row.get("unrealized_pnl") or 0.0),
            halted_at=row.get("halted_at"),
            first_met_at=row.get("first_met_at"),
        )
