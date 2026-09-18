"""Paper Trading Runner: background thread that executes paper trading tasks.

V2 architecture: signal -> judgment -> pending_decision -> bar_open execution.
Uses VirtualAccount instead of PositionManager.
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
    """Mark running paper trading tasks as stopped (crash recovery)."""
    from core.persistence.repository import paper_trading as repo
    recovered = repo.recover_stale_trading_tasks(db_path)
    if recovered > 0:
        logger.info("Recovered %d stale trading tasks", recovered)


from core.data.timeframes import TF_SECONDS as _BAR_SECONDS  # B4.1 归一


class TradingRunner(threading.Thread):
    """Daemon thread that picks up pending paper trading tasks and runs them."""

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
            # Save final state (preserves remaining equity snapshots)
            if account is not None:
                try:
                    self._save_account_state(account, task_id, pending_decision=final_pending)
                except Exception:
                    logger.warning("Failed to save final state for %s", task_id, exc_info=True)
            self._active_task_id = None
            _active_controllers.pop(task_id, None)

    def _update_status(self, task_id: str, status: str, reason: str = None) -> None:
        from core.persistence.db_ext import update_paper_trading_task
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
        from core.persistence.db_ext import (
            update_paper_trading_task, save_paper_trade,
        )

        # Mark running
        update_paper_trading_task(
            self.db_path, task_id, status="running", started_at=_now_iso(),
        )
        _push_ws(task_id, {"type": "task_started", "task_id": task_id})

        # Parse DNA
        dna = StrategyDNA.from_json(task_row["dna_json"])
        symbol = task_row["symbol"]
        timeframe = task_row["timeframe"]
        init_cash = task_row["initial_cash"]
        fee = task_row["fee"]
        created_at = task_row.get("created_at")

        # Create VirtualAccount (user config overrides DNA defaults)
        account = VirtualAccount(
            dna, init_cash=init_cash, fee=fee, slippage=0.0005,
            leverage=task_row.get("leverage"),
            direction=task_row.get("direction"),
            timeframe=timeframe,
        )

        # Restore state if resuming
        self._restore_account_state(account, task_row)
        self._restore_balance_and_position(account, task_row)

        # Load data
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

        # Trim warmup rows where indicators produced NaN
        df = self._trim_nan_rows(df)

        # Filter forming bar
        df = self._filter_forming_bar(df, timeframe)

        # MTF: load all timeframe data if DNA is multi-timeframe
        dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None
        mtf_timeframes: List[str] = []
        if dna.is_mtf:
            mtf_timeframes = dna.timeframes
            needed_tfs = set(mtf_timeframes)
            dfs_by_timeframe = load_mtf_data(
                self.data_dir, symbol, timeframe, df, needed_tfs,
            )
            # Integrity check: all required timeframes must be present
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
            # Trim warmup rows + filter forming bars for each timeframe independently
            for tf in list(dfs_by_timeframe.keys()):
                dfs_by_timeframe[tf] = self._trim_nan_rows(dfs_by_timeframe[tf])
                dfs_by_timeframe[tf] = self._filter_forming_bar(
                    dfs_by_timeframe[tf], tf,
                )
            logger.info(
                "MTF data loaded for task %s: %s",
                task_id, sorted(dfs_by_timeframe.keys()),
            )

        # Build judgment config from task settings
        config = JudgmentConfig(
            confidence_sizing_enabled=bool(task_row.get("confidence_sizing_enabled", 0)),
            use_limit_orders=bool(task_row.get("use_limit_orders", 0)),
            pricing_alpha_base=float(task_row.get("pricing_alpha_base", 0.3)),
            pricing_alpha_range=float(task_row.get("pricing_alpha_range", 0.5)),
            pricing_min_fill_prob=float(task_row.get("pricing_min_fill_prob", 0.3)),
            order_max_wait_bars=int(task_row.get("order_max_wait_bars", 5)),
        )

        # Initialize prediction system
        predictor = self._init_predictor(df, task_row)

        # Initialize decision pipeline
        from core.trading.pipeline import DecisionPipeline
        pipeline = DecisionPipeline(config=config, dna_risk_genes=dna.risk_genes)

        pending_decision: Optional[Decision] = None
        last_bar_time = task_row.get("last_bar_time")
        if last_bar_time:
            # Task has previous state -- crash recovery scenario.
            # Per design: crash is a bug, not a state to recover from.
            # Mark as error and stop so user can restart a fresh task.
            logger.error(
                "Task %s has last_bar_time=%s but is pending -- "
                "possible crash recovery. Marking as error.",
                task_id, last_bar_time,
            )
            self._update_status(task_id, "stopped", "crash_recovery")
            return

        # Save state after replay
        self._save_account_state(account, task_id, df, pending_decision)
        self._push_position_update(account, task_id)

        # Main loop
        bar_interval = _BAR_SECONDS.get(timeframe, 86400)
        poll_wait = min(bar_interval, 60)

        # MTF: track last refresh time per timeframe for throttled refresh
        now_utc = pd.Timestamp.now(tz="UTC")
        tf_last_refresh: Dict[str, pd.Timestamp] = {}
        if dfs_by_timeframe:
            for tf in mtf_timeframes:
                tf_last_refresh[tf] = now_utc

        while not controller.stop_requested:
            controller.check_stop()
            try:
                # MTF: refresh each timeframe only when its bar interval has elapsed
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

                    # Update execution TF in dfs_by_timeframe
                    if dfs_by_timeframe is not None:
                        dfs_by_timeframe[timeframe] = new_df

                    sig_set = dna_to_signal_set(dna, new_df, dfs_by_timeframe)

                    for i in range(len(df), len(new_df)):
                        controller.check_stop()
                        row = new_df.iloc[i]
                        ts = new_df.index[i]

                        # Skip pre-creation bars for new tasks
                        if last_bar_time is None and created_at and ts.isoformat() < created_at:
                            continue

                        # Delegate all decision logic to pipeline
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
    # Helpers
    # ------------------------------------------------------------------

    def _init_predictor(self, df, task_row: dict):
        """Initialize PriceRangePredictor with stored or default DNA."""
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
        """Exclude incomplete (forming) bar."""
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
        """Drop leading rows where indicator columns are all NaN (warmup period)."""
        if df.empty:
            return df
        ohlcv_cols = {"open", "high", "low", "close", "volume"}
        ind_cols = [c for c in df.columns if c not in ohlcv_cols]
        if not ind_cols:
            return df
        valid_mask = df[ind_cols].notna().any(axis=1)
        first_valid = valid_mask.idxmax()
        if not valid_mask.loc[first_valid]:
            return df.iloc[0:0]  # all NaN
        first_idx = df.index.get_loc(first_valid)
        if first_idx == 0:
            return df
        logger.info("Trimmed %d warmup rows with NaN indicators", first_idx)
        return df.iloc[first_idx:]

    def _load_data(self, symbol: str, timeframe: str):
        from core.data.storage import load_parquet
        path = self.data_dir / f"{symbol}_{timeframe}.parquet"
        if not path.exists():
            return None
        return load_parquet(path)

    def _fetch_and_update(self, symbol: str, timeframe: str):
        try:
            return update_market_data(
                symbol=symbol, interval=timeframe, data_dir=self.data_dir,
            )
        except Exception:
            return None

    def _restore_account_state(self, account: VirtualAccount, task: dict) -> None:
        """Restore VirtualAccount state from DB row."""
        account._prior_trades = task.get("total_trades", 0)
        account._prior_pnl = task.get("total_pnl", 0.0)
        account._prior_wins = task.get("win_count", 0)
        account._prior_losses = task.get("loss_count", 0)

    def _restore_balance_and_position(self, account: VirtualAccount, task: dict) -> None:
        """Restore balance and position from DB row."""
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
        """Persist VirtualAccount state to DB."""
        import json
        from core.persistence.db_ext import update_paper_trading_task
        kwargs = {"balance": account.balance}

        # Serialize pending_decision if present
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
            from core.persistence.db_ext import save_equity_snapshots
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
