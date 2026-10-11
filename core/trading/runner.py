"""Paper 模拟盘任务生命周期引擎（后台线程 TradingRunner）。

职能与架构：V2 决策链 signal -> judgment -> pending_decision ->
bar_open 执行；持仓记账走 VirtualAccount（非 PositionManager）。

服务对象：API 路由层（api/routes/trading.py 创建任务落 DB pending、
停止/查询经 TaskController 与本模块全局控制器表）与前端模拟盘视图
（WS 推送 task_started/position_update）。

职责边界：任务串行执行（同一时刻至多一个 active 任务）；bar 轮询
推进 + 增量落库（成交事件/权益快照/心跳）；崩溃恢复只做停机标记
（crash 是 bug 不是可恢复状态——设计裁决，见 _execute_task 主循环
前的 last_bar_time 检查）；不含实盘链（core/live_trading）与历史
回放（core/trading/replay.py）。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.logging import get_logger
from core.strategy.dna import StrategyDNA
from core.strategy.executor import dna_to_signal_set
from core.data.updater import update_market_data
from core.data.mtf_loader import load_mtf_data
from core.trading.account import VirtualAccount
from core.trading.position import Position
from core.trading.types import BarSignals, Decision, JudgmentConfig, PositionPlan
from core.trading.judgment import evaluate
from core.prediction import PriceRangePredictor
from core.prediction.genes import PredictionDNA

logger = get_logger("TRADING_RUNNER")

_ws_push_fn = None
_active_controllers: Dict[str, "TaskController"] = {}


def set_trading_ws_push_fn(fn) -> None:
    global _ws_push_fn
    _ws_push_fn = fn


def get_trading_controllers() -> Dict[str, "TaskController"]:
    return _active_controllers


from core.concurrency.task_control import TaskController, TaskStopRequested  # B4.2 归一


from core.persistence.timestamps import utc_now_iso as _now_iso  # B4.1 归一


def _push_ws(task_id: str, payload: dict) -> None:
    if _ws_push_fn is not None:
        try:
            _ws_push_fn(task_id, payload)
        except Exception:
            logger.warning("WS push failed for trading task %s", task_id, exc_info=True)


def recover_stale_trading_tasks(db_path: Path) -> None:
    """崩溃恢复收尾：把遗留 running 的模拟盘任务标记为 stopped。

    进程崩溃后启动期调用——遗留 running 状态已无执行线程，不标记
    会永久卡死在 running（前端不可重启）。
    """
    from core.persistence.repository import paper_trading as repo
    recovered = repo.recover_stale_trading_tasks(db_path)
    if recovered > 0:
        logger.info("Recovered %d stale trading tasks", recovered)


from core.data.timeframes import TF_SECONDS as _BAR_SECONDS  # B4.1 归一


class TradingRunner(threading.Thread):
    """模拟盘任务守护线程：轮询拾取 pending 任务并串行执行。

    单任务模型：_active_task_id 非空即忙（不并发多任务，任务间无
    资源竞争）；tick 异常兜底记日志不退出（线程存活优先——单 tick
    失败不应停摆后续任务调度）；poll_interval 控空转节奏。
    """

    def __init__(
        self,
        db_path: Path,
        data_dir: Path,
        poll_interval: float = 2.0,
    ) -> None:
        super().__init__(daemon=True, name="trading-runner")
        self.db_path = db_path
        self.data_dir = data_dir
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._active_task_id: Optional[str] = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("TradingRunner started (db=%s)", self.db_path)
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.error("tick error", exc_info=True)
            self._stop_event.wait(self.poll_interval)
        logger.info("TradingRunner stopped")

    def get_status(self) -> dict:
        return {
            "is_alive": self.is_alive(),
            "active_task_id": self._active_task_id,
        }

    def _tick(self) -> None:
        if self._active_task_id:
            row = self._get_task(self._active_task_id)
            if row is None or row["status"] not in ("running", "pending"):
                self._active_task_id = None
            return

        task = self._find_pending_task()
        if task:
            self._run_task(task)

    def _find_pending_task(self) -> Optional[Dict[str, Any]]:
        from core.persistence.repository import paper_trading as repo
        return repo.find_pending_trading_task(self.db_path)

    def _get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        from core.persistence.repository import paper_trading as repo
        return repo.get_paper_trading_task(self.db_path, task_id)

    def _run_task(self, task_row: Dict[str, Any]) -> None:
        task_id = task_row["task_id"]
        self._active_task_id = task_id
        controller = TaskController()
        _active_controllers[task_id] = controller

        account: Optional[VirtualAccount] = None
        final_pending: Optional[Decision] = None
        try:
            result = self._execute_task(task_row, task_id, controller)
            if result is not None:
                if isinstance(result, tuple):
                    account, final_pending = result
                else:
                    account = result
        except TaskStopRequested:
            logger.info("Trading task %s stopped via controller", task_id)
            self._update_status(task_id, "stopped", "user_stop")
        except Exception:
            logger.error("Trading task %s failed", task_id, exc_info=True)
            self._update_status(task_id, "stopped", "error")
        finally:
            # 终态落库兜底（未写盘的权益快照不随任务结束丢失）
            if account is not None:
                try:
                    self._save_account_state(account, task_id, pending_decision=final_pending)
                except Exception:
                    logger.warning("Failed to save final state for %s", task_id, exc_info=True)
            self._active_task_id = None
            _active_controllers.pop(task_id, None)

    def _update_status(self, task_id: str, status: str, reason: str = None) -> None:
        from core.persistence.repository.paper_trading import update_paper_trading_task
        kwargs = {"status": status}
        if reason:
            kwargs["stop_reason"] = reason
        if status == "stopped":
            kwargs["stopped_at"] = _now_iso()
        update_paper_trading_task(self.db_path, task_id, **kwargs)

    def _execute_task(
        self,
        task_row: Dict[str, Any],
        task_id: str,
        controller: TaskController,
    ) -> VirtualAccount:
        from core.persistence.repository.paper_trading import (
            update_paper_trading_task, save_paper_trade,
        )

        # 交易链兼容性插桩（0.4.2 R2/D3：paper 任务装载点——trail/分批
        # 基因在此显式拒绝，任务不静默按 pct 执行；插桩先于 running 标记
        # ——载入即拒的任务不进入 running 状态）。atr 在 paper 链放行
        # （DecisionPipeline 按 sl_mode 分派——既有能力）
        dna = StrategyDNA.from_json(task_row["dna_json"])
        from core.strategy.validator import validate_trading_compatibility
        _reasons = validate_trading_compatibility(dna, "paper")
        if _reasons:
            raise ValueError(
                "模拟盘链不支持该策略（DNA 载入即拒）: " + "; ".join(_reasons))

        # 标记 running（装载校验已过——载入即拒的任务不进 running）
        update_paper_trading_task(
            self.db_path, task_id, status="running", started_at=_now_iso(),
        )
        _push_ws(task_id, {"type": "task_started", "task_id": task_id})

        symbol = task_row["symbol"]
        timeframe = task_row["timeframe"]
        init_cash = task_row["initial_cash"]
        fee = task_row["fee"]
        created_at = task_row.get("created_at")

        # 构造 VirtualAccount（用户任务配置覆写 DNA 缺省——初始资金/
        # 杠杆/方向以任务行为准）
        account = VirtualAccount(
            dna, init_cash=init_cash, fee=fee, slippage=0.0005,
            leverage=task_row.get("leverage"),
            direction=task_row.get("direction"),
            timeframe=timeframe,
        )

        # 恢复既有状态（任务重启：累计统计与余额/持仓快照续接）
        self._restore_account_state(account, task_row)
        self._restore_balance_and_position(account, task_row)

        # 装载行情数据
        controller.check_stop()
        df = self._load_data(symbol, timeframe)
        if df is None or df.empty:
            self._update_status(task_id, "stopped", "no_data")
            return

        from core.features.indicators import compute_all_indicators
        from core.data.mtf_loader import _try_merge_derivatives
        import re as _re
        _safe_sym = _re.sub(r"[^A-Za-z0-9]", "", symbol)
        df = _try_merge_derivatives(self.data_dir, _safe_sym, timeframe, df)
        df = compute_all_indicators(df)

        # 截除指标预热 NaN 头部行
        df = self._trim_nan_rows(df)

        # 过滤未闭合 forming bar
        df = self._filter_forming_bar(df, timeframe)

        # MTF：DNA 多周期时装载全部所需周期数据
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None
        mtf_timeframes: List[str] = []
        if dna.is_mtf:
            mtf_timeframes = dna.timeframes
            needed_tfs = set(mtf_timeframes)
            dfs_by_timeframe = load_mtf_data(
                self.data_dir, symbol, timeframe, df, needed_tfs,
            )
            # 完整性校验：DNA 所需周期数据必须齐备（缺任一即停机）
            if dfs_by_timeframe is None:
                self._update_status(
                    task_id, "stopped",
                    f"mtf_data_missing: {','.join(sorted(needed_tfs))}",
                )
                return
            loaded_tfs = set(dfs_by_timeframe.keys())
            missing = needed_tfs - loaded_tfs
            if missing:
                self._update_status(
                    task_id, "stopped",
                    f"mtf_data_missing: {','.join(sorted(missing))}",
                )
                return
            # 各周期独立截预热行+过滤 forming bar（口径与执行周期一致）
            for tf in list(dfs_by_timeframe.keys()):
                dfs_by_timeframe[tf] = self._trim_nan_rows(dfs_by_timeframe[tf])
                dfs_by_timeframe[tf] = self._filter_forming_bar(
                    dfs_by_timeframe[tf], tf,
                )
            logger.info(
                "MTF data loaded for task %s: %s",
                task_id, sorted(dfs_by_timeframe.keys()),
            )

        # 由任务设置组装判定配置（置信度仓位/限价单/定价参数）
        config = JudgmentConfig(
            confidence_sizing_enabled=bool(task_row.get("confidence_sizing_enabled", 0)),
            use_limit_orders=bool(task_row.get("use_limit_orders", 0)),
            pricing_alpha_base=float(task_row.get("pricing_alpha_base", 0.3)),
            pricing_alpha_range=float(task_row.get("pricing_alpha_range", 0.5)),
            pricing_min_fill_prob=float(task_row.get("pricing_min_fill_prob", 0.3)),
            order_max_wait_bars=int(task_row.get("order_max_wait_bars", 5)),
        )

        # 初始化预测系统（失败降级 None——决策管线走无预测路径）
        predictor = self._init_predictor(df, task_row)

        # 初始化决策管线（判定配置+DNA 风控基因注入）
        from core.trading.pipeline import DecisionPipeline
        pipeline = DecisionPipeline(config=config, dna_risk_genes=dna.risk_genes)

        pending_decision: Optional[Decision] = None
        last_bar_time = task_row.get("last_bar_time")
        if last_bar_time:
            # 任务携带前次状态（pending 却有 last_bar_time）= 崩溃残留。
            # 设计裁决：crash 是 bug 而非可恢复状态——标记停机，
            # 用户以新任务重启（不静默续跑来路不明的中间态）
            logger.error(
                "Task %s has last_bar_time=%s but is pending -- "
                "possible crash recovery. Marking as error.",
                task_id, last_bar_time,
            )
            self._update_status(task_id, "stopped", "crash_recovery")
            return

        # 回放后状态落库（初始权益快照/持仓基线上屏）
        self._save_account_state(account, task_id, df, pending_decision)
        self._push_position_update(account, task_id)

        # 主循环：轮询增量数据逐 bar 推进
        bar_interval = _BAR_SECONDS.get(timeframe, 86400)
        poll_wait = min(bar_interval, 60)

        # MTF：按周期记录上次刷新时间（节流刷新的基准）
        now_utc = pd.Timestamp.now(tz="UTC")
        tf_last_refresh: Dict[str, pd.Timestamp] = {}
        if dfs_by_timeframe:
            for tf in mtf_timeframes:
                tf_last_refresh[tf] = now_utc

        while not controller.stop_requested:
            controller.check_stop()
            try:
                # MTF：各周期仅在其 bar 间隔到期后才刷新（高频周期不
                # 拖累低频周期的既有数据）
                if dfs_by_timeframe is not None:
                    now_utc = pd.Timestamp.now(tz="UTC")
                    for tf in mtf_timeframes:
                        tf_interval = _BAR_SECONDS.get(tf, 86400)
                        elapsed = (now_utc - tf_last_refresh[tf]).total_seconds()
                        if elapsed >= tf_interval:
                            new_tf_df = self._fetch_and_update(symbol, tf)
                            if new_tf_df is not None:
                                prev_len = len(dfs_by_timeframe[tf])
                                new_tf_df = compute_all_indicators(new_tf_df)
                                new_tf_df = self._filter_forming_bar(new_tf_df, tf)
                                if len(new_tf_df) >= prev_len:
                                    dfs_by_timeframe[tf] = new_tf_df
                                    tf_last_refresh[tf] = now_utc
                                    logger.debug(
                                        "Refreshed MTF data for %s (task %s)",
                                        tf, task_id,
                                    )

                new_df = self._fetch_and_update(symbol, timeframe)
                if new_df is not None and len(new_df) > len(df):
                    new_df = compute_all_indicators(new_df)
                    new_df = self._filter_forming_bar(new_df, timeframe)

                    # 执行周期新数据同步进 dfs_by_timeframe（MTF 信号
                    # 组装消费同一份最新帧）
                    if dfs_by_timeframe is not None:
                        dfs_by_timeframe[timeframe] = new_df

                    sig_set = dna_to_signal_set(dna, new_df, dfs_by_timeframe)

                    for i in range(len(df), len(new_df)):
                        controller.check_stop()
                        row = new_df.iloc[i]
                        ts = new_df.index[i]

                        # 新任务跳过创建时刻之前的 bar（历史 bar 不
                        # 补跑——任务从创建时点开始感知市场）
                        if last_bar_time is None and created_at and ts.isoformat() < created_at:
                            continue

                        # 全部决策逻辑委托管线（判定/挂单/成交/事件
                        # 产出，本类只做编排与落库）
                        pipe_result = pipeline.process_bar(
                            bar_high=float(row["high"]),
                            bar_low=float(row["low"]),
                            bar_open=float(row["open"]),
                            bar_close=float(row["close"]),
                            bar_time=ts.isoformat(),
                            bar_idx=i,
                            account=account,
                            predictor=predictor,
                            df=new_df,
                            sig_set=sig_set,
                            position_size=dna.risk_genes.position_size,
                            stop_loss_pct=dna.risk_genes.stop_loss or 0.05,
                        )

                        self._log_events(
                            save_paper_trade, task_id, ts.isoformat(), pipe_result.events,
                        )
                        pending_decision = pipe_result.pending_decision
                        # B1.3: 事件落库成功即推进游标——中途异常被外层兜底后，
                        # 下一轮 poll 从断点继续，只重试未完成 bar，不重放已
                        # 落库 bar（消除重复 paper_trade 事件；range 已固化，
                        # 循环内改 df 不影响本轮迭代）
                        df = new_df.iloc[:i + 1]

                    df = new_df
            except Exception:
                logger.warning("Error in data processing for %s", task_id, exc_info=True)

            self._save_account_state(account, task_id, df, pending_decision)
            self._push_position_update(account, task_id)
            update_paper_trading_task(
                self.db_path, task_id, heartbeat_at=_now_iso(),
            )
            controller.wait(poll_wait)

        return account, pending_decision

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _init_predictor(self, df, task_row: dict):
        """初始化价格区间预测器（PriceRangePredictor）。

        职责契约：优先用任务行存储的 prediction_dna_json 构造，缺省
        回落内置默认 DNA；warmup 以 min(100, len(df)//2) 根 bar 预热。
        构造或 warmup 任一失败降级返回 None——决策管线对 None 走无
        预测路径，任务不因预测系统失败而中止。
        """
        import json as _json
        pred_dna_json = task_row.get("prediction_dna_json")
        try:
            if pred_dna_json:
                pred_dna = PredictionDNA.from_json(pred_dna_json)
            else:
                pred_dna = PredictionDNA(
                    omega=1e-5, alpha=0.10, beta=0.80,
                    k_base=0.8, k_min=0.3,
                    factor_weights={},
                    short_window=15, mid_window=60, long_window=200,
                )
            predictor = PriceRangePredictor(pred_dna)
            n_warmup = min(100, len(df) // 2)
            if n_warmup > 0:
                predictor.warmup(df, n_bars=n_warmup)
            return predictor
        except Exception:
            logger.warning("Failed to init predictor, running without prediction",
                         exc_info=True)
            return None

    def _filter_forming_bar(self, df, timeframe: str):
        """剔除未闭合（forming）bar。

        判据：末根 bar 时间 + 周期间隔仍晚于当前时刻 = 该 bar 尚在
        形成（high/low 未定），截掉末行——决策只消费已闭合 bar，
        防未完成 bar 触发信号。
        """
        if df.empty:
            return df
        interval_seconds = _BAR_SECONDS.get(timeframe, 14400)
        now = pd.Timestamp.now(tz="UTC")
        last_bar_time = df.index[-1]
        if last_bar_time.tz is None:
            last_bar_time = last_bar_time.tz_localize("UTC")
        if last_bar_time + pd.Timedelta(seconds=interval_seconds) > now:
            return df.iloc[:-1]
        return df

    def _trim_nan_rows(self, df):
        """丢弃指标列全 NaN 的头部预热行（warmup 期间指标周期未满，
        无有效信号可产——首个任一指标列非 NaN 的行之前全部截除）。"""
        if df.empty:
            return df
        ohlcv_cols = {"open", "high", "low", "close", "volume"}
        ind_cols = [c for c in df.columns if c not in ohlcv_cols]
        if not ind_cols:
            return df
        valid_mask = df[ind_cols].notna().any(axis=1)
        first_valid = valid_mask.idxmax()
        if not valid_mask.loc[first_valid]:
            return df.iloc[0:0]  # 全 NaN（区间短于所有指标周期）
        first_idx = df.index.get_loc(first_valid)
        if first_idx == 0:
            return df
        logger.info("Trimmed %d warmup rows with NaN indicators", first_idx)
        return df.iloc[first_idx:]

    def _load_data(self, symbol: str, timeframe: str):
        """从数据目录装载执行周期 parquet（文件不存在返回 None——
        调用方按 no_data 停机）。"""
        from core.data.storage import load_parquet
        path = self.data_dir / f"{symbol}_{timeframe}.parquet"
        if not path.exists():
            return None
        return load_parquet(path)

    def _fetch_and_update(self, symbol: str, timeframe: str):
        """拉取增量行情并更新本地 parquet（更新失败返回 None——主循环
        按无新数据处理，轮询继续）。"""
        try:
            return update_market_data(
                symbol=symbol, interval=timeframe, data_dir=self.data_dir,
            )
        except Exception:
            return None

    def _restore_account_state(self, account: VirtualAccount, task: dict) -> None:
        """从任务行恢复 VirtualAccount 累计统计基数（交易数/累计盈亏/
        胜负计数）——total_trades 等聚合口径跨执行段连续。"""
        account._prior_trades = task.get("total_trades", 0)
        account._prior_pnl = task.get("total_pnl", 0.0)
        account._prior_wins = task.get("win_count", 0)
        account._prior_losses = task.get("loss_count", 0)

    def _restore_balance_and_position(self, account: VirtualAccount, task: dict) -> None:
        """从任务行恢复余额与持仓（balance 直覆写；position_side 非空
        时按快照重建 Position——含保证金/累计资金费/开仓成本）。"""
        if task.get("balance") is not None:
            account.balance = task["balance"]

        if task.get("position_side") is None:
            return
        account._bars_held_count = task.get("bars_held", 0)
        account.position = Position(
            side=task["position_side"],
            entry_price=task["position_entry"],
            quantity=task["position_quantity"],
            margin=task["position_margin"],
            cumulative_funding=task.get("position_funding", 0.0),
            open_cost=task.get("position_open_cost", 0.0),
        )

    def _save_account_state(self, account: VirtualAccount, task_id: str,
                            df=None, pending_decision: Optional[Decision] = None) -> None:
        """VirtualAccount 状态回写任务行。

        职责契约：余额/持仓快照/待执行决策（pending_decision）/
        last_bar_time/累计统计一次写齐——任务中断重启的恢复依据；
        权益快照增量落库（内存仅保留末条作下次写盘基线）。
        """
        import json
        from core.persistence.repository.paper_trading import update_paper_trading_task
        kwargs = {"balance": account.balance}

        # pending_decision 存在则序列化（bar 开盘待执行动作）
        if pending_decision is not None:
            kwargs["pending_decision_json"] = json.dumps({
                "action": pending_decision.action,
                "direction": pending_decision.direction,
                "target_position_pct": pending_decision.target_position_pct,
                "entry_size_pct": pending_decision.entry_size_pct,
                "reason": pending_decision.reason,
            })
        else:
            kwargs["pending_decision_json"] = None

        last_close = 0.0
        if df is not None and len(df) > 0:
            last_close = float(df.iloc[-1]["close"])

        if account.position is not None:
            pos = account.position
            kwargs.update({
                "position_side": pos.side,
                "position_entry": pos.entry_price,
                "position_quantity": pos.quantity,
                "position_margin": pos.margin,
                "position_funding": pos.cumulative_funding,
                "position_open_cost": pos.open_cost,
                "unrealized_pnl": account._unrealized_pnl(last_close),
            })
        else:
            kwargs.update({
                "position_side": None,
                "position_entry": None,
                "position_quantity": None,
                "position_margin": None,
                "position_funding": 0.0,
                "unrealized_pnl": 0.0,
            })

        if account.equity_snapshots:
            snap = account.equity_snapshots[-1]
            kwargs["last_bar_time"] = snap.timestamp
            kwargs["last_bar_close"] = last_close

        kwargs.update({
            "total_trades": account.total_trades,
            "total_pnl": account.total_pnl,
            "win_count": account.win_count,
            "loss_count": account.loss_count,
            "bars_held": account._bars_held() if account.position else 0,
        })

        update_paper_trading_task(self.db_path, task_id, **kwargs)

        if len(account.equity_snapshots) > 1:
            from core.persistence.repository.paper_trading import save_equity_snapshots
            snap_dicts = [
                {
                    "timestamp": s.timestamp,
                    "equity": s.equity,
                    "balance": s.balance,
                    "unrealized_pnl": s.unrealized_pnl,
                    "position_side": s.position_side,
                }
                for s in account.equity_snapshots[:-1]
            ]
            save_equity_snapshots(self.db_path, task_id, snap_dicts)
            account.equity_snapshots = account.equity_snapshots[-1:]

    def _log_events(self, save_trade_fn, task_id: str, bar_time: str,
                    events: List[dict]) -> None:
        for ev in events:
            action = ev["type"]
            fee_paid = ev.get("fee_paid", 0.0)
            if action == "position_opened":
                save_trade_fn(
                    self.db_path,
                    task_id=task_id, bar_time=bar_time,
                    side=ev["side"], action="open",
                    price=ev["entry_price"], quantity=ev.get("quantity", 0),
                    fee_paid=fee_paid,
                )
            elif action == "position_closed":
                save_trade_fn(
                    self.db_path,
                    task_id=task_id, bar_time=bar_time,
                    side=ev["side"], action="close",
                    price=ev["exit_price"], quantity=ev.get("quantity", 0),
                    pnl=ev["pnl"], reason=ev["exit_reason"],
                    fee_paid=fee_paid,
                )
            elif action == "position_added":
                save_trade_fn(
                    self.db_path,
                    task_id=task_id, bar_time=bar_time,
                    side=ev.get("side", ""), action="add",
                    price=ev.get("price", 0),
                    quantity=ev.get("quantity_added", 0),
                    fee_paid=fee_paid,
                )
            elif action == "position_reduced":
                save_trade_fn(
                    self.db_path,
                    task_id=task_id, bar_time=bar_time,
                    side=ev.get("side", ""), action="reduce",
                    price=ev.get("price", 0),
                    quantity=ev.get("quantity_reduced", 0),
                    pnl=ev.get("pnl"),
                    fee_paid=fee_paid,
                )

    def _push_position_update(self, account: VirtualAccount, task_id: str) -> None:
        pos_data = None
        if account.position is not None:
            pos = account.position
            pos_data = {
                "side": pos.side,
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "margin": pos.margin,
                "cumulative_funding": pos.cumulative_funding,
            }

        equity = account._init_cash
        unrealized = 0.0
        if account.equity_snapshots:
            snap = account.equity_snapshots[-1]
            equity = snap.equity
            unrealized = snap.unrealized_pnl

        _push_ws(task_id, {
            "type": "position_update",
            "task_id": task_id,
            "position": pos_data,
            "balance": account.balance,
            "equity": equity,
            "unrealized_pnl": unrealized,
            "total_trades": account.total_trades,
            "total_pnl": account.total_pnl,
        })
