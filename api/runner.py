"""Evolution Runner: background thread that executes evolution tasks.

Polls SQLite for pending tasks, runs EvolutionEngine.evolve(),
and pushes generation updates via WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from core.evolution.engine import EvolutionEngine
from core.evolution.diversity import compute_phenotype_diversity
from core.evolution.champion import ChampionTracker
from core.evolution import evaluation
from core.strategy.dna import StrategyDNA
from core.persistence.db import (
    connect,
    save_history,
    save_snapshot,
    update_task,
)

from core.logging import get_logger

logger = get_logger("RUNNER")

# Global reference to the FastAPI app's asyncio loop for WS push
_ws_push_fn: Optional[Callable] = None

# Active task controllers: task_id -> TaskController
_active_controllers: Dict[str, "TaskController"] = {}


def get_active_controllers() -> Dict[str, "TaskController"]:
    return _active_controllers


# ---------------------------------------------------------------------------
# TaskController: direct stop signal via threading.Event
# ---------------------------------------------------------------------------

class TaskStopRequested(Exception):
    """Raised when a task stop is requested via TaskController."""
    pass


class TaskController:
    """Thread-safe task controller for cooperative cancellation."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def check_stop(self) -> None:
        if self._stop_event.is_set():
            raise TaskStopRequested()


# ---------------------------------------------------------------------------
# Progress & heartbeat helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_heartbeat(db_path: Path, task_id: str) -> None:
    """Update heartbeat_at timestamp for a running task."""
    from core.persistence.db import _connect
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE evolution_task SET heartbeat_at = ? WHERE task_id = ?",
                (_now_iso(), task_id),
            )
            conn.commit()
    except Exception:
        logger.warning("heartbeat update failed for %s", task_id, exc_info=True)


def update_phase(db_path: Path, task_id: str, phase: str) -> None:
    """Update current_phase for a task and push WS notification."""
    from core.persistence.db import _connect
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE evolution_task SET current_phase = ?, updated_at = ? WHERE task_id = ?",
                (phase, _now_iso(), task_id),
            )
            conn.commit()
    except Exception:
        logger.warning("phase update failed for %s", task_id, exc_info=True)
    _push_ws(task_id, {
        "type": "phase_changed",
        "task_id": task_id,
        "phase": phase,
    })


def update_progress(db_path: Path, task_id: str, progress: dict) -> None:
    """Write structured progress JSON to DB."""
    from core.persistence.db import _connect
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "UPDATE evolution_task SET progress_json = ?, updated_at = ? WHERE task_id = ?",
                (json.dumps(progress, ensure_ascii=False), _now_iso(), task_id),
            )
            conn.commit()
    except Exception:
        logger.warning("progress update failed for %s", task_id, exc_info=True)


def recover_stale_tasks(db_path: Path) -> None:
    """Mark all running tasks as stopped (crash recovery).

    Called once at app startup to clean up after unclean shutdowns.

    batch1 D3 语义注记: 恢复类写入是「残留态清理」（WHERE status='running'
    的条件更新即其前态语义），不走 update_task 的动作前态守护——pending
    任务不受影响（创建即 pending 后不会被误清，spec 场景「崩溃恢复不误清
    未接手任务」）。
    """
    from core.persistence.db import _connect
    with _connect(db_path) as conn:
        result = conn.execute(
            "UPDATE evolution_task SET status = 'stopped', "
            "stop_reason = 'crash_recovery', updated_at = ? "
            "WHERE status = 'running'",
            (_now_iso(),),
        )
        conn.commit()
        if result.rowcount > 0:
            logger.info("Recovered %d stale tasks on startup", result.rowcount)


def restart_crashed_tasks(db_path: Path, max_restarts: int = 3) -> int:
    """Restart crashed evolution tasks that haven't produced a champion.

    Scans tasks stopped due to crash/heartbeat-timeout without a champion and marks
    them pending again so _find_pending_task re-picks them for a fresh run. Uses
    progress_json.restart_count to bound retries, preventing infinite restart of
    deterministically-crashing tasks.

    Called at app startup after recover_stale_tasks. NOTE: this re-runs evolution from
    initial_dna (same config), NOT individual-level resume — EvolutionEngine.evolve has
    no initial_population param (checkpoint resume 已按裁决 6 移除)
    (dead code). Crashed-but-incomplete tasks get a bounded retry instead of silent loss.
    """
    from core.persistence.db import _connect
    restarted = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, progress_json FROM evolution_task "
            "WHERE status = 'stopped' "
            "AND stop_reason IN ('crash_recovery', 'heartbeat_timeout') "
            "AND (champion_dna IS NULL OR champion_dna = '')",
        ).fetchall()
        for row in rows:
            task_id = row["task_id"]
            try:
                progress = json.loads(row["progress_json"]) if row["progress_json"] else {}
            except Exception:
                progress = {}
            restart_count = int(progress.get("restart_count", 0))
            if restart_count >= max_restarts:
                continue
            progress["restart_count"] = restart_count + 1
            conn.execute(
                "UPDATE evolution_task SET status = 'pending', progress_json = ?, "
                "stop_reason = NULL, updated_at = ? WHERE task_id = ?",
                (json.dumps(progress, ensure_ascii=False), _now_iso(), task_id),
            )
            restarted += 1
        conn.commit()
    if restarted > 0:
        logger.info("Restarted %d crashed tasks (max %d retries each)", restarted, max_restarts)
    return restarted


def set_ws_push_fn(fn: Callable) -> None:
    """Register a function that the runner can call to push WS messages.

    Called once during app startup.
    """
    global _ws_push_fn
    _ws_push_fn = fn


def _push_ws(task_id: str, payload: Dict[str, Any]) -> None:
    """Fire-and-forget WS push (non-blocking) with observability."""
    if _ws_push_fn is not None:
        try:
            _ws_push_fn(task_id, payload)
        except Exception:
            logger.warning("ws push failed for task %s", task_id, exc_info=True)


def _split_train_test(
    df: pd.DataFrame,
    train_ratio: float = 0.7,
    min_bars: int = 100,
) -> tuple:
    """Split a time-series DataFrame into train and test sets.

    Returns (train_df, test_df). If data is too short, test_df is None
    and the full df is used as train_df.
    """
    if len(df) < min_bars:
        logger.warning(
            "Data too short for train/test split (%d bars < %d min), "
            "using full dataset for training",
            len(df), min_bars,
        )
        return df, None

    if train_ratio <= 0.0 or train_ratio >= 1.0:
        return df, None

    split_idx = int(len(df) * train_ratio)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()
    return train_df, test_df


class EvolutionRunner(threading.Thread):
    """Daemon thread that picks up pending evolution tasks and runs them."""

    def __init__(
        self,
        db_path: Path,
        data_dir: Path,
        poll_interval: float = 2.0,
    ) -> None:
        super().__init__(daemon=True, name="evolution-runner")
        self.db_path = db_path
        self.data_dir = data_dir
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._active_task_id: Optional[str] = None
        self._population_count: int = 0  # Track continuous population count
        self._last_tick_time: float = 0.0
        self._tick_count: int = 0

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Thread main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("EvolutionRunner started (db=%s)", self.db_path)
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.error("tick error", exc_info=True)
            self._last_tick_time = time.monotonic()
            self._tick_count += 1
            self._stop_event.wait(self.poll_interval)
        logger.info("EvolutionRunner stopped")

    def get_status(self) -> dict:
        """Return thread-safe health snapshot for API consumption."""
        age = time.monotonic() - self._last_tick_time if self._last_tick_time else None
        return {
            "is_alive": self.is_alive(),
            "last_tick_age_seconds": round(age, 1) if age else None,
            "tick_count": self._tick_count,
            "active_task_id": self._active_task_id,
        }

    def _tick(self) -> None:
        """One poll cycle: pick a task, run one generation, update DB."""
        if self._active_task_id:
            # Check if the active task was paused / stopped
            from core.persistence.db import get_task
            row = get_task(self.db_path, self._active_task_id)
            if row is None or row["status"] not in ("running", "pending"):
                self._active_task_id = None
                return
            # Continue evolving - handled by the on_generation callback loop
            return

        # Look for a pending task
        task = self._find_pending_task()
        if task is None:
            return

        self._run_task(task)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    def _find_pending_task(self) -> Optional[Dict[str, Any]]:
        conn = connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM evolution_task WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def _run_task(self, task_row: Dict[str, Any]) -> None:
        """Exception boundary for task execution.

        Guarantees:
        - _active_task_id is ALWAYS cleared (finally block)
        - task status is updated to 'stopped' on any unhandled exception
        - TaskController is ALWAYS unregistered (finally block)
        """
        task_id = task_row["task_id"]
        self._active_task_id = task_id

        controller = TaskController()
        _active_controllers[task_id] = controller

        try:
            self._execute_task(task_row, task_id, controller)
        except TaskStopRequested:
            logger.info("Task %s stopped via TaskController", task_id)
            # batch1 D3 语义注记: 失败/中止路径的无条件写是「异常终态最后写」——
            # 前态必然 running（单线程执行流内），不接动作前态守护
            update_task(self.db_path, task_id, status="stopped", stop_reason="user_stop")
        except Exception:
            logger.error("Task %s failed", task_id, exc_info=True)
            update_task(self.db_path, task_id, status="stopped", stop_reason="error")
        finally:
            self._active_task_id = None
            _active_controllers.pop(task_id, None)

    def _execute_task(self, task_row: Dict[str, Any], task_id: str, controller: TaskController) -> None:
        """Full task execution. All exceptions propagate to _run_task's handler."""

        # Mark as running
        update_task(self.db_path, task_id, status="running")

        # Immediately notify frontend that the task has started
        _push_ws(task_id, {
            "type": "task_started",
            "task_id": task_id,
            "status": "running",
            "target_score": task_row["target_score"],
            "max_generations": task_row.get("max_generations", 200),
        })

        update_phase(self.db_path, task_id, "initializing")
        update_heartbeat(self.db_path, task_id)

        # Parse initial DNA
        try:
            dna = StrategyDNA.from_json(task_row["initial_dna"])
        except Exception:
            logger.error("Failed to parse initial_dna for task %s", task_id)
            update_task(self.db_path, task_id, status="stopped", stop_reason="invalid_dna")
            return

        target_score = task_row["target_score"]

        # Adjust target_score default based on objective type.
        # Frontend does not pass target_score, so it defaults to 1.0 from the schema.
        # Sharpe: 1.0 is a reasonable target. annual_return: use user's min_annual_return.
        req = self._build_requirements(task_row)
        if target_score == 1.0 and req.objective == "annual_return" and req.min_annual_return > 0:
            target_score = req.min_annual_return
        elif target_score == 1.0 and req.objective == "calmar":
            target_score = 2.0

        template = task_row["score_template"]
        pop_size = task_row.get("population_size", 15)
        max_gens = task_row.get("max_generations", 200)
        leverage = task_row.get("leverage", 1)
        direction = task_row.get("direction", "long")

        # Parse timeframe_pool from task config
        tf_pool_raw = task_row.get("timeframe_pool")
        tf_pool = None
        if tf_pool_raw:
            try:
                tf_pool = json.loads(tf_pool_raw) if isinstance(tf_pool_raw, str) else tf_pool_raw
            except (json.JSONDecodeError, Exception):
                tf_pool = None

        # Load market data first, then create engine with actually loaded TFs
        controller.check_stop()
        update_phase(self.db_path, task_id, "data_loading")

        from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

        _symbol = task_row["symbol"]
        _timeframe = task_row["execution_timeframe"] if "execution_timeframe" in task_row else task_row["timeframe"]
        _data_start = task_row.get("data_start")
        _data_end = task_row.get("data_end")

        _enhanced_df = load_and_prepare_df(
            self.data_dir, _symbol, _timeframe, _data_start, _data_end,
            stop_check=controller.check_stop,
        )
        if _enhanced_df is None:
            update_task(self.db_path, task_id, status="stopped", stop_reason="no_data")
            return

        # Train/test split for out-of-sample validation
        train_ratio = float(task_row.get("train_ratio", 0.7))
        _train_df, _test_df = _split_train_test(_enhanced_df, train_ratio)
        if _test_df is not None:
            logger.info(
                "Task %s: train/test split %.0f%%/%.0f%% (%d/%d bars)",
                task_id, train_ratio * 100, (1 - train_ratio) * 100,
                len(_train_df), len(_test_df),
            )

        controller.check_stop()
        update_phase(self.db_path, task_id, "evolution_running")
        update_heartbeat(self.db_path, task_id)

        _dfs_by_timeframe = None
        if tf_pool and len(tf_pool) > 1:
            _dfs_by_timeframe = load_mtf_data(
                self.data_dir, _symbol, _timeframe, _enhanced_df,
                set(tf_pool), _data_start, _data_end,
                stop_check=controller.check_stop,
            )

        # Use actually loaded TFs for evolution, not raw tf_pool
        _loaded_tfs = list(_dfs_by_timeframe.keys()) if _dfs_by_timeframe else [_timeframe]

        engine = EvolutionEngine(
            target_score=target_score,
            template_name=template,
            population_size=pop_size,
            max_generations=max_gens,
            leverage=leverage,
            direction=direction,
            timeframe_pool=_loaded_tfs,
        )

        # Build evaluate_fn and evaluate_population for batch evaluation
        # NOTE: direction is fixed to the task's original direction (e.g. "mixed").
        # Continuous evolution direction rotation only affects seed creation, NOT evaluation.
        _eval_direction = direction

        def evaluate_fn(individual: StrategyDNA) -> float:
            result = self._evaluate_dna(
                individual, task_row, leverage, _eval_direction,
                enhanced_df=_train_df, dfs_by_timeframe=_dfs_by_timeframe,
            )
            if isinstance(result, dict):
                individual._eval_diagnostics = result
                return result["score"]
            return result

        def evaluate_population_fn(population: list[StrategyDNA]) -> list[float]:
            """Batch-evaluate a population using BacktestEngine.batch_run."""
            return self._evaluate_population(
                population, task_row, leverage, _eval_direction,
                enhanced_df=_train_df, dfs_by_timeframe=_dfs_by_timeframe,
            )

        # Track mutations for logging
        last_mutations: List[str] = []
        global_gen_offset = 0  # Offset for continuous mode: avoids history overwrite
        discovered_signatures: set = set()  # Signatures of auto-extracted strategies
        strategy_threshold = task_row.get("strategy_threshold", 1.0)
        champion_tracker = ChampionTracker()

        def on_generation(gen: int, best_score: float, avg_score: float) -> None:
            nonlocal last_mutations, global_gen_offset

            # Fast cooperative cancellation via threading.Event
            controller.check_stop()
            update_heartbeat(self.db_path, task_id)

            # Update structured progress
            update_progress(self.db_path, task_id, {
                "phase": "evolution_running",
                "current_generation": gen + global_gen_offset,
                "best_score": best_score,
                "avg_score": avg_score,
            })

            # Update current_generation in DB (use global offset for continuous mode)
            global_gen = gen + global_gen_offset
            conn = connect(self.db_path)
            conn.execute(
                "UPDATE evolution_task SET current_generation = ?, updated_at = ? WHERE task_id = ?",
                (global_gen, datetime.now(timezone.utc).isoformat(), task_id),
            )
            conn.commit()
            conn.close()

            # Compute generation-level diagnostics from population
            gen_diag = {}
            if hasattr(engine, '_population') and engine._population:
                diags = [getattr(ind, '_eval_diagnostics', None) for ind in engine._population]
                diags = [d for d in diags if d]

                if diags:
                    avg_trades = sum(d.get("total_trades", 0) for d in diags) / len(diags)
                    phenotype_div = compute_phenotype_diversity(engine._population)
                    gen_diag = {
                        "avg_trades": round(avg_trades, 1),
                        "diversity": phenotype_div,
                    }

            # Save history record with diagnostics
            top3_summary = f"best={best_score:.1f}|pop={self._population_count + 1}"
            if gen_diag:
                top3_summary += f"|diag={json.dumps(gen_diag)}"
            save_history(self.db_path, task_id, global_gen, best_score, avg_score, top3_summary)

            # Save snapshot for each generation
            if hasattr(engine, '_population') and engine._population:
                try:
                    # Find best individual from current population
                    best_ind = engine._population[0]
                    save_snapshot(
                        self.db_path, task_id, global_gen,
                        best_score=best_score,
                        avg_score=avg_score,
                        best_dna=best_ind,
                        population=engine._population,
                        mutation_log=json.dumps(gen_diag) if gen_diag else None,
                    )
                except Exception:
                    # B1.2: 快照为非关键路径数据，写失败降级为本代快照缺失 +
                    # error 日志（含原因链），不吞掉也不中断进化
                    logger.error(
                        "save_snapshot failed for task %s gen %d — 本代快照缺失，进化继续",
                        task_id, global_gen,
                        exc_info=True,
                    )

            # Update champion atomically via ChampionTracker
            if best_score > 0 and hasattr(engine, '_population') and engine._population:
                best_ind = engine._population[0]
                best_ind_diagnostics = getattr(best_ind, '_eval_diagnostics', None)

                if best_ind_diagnostics and "raw_metrics" in best_ind_diagnostics:
                    updated = champion_tracker.update(
                        score=best_score,
                        metrics=best_ind_diagnostics.get("raw_metrics"),
                        dimension_scores=best_ind_diagnostics.get("dimension_scores", {}),
                        generation=global_gen,
                    )

                    if updated:
                        champion_rec = champion_tracker.get_champion()
                        conn2 = connect(self.db_path)
                        conn2.execute(
                            "UPDATE evolution_task SET best_score = ?, best_fitness = ?, champion_metrics = ?, champion_dimension_scores = ? WHERE task_id = ?",
                            (
                                champion_rec.score,
                                champion_rec.score,
                                json.dumps(champion_rec.metrics),
                                json.dumps(champion_rec.dimension_scores),
                                task_id,
                            ),
                        )
                        conn2.commit()
                        conn2.close()
                elif best_score > 0:
                    # No diagnostics but score is positive - update best_score only
                    updated = champion_tracker.update(score=best_score, generation=global_gen)
                    if updated:
                        conn2 = connect(self.db_path)
                        conn2.execute(
                            "UPDATE evolution_task SET best_score = ?, best_fitness = ? WHERE task_id = ? AND (best_score IS NULL OR best_score < ?)",
                            (best_score, best_score, task_id, best_score),
                        )
                        conn2.commit()
                        conn2.close()

            # Auto-extract high-scoring strategies to strategy table
            if hasattr(engine, '_population') and engine._population:
                for ind in engine._population:
                    diag = getattr(ind, '_eval_diagnostics', None)
                    if not diag or not diag.get("qualified", False):
                        continue
                    sig = ind.gene_signature
                    if sig in discovered_signatures:
                        continue
                    discovered_signatures.add(sig)
                    try:
                        from core.persistence.db_ext import save_strategy
                        from core.strategy.dna import generate_strategy_name
                        name = generate_strategy_name(ind)
                        save_strategy(
                            self.db_path,
                            strategy_id=ind.strategy_id,
                            dna_json=ind.to_json(),
                            symbol=task_row.get("symbol", "BTCUSDT"),
                            timeframe=task_row.get("timeframe", "4h"),
                            name=name,
                            source="evolution",
                            source_task_id=task_id,
                            best_score=diag.get("fitness", diag.get("score", 0)),
                            gene_signature=sig,
                            generation=global_gen,
                            metrics_json=json.dumps(diag.get("raw_metrics")) if diag.get("raw_metrics") else None,
                        )
                        _push_ws(task_id, {
                            "type": "strategy_discovered",
                            "task_id": task_id,
                            "strategy_id": ind.strategy_id,
                            "score": diag.get("fitness", diag.get("score", 0)),
                            "fitness": diag.get("fitness", 0),
                            "qualified": diag.get("qualified", False),
                            "name": name,
                            "generation": global_gen,
                        })
                    except Exception:
                        logger.debug("strategy extract failed", exc_info=True)

            # Push WS update with champion DNA
            ws_payload = {
                "type": "generation_complete",
                "task_id": task_id,
                "generation": global_gen,
                "best_score": best_score,
                "best_fitness": best_score,
                "avg_score": avg_score,
                "target_score": target_score,
                "max_generations": max_gens,
            }
            # Add qualified_count from population diagnostics
            if hasattr(engine, '_population') and engine._population:
                qualified_count = sum(
                    1 for ind in engine._population
                    if getattr(ind, '_eval_diagnostics', {}).get("qualified", False)
                )
                ws_payload["qualified_count"] = qualified_count
            if hasattr(engine, '_population') and engine._population:
                best_ind = engine._population[0]
                if hasattr(best_ind, 'to_dict'):
                    ws_payload["champion_dna"] = best_ind.to_dict()
            _push_ws(task_id, ws_payload)

        result = engine.evolve(
            ancestor=dna,
            evaluate_fn=evaluate_fn,
            on_generation=on_generation,
            stop_check=controller.check_stop,
            evaluate_population=evaluate_population_fn,
        )

        champion = result["champion"]
        stop_reason = result["stop_reason"]

        # Continuous evolution loop: keep starting new populations
        # until user manually stops or an error occurs
        continuous = bool(task_row.get("continuous", 1))

        while continuous and stop_reason not in ("error",):
            controller.check_stop()

            self._population_count += 1

            logger.info(
                "Task %s: starting population #%d (stop_reason=%s)",
                task_id, self._population_count + 1, stop_reason,
            )

            # Notify frontend that a new population has started
            _push_ws(task_id, {
                "type": "population_started",
                "task_id": task_id,
                "population_count": self._population_count + 1,
                "best_score_ever": result.get("champion_score", 0) if result else 0,
                "total_generations_so_far": global_gen_offset,
            })

            # Reset for new population with expanded search space
            # Inject diverse strategy templates to avoid searching the same region
            extra_ancestors = []
            if champion is not None:
                dna = champion
                dna.mutation_ops = []

            # Inject 2 random strategy templates as seeds (trend/momentum/mean-reversion etc.)
            # This expands the search space beyond the champion's strategy region
            from core.evolution.population import STRATEGY_TEMPLATES, _dna_from_template
            _tf = task_row.get("execution_timeframe", task_row.get("timeframe", "4h"))
            _sym = task_row.get("symbol", "BTCUSDT")
            template_seeds = random.sample(
                STRATEGY_TEMPLATES, min(2, len(STRATEGY_TEMPLATES))
            )
            for tpl in template_seeds:
                seed = _dna_from_template(
                    tpl, _tf, _sym, leverage, direction,
                )
                extra_ancestors.append(seed)

            # Collect signatures from previous population for dedup
            # MUST happen before setting _population = None
            if hasattr(engine, '_population') and engine._population:
                for ind in engine._population:
                    discovered_signatures.add(ind.gene_signature)
                # Preserve top elites as ancestors for next population
                extra_ancestors.extend(engine._population[:3])

            engine._population = None

            result = engine.evolve(
                ancestor=dna,
                evaluate_fn=evaluate_fn,
                on_generation=on_generation,
                extra_ancestors=extra_ancestors if extra_ancestors else None,
                exclude_signatures=discovered_signatures,
                stop_check=controller.check_stop,
                evaluate_population=evaluate_population_fn,
            )
            champion = result["champion"]
            stop_reason = result["stop_reason"]
            # Accumulate global generation offset to prevent history overwrite
            global_gen_offset += result["total_generations"]

        # Save champion
        if champion is not None:
            # batch1 D3: completed 前态守护 {running}——与路由 stop 写入的竞态中
            # 先到者胜（先 completed 则 stop 被 409 拒绝；先 stopped 则本写不生效）
            update_task(
                self.db_path, task_id,
                status="completed",
                champion_dna=champion,
                stop_reason=stop_reason,
                expected_statuses=("running",),
            )

            # Save final champion metrics from tracker (consistent snapshot)
            champion_rec = champion_tracker.get_champion()
            if champion_rec and champion_rec.metrics:
                conn = connect(self.db_path)
                conn.execute(
                    "UPDATE evolution_task SET best_fitness = ?, champion_metrics = ?, champion_dimension_scores = ? WHERE task_id = ?",
                    (champion_rec.score,
                     json.dumps(champion_rec.metrics),
                     json.dumps(champion_rec.dimension_scores),
                     task_id),
                )
                conn.commit()
                conn.close()

            # Out-of-sample validation on test split
            if _test_df is not None and len(_test_df) >= 50:
                oos_result = self._evaluate_dna(
                    champion, task_row, leverage, _eval_direction,
                    enhanced_df=_test_df, dfs_by_timeframe=None,
                )
                oos_fitness = oos_result.get("fitness", 0.0) if isinstance(oos_result, dict) else 0.0
                oos_qualified = oos_result.get("qualified", False) if isinstance(oos_result, dict) else False
                oos_metrics = oos_result.get("raw_metrics", {}) if isinstance(oos_result, dict) else {}

                logger.info(
                    "Task %s: OOS validation fitness=%.4f qualified=%s trades=%s",
                    task_id, oos_fitness, oos_qualified,
                    oos_result.get("total_trades", 0) if isinstance(oos_result, dict) else 0,
                )

                # Persist OOS results
                conn = connect(self.db_path)
                conn.execute(
                    "UPDATE evolution_task SET oos_fitness = ?, oos_qualified = ?, oos_metrics = ? WHERE task_id = ?",
                    (oos_fitness, int(oos_qualified), json.dumps(oos_metrics), task_id),
                )
                conn.commit()
                conn.close()

        else:
            update_task(
                self.db_path, task_id,
                status="completed",
                stop_reason=stop_reason,
                expected_statuses=("running",),
            )

        # Final WS push
        _push_ws(task_id, {
            "type": "evolution_complete",
            "task_id": task_id,
            "stop_reason": stop_reason,
            "total_generations": result["total_generations"],
            "champion_score": result["champion_fitness"],
            "generation": result["total_generations"],
            "best_score": result["champion_fitness"],
            "best_fitness": result["champion_fitness"],
            "target_score": target_score,
            "max_generations": max_gens,
        })

    def _build_requirements(self, task_row: Dict[str, Any]):
        """Build RequirementsConfig from task config (delegates to core.evolution.evaluation)."""
        return evaluation.build_requirements(task_row)

    def _evaluate_dna(self, individual: StrategyDNA, task_row: Dict[str, Any],
                       leverage: int = 1, direction: str = "long",
                       enhanced_df=None, dfs_by_timeframe=None) -> dict:
        """Score a DNA via backtest (delegates to core.evolution.evaluation)."""
        return evaluation.evaluate_dna(
            individual, task_row, getattr(self, "data_dir", None), leverage, direction,
            enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
        )

    def _evaluate_population(
        self,
        population: list[StrategyDNA],
        task_row: Dict[str, Any],
        leverage: int = 1,
        direction: str = "long",
        enhanced_df=None,
        dfs_by_timeframe=None,
    ) -> list[float]:
        """Batch-evaluate a population (delegates to core.evolution.evaluation)."""
        return evaluation.evaluate_population(
            population, task_row, getattr(self, "data_dir", None), leverage, direction,
            enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
        )


    def _find_parquet(self, safe_symbol: str, timeframe: str):
        """Find parquet file for a symbol+timeframe, trying aliases."""
        from core.data.mtf_loader import find_parquet
        return find_parquet(self.data_dir, safe_symbol, timeframe)


