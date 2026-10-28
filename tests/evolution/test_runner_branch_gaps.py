"""runner 执行链分支盲区消解（阶段四闸门第 2 步）。

盲区来源（coverage_gate2.json 交集+文件级判据）：
- invalid_dna 分支（runner.py:339-342）
- calmar 缺省抬升分支（:351/:353）
- 多周期 tf_pool 装载分支（:397）
- gen_diag 诊断段（:468-481 分支——替身须设 _eval_diagnostics）
- 达标抽取失败降级（:574-575）
- OOS 样本外验证段（200 bar test>=50——:687-734）

不可达判定（本文件锚定，带调用图依据）：
- evaluate_fn 闭包 dict 处理段（:421-428）：runner._execute_task 恒传
  evaluate_population → engine.py:313-332 分派逻辑走批评估路径，
  evaluate_fn 仅在批评估为 None 的 else 分支调用（engine.py:332）
  ——本仓库 runner 调用图内不可达。
- champion is None 族（:647-653/:666-672 尾分支/:734）：引擎在种群
  评估完成即置 champion（engine.py:347-349），仅当代评估整体失败
  （抛错上抛）才会缺 champion——该路径经 _run_task except Exception
  提前收束，不进入终态落库段。
- continuous 循环 error 退出分支（:589-595 stop_reason=error 变体）：
  引擎 stop_reason=error 需真实评估故障（替身域外）。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import get_task, save_task
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_dna, make_ohlcv


def _stub_score(dna) -> float:
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)


class TestRunnerBranchGaps:
    def _mk_runner(self, tmp_path):
        from api.runner import EvolutionRunner
        db = tmp_path / "bg.db"
        init_db_ext(db)
        return db, EvolutionRunner(db_path=db, data_dir=tmp_path,
                                   poll_interval=0.05)

    def _run(self, runner, db, task_id, row_overrides=None,
             qualified=False, with_diag=False, scores=None,
             save_strategy_effect=None):
        """直调 _run_task 的受控执行（沿 happy path 形制）。"""
        from core.features.indicators import compute_all_indicators

        save_task(db, task_id=task_id, target_score=1.0,
                  template="profit_first", symbol="BTCUSDT",
                  timeframe="4h", initial_dna=make_dna())
        conn = sqlite3.connect(db)
        sets = {"max_generations": 2, "continuous": 0}
        sets.update(row_overrides or {})
        for k, v in sets.items():
            conn.execute(
                f"UPDATE evolution_task SET {k} = ? WHERE task_id = ?",
                (v, task_id))
        conn.commit()
        conn.close()

        def fake_pop(pop, *a, **kw):
            for d in pop:
                if with_diag:
                    d._eval_diagnostics = {
                        "total_trades": 3, "raw_metrics": {"pf": 1.5},
                        "dimension_scores": {"a": 1},
                        "qualified": qualified,
                        "fitness": _stub_score(d), "score": _stub_score(d)}
                # 无诊断时不 setattr（贴近真实评估器——显式设 None
                # 在 mtf 装载路径触发下游 AttributeError，实证在案）
            return [scores(i) if scores else _stub_score(d)
                    for i, d in enumerate(pop)]

        fake_dna = (lambda ind, *a, **kw: {
            "score": _stub_score(ind), "fitness": _stub_score(ind),
            "qualified": qualified, "raw_metrics": {"pf": 1.5},
            "dimension_scores": {}, "total_trades": 3})

        enhanced = compute_all_indicators(make_ohlcv(200, "4h"))
        patches = [
            patch("core.data.mtf_loader.load_and_prepare_df",
                  return_value=enhanced),
            patch("api.runner_progress._push_ws"),
            patch.object(runner, "_evaluate_population", fake_pop),
            patch.object(runner, "_evaluate_dna", fake_dna),
        ]
        if save_strategy_effect is not None:
            patches.append(patch(
                "core.persistence.repository.strategy.save_strategy",
                side_effect=save_strategy_effect))
        for p in patches:
            p.start()
        try:
            runner._run_task(dict(get_task(db, task_id)))
        finally:
            for p in patches:
                p.stop()

    def test_invalid_dna_stops_task(self, tmp_path):
        """invalid_dna 分支：initial_dna 非法 JSON→stopped/invalid_dna。"""
        from api.runner import EvolutionRunner
        db = tmp_path / "inv.db"
        init_db_ext(db)
        runner = EvolutionRunner(db_path=db, data_dir=tmp_path)
        save_task(db, "inv-1", 1.0, "profit_first", "BTCUSDT", "4h",
                  make_dna())
        conn = sqlite3.connect(db)
        conn.execute("UPDATE evolution_task SET initial_dna = 'not-json', "
                     "continuous = 0, max_generations = 1 "
                     "WHERE task_id = 'inv-1'")
        conn.commit()
        conn.close()
        with patch("api.runner_progress._push_ws"):
            runner._run_task(dict(get_task(db, "inv-1")))
        row = get_task(db, "inv-1")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "invalid_dna"

    def test_calmar_default_target_and_diag_and_oos(self, tmp_path):
        """calmar 缺省抬升+诊断段+OOS 段贯通：requirements_json 的
        objective=calmar 且 target=1.0→2.0；个体带诊断→gen_diag 段；
        200 bar→test>=50→OOS 验证执行。"""
        db, runner = self._mk_runner(tmp_path)
        self._run(runner, db, "cal-1",
                  row_overrides={
                      "requirements_json": '{"objective": "calmar"}'},
                  qualified=True, with_diag=True)
        row = get_task(db, "cal-1")
        assert row["status"] == "completed"
        assert row["oos_fitness"] is not None, (
            "OOS 段应执行（200 bar test=60>=50）")

    def test_mtf_pool_multi_tf_loads(self, tmp_path):
        """多周期 tf_pool 分支：timeframe_pool 两 TF（JSON 数组形制）
        →load_mtf_data 装载分支执行（数据仅 4h——装载成功集退化为
        可用集，任务正常完成）。"""
        db, runner = self._mk_runner(tmp_path)
        self._run(runner, db, "mtf-1",
                  row_overrides={"timeframe_pool": '["4h", "1d"]',
                                 "max_generations": 1})
        row = get_task(db, "mtf-1")
        assert row["status"] == "completed"

    def test_strategy_extract_failure_degrades(self, tmp_path, caplog):
        """达标抽取失败降级：qualified 个体+save_strategy 抛错→debug
        日志+任务仍 completed（抽取非关键路径）。"""
        db, runner = self._mk_runner(tmp_path)
        self._run(runner, db, "ext-1", qualified=True, with_diag=True,
                  save_strategy_effect=RuntimeError("构造抽取失败"))
        row = get_task(db, "ext-1")
        assert row["status"] == "completed", "抽取失败不得中断任务"
