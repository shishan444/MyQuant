"""进化任务执行线程（EvolutionRunner）。

职能：轮询 SQLite 取 pending 进化任务，驱动 EvolutionEngine.evolve()
逐代执行，落库代际历史/快照/冠军并经推送函数把进度送到实时通道。
服务对象：/api/evolution 路由（建任务/状态查询）与 web 进化页
（实时进度消费）；协作 core.evolution 引擎与 core.persistence 落库。
职责边界：任务生命周期编排与恢复，进化算法本身在 core.evolution。
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
    save_history,
    save_snapshot,
    update_task,
)

from core.logging import get_logger

logger = get_logger("RUNNER")

# 在跑任务的控制器注册表：task_id -> TaskController
_active_controllers: Dict[str, "TaskController"] = {}


def get_active_controllers() -> Dict[str, "TaskController"]:
    return _active_controllers


# ---------------------------------------------------------------------------
# TaskController：经 threading.Event 的直达停止信号
# ---------------------------------------------------------------------------

from core.concurrency.task_control import TaskController, TaskStopRequested  # B4.2 归一


# ---------------------------------------------------------------------------
# 进度与心跳工具群（B5.2 拆分自本模块）
# ---------------------------------------------------------------------------

from api.runner_progress import (  # B5.2 拆分
    _push_ws, set_ws_push_fn, update_heartbeat, update_phase, update_progress,
)


def recover_stale_tasks(db_path: Path) -> None:
    """崩溃恢复：把全部 running 任务标记为 stopped。

    app 启动时调用一次，清理非正常关停的残留态。

    batch1 D3 语义注记: 恢复类写入是「残留态清理」（WHERE status='running'
    的条件更新即其前态语义），不走 update_task 的动作前态守护——pending
    任务不受影响（创建即 pending 后不会被误清，spec 场景「崩溃恢复不误清
    未接手任务」）。
    """
    from core.persistence.repository import evolution as repo
    recovered = repo.recover_stale_tasks(db_path)
    if recovered > 0:
        logger.info("Recovered %d stale tasks on startup", recovered)


def restart_crashed_tasks(db_path: Path, max_restarts: int = 3) -> int:
    """重启未产出冠军的崩溃任务（有界重试）。

    扫描因 crash/心跳超时停止且无冠军的任务，标回 pending 供主循环
    重新接手完整重跑；以 progress_json.restart_count 限制重试次数，
    防止确定性崩溃任务无限重启。

    app 启动时在 recover_stale_tasks 之后调用。注意：这是从 initial_dna
    （同配置）的完整重跑而非个体级 resume——EvolutionEngine.evolve 无
    initial_population 参数（checkpoint resume 已按裁决 6 移除）。
    崩溃未完任务获得有界重试而非静默丢失。
    """
    from core.persistence.repository import evolution as repo
    restarted = repo.restart_crashed_tasks(db_path, max_restarts)
    if restarted > 0:
        logger.info("Restarted %d crashed tasks (max %d retries each)", restarted, max_restarts)
    return restarted


def _split_train_test(
    df: pd.DataFrame,
    train_ratio: float = 0.7,
    min_bars: int = 100,
) -> tuple:
    """时序 DataFrame 的训练/测试切分（样本外验证用）。

    Returns:
        (train_df, test_df)；数据过短或比例非法时 test_df 为 None，
        全量 df 作为 train_df。
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
    """进化任务唯一执行线程（daemon：轮询 pending 任务并逐个完整执行）。"""

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
        self._population_count: int = 0  # 连续进化（多代种群）计数
        self._last_tick_time: float = 0.0
        self._tick_count: int = 0

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # 线程主循环
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
        """线程健康快照（/api/evolution/runner/status 消费，线程安全读取）。"""
        age = time.monotonic() - self._last_tick_time if self._last_tick_time else None
        return {
            "is_alive": self.is_alive(),
            "last_tick_age_seconds": round(age, 1) if age else None,
            "tick_count": self._tick_count,
            "active_task_id": self._active_task_id,
        }

    def _tick(self) -> None:
        """单次轮询周期：接手任务/校验在跑任务状态（代际推进在回调循环内）。"""
        if self._active_task_id:
            # 校验在跑任务是否已被暂停/停止（DB 状态为准）
            from core.persistence.db import get_task
            row = get_task(self.db_path, self._active_task_id)
            if row is None or row["status"] not in ("running", "pending"):
                self._active_task_id = None
                return
            # 代际推进由 on_generation 回调循环自行处理
            return

        # 取一个 pending 任务
        task = self._find_pending_task()
        if task is None:
            return

        self._run_task(task)

    # ------------------------------------------------------------------
    # 任务执行
    # ------------------------------------------------------------------

    def _find_pending_task(self) -> Optional[Dict[str, Any]]:
        from core.persistence.repository import evolution as repo
        return repo.find_pending_task(self.db_path)

    def _run_task(self, task_row: Dict[str, Any]) -> None:
        """任务执行的异常边界（不变量保障）。

        保证：_active_task_id 与 TaskController 注册项在 finally 中
        必然清理；未处理异常把任务置 'stopped'（stop_reason=error），
        线程自身永不为单任务故障退出。
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
        """任务完整执行体（全部异常上抛至 _run_task 边界统一处理）。"""

        # 标记 running（接手动作前态：pending）
        update_task(self.db_path, task_id, status="running")

        # 任务启动即刻推送前端（不等首个代际）
        _push_ws(task_id, {
            "type": "task_started",
            "task_id": task_id,
            "status": "running",
            "target_score": task_row["target_score"],
            "max_generations": task_row.get("max_generations", 200),
        })

        update_phase(self.db_path, task_id, "initializing")
        update_heartbeat(self.db_path, task_id)

        # 解析 initial DNA（非法即终止任务）
        try:
            dna = StrategyDNA.from_json(task_row["initial_dna"])
        except Exception:
            logger.error("Failed to parse initial_dna for task %s", task_id)
            update_task(self.db_path, task_id, status="stopped", stop_reason="invalid_dna")
            return

        target_score = task_row["target_score"]

        # 按 objective 调整 target_score 缺省：前端不传该值（schema 缺省 1.0）——
        # sharpe 下 1.0 即合理目标；annual_return 取用户 min_annual_return；
        # calmar 缺省抬至 2.0
        req = self._build_requirements(task_row)
        if target_score == 1.0 and req.objective == "annual_return" and req.min_annual_return > 0:
            target_score = req.min_annual_return
        elif target_score == 1.0 and req.objective == "calmar":
            target_score = 2.0

        pop_size = task_row.get("population_size", 15)
        max_gens = task_row.get("max_generations", 200)
        leverage = task_row.get("leverage", 1)
        direction = task_row.get("direction", "long")

        # 解析任务配置的 timeframe_pool
        tf_pool = evaluation.parse_timeframe_pool(task_row)

        # 先装载行情数据，再以实际装载成功的 TF 集构造引擎
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

        # 训练/测试切分（样本外验证用）
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
        if len(tf_pool) > 1:
            _dfs_by_timeframe = load_mtf_data(
                self.data_dir, _symbol, _timeframe, _enhanced_df,
                set(tf_pool), _data_start, _data_end,
                stop_check=controller.check_stop,
            )

        # 进化用实际装载成功的 TF 集，非原始 tf_pool
        _loaded_tfs = list(_dfs_by_timeframe.keys()) if _dfs_by_timeframe else [_timeframe]

        engine = EvolutionEngine(
            target_score=target_score,
            population_size=pop_size,
            max_generations=max_gens,
            leverage=leverage,
            direction=direction,
            timeframe_pool=_loaded_tfs,
        )

        # 构造逐个体/种群批评估函数
        # 口径：direction 固定为任务原始方向（如 "mixed"）；连续进化的
        # 方向轮换只影响种子生成，不影响评估
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
            """种群批评估（经 BacktestEngine.batch_run 单次装载多回测）。"""
            return self._evaluate_population(
                population, task_row, leverage, _eval_direction,
                enhanced_df=_train_df, dfs_by_timeframe=_dfs_by_timeframe,
            )

        # 变异记录（日志展示用）
        last_mutations: List[str] = []
        global_gen_offset = 0  # 连续进化全局代偏移：防跨种群历史覆盖
        discovered_signatures: set = set()  # 已自动入库策略的签名集（去重）
        strategy_threshold = task_row.get("strategy_threshold", 1.0)
        champion_tracker = ChampionTracker()

        def on_generation(gen: int, best_score: float, avg_score: float) -> None:
            nonlocal last_mutations, global_gen_offset

            # 快速协作取消检查（threading.Event 直达信号）
            controller.check_stop()
            update_heartbeat(self.db_path, task_id)

            # 结构化进度落库
            update_progress(self.db_path, task_id, {
                "phase": "evolution_running",
                "current_generation": gen + global_gen_offset,
                "best_score": best_score,
                "avg_score": avg_score,
            })

            # DB 的 current_generation 用全局偏移（连续进化跨种群累计）
            global_gen = gen + global_gen_offset
            from core.persistence.repository import evolution as _repo
            _repo.update_current_generation(self.db_path, task_id, global_gen)

            # 由种群计算代际诊断（平均交易数/表型多样性）
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

            # 历史记录落库（附诊断摘要）
            top3_summary = f"best={best_score:.1f}|pop={self._population_count + 1}"
            if gen_diag:
                top3_summary += f"|diag={json.dumps(gen_diag)}"
            save_history(self.db_path, task_id, global_gen, best_score, avg_score, top3_summary)

            # 逐代快照落库
            if hasattr(engine, '_population') and engine._population:
                try:
                    # 当代种群首位的个体即最优（引擎已排序）
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

            # 冠军更新经 ChampionTracker 原子判定（只在刷新纪录时落库）
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
                        from core.persistence.repository import evolution as _repo
                        champion_rec = champion_tracker.get_champion()
                        _repo.update_champion_full(
                            self.db_path, task_id, best_score=champion_rec.score,
                            champion_metrics=json.dumps(champion_rec.metrics),
                            champion_dimension_scores=json.dumps(champion_rec.dimension_scores),
                        )
                elif best_score > 0:
                    # 无诊断但分数为正——仅更新 best_score
                    updated = champion_tracker.update(score=best_score, generation=global_gen)
                    if updated:
                        from core.persistence.repository import evolution as _repo
                        _repo.update_best_score_if_higher(self.db_path, task_id, best_score)

            # 达标策略自动抽取入策略库（签名去重）
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

            # 代际完成推送（附冠军 DNA）
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
            # 达标数取自种群诊断
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

        # 连续进化循环：种群收敛/达标后自动开新种群扩域搜索，
        # 直到用户手动停止或出错
        continuous = bool(task_row.get("continuous", 1))

        while continuous and stop_reason not in ("error",):
            controller.check_stop()

            self._population_count += 1

            logger.info(
                "Task %s: starting population #%d (stop_reason=%s)",
                task_id, self._population_count + 1, stop_reason,
            )

            # 新种群开跑即推送前端
            _push_ws(task_id, {
                "type": "population_started",
                "task_id": task_id,
                "population_count": self._population_count + 1,
                "best_score_ever": result.get("champion_score", 0) if result else 0,
                "total_generations_so_far": global_gen_offset,
            })

            # 新种群重置并扩大搜索域：注入多元策略模板避免重复搜索同域
            extra_ancestors = []
            if champion is not None:
                dna = champion
                dna.mutation_ops = []

            # 随机注入 2 个策略模板作种子（趋势/动量/均值回归等）——
            # 把搜索域扩展到冠军策略区域之外
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

            # 收集上一种群签名供去重——必须在 _population 置 None 前完成
            if hasattr(engine, '_population') and engine._population:
                for ind in engine._population:
                    discovered_signatures.add(ind.gene_signature)
                # 顶部精英保留为下一种群祖先
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
            # 累计全局代偏移（防跨种群历史覆盖）
            global_gen_offset += result["total_generations"]

        # 冠军落库（completed 前态守护见下）
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

            # 最终冠军指标取自 tracker（与代际更新一致的快照源）
            champion_rec = champion_tracker.get_champion()
            if champion_rec and champion_rec.metrics:
                from core.persistence.repository import evolution as _repo
                _repo.update_champion_metrics(
                    self.db_path, task_id,
                    best_fitness=champion_rec.score,
                    champion_metrics=json.dumps(champion_rec.metrics),
                    champion_dimension_scores=json.dumps(champion_rec.dimension_scores),
                )

            # 样本外验证（测试切分区）
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

                # 样本外结果落库
                from core.persistence.repository import evolution as _repo
                _repo.update_oos_results(
                    self.db_path, task_id,
                    oos_fitness=oos_fitness, oos_qualified=int(oos_qualified),
                    oos_metrics=json.dumps(oos_metrics),
                )

        else:
            update_task(
                self.db_path, task_id,
                status="completed",
                stop_reason=stop_reason,
                expected_statuses=("running",),
            )

        # 收尾推送
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
        """由任务配置构造 RequirementsConfig（委托 core.evolution.evaluation）。"""
        return evaluation.build_requirements(task_row)

    def _evaluate_dna(self, individual: StrategyDNA, task_row: Dict[str, Any],
                       leverage: int = 1, direction: str = "long",
                       enhanced_df=None, dfs_by_timeframe=None) -> dict:
        """回测给 DNA 打分（委托 core.evolution.evaluation）。"""
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
        """种群批评估（委托 core.evolution.evaluation）。"""
        return evaluation.evaluate_population(
            population, task_row, getattr(self, "data_dir", None), leverage, direction,
            enhanced_df=enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
        )


