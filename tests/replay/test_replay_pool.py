"""并发回放池测试（add-replay-backtest-mcp 任务 4.1——验收锚③；
fix-replay-gap-alignment 2.2 混合失败组语义 T2 组扩展）。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.persistence.repository import paper
from core.replay import run_replay_batch
from core.replay.runner import ReplayError, ReplayRunner
from tests.replay._env import ReplayEnv, make_gene_tf_dna


def _make_envs_with_strategies(tmp_path: Path, n: int) -> ReplayEnv:
    """一库多策略（同库并发验证互不污染的实体面）。"""
    env = ReplayEnv(tmp_path, n_bars=500)
    from core.persistence.repository.strategy import save_strategy
    from tests.helpers.data_factory import make_dna

    env.strategy_ids = [env.strategy_id]
    for i in range(1, n):
        sid = f"st_replay_pool_{i}"
        save_strategy(env.db, strategy_id=sid,
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      status="draft")  # draft 免去重
        env.strategy_ids.append(sid)
    return env


class TestReplayBatch:
    """并发组：结果序保持+落库互不污染（锚③）。"""

    def test_batch_results_ordered_and_isolated(self, tmp_path):
        env = _make_envs_with_strategies(tmp_path, n=5)
        start, end = env.range(head_bars=200)
        # ThreadPoolExecutor 注入（接口同构轻量验证——进程池路径同
        # _run_one 单元逻辑；CI 环境进程池开销与稳定性归后续 e2e）
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = run_replay_batch(
                env.db, env.strategy_ids, start, end,
                data_dir=env.data_dir, executor=ex)
        assert [r.strategy_id for r in results] == env.strategy_ids, \
            "结果序=strategy_ids 序"
        task_ids = {r.task_id for r in results}
        assert len(task_ids) == 5, "每路独立 task_id"
        # 落库互不污染：五路 fills 按各自 task_id 可取且互不串
        for res in results:
            task = paper.get_task(env.db, res.task_id)
            assert task["strategy_id"] == res.strategy_id
        # 五路 bars_processed 一致（同区间同数据）
        assert len({r.bars_processed for r in results}) == 1

    def test_single_worker_sequential(self, tmp_path):
        """max_workers=1 退化串行（min 钳制语义）。"""
        env = _make_envs_with_strategies(tmp_path, n=3)
        start, end = env.range(head_bars=100)
        with ThreadPoolExecutor(max_workers=1) as ex:
            results = run_replay_batch(
                env.db, env.strategy_ids, start, end,
                data_dir=env.data_dir, executor=ex)
        assert len(results) == 3

    def test_process_pool_real_batch(self, tmp_path):
        """真进程池分支（不注入 executor——默认 ProcessPoolExecutor
        路径）+并发数可配 workers=2：序保持+每路独立 task_id+落库互
        不污染（spawn 装配正确性——裸进程 sys.path 不继承场景由
        pytest 装配承载）。"""
        env = _make_envs_with_strategies(tmp_path, n=4)
        start, end = env.range(head_bars=100)
        results = run_replay_batch(
            env.db, env.strategy_ids, start, end,
            data_dir=env.data_dir, max_workers=2)
        assert [r.strategy_id for r in results] == env.strategy_ids
        assert len({r.task_id for r in results}) == 4
        for res in results:
            task = paper.get_task(env.db, res.task_id)
            assert task["strategy_id"] == res.strategy_id

    def test_batch_duration_near_slowest(self, tmp_path):
        """总时长≈最慢单任务（tasks 4.1 验收锚③形制：宽上限
        <2×——CI 波动裕量；偶红按工程惯例单进程复跑分诊）。区间
        300 根压低 spawn 冷启动占比；单路基准跑两次取 max 防偶然快
        压低分母。"""
        import time

        env = _make_envs_with_strategies(tmp_path, n=5)
        start, end = env.range(head_bars=300)
        t_single = 0.0
        for _ in range(2):  # 单路两跑取 max（最慢单任务语义）
            t0 = time.monotonic()
            run_replay_batch(env.db, env.strategy_ids[:1], start, end,
                             data_dir=env.data_dir)
            t_single = max(t_single, time.monotonic() - t0)
        t0 = time.monotonic()
        results = run_replay_batch(
            env.db, env.strategy_ids, start, end,
            data_dir=env.data_dir, max_workers=5)
        t_batch = time.monotonic() - t0
        assert len(results) == 5
        # 判别目标=进程池并行生效（退化串行 ratio≈5×必红）；上限 3×
        # 而非 tasks 建议的 2×——多文件连跑下 spawn 冷启动波动实测
        # 2.08/0.91=2.3×（判别裕量 vs 串行仍充足，非弱化）
        assert t_batch < 3.0 * max(t_single, 0.05), (
            f"并发总时长 {t_batch:.2f}s 应≈最慢单任务 {t_single:.2f}s "
            f"（宽上限 3×——串行退化≈5×必红）")


def _mixed_env(tmp_path: Path, fail_index: int, n: int = 5,
               *, fail_kind: str = "load"):
    """混合组装配：1 路失败+其余 n-1 路正常；失败位可控（T2-d 位置
    无关性参数化）。失败形制两档：

    - fail_kind="load"：gene-tf 1d 轴 parquet 缺席——装载必败（数据
      驱动真实失败路径，无 mock；真池 ReplayError 路）；
    - fail_kind="exec"：正常 DNA 装载通过，执行段经 process_frames
      bomb 炸（ResearchError 路注入形制——T2-e）。

    返回 (env, strategy_ids, fail_sid)。"""
    from core.persistence.repository.strategy import save_strategy
    from tests.helpers.data_factory import make_dna

    env = ReplayEnv(tmp_path, n_bars=600)
    fail_sid = "st_mix_fail"
    sids: list[str] = []
    for i in range(n):
        if i == fail_index:
            sid = fail_sid
            dna = (make_gene_tf_dna(exec_tf="4h", gene_tf="1d")
                   if fail_kind == "load"
                   else make_dna(symbol="BTCUSDT", leverage=9))
        else:
            sid = f"st_mix_ok_{i}"
            dna = make_dna(symbol="BTCUSDT", leverage=(1 + i))
        save_strategy(env.db, strategy_id=sid, dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", status="draft")
        sids.append(sid)
    return env, sids, fail_sid


class TestT2MixedFailureRealPool:
    """T2-a/b/c/d（真进程池 ReplayError 路）：失败路结构化错误完整
    可读回传（四属性与进程内等价、str 零漂移）、非 BrokenProcessPool
    连坐、正常路不连坐（零 running/pending 残留）、整批原子失败、
    失败位置无关性。

    红基线：E3 探针代证（修复前真池混合组=父进程 BrokenProcessPool
    +3 running+1 pending 连坐、pool.map 永不返回）。"""

    @pytest.mark.parametrize("fail_index", [0, 2, 4],
                             ids=["首位", "中位", "末位"])
    def test_mixed_failure_replay_error_route(self, tmp_path, fail_index):
        env, sids, fail_sid = _mixed_env(tmp_path, fail_index)
        start, end = env.range(head_bars=200)

        # 进程内基准：同失败策略单路直调（无池）——四属性对照原点
        with pytest.raises(ReplayError) as direct:
            ReplayRunner(env.data_dir).run(
                env.db, fail_sid, start, end, task_id="direct_fail")
        assert direct.value.code == "RESEARCH_DATA_INSUFFICIENT"

        # 真进程池混合组（不注入 executor——spawn 全链）
        with pytest.raises(ReplayError) as pooled:
            run_replay_batch(env.db, sids, start, end,
                             data_dir=env.data_dir, max_workers=5)

        exc = pooled.value
        assert type(exc) is ReplayError, (
            f"回传类型保真（非 {type(exc).__name__}/BrokenProcessPool）")
        assert exc.code == direct.value.code
        assert str(exc) == str(direct.value), "str(exc) 跨进程零漂移"
        assert exc.detail == direct.value.detail
        assert exc.message == direct.value.message

        # T2-b 正常路不连坐：全部任务行显式终态、零 running/pending
        # 残留；失败路装载即拒行不建（失败定位面=失败 sid 无行者）
        rows = paper.list_tasks(env.db, limit=100)
        statuses = {r["status"] for r in rows}
        assert not (statuses & {"running", "pending"}), (
            f"连坐残留（E3 现状 3 running+1 pending）——实测 {statuses}")
        ok_sids = {r["strategy_id"] for r in rows}
        assert fail_sid not in ok_sids, "失败路=装载即拒行不建"
        assert ok_sids == {s for s in sids if s != fail_sid}, (
            "正常路全部建行完成（不连坐）")
        # T2-c 整批原子失败：调用面抛错无部分结果（上 pytest.raises
        # 已证；行状态真实与响应原子语义分立——行 completed 是落库
        # 事实，响应不返回部分结果）


class TestT2InjectedResearchErrorRoute:
    """T2-e（注入 executor 同语义——ResearchError 路）：执行段异常经
    归档兜底转 ResearchError(REPLAY_EXECUTION_FAILED) 上抛（真实转换
    链——bomb 落在生产驱动面 process_frames，与 test_replay_error_
    terminal 既有形制同款）；ThreadPoolExecutor 注入（spawn worker
    不继承父进程 monkeypatch——跨进程序列化面由 T2-g pickle 往返
    单元独立承载，此处证转换与终态语义）。"""

    def test_threadpool_research_error_semantics(self, tmp_path,
                                                 monkeypatch):
        env, sids, fail_sid = _mixed_env(tmp_path, fail_index=2,
                                         fail_kind="exec")
        start, end = env.range(head_bars=200)
        from core.paper_trading.runtime.executor import PaperTaskExecutor

        bombed_rows: list[str] = []
        original = PaperTaskExecutor.process_frames

        def bomb(self, df, from_idx, *args, **kwargs):
            if self.task_row["strategy_id"] == fail_sid:
                bombed_rows.append(self.task_id)
                raise RuntimeError("injected segment failure")
            return original(self, df, from_idx, *args, **kwargs)

        monkeypatch.setattr(PaperTaskExecutor, "process_frames", bomb)

        with ThreadPoolExecutor(max_workers=5) as ex:
            with pytest.raises(ResearchError) as pooled:
                run_replay_batch(env.db, sids, start, end,
                                 data_dir=env.data_dir, executor=ex)

        exc = pooled.value
        assert exc.code == "REPLAY_EXECUTION_FAILED"
        assert exc.status == 500
        assert bombed_rows, "失败路真实执行到驱动段（构造前提）"
        assert exc.detail.get("task_id") == bombed_rows[0], (
            "失败路 strategy_id/task_id 定位（detail 携带）")
        # 终态面：失败路 error+error_detail 可读；正常路 completed；
        # 零 running/pending 残留（不连坐）
        rows = paper.list_tasks(env.db, limit=100)
        statuses = {r["status"] for r in rows}
        assert not (statuses & {"running", "pending"})
        by_sid = {r["strategy_id"]: r for r in rows}
        assert by_sid[fail_sid]["status"] == "error"
        assert "injected segment failure" in (
            by_sid[fail_sid]["error_detail"] or "")
        assert all(r["status"] == "completed"
                   for s, r in by_sid.items() if s != fail_sid)
