"""研究计算线程池执行器单测（add-ai-research-mcp 1.4）。

覆盖：快路径同步返回、异步句柄轮询至终态、协作式超时、守护超时标记
（先到者胜）、中断回收、执行器运行期间调用线程不被阻塞、ResearchError
结构化收尾（failed reason 前缀 [code]）。
"""

import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.executor import (
    ResearchExecutor,
    recover_stale_analysis_tasks,
)
from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "exec.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


def _wait_terminal(db: Path, task_id: str, timeout_s: float = 10.0) -> dict:
    """轮询辅助：等任务到终态。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        t = rr.get_analysis_task(db, task_id)
        if t is not None and t["status"] in ("succeeded", "failed"):
            return t
        time.sleep(0.05)
    pytest.fail(f"任务 {task_id} 未在 {timeout_s}s 内到终态")


class TestFastPath:
    def test_light_task_runs_sync(self, db_path: Path) -> None:
        """预估 <1s 的任务同步返回终态，不落句柄。"""
        ex = ResearchExecutor(db_path)
        try:
            out = ex.submit("light", lambda stop: 42, estimated_cost_s=0.1)
            assert out["status"] == "succeeded"
            assert out["result"] == 42
            assert out["fast_path"] is True
            assert rr.get_analysis_task(db_path, out["task_id"]) is None
        finally:
            ex.shutdown()

    def test_fast_path_failure(self, db_path: Path) -> None:
        ex = ResearchExecutor(db_path)
        try:
            def boom(stop):
                raise ValueError("x")
            out = ex.submit("light", boom, estimated_cost_s=0.1)
            assert out["status"] == "failed"
            assert "x" in out["reason"]
        finally:
            ex.shutdown()


class TestAsyncPath:
    def test_heavy_task_returns_handle_then_succeeds(self, db_path: Path) -> None:
        """重组任务立即返回句柄，可轮询至 succeeded。"""
        ex = ResearchExecutor(db_path)
        try:
            out = ex.submit(
                "heavy",
                lambda stop: "done",
                estimated_cost_s=5.0, timeout_s=10.0)
            assert out["status"] == "pending" and out["fast_path"] is False
            t = _wait_terminal(db_path, out["task_id"])
            assert t["status"] == "succeeded"
            assert t["result_ref"] == "done"
            assert t["finished_at"] is not None
        finally:
            ex.shutdown()

    def test_task_failure_recorded(self, db_path: Path) -> None:
        ex = ResearchExecutor(db_path)
        try:
            def boom(stop):
                raise RuntimeError("训练失败")
            out = ex.submit("heavy", boom, estimated_cost_s=5.0)
            t = _wait_terminal(db_path, out["task_id"])
            assert t["status"] == "failed"
            assert "训练失败" in t["reason"]
        finally:
            ex.shutdown()

    def test_caller_not_blocked(self, db_path: Path) -> None:
        """长计算占用 worker 期间，调用线程仍可提交并完成轻量任务。

        这是"计算压力不拖垮服务"的线程层证据（API 层绑定随 3.x 路由
        接入补真服务验证）。
        """
        ex = ResearchExecutor(db_path, max_workers=1)
        try:
            started = threading.Event()

            def long_task(stop):
                started.set()
                time.sleep(2.0)
                return "slow"

            def quick(stop):
                return "quick"

            ex.submit("heavy", long_task, estimated_cost_s=5.0)
            assert started.wait(2.0), "长任务未开始"

            wall = time.monotonic()
            out = ex.submit("light", quick, estimated_cost_s=0.1)
            elapsed = time.monotonic() - wall
            assert out["status"] == "succeeded"
            assert elapsed < 0.5, f"调用线程被阻塞 {elapsed:.2f}s"
        finally:
            ex.shutdown()


class TestResearchErrorStructured:
    """ResearchError 结构化收尾：failed reason 前缀 [code]——heavy 路径
    无 HTTP 422 通道，轮询方靠 reason 解析错误码（与快路径 422 对齐）。"""

    def test_heavy_research_error_reason_carries_code(
            self, db_path: Path) -> None:
        ex = ResearchExecutor(db_path)
        try:
            def reject(stop):
                raise ResearchError(
                    "RESEARCH_WARMUP_INSUFFICIENT",
                    "warmup 不足：需要 200 bar，实际 100 bar")
            out = ex.submit("heavy", reject, estimated_cost_s=5.0)
            t = _wait_terminal(db_path, out["task_id"])
            assert t["status"] == "failed"
            assert t["reason"].startswith("[RESEARCH_WARMUP_INSUFFICIENT]")
            assert "warmup 不足" in t["reason"]
        finally:
            ex.shutdown()

    def test_fast_path_research_error_reason_carries_code(
            self, db_path: Path) -> None:
        ex = ResearchExecutor(db_path)
        try:
            def reject(stop):
                raise ResearchError(
                    "RESEARCH_PARAM_INVALID", "forward_bars 须为正整数")
            out = ex.submit("light", reject, estimated_cost_s=0.1)
            assert out["status"] == "failed"
            assert out["reason"].startswith("[RESEARCH_PARAM_INVALID]")
            assert "forward_bars" in out["reason"]
        finally:
            ex.shutdown()

class TestTimeout:
    def test_cooperative_timeout_via_stop_check(self, db_path: Path) -> None:
        """模板配合 stop_check：超时后主动退出 → failed(timeout)。"""
        ex = ResearchExecutor(db_path)
        try:
            def loop(stop):
                for _ in range(100):
                    time.sleep(0.05)
                    stop()
                return "never"

            out = ex.submit("heavy", loop, estimated_cost_s=5.0, timeout_s=0.3)
            t = _wait_terminal(db_path, out["task_id"])
            assert t["status"] == "failed" and t["reason"] == "timeout"
        finally:
            ex.shutdown()

    def test_watchdog_marks_uncooperative_task(self, db_path: Path) -> None:
        """不配合 stop_check 的任务：守护扫描先置 failed(timeout)，
        之后跑完的结果不覆盖终态（先到者胜）。"""
        ex = ResearchExecutor(db_path, default_timeout_s=0.5)
        try:
            def stubborn(stop):
                time.sleep(1.5)  # 超过 deadline 且不调 stop
                return "late"

            out = ex.submit("heavy", stubborn, estimated_cost_s=5.0)
            t = _wait_terminal(db_path, out["task_id"])
            assert t["status"] == "failed" and t["reason"] == "timeout"
            # 等迟到的回调跑完，终态不被覆盖
            time.sleep(1.5)
            t2 = rr.get_analysis_task(db_path, out["task_id"])
            assert t2["status"] == "failed" and t2["reason"] == "timeout"
        finally:
            ex.shutdown(wait=True)


class TestRecovery:
    def test_recover_stale_running(self, db_path: Path) -> None:
        """API 启动回收：孤儿 running → failed(interrupted)。"""
        rr.save_analysis_task(db_path, task_id="ant_orphan", template="t", params="{}")
        rr.update_analysis_task(db_path, task_id="ant_orphan", status="running")

        n = recover_stale_analysis_tasks(db_path)
        assert n == 1
        t = rr.get_analysis_task(db_path, "ant_orphan")
        assert t["status"] == "failed" and t["reason"] == "interrupted"

        # 幂等：再次回收无新行
        assert recover_stale_analysis_tasks(db_path) == 0


class TestGapClosureExecutor:
    """阶段四盲区消解：单点分支直测。"""

    def test_get_task_直查库表_未知任务None(self, db_path: Path) -> None:
        from core.analysis.executor import ResearchExecutor
        ex = ResearchExecutor(db_path, max_workers=1)
        assert ex.get_task("no_such_task") is None

    def test_get_executor未初始化_RuntimeError(self, monkeypatch) -> None:
        from core.analysis import executor as exec_mod
        monkeypatch.setattr(exec_mod, "_EXECUTOR", None)
        with pytest.raises(RuntimeError, match="未初始化"):
            exec_mod.get_executor()
