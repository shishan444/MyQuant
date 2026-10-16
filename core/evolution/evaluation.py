"""进化评估：StrategyDNA 个体经回测 + fitness 打分。

服务对象：api/runner.py 进化任务线程（以 evaluate_fn/
evaluate_population 回调注入 EvolutionEngine.evolve）与
tests/evolution 契约测试。从 api/runner.py 原样迁入使进化核心
自持评估逻辑——引擎只认回调签名，默认实现归 core 不归 API runner。
无 runner 状态：data_dir/task_row 走参数，CLI/测试可直调。

迁移不变量：公式与控制流原样搬——行为零变化（后续 B1.1 报错化
与 tf_pool 收敛为独立变更，各有验证）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from core.strategy.dna import StrategyDNA

logger = logging.getLogger(__name__)

# 被清洗个体（前置清洗剔除过坏基因）的适应度惩罚系数
# （unify-tool-return-contract 实施定值）：剔除本身是修复，轻惩罚
# 保留基因信息——避免被清洗个体以剔除后更松的条件集（AND 下更易
# 触发）获得虚高适应度
SANITIZED_FITNESS_PENALTY = 0.9


def build_requirements(task_row: Dict[str, Any]):
    """从任务配置构造 RequirementsConfig。

    优先 requirements_json（前端完整配置）；回退 legacy
    min_annual_return/max_drawdown_limit 列。

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


def parse_timeframe_pool(task_row: Dict[str, Any]) -> List[str]:
    """task_row.timeframe_pool → 周期列表（evaluation 与 api/runner 共用）。

    B1.1 形制：解析失败或形制非 List[str] 时显式报错——静默置 None 会
    退化单周期，进化在错误数据面上无感知跑完，属数据正确性失败。
    runner 路径冒泡至 _run_task 兜底标 stopped/error；评估路径由
    evaluate_dna 兜底记 0 分 + error 标记。

    接受 JSON 字符串（如 '["4h","1d"]'，DB 列形制）或已解 list；空值
    返回空列表（无多周期配置为合法状态）。
    """
    raw = task_row.get("timeframe_pool")
    if not raw:
        return []
    try:
        pool = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(pool, list) or not all(isinstance(tf, str) for tf in pool):
            raise ValueError(f"形制须为 List[str]: {raw!r}")
    except Exception as e:
        logger.error(
            "timeframe_pool 解析失败，拒绝静默退化单周期 (task_id=%s, raw=%.200r)",
            task_row.get("task_id"), raw, exc_info=True,
        )
        raise ValueError(f"timeframe_pool 解析失败: {e!r}") from e
    return pool


def evaluate_dna(
    individual: StrategyDNA,
    task_row: Dict[str, Any],
    data_dir: Path,
    leverage: int = 1,
    direction: str = "long",
    enhanced_df=None,
    dfs_by_timeframe=None,
) -> dict:
    """对单个 DNA 以数据集回测打分。

    Returns:
        dict：score + 诊断字段（used_real_data/data_bars/
        total_trades/fallback/liquidated 等；异常路径 0 分 +
        error=True，不返回随机噪声）。

    接受预载数据（enhanced_df/dfs_by_timeframe）避免逐个体重复
    I/O；预载缺失时按需装载（兼容独立调用）。
    """
    # 回测前强制覆盖任务级约束
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

        # 数据未预载时按需装载（向后兼容独立调用）
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

            # 任务带 timeframe_pool 时装载多周期数据
            tf_pool = parse_timeframe_pool(task_row)

            if len(tf_pool) > 1:
                dfs_by_timeframe = load_mtf_data(
                    data_dir, symbol, timeframe, enhanced_df,
                    set(tf_pool), data_start, data_end,
                )

        diagnostics["used_real_data"] = True
        diagnostics["data_bars"] = len(enhanced_df)

        # 信号只算一次传给 BacktestEngine（避免引擎内重复计算）
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

        # 直接用 BacktestEngine 预计算的 metrics（避免双算）
        metrics = bt_result.metrics_dict

        # 从任务配置构造 requirements（requirements_json 优先于 legacy 列）
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
        # 兜底：0 分（不返回随机噪声）
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
    """以 BacktestEngine.batch_run 批量评估种群。

    Returns:
        分数列表（与输入种群同序）。批量失败或无预载数据时回退
        逐个体 evaluate_dna。
    """
    try:
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import dna_to_signal_set, _empty_signal_set, clear_indicator_cache
        from core.scoring.scorer import compute_fitness

        if enhanced_df is None:
            # 无预载数据不可批量——回退逐个体评估
            return [
                evaluate_dna(
                    ind, task_row, data_dir, leverage, direction,
                    enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
                ).get("score", 0.0)
                for ind in population
            ]

        # 评估前对全种群强制任务级约束
        for ind in population:
            ind.risk_genes.leverage = leverage
            ind.risk_genes.direction = direction

        # 每代开始清一次指标缓存
        clear_indicator_cache()

        # unify-tool-return-contract 评估侧前置清洗（可达性维）：繁殖侧
        # 已检条件类型（无数据帧）；此处进化当次数据帧在场，逐个体试
        # 解析基因列——不可达基因剔除+mutation_ops 注记。清洗后零
        # ENTRY_TRIGGER 的淘汰形在评估侧不炸批：个体保持原样，batch
        # 增捕兜底+零信号自然 0 分。被清洗个体适应度按
        # SANITIZED_FITNESS_PENALTY 温和降权（剔除本身是修复，轻惩罚
        # 保留基因信息——系数为实施定值）
        sanitized_ids = set()
        for ind in population:
            n_ops_before = len(getattr(ind, "mutation_ops", None) or [])
            try:
                from core.evolution.engine import validate_offspring
                validate_offspring(ind, enhanced_df)
            except ValueError:
                pass
            if len(getattr(ind, "mutation_ops", None) or []) > n_ops_before:
                sanitized_ids.add(id(ind))

        # 批量回测（内置信号计算）
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

        # requirements_json 优先于 legacy 列
        req = build_requirements(task_row)

        scores = []
        for i, (ind, bt_result) in enumerate(zip(population, bt_results)):
            metrics = bt_result.metrics_dict
            fitness_result = compute_fitness(
                metrics, requirements=req,
                liquidated=bt_result.liquidated,
            )
            # 被清洗个体（本代有基因剔除）温和降权（unify-tool-return-
            # contract 前置清洗配套——降权与诊断同值，避免多样性度量
            # 消费到两个口径）
            score = fitness_result["fitness"]
            if id(ind) in sanitized_ids:
                score *= SANITIZED_FITNESS_PENALTY
            # 诊断挂到个体（与逐个体路径同构，供多样性度量消费）
            ind._eval_diagnostics = {
                "score": score,
                "fitness": score,
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
            scores.append(score)

        return scores

    except Exception:
        # 回退逐个体评估
        logger.warning("evaluate_population unexpected error, falling back", exc_info=True)
        return [
            evaluate_dna(
                ind, task_row, data_dir, leverage, direction,
                enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
            ).get("score", 0.0)
            for ind in population
        ]
