"""研究计算线程池执行器（add-ai-research-mcp 1.4）。

定位（D3/D11）：分析模板与模型 fit/predict 是 CPU 密集计算，在独立
线程池执行器中运行，不占 API 事件循环——验收绑定"长计算期间交易
端点保持响应"。每任务设超时上限；预估 <1s 的轻量统计走同步快路径。

超时语义：CPU 密集任务无法被线程强杀，超时=守护扫描将句柄置
failed(reason=timeout)（先到者胜：之后跑完的回调不覆盖终态）。
模板侧配合 stop_check 协作式提前退出（工程 stop_check 先例）。

中断回收：进程重启后 analysis_tasks 里的 running 行是孤儿，
recover_stale_analysis_tasks 在 API 启动时置 failed(reason=interrupted)
（照 recover_stale_trading_tasks 先例）。

结构化拒绝：模板/装载层抛的 ResearchError 在无 422 通道的路径（线程池
heavy、快路径竞态）落 failed 时，reason 前缀 "[code] "——轮询方可解析
错误码，与快路径 422 的结构化语义对齐。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)

# 轻量快路径阈值（秒）：预估低于此值同步执行，不落任务句柄
FAST_PATH_THRESHOLD_S = 1.0

# 超时守护扫描间隔（秒）
_WATCH_INTERVAL_S = 1.0


class ResearchExecutor:
    """研究长任务线程池执行器。

    生命周期跟随 API 进程（模块级单例经 :func:`get_executor` 获取）；
    SIGTERM 优雅停止时调 :meth:`shutdown`。
    """

    def __init__(
        self,
        db_path: Path,
        *,
        max_workers: int = 2,
        default_timeout_s: float = 300.0,
    ) -> None:
        self._db_path = Path(db_path)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="research")
        self._default_timeout_s = default_timeout_s
        # task_id -> (future, deadline)——在途句柄与超时线（完成即遗忘）
        self._running: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._watch_stop = threading.Event()
        self._watcher = threading.Thread(
            target=self._watch_loop, name="research-watchdog", daemon=True)
        self._watcher.start()

    # ------------------------------------------------------------ 提交

    def submit(
        self,
        template: str,
        fn: Callable[[Callable[[], None]], Any],
        *,
        task_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        params_json: str = "{}",
        estimated_cost_s: float = FAST_PATH_THRESHOLD_S,
    ) -> Dict[str, Any]:
        """提交一个研究计算。

        Args:
            template: 模板名（analysis_tasks.template）。
            fn: 计算函数，签名 fn(stop_check) -> result（result 须可 str 化）。
            estimated_cost_s: 调用方预估耗时；低于快路径阈值则同步执行。

        Returns:
            任务视图 dict：快路径直接含 status 终态；异步含句柄 task_id
            供轮询（GET analysis/tasks/{id} 语义）。
        """
        task_id = task_id or f"ant_{uuid4().hex[:12]}"
        timeout = timeout_s or self._default_timeout_s

        if estimated_cost_s < FAST_PATH_THRESHOLD_S:
            # 快路径：同步执行，不落句柄（轻量统计不值得一次库写+轮询）。
            # 卡5：失败改 raise 直传调用方既有结构化通道（ResearchError
            # 原样/其余转 RESEARCH_TEMPLATE_INTERNAL 500）——原伪 task
            # dict 不落库、轮询必 404；本分支生产恒不可达（两消费方
            # 强制 10.0 > 阈值 1.0），形制对齐防未来阈值调整复活伪 task
            try:
                result = fn(_no_stop)
                return {"task_id": task_id, "status": "succeeded",
                        "result": result, "fast_path": True}
            except ResearchError:
                logger.warning("快路径执行拒绝 %s", template)
                raise
            except Exception as exc:  # noqa: BLE001 —— 内部失败转结构化 500
                logger.warning("快路径执行失败 %s: %s", template, exc)
                raise ResearchError(
                    "RESEARCH_TEMPLATE_INTERNAL",
                    f"快路径执行失败: {type(exc).__name__}",
                    status=500,
                    detail={"template": template,
                            "exception_type": type(exc).__name__,
                            "summary": str(exc)[:200]}) from exc

        rr.save_analysis_task(self._db_path, task_id=task_id,
                              template=template, params=params_json)
        deadline = time.monotonic() + timeout
        future = self._pool.submit(self._run_one, task_id, fn, deadline)
        with self._lock:
            self._running[task_id] = (future, deadline)
        future.add_done_callback(lambda _f: self._forget(task_id))
        return {"task_id": task_id, "status": "pending",
                "result": None, "fast_path": False}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """任务轮询入口：直查 analysis_tasks 库表（快路径结果经提交
        响应直返不落句柄，轮询语义只覆盖异步路径）。"""
        return rr.get_analysis_task(self._db_path, task_id)

    def shutdown(self, wait: bool = False) -> None:
        """优雅停止（SIGTERM）：停守护扫描，不再等待在途计算。"""
        self._watch_stop.set()
        self._pool.shutdown(wait=wait)

    # ------------------------------------------------------------ 内部

    def _run_one(
        self,
        task_id: str,
        fn: Callable[[Callable[[], None]], Any],
        deadline: float,
    ) -> Any:
        rr.update_analysis_task(self._db_path, task_id=task_id, status="running")

        def _stop_if_expired() -> None:
            if time.monotonic() > deadline:
                raise _TaskTimedOut()

        try:
            result = fn(_stop_if_expired)
        except _TaskTimedOut:
            self._finalize(task_id, "failed", reason="timeout", result_ref=None)
            return None
        except ResearchError as exc:
            # 结构化拒绝：ResearchError 的 str() 只有 message，code 显式
            # 前缀进 reason——heavy 路径无 422 通道，轮询方靠它拿错误码
            logger.warning("研究任务拒绝 %s: [%s] %s",
                           task_id, exc.code, exc.message)
            self._finalize(task_id, "failed",
                           reason=f"[{exc.code}] {exc.message}"[:200],
                           result_ref=None)
            return None
        except Exception as exc:  # noqa: BLE001 —— 单任务失败不拖垮池
            logger.warning("研究任务失败 %s: %s", task_id, exc)
            self._finalize(task_id, "failed", reason=str(exc)[:200], result_ref=None)
            return None
        self._finalize(task_id, "succeeded", reason=None,
                       result_ref=str(result)[:200000])
        return result

    def _finalize(
        self, task_id: str, status: str,
        *, reason: Optional[str], result_ref: Optional[str],
    ) -> None:
        """写终态（先到者胜：守护已置 failed(timeout) 时不覆盖）。"""
        current = rr.get_analysis_task(self._db_path, task_id)
        if current is None:
            return
        if current["status"] in ("succeeded", "failed"):
            return  # 守护超时判定先行，本结果作废
        rr.update_analysis_task(
            self._db_path, task_id=task_id,
            status=status, reason=reason, result_ref=result_ref)

    def _forget(self, task_id: str) -> None:
        with self._lock:
            self._running.pop(task_id, None)

    def _watch_loop(self) -> None:
        """超时守护：扫内存 running 表，超 deadline 的句柄置 failed(timeout)。"""
        while not self._watch_stop.wait(_WATCH_INTERVAL_S):
            now = time.monotonic()
            with self._lock:
                expired = [tid for tid, (_, dl) in self._running.items() if now > dl]
            for tid in expired:
                current = rr.get_analysis_task(self._db_path, tid)
                if current is not None and current["status"] == "running":
                    rr.update_analysis_task(
                        self._db_path, task_id=tid,
                        status="failed", reason="timeout")
                    logger.info("研究任务超时标记: %s", tid)


class _TaskTimedOut(Exception):
    """协作式超时信号（stop_check 抛出，由 _run_one 归一为 failed(timeout)）。"""


def _no_stop() -> None:
    """快路径 stop_check：永不停止。"""


# --------------------------------------------------------------- 模块级单例

_EXECUTOR: Optional[ResearchExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def init_executor(db_path: Path, **kwargs: Any) -> ResearchExecutor:
    """初始化进程级执行器（API 启动时调用；先回收中断任务）。"""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ResearchExecutor(db_path, **kwargs)
        return _EXECUTOR


def get_executor() -> ResearchExecutor:
    if _EXECUTOR is None:
        raise RuntimeError("执行器未初始化（API 启动时应调 init_executor）")
    return _EXECUTOR


def recover_stale_analysis_tasks(db_path: Path) -> int:
    """API 启动时回收孤儿 running 任务 → failed(reason=interrupted)。

    进程重启后内存态丢失，库表中的 running 行是上次生命周期的遗骸。
    Returns:
        回收行数。
    """
    from core.persistence.timestamps import utc_now_iso

    stale = rr.list_stale_running_tasks(db_path, created_before=utc_now_iso())
    for task in stale:
        rr.update_analysis_task(
            db_path, task_id=task["task_id"],
            status="failed", reason="interrupted")
    if stale:
        logger.info("回收中断研究任务 %d 个", len(stale))
    return len(stale)


def ensure_research_tables(db_path: Path) -> None:
    """幂等自愈：研究域八表缺失时按 ORM 声明补建（仅建不存在的表）。

    定位澄清：schema 演进唯一轨仍是 Alembic（列变更走迁移，autogenerate
    零漂移有测试锁定）；本函数只为首次部署/测试库兜底——init_db_ext
    只建 legacy 表，研究域表经 ``alembic upgrade head`` 或本函数到位，
    两条路径的 DDL 同源于 ORM 声明。
    """
    from core.persistence.orm.models import (
        AnalysisTask,
        FeatureRequest,
        FormulaPlugin,
        ModelPlugin,
        ResearchHypothesis,
        ResearchLog,
        SignalRegistryEntry,
        ValidationRecord,
    )
    from core.persistence.repository.dataset_meta import _engine_for

    tables = [
        ResearchHypothesis.__table__, ResearchLog.__table__,
        AnalysisTask.__table__, FormulaPlugin.__table__,
        ModelPlugin.__table__, SignalRegistryEntry.__table__,
        FeatureRequest.__table__, ValidationRecord.__table__,
    ]
    from core.persistence.orm import Base

    Base.metadata.create_all(_engine_for(db_path), tables=tables)
