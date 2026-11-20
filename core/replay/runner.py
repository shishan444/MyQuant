"""回放驱动编排（ReplayRunner）——组装执行核心+分段驱动+结果汇总。

change add-replay-engine 任务 2.2：以 PaperTaskExecutor 为执行核心
（不调 run()/subscribe_feeds——直驱 process_frames 回放注入通道），
按 ReplayFeeder 分段纪律驱动（段间刷新辅帧引用），落库行为=执行行为
的一部分（A2：_persist_bar 单 bar 原子事务复用，目标库由调用方指定，
P1=验证用临时库）。装载链 1-11 全部在 pt_task 行创建之前完成（任一
步失败抛 ReplayError、行不建——「载入即拒」纪律）。
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from core.analysis.errors import ResearchError
from core.data.validation import validate_symbol
from core.persistence.repository import paper as paper_repo
from core.persistence.repository.strategy import get_strategy
from core.replay.feeder import ReplayFeeder
from core.strategy.dna import StrategyDNA
from core.strategy.validator import (
    has_plugin_sources,
    validate_dna,
    validate_trading_compatibility,
)

logger = logging.getLogger("replay")


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
    halt_reason: Optional[str]
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
        throttle_seconds: Optional[float] = None,
        _dna_override: Optional[StrategyDNA] = None,
    ) -> ReplayResult:
        """跑一次完整回放（装载失败即抛 ReplayError，行不建）。

        throttle_seconds（add-replay-paced-mode）：可选限速演示参数
        （None=不限速零开销缺省）——透传 process_frames 逐 bar sleep
        （bar 级节流；段级锚定对单周期策略只调一次不可满足——2026-11
        -19 战役教训四）。(0,60] 值域校验（0/负/>60/nan/inf 显式拒绝
        ——C5 审查 F1）。MCP 参数面不暴露（演示语义归人类脚本直调）。
        """
        # ── 装载链 0：任务覆写面数值参数有限性+值域校验（载入即拒
        # ——nan/inf/越界在 pt_task 行创建前显式拒绝，行不建）。REST
        # 面已由 schema 值域+require_* 同域前置，此块收口直调通道
        # （run/run_from_dna/pool worker 四路汇聚点，一处全覆盖）。
        # 值域与既有面逐字对齐不发明：fee/slippage/initial_cash 同
        # ReplayBacktestRequest schema，stop_loss_pct/halt_drawdown_
        # pct 同 require_halt_drawdown_pct 开区间（0 无意义、1 恒不
        # 触发同为非法配置面），throttle 上界 60 封 inf。isfinite 显式
        # 前置沿 fapi 覆写先例：nan 落库即 NULL——止损/熔断静默变
        # 「未配置」，inf 恒不触发——病态值封死在保护参数面）──
        if not math.isfinite(fee) or not 0.0 <= fee <= 1.0:
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"fee 非法（[0,1] 费率比率）: {fee!r}")
        if not math.isfinite(slippage) or not 0.0 <= slippage <= 1.0:
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"slippage 非法（[0,1] 滑点比率）: {slippage!r}")
        if (not math.isfinite(initial_cash)
                or not 1.0 <= initial_cash <= 1e12):
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"initial_cash 非法（[1,1e12] 初始资金）: {initial_cash!r}")
        if (stop_loss_pct is not None
                and (not math.isfinite(stop_loss_pct)
                     or not 0.0 < stop_loss_pct < 1.0)):
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"stop_loss_pct 非法（(0,1) 开区间亏损比率）: "
                f"{stop_loss_pct!r}")
        if (halt_drawdown_pct is not None
                and (not math.isfinite(halt_drawdown_pct)
                     or not 0.0 < halt_drawdown_pct < 1.0)):
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"halt_drawdown_pct 非法（(0,1) 开区间回撤比率）: "
                f"{halt_drawdown_pct!r}")
        if (throttle_seconds is not None
                and (not math.isfinite(throttle_seconds)
                     or not 0.0 < throttle_seconds <= 60.0)):
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"throttle_seconds 非法（(0,60] 秒）: "
                f"{throttle_seconds!r}")
        db_path = Path(db_path)
        # ── 装载链 1-5（策略域校验；6-10 归 feeder.load()）──
        # _dna_override：内存 DNA 直传入口（run_from_dna 进——exit_sweep
        # 等研究模板构造的内存策略不落库直跑；校验段照走）
        if _dna_override is not None:
            dna = _dna_override
            strategy_row = {
                "strategy_id": strategy_id,
                "dna_json": _dna_override.to_json()}
        else:
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
            # validate_dna 仅对库策略跑（入库完整性校验——值域+交叉）；
            # 研究模板内存 dna（run_from_dna）跳过：模板校验期自成体系
            # （模式×双量纲值域在 exit_sweep 等模板内单点校验），库校验
            # 的 tp>sl 交叉不分量纲与实验网格双量纲合法形制冲突
            # （add-replay-backtest-mcp 任务 2.2 适配）。
            result = validate_dna(dna)
            if result is not None and not result.is_valid:
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
        # symbol 命名白名单前置（校验式拒绝非规整）：库策略与内存
        # DNA override 两路径唯一汇聚点——未校验符号原直达 feeder
        # funding 索引与 executor 衍生品注入两处 parquet 拼接点；时
        # 序上先于 feeder.load() 归档读取与 prepare 校验（先读后拒
        # 的时序倒置一并关闭）
        try:
            validate_symbol(symbol)
        except ValueError as exc:
            raise ReplayError(
                "RESEARCH_PARAM_INVALID",
                f"symbol 非法: {exc}") from exc
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
        research_status = (
            "unverified" if _dna_override is not None else
            paper_repo.resolve_research_status(db_path, strategy_row))
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
            halt_drawdown_pct=halt_drawdown_pct, task_id=tid,
            origin="replay")
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
        # ── 建行后全程异常兜底（fix-replay-task-lifecycle 2.2 扩范围
        # ——Lead 终裁「prepare 缝」闭合）：try 从 prepare 段起包，
        # 任何异常先落 error 终态再上抛，行不残 pending/running。
        # _finalize 经实现面核对：update_task 无 CAS 前态门，pending
        # 直迁 error 合法；_finalized 标志保进程内幂等。──
        main = feeder.main
        try:
            if _dna_override is None and not executor.prepare():
                # prepare=纸盘上盘纵深防线（库 dna 被改后重启场景），回放
                # 库策略路径沿用（防库行被外部篡改）；研究模板 override 场
                # 景跳过——其职能（数据可行性/compat/指标可算/atr）已由
                # 回放装载链 3/4/8/9 覆盖，纵深校验的库值域交叉与实验
                # dna 双量纲合法形制冲突（任务 2.2 适配）
                raise ReplayError(
                    "REPLAY_PREPARE_FAILED", "执行器装载校验意外未过")
            # 置 running CAS 自检（审查修复 CARD-01/S-2——与纸盘 run()
            # 启动 CAS 零命中放弃同构）：零命中=行已被外部写者迁出可
            # 启动态（人工 SQL/未来他写者）——数据写入无状态门（状态
            # 机守门只管 pt_task.status 字段本身），闷头驱动=状态-数据
            # 背离，记日志中止驱动并上抛结构化 422
            hit = paper_repo.update_task(
                db_path, tid, status="running",
                expected_status={"pending", "running"})
            if hit == 0:
                logger.warning(
                    "回放任务 %s 置 running 零命中（行被外部改写）"
                    "——中止驱动", tid)
                raise ReplayError(
                    "REPLAY_TASK_PREEMPTED",
                    "回放任务行启动前已被外部改写，放弃驱动",
                    detail=f"task_id={tid}")
            # ── 分段驱动（前缀切片形制：每段 process_frames 收到「含全
            # 部历史的前缀帧」+from_idx=段起点——信号窗口完整、循环自然
            # 止于段边界〔range(from_idx, len(前缀))〕；段边界刷新辅帧
            # 引用即重建可见性，与全帧单段特例同构）──
            for seg_start, seg_end, aux_frames in feeder.segments():
                executor._aux_frames.update(aux_frames)
                prefix = main.iloc[:seg_end + 1]
                executor.process_frames(
                    prefix, seg_start, throttle_seconds=throttle_seconds)
                if executor._concluded_status is not None:
                    break
        except Exception as exc:
            # 建行后异常兜底（fix-replay-task-lifecycle 2.2——与纸盘
            # run() 异常面同构）：先落 error 终态再上抛，行不残
            # pending/running。_finalize 幂等（_finalized 标志）——
            # 先行终态（本段窗口到点/停止条件后清理路径异常）不重复
            # 迁移不覆盖既有终态与 error_detail。上抛分派：ReplayError
            # （prepare 段客户端语义码）原样上抛走 422 通道零漂移；
            # 其余异常转 ResearchError 语义 5xx（全局 handler
            # research_error_to_http 结构化出站，禁裸 500 栈穿透）。
            logger.exception("回放任务 %s 建行后异常", tid)
            executor._finalize(
                "error", error_detail=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, ReplayError):
                raise
            raise ResearchError(
                "REPLAY_EXECUTION_FAILED",
                f"回放执行段异常: {type(exc).__name__}: {exc}",
                status=500, detail={"task_id": tid}) from exc
        if executor._concluded_status is None:
            executor._finalize("completed")  # 区间自然耗尽
        return self._collect(db_path, tid, strategy_id, start, end,
                             symbol, timeframe)

    def run_from_dna(
        self, db_path: Path, dna, start: str, end: str, *,
        strategy_id: Optional[str] = None, task_id: Optional[str] = None,
        **run_kwargs,
    ) -> ReplayResult:
        """内存 DNA 直跑（研究模板入口——exit_sweep 逐组等构造的
        内存策略不落库直跑；校验段照走：series 档/插件源装载拒绝面
        与库策略一致）。strategy_id 缺省 memory-<uuid> 占位。"""
        import uuid as _uuid

        return self.run(
            db_path, strategy_id or f"memory-{_uuid.uuid4().hex[:8]}",
            start, end, task_id=task_id,
            _dna_override=dna, **run_kwargs)

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
            # 权益单源口径=Ledger.equity：balance+position_margin
            # +unrealized_pnl（开仓划出的保证金须加回——终态持仓时
            # 漏 margin 项系统性偏低一个保证金值）
            final_equity=float(row.get("balance") or 0.0)
            + float(row.get("position_margin") or 0.0)
            + float(row.get("unrealized_pnl") or 0.0),
            halted_at=row.get("halted_at"),
            halt_reason=row.get("halt_reason"),
            first_met_at=row.get("first_met_at"),
        )
