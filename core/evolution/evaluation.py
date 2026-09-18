"""Evolution evaluation: score StrategyDNA individuals via backtest + fitness.

Moved verbatim from api/runner.py so the evolution core owns its evaluation
logic — EvolutionEngine.evolve() already accepts evaluate_fn/evaluate_population
callbacks (core/evolution/engine.py), so the default implementation belongs in
core, not the API runner. Pure of runner state: takes data_dir/task_row as
parameters, making it reusable from CLI/tests, not just the runner thread.

Invariant: formulas and control flow moved verbatim — no behavior change.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)


def build_requirements(task_row: Dict[str, Any]):
    """Build RequirementsConfig from task config.

    Prefers requirements_json (full config from frontend).
    Falls back to legacy min_annual_return/max_drawdown_limit columns.

    B1.1: requirements_json 解析失败必须显式报错——静默回退会用默认
    objective (sharpe) 偷换用户的进化优化目标，任务朝错误目标进化且无
    感知，属数据正确性失败。runner 路径冒泡至 _run_task 兜底标
    stopped/error；评估路径由 evaluate_dna 兜底记 0 分 + error 标记。
    """
    from core.scoring.scorer import RequirementsConfig

    requirements_json = task_row.get("requirements_json")
    if requirements_json:
        try:
            data = json.loads(requirements_json) if isinstance(requirements_json, str) else requirements_json
            return RequirementsConfig(
                objective=data.get("objective", "sharpe"),
                min_annual_return=data.get("min_annual_return", 0.0),
                max_drawdown=data.get("max_drawdown", 0.30),
                min_win_rate=data.get("min_win_rate", 0.0),
                min_total_trades=data.get("min_total_trades", 10),
                min_profit_factor=data.get("min_profit_factor", 1.2),
            )
        except Exception as e:
            logger.error(
                "requirements_json 解析失败，拒绝静默回退默认 objective "
                "(task_id=%s, raw=%.200r)",
                task_row.get("task_id"), requirements_json,
                exc_info=True,
            )
            raise ValueError(
                f"requirements_json 解析失败，进化优化目标无法确定: {e!r}"
            ) from e

    return RequirementsConfig(
        objective="sharpe",
        min_annual_return=task_row.get("min_annual_return", 0.0),
        max_drawdown=task_row.get("max_drawdown_limit") or 0.30,
    )


def evaluate_dna(
    individual: StrategyDNA,
    task_row: Dict[str, Any],
    data_dir: Path,
    leverage: int = 1,
    direction: str = "long",
    enhanced_df=None,
    dfs_by_timeframe=None,
) -> dict:
    """Score a DNA using backtesting against the dataset.

    Returns a dict with score and diagnostics.
    Accepts pre-loaded data to avoid redundant I/O per individual.
    """
    # Force override task-level constraints before backtesting
    individual.risk_genes.leverage = leverage
    individual.risk_genes.direction = direction

    diagnostics: Dict[str, Any] = {
        "used_real_data": False,
        "data_bars": 0,
        "total_trades": 0,
        "fallback": True,
        "liquidated": False,
    }

    try:
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import dna_to_signal_set
        from core.scoring.scorer import compute_fitness

        # Load data on demand if not pre-loaded (backward compatibility)
        if enhanced_df is None:
            from core.data.mtf_loader import load_and_prepare_df, load_mtf_data

            symbol = task_row["symbol"]
            timeframe = task_row["execution_timeframe"] if "execution_timeframe" in task_row else task_row["timeframe"]
            data_start = task_row.get("data_start")
            data_end = task_row.get("data_end")
            enhanced_df = load_and_prepare_df(
                data_dir, symbol, timeframe, data_start, data_end,
            )

            if enhanced_df is None:
                diagnostics["score"] = 0.0
                return diagnostics

            # Load multi-timeframe data if task has timeframe_pool
            tf_pool_raw = task_row.get("timeframe_pool")
            tf_pool = None
            if tf_pool_raw:
                try:
                    tf_pool = json.loads(tf_pool_raw) if isinstance(tf_pool_raw, str) else tf_pool_raw
                except (json.JSONDecodeError, Exception):
                    tf_pool = None

            if tf_pool and len(tf_pool) > 1:
                dfs_by_timeframe = load_mtf_data(
                    data_dir, symbol, timeframe, enhanced_df,
                    set(tf_pool), data_start, data_end,
                )

        diagnostics["used_real_data"] = True
        diagnostics["data_bars"] = len(enhanced_df)

        # Compute signals once, pass to BacktestEngine to avoid double computation
        sig_set = dna_to_signal_set(individual, enhanced_df,
                                     dfs_by_timeframe=dfs_by_timeframe)

        if sig_set.entries.sum() == 0:
            diagnostics["score"] = 0.0
            diagnostics["fitness"] = 0.0
            diagnostics["qualified"] = False
            diagnostics["fallback"] = False
            return diagnostics

        bt = BacktestEngine()
        bt_result = bt.run(individual, enhanced_df,
                           dfs_by_timeframe=dfs_by_timeframe,
                           signal_set=sig_set)

        # Use pre-computed metrics from BacktestEngine (avoids double computation)
        metrics = bt_result.metrics_dict

        # Build requirements from task config (prefer requirements_json over legacy columns)
        req = build_requirements(task_row)
        fitness_result = compute_fitness(
            metrics, requirements=req,
            liquidated=bt_result.liquidated,
        )

        diagnostics["score"] = fitness_result["fitness"]
        diagnostics["fitness"] = fitness_result["fitness"]
        diagnostics["qualified"] = fitness_result["qualified"]
        diagnostics["satisfaction"] = fitness_result.get("satisfaction", {})
        diagnostics["total_trades"] = bt_result.total_trades
        diagnostics["fallback"] = False
        diagnostics["liquidated"] = bt_result.liquidated
        diagnostics["data_bars"] = bt_result.data_bars
        diagnostics["raw_metrics"] = fitness_result["raw_metrics"]
        diagnostics["dimension_scores"] = {
            k: round(v["ratio"] * 100, 1)
            for k, v in fitness_result.get("satisfaction", {}).items()
        }

        return diagnostics

    except Exception:
        # Fallback: zero score (not random noise)
        logger.warning(
            "evaluate_dna failed for %s",
            getattr(individual, 'strategy_id', 'unknown'),
            exc_info=True,
        )
        diagnostics["score"] = 0.0
        diagnostics["error"] = True
        return diagnostics


def evaluate_population(
    population: List[StrategyDNA],
    task_row: Dict[str, Any],
    data_dir: Path,
    leverage: int = 1,
    direction: str = "long",
    enhanced_df=None,
    dfs_by_timeframe=None,
) -> List[float]:
    """Batch-evaluate a population using BacktestEngine.batch_run.

    Returns scores in the same order as the input population.
    Falls back to per-individual evaluation on batch failure.
    """
    try:
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import dna_to_signal_set, _empty_signal_set, clear_indicator_cache
        from core.scoring.scorer import compute_fitness

        if enhanced_df is None:
            # Cannot batch without data -- fallback to individual evaluation
            return [
                evaluate_dna(
                    ind, task_row, data_dir, leverage, direction,
                    enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
                ).get("score", 0.0)
                for ind in population
            ]

        # Enforce constraints on all individuals before evaluation
        for ind in population:
            ind.risk_genes.leverage = leverage
            ind.risk_genes.direction = direction

        # Clear indicator cache at start of each generation
        clear_indicator_cache()

        # Batch backtest with built-in signal computation
        bt = BacktestEngine()
        try:
            bt_results = bt.batch_run(
                population, enhanced_df,
                dfs_by_timeframe=dfs_by_timeframe,
            )
        except Exception:
            logger.warning("batch_run failed, falling back to per-individual evaluation", exc_info=True)
            return [
                evaluate_dna(
                    ind, task_row, data_dir, leverage, direction,
                    enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
                ).get("score", 0.0)
                for ind in population
            ]

        # Build requirements from task config (prefer requirements_json over legacy columns)
        req = build_requirements(task_row)

        scores = []
        for i, (ind, bt_result) in enumerate(zip(population, bt_results)):
            metrics = bt_result.metrics_dict
            fitness_result = compute_fitness(
                metrics, requirements=req,
                liquidated=bt_result.liquidated,
            )
            # Store diagnostics on individual (same as evaluate_fn)
            ind._eval_diagnostics = {
                "score": fitness_result["fitness"],
                "fitness": fitness_result["fitness"],
                "qualified": fitness_result["qualified"],
                "satisfaction": fitness_result.get("satisfaction", {}),
                "total_trades": bt_result.total_trades,
                "data_bars": bt_result.data_bars,
                "raw_metrics": fitness_result["raw_metrics"],
                "dimension_scores": {
                    k: round(v["ratio"] * 100, 1)
                    for k, v in fitness_result.get("satisfaction", {}).items()
                },
                "liquidated": bt_result.liquidated,
                "used_real_data": True,
                "fallback": False,
            }
            scores.append(fitness_result["fitness"])

        return scores

    except Exception:
        # Fallback to per-individual evaluation
        logger.warning("evaluate_population unexpected error, falling back", exc_info=True)
        return [
            evaluate_dna(
                ind, task_row, data_dir, leverage, direction,
                enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
            ).get("score", 0.0)
            for ind in population
        ]
