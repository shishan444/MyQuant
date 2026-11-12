"""终态冠军入库双出口契约（fix-evolution-champion-persist）。

M1 双出口不变式：completed 出口入库先于 completed 终态写入；user_stop
出口经快照恢复链（状态先行→快照 top1 恢复→补写 champion_dna→入库）。
M2 失败例外：入库/恢复链失败 → stopped+champion_persist_failed+error
日志（不静默），champion_dna 保留程度取决于失败点（completed 照写恒
非空；user_stop 恢复成功后失败=非空、恢复链早期失败=空）。M3 空边界：
0 分冠军也是合法冠军必入库。M4 挂盘分派锚落 tests/paper_trading/ 既有族
（本文件不承载）。

判别式（design D6）第 1（含 0 分子面）/2/3/4/5 行行为由本文件承载；第 6 行
(stopped, error) 无保证不测。形制对齐 tests/evolution/test_evolution_runner_
happy_path.py（tmp_db + stub 评估注入）。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import get_task, save_task  # noqa: E402
from core.persistence.db_ext import init_db_ext  # noqa: E402
from core.strategy.dna import StrategyDNA  # noqa: E402
from tests.helpers.data_factory import make_dna, make_ohlcv  # noqa: E402


def _stub_score(dna) -> float:
    """确定性 score（驱动编排流程；回测正确性由 L0-B 守护）。"""
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)


def _make_task(db, task_id: str, **overrides) -> dict:
    """建任务行并返回可变 task_row（与既有 happy path 测试同形制）。"""
    dna = make_dna(indicator="RSI")
    save_task(db, task_id=task_id, target_score=80.0, template="profit_first",
              symbol="BTCUSDT", timeframe="4h", initial_dna=dna)
    row = get_task(db, task_id)
    assert row is not None
    task_row = dict(row)
    task_row.update(overrides)
    return task_row


class TestCompletedExitPersist:
    """M1：completed 出口冠军入库不变式（判别式第 1 行）。"""

    def test_completed_champion_in_library(self, tmp_path):
        from core.features.indicators import compute_all_indicators
        from core.persistence.repository.strategy import get_strategy

        db = tmp_path / "cp11.db"
        init_db_ext(db)
        data_dir = tmp_path / "data11"; data_dir.mkdir()

        task_row = _make_task(db, "cp-11")
        task_row["max_generations"] = 2
        task_row["continuous"] = 0
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))

        from api.runner import EvolutionRunner
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [_stub_score(d) for d in pop]):
            runner._run_task(task_row)

        row = get_task(db, "cp-11")
        assert row["status"] == "completed", \
            f"任务未完成: {row['status']}/{row.get('stop_reason')}"
        assert row["champion_dna"], "completed 冠军 champion_dna 必落库"

        champion = StrategyDNA.from_json(row["champion_dna"])
        lib_row = get_strategy(db, champion.strategy_id)
        assert lib_row is not None, "冠军 strategy_id 在策略库查无此策（断裂核心锚）"
        assert lib_row["source"] == "evolution"
        assert lib_row["source_task_id"] == "cp-11"
        assert lib_row["status"] == "active"
        assert lib_row["best_score"] == pytest.approx(_stub_score(champion), rel=1e-9)

    def test_zero_score_champion_persisted(self, tmp_path):
        """判别式第 1 行「含 0 分」子面：0 分冠军也是合法冠军必入库。

        champion_fitness 初始 -1.0，best_fitness > -1 即记录——全种群
        0 分时冠军=当代首位个体（engine.py:347 严格大于判定）。
        """
        from core.features.indicators import compute_all_indicators
        from core.persistence.repository.strategy import get_strategy

        db = tmp_path / "cp-z0.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataz0"; data_dir.mkdir()

        task_row = _make_task(db, "cp-z0")
        task_row["max_generations"] = 2
        task_row["continuous"] = 0
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))

        from api.runner import EvolutionRunner
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [0.0 for _ in pop]):
            runner._run_task(task_row)

        row = get_task(db, "cp-z0")
        assert row["status"] == "completed", \
            f"0 分冠军任务照常完成: {row['status']}/{row.get('stop_reason')}"
        assert row["champion_dna"], "0 分冠军也是合法冠军（>-1 即记录）"

        champion = StrategyDNA.from_json(row["champion_dna"])
        lib_row = get_strategy(db, champion.strategy_id)
        assert lib_row is not None, "0 分冠军必入库（判别式第 1 行含 0 分）"
        assert lib_row["best_score"] == 0.0
        assert lib_row["status"] == "active"


class TestCompletedNoChampion:
    """M3 空边界（判别式第 2 行）：completed 无冠军 → 无义务。"""

    def test_no_champion_completed_no_obligation(self, tmp_path):
        """全代 best_fitness <= -1.0 → champion=None → 照常 completed、零入库。

        构造依据 engine.py:347-349：best_fitness 严格大于 champion_fitness
        （初始 -1.0）才记录——全负分时冠军恒 None，runner 走 855 else
        分支直写 completed（无 champion_dna、无入库动作）。
        """
        from core.features.indicators import compute_all_indicators
        from core.persistence.repository.strategy import list_strategies

        db = tmp_path / "cp-nc.db"
        init_db_ext(db)
        data_dir = tmp_path / "datanc"; data_dir.mkdir()

        task_row = _make_task(db, "cp-nc")
        task_row["max_generations"] = 2
        task_row["continuous"] = 0
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))

        from api.runner import EvolutionRunner
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [-10.0 for _ in pop]):
            runner._run_task(task_row)

        row = get_task(db, "cp-nc")
        assert row["status"] == "completed", \
            f"无冠军任务照常完成: {row['status']}/{row.get('stop_reason')}"
        assert row["stop_reason"] == "max_generations"
        assert not row["champion_dna"], "无冠军：champion_dna 无义务（保持空）"
        assert not list_strategies(db, source_task_id="cp-nc"), "无冠军零入库"


class TestCompletedFailures:
    """M2：completed 出口入库失败形制（判别式第 5 行 completed 部分）。"""

    def test_persist_failure_turns_stopped_with_dna_kept(self, tmp_path):
        """save_strategy 注入失败 → champion_persist_failed+champion_dna 照写。"""
        from api.runner import EvolutionRunner
        from core.features.indicators import compute_all_indicators

        db = tmp_path / "cp-f1.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataf1"; data_dir.mkdir()

        task_row = _make_task(db, "cp-f1")
        task_row["continuous"] = 0
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        ws_calls: list = []

        def _ws_recorder(task_id, payload):
            ws_calls.append((task_id, payload))

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws", side_effect=_ws_recorder), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [_stub_score(d) for d in pop]), \
             patch("core.persistence.repository.strategy.save_strategy",
                   side_effect=RuntimeError("injected persist failure")):
            runner._run_task(task_row)

        row = get_task(db, "cp-f1")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "champion_persist_failed"
        assert row["champion_dna"], "completed 出口失败：champion_dna 恒照写（判别式第 5 行）"

        from core.persistence.repository.strategy import list_strategies
        rows = list_strategies(db, source_task_id="cp-f1")
        assert not rows, "入库失败库无行（唯一合法例外）"

        # 推送缺席锚：失败分支提前 return——evolution_complete 不出现
        assert not any(
            c[1].get("type") == "evolution_complete" for c in ws_calls
        ), "失败分支必须提前 return（evolution_complete 推送缺席）"
        assert ws_calls, "非收尾推送（task_started 等）照常"

    def test_persist_failure_logs_structured_error(self, tmp_path, caplog):
        """error 级结构化日志在案（task_id/strategy_id/异常链）。"""
        from api.runner import EvolutionRunner
        from core.features.indicators import compute_all_indicators

        db = tmp_path / "cp-f2.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataf2"; data_dir.mkdir()

        task_row = _make_task(db, "cp-f2")
        task_row["continuous"] = 0
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch("core.persistence.repository.strategy.save_strategy",
                   side_effect=RuntimeError("injected persist failure")):
            runner._run_task(task_row)

        assert "champion persist failed" in caplog.text, "error 日志在案"
        assert "cp-f2" in caplog.text, "日志含 task_id"
        assert "strategy_id=" in caplog.text, "日志含 strategy_id"


class TestUserStopExitRestore:
    """M1/M2：user_stop 出口快照恢复链（判别式第 3/4/5 行 user_stop 部分）。"""

    def test_user_stop_with_snapshot_restored_and_persisted(self, tmp_path):
        """判别式第 3 行：有快照 → champion_dna 补写+库行与快照 top1 对账。"""
        from api.runner import EvolutionRunner, TaskStopRequested
        from core.features.indicators import compute_all_indicators

        db = tmp_path / "cp-us1.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataus1"; data_dir.mkdir()

        task_row = _make_task(db, "cp-us1")
        task_row["max_generations"] = 2
        task_row["continuous"] = 0
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        call_count = 0

        def eval_with_stop_at_gen2(pop, *a, **kw):
            # 代 1 评估照常（快照落库），代 2 评估入口抛 stop（engine 批量
            # 路径 stop_check() 在 evaluate_population 之前——无 except 包
            # 裹直上抛 _run_task except 分支）
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise TaskStopRequested()
            return [_stub_score(d) for d in pop]

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population", eval_with_stop_at_gen2):
            runner._run_task(task_row)

        row = get_task(db, "cp-us1")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "user_stop"
        assert row["champion_dna"], "user_stop 有快照：champion_dna 必补写"

        from core.persistence.repository.evolution import list_top_snapshots
        from core.persistence.repository.strategy import get_strategy
        snaps = list_top_snapshots(db, "cp-us1", limit=1)
        assert snaps, "有快照前提"
        champion = StrategyDNA.from_json(row["champion_dna"])
        lib_row = get_strategy(db, champion.strategy_id)
        assert lib_row is not None
        assert lib_row["best_score"] == pytest.approx(snaps[0]["best_score"], rel=1e-9)
        assert lib_row["generation"] == snaps[0]["generation"]
        assert lib_row["source"] == "evolution"
        assert lib_row["source_task_id"] == "cp-us1"
        assert lib_row.get("metrics_json") is None


    def test_user_stop_without_snapshot_no_obligation(self, tmp_path):
        """判别式第 4 行：无快照 → 无义务（champion_dna 保持空）。"""
        from api.runner import EvolutionRunner, TaskStopRequested
        from core.features.indicators import compute_all_indicators

        db = tmp_path / "cp-us2.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataus2"; data_dir.mkdir()
        df = make_ohlcv(120, "4h")
        df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        task_row = _make_task(db, "cp-us2")
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)

        # stop 在数据加载段触发（快照表零行）——对齐 test_stop_check.py 形制：
        # 指标计算的 stop_check 检查序列第 5 次触发停止
        import core.features.indicators as indicators_mod
        original_compute = indicators_mod.compute_all_indicators
        calls = {"n": 0}

        def compute_with_stop(*args, **kwargs):
            inner_stop = kwargs.get("stop_check")

            def stop_at_5th():
                calls["n"] += 1
                if calls["n"] >= 5:
                    raise TaskStopRequested()
                if inner_stop:
                    inner_stop()

            return original_compute(*args, stop_check=stop_at_5th)

        with patch.object(indicators_mod, "compute_all_indicators",
                          side_effect=compute_with_stop):
            runner._run_task(task_row)

        row = get_task(db, "cp-us2")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "user_stop"
        assert not row["champion_dna"], "无快照 user_stop 无义务补写"
        from core.persistence.repository.strategy import list_strategies
        assert not list_strategies(db, source_task_id="cp-us2"), "库零新增"


    def test_user_stop_restore_failure_early(self, tmp_path):
        """判别式第 5 行 user_stop 早期失败：champion_dna 保持空。"""
        from api.runner import EvolutionRunner, TaskStopRequested
        from core.features.indicators import compute_all_indicators

        db = tmp_path / "cp-us3.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataus3"; data_dir.mkdir()

        task_row = _make_task(db, "cp-us3")
        task_row["max_generations"] = 2
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        call_count = 0

        def eval_with_stop_at_gen2(pop, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise TaskStopRequested()
            return [_stub_score(d) for d in pop]

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population", eval_with_stop_at_gen2), \
             patch("core.persistence.repository.evolution.list_top_snapshots",
                   side_effect=RuntimeError("injected snapshot read failure")):
            runner._run_task(task_row)

        row = get_task(db, "cp-us3")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "champion_persist_failed"
        assert not row["champion_dna"], "恢复链早期失败：champion_dna 保持空"


    def test_user_stop_restore_then_persist_failure_keeps_dna(self, tmp_path):
        """判别式第 5 行 user_stop 恢复成功后失败：champion_dna 保留非空。"""
        from api.runner import EvolutionRunner, TaskStopRequested
        from core.features.indicators import compute_all_indicators

        db = tmp_path / "cp-us4.db"
        init_db_ext(db)
        data_dir = tmp_path / "dataus4"; data_dir.mkdir()

        task_row = _make_task(db, "cp-us4")
        task_row["max_generations"] = 2
        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        runner = EvolutionRunner(db_path=db, data_dir=data_dir)
        call_count = 0

        def eval_with_stop_at_gen2(pop, *a, **kw):
            # engine 批量路径 stop_check 先行，TaskStopRequested 直上抛
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise TaskStopRequested()
            return [_stub_score(d) for d in pop]

        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population", eval_with_stop_at_gen2), \
             patch("core.persistence.repository.strategy.save_strategy",
                   side_effect=RuntimeError("injected persist failure")):
            runner._run_task(task_row)

        row = get_task(db, "cp-us4")
        assert row["status"] == "stopped"
        assert row["stop_reason"] == "champion_persist_failed"
        assert row["champion_dna"], "恢复成功后失败：champion_dna 保留非空"

        from core.persistence.repository.strategy import list_strategies
        assert not list_strategies(db, source_task_id="cp-us4"), \
            "user_stop 恢复后入库失败：库无行（不可查，唯一合法例外）"
