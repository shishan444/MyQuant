"""任务调度器（PaperScheduler——design §5.1，守护线程+五态状态机）。

形制：后台守护线程（poll_interval 2s 轮询；app.py lifespan 挂载——
新增挂载，旧 TradingRunner 挂载原位保留至组 7 切换，新旧端点族前缀
不同不暴露双入口）。

职责：
- 状态机迁移集单源（唯一写者=调度器/执行器协作，REST/MCP 仅发请求）；
- 拾取：活跃任务数 < MAX_RUNNING_TASKS 时按 created_at 顺序拾取
  pending（每任务一个工作线程+TaskController 协作取消——沿
  core/concurrency 既有基建）；
- 心跳移交：running 但心跳超时（>3×bar 周期）的任务移交恢复器
  （注入面——core/paper_trading/runtime/recovery.recover_task）；
- feed 驱动：活跃 feed 的 refresh（执行器订阅的共享作业单点刷新）；
- 终态回补例程（Q-2，tasks 3.5）：定期扫全部任务（含终态）的
  pending 资金费行，parquet 可达则回填（不动 balance）+触发报告
  修订版回调。

测试通道：run_once() 单轮同步执行（拾取/心跳/feed 刷新/回补四段），
不经守护线程与时钟。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.concurrency.task_control import TaskController
from core.data.timeframes import TF_SECONDS
from core.paper_trading.constants import (
    MAX_RUNNING_TASKS,
    POLL_INTERVAL_SECONDS,
)
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry
from core.persistence.repository import paper as paper_repo
from core.persistence.repository.paper import PT_STATUS_TRANSITIONS
from core.persistence.timestamps import utc_now_iso

logger = logging.getLogger("paper_runtime")

# 五态状态机合法迁移集（design §5.1；其余一律拒绝——写库前守门）。
# 数据源=仓库层 PT_STATUS_TRANSITIONS 派生视图（CAS 增补改引——权威
# 数据 PT_STATUS_PREDECESSORS 在仓库层定义，persistence 零域依赖方向
# 正、约束离表近；本模块仅消费派生 (from,to) 集，杜绝两处状态集漂移）
LEGAL_TRANSITIONS = PT_STATUS_TRANSITIONS

# 心跳超时系数（>3×bar 周期视为失联——design §5.1）
HEARTBEAT_TIMEOUT_MULTIPLIER = 3

# 未知周期兜底（沿 feed._DEFAULT_BAR_SECONDS 形制）
_DEFAULT_BAR_SECONDS = 14400


def can_transition(from_status: str, to_status: str) -> bool:
    """状态机迁移合法性（终态吸收——任意终态出发的迁移恒拒绝）。"""
    return (from_status, to_status) in LEGAL_TRANSITIONS


class PaperScheduler:
    """纸盘任务调度器（守护线程；pt_task 状态机写者之一）。

    执行器终态回调只做登记清理（线程归属执行器自身）；状态迁移由
    执行器写（running→终态）或调度器写（pending→stopped）。
    """

    def __init__(
        self,
        db_path: Path,
        *,
        data_dir: Optional[Path] = None,
        registry: Optional[FeedRegistry] = None,
        push: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_concluded: Optional[Callable[[str, str], None]] = None,
        recover_fn: Optional[Callable[..., Any]] = None,
        backfill_fn: Optional[Callable[[Path], None]] = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        max_running: Optional[int] = None,
        now_fn: Optional[Callable[[], Any]] = None,
    ):
        """初始化调度器（不自启——start()/stop() 管理生命周期）。

        Args:
            db_path: research.db 库路径。
            data_dir: Parquet 数据目录（registry 未传时用于建注册表；
                生产由 app.py 传入项目根 data/market）。
            registry: feed 注册表（显式共享实例注入面——缺省按
                data_dir 新建）。
            push: WS 推送回调（转传执行器）。
            on_concluded: 终态回调（转传执行器——组 4 结题固化挂点）。
            recover_fn: 心跳超时移交函数（缺省
                recovery.recover_task(db_path, task_row, ...)→执行器|
                None——None=不可信已转 error）。
            backfill_fn: 终态后资金费回补例程（Q-2；缺省
                recovery.backfill_terminal_funding(db_path)）。
            poll_interval: 守护循环间隔（秒）。
            max_running: 并发上限（缺省 constants.MAX_RUNNING_TASKS
                单源——环境变量 PAPER_MAX_RUNNING_TASKS 可覆盖）。
            now_fn: 当前时刻（心跳超时判定；测试注入固定时刻）。
        """
        self.db_path = Path(db_path)
        if registry is not None:
            self.registry = registry
        else:
            base = data_dir if data_dir is not None else \
                self._default_data_dir()
            self.registry = FeedRegistry(base)
        self.push = push
        self._on_concluded_client = on_concluded
        self._recover_fn = recover_fn
        self._backfill_fn = backfill_fn
        self._poll_interval = poll_interval
        self.max_running = (
            MAX_RUNNING_TASKS if max_running is None else int(max_running))
        self._now_fn = now_fn

        self._lock = threading.Lock()
        self._executors: Dict[str, PaperTaskExecutor] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _default_data_dir() -> Path:
        """缺省数据目录（沿 api/app.py 项目根 data/market 惯例；
        scheduler.py→runtime→paper_trading→core→项目根 四层）。"""
        return Path(__file__).resolve().parents[3] / "data" / "market"

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        """启动守护线程（幂等——已启动 no-op）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._daemon_loop, name="paper-scheduler",
                daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """停止：置停+协作停全部执行器（bar 边界退出）。"""
        self._stop_event.set()
        with self._lock:
            executors = list(self._executors.values())
        for ex in executors:
            ex.controller.request_stop()

    def join(self, timeout: float = 5.0) -> None:
        """等待守护线程结束（停机收尾；超时放行——daemon 线程）。"""
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _daemon_loop(self) -> None:
        """守护循环（poll 间隔轮询 run_once；单轮异常记日志续轮）。"""
        while not self._stop_event.wait(self._poll_interval):
            try:
                self.run_once()
            except Exception:
                logger.exception("纸盘调度器单轮异常（续轮）")

    def startup_recover(self) -> Dict[str, int]:
        """启动期恢复扫描（app lifespan 入口，一次性——不经守护循环）。

        无条件扫全部 running 残态（进程重启遗留）：不可信转 error 保留
        数据；可信重建执行器并由本调度器起工作线程续跑（不待心跳超时）。
        """
        counts = {"recovered": 0, "untrusted": 0}
        for row in paper_repo.list_tasks(
                self.db_path, status="running", limit=10_000):
            with self._lock:
                known = row["task_id"] in self._executors
            if known:
                continue
            executor = self._call_recover(row)
            if executor is None:
                counts["untrusted"] += 1  # 恢复器已转 error（保留数据）
                continue
            self._start_recovered(row, executor)
            counts["recovered"] += 1
        if counts["recovered"] or counts["untrusted"]:
            logger.info("纸盘启动恢复：续跑 %d / 不可信 %d",
                        counts["recovered"], counts["untrusted"])
        return counts

    def _call_recover(self, row: Dict[str, Any]):
        """恢复函数调用单点（注入面 recover_fn——异常记日志归 None）。"""
        recover = self._recover_fn or self._default_recover
        try:
            return recover(
                self.db_path, row,
                registry=self.registry, push=self.push,
                on_concluded=self._executor_concluded)
        except Exception:
            logger.exception("纸盘任务 %s 恢复调用失败", row["task_id"])
            return None

    def _start_recovered(
        self, row: Dict[str, Any], executor: PaperTaskExecutor,
    ) -> None:
        """恢复执行器续跑起线程（startup_recover 与 _hand_over 共用——
        锁内登记+start；review B3 同构收敛单点）。"""
        task_id = row["task_id"]
        thread = threading.Thread(
            target=executor.run, name=f"paper-recovered-{task_id}",
            daemon=True)
        with self._lock:
            self._executors[task_id] = executor
            self._threads[task_id] = thread
        thread.start()

    # ── 单轮扫描（测试通道） ────────────────────────────────

    def run_once(self) -> None:
        """单轮：终态线程清理→心跳超时移交→feed 刷新→拾取→终态资金费
        回补（handed 集合防同轮重复移交）。

        启动期恢复扫描（无条件扫 running）不经本循环——app lifespan
        挂载段直调 startup_recover（恢复执行器起线程续跑）；守护循环
        只按心跳超时移交（design §5.4 两触发面）。
        """
        handed: set[str] = set()
        self._reap_finished()
        self._check_heartbeats(handed)
        self._refresh_feeds()
        self._pickup_pending()
        self._run_backfill()

    def _reap_finished(self) -> None:
        """摘除已结束的执行器线程登记（终态回调兜底）。"""
        with self._lock:
            for task_id in [
                    tid for tid, t in self._threads.items()
                    if not t.is_alive()]:
                self._executors.pop(task_id, None)
                self._threads.pop(task_id, None)

    def _check_heartbeats(self, handed: set[str]) -> None:
        """心跳超时移交：running 且 heartbeat_at 距今 >3×bar 周期。

        本进程执行器假死先协作停（request_stop）下一轮再移交；启动
        遗留/他进程崩溃直接移交。
        """
        now = self._now()
        for row in paper_repo.list_tasks(
                self.db_path, status="running", limit=1000):
            heartbeat = row.get("heartbeat_at")
            if heartbeat is None:
                continue  # 尚未处理过 bar（刚置 running）——不判超时
            timeout = HEARTBEAT_TIMEOUT_MULTIPLIER * TF_SECONDS.get(
                row["timeframe"], _DEFAULT_BAR_SECONDS)
            try:
                elapsed = (now - _parse_iso(heartbeat)).total_seconds()
            except (TypeError, ValueError):
                continue
            if elapsed <= timeout:
                continue
            task_id = row["task_id"]
            with self._lock:
                executor = self._executors.get(task_id)
            if executor is not None:
                executor.controller.request_stop()  # 协作停——下轮再验
                continue
            self._hand_over(row, handed, reason="heartbeat_timeout")

    def _hand_over(self, row: Dict[str, Any], handed: set[str], *,
                   reason: str) -> None:
        """移交恢复器（recover_fn(db_path, task_row, ...)→执行器|None）。

        可信→返回重建执行器并由本调度器起工作线程续跑（登记并发槽）；
        不可信→恢复器已写 error 保留数据。handed 集合防同轮重复移交
        （两个扫描环节间的竞态窗口）。
        """
        task_id = row["task_id"]
        if task_id in handed:
            return
        handed.add(task_id)
        executor = self._call_recover(row)
        if executor is None:
            return  # 不可信——恢复器已转 error（保留数据）
        self._start_recovered(row, executor)
        logger.info("纸盘任务 %s 经恢复器续跑（%s）", task_id, reason)

    @staticmethod
    def _default_recover(db_path: Path, row: Dict[str, Any],
                         **kwargs) -> Optional[PaperTaskExecutor]:
        """缺省恢复函数（延迟 import 防 scheduler↔recovery 循环）。"""
        from core.paper_trading.runtime.recovery import recover_task

        return recover_task(db_path, row, **kwargs)

    def _refresh_feeds(self) -> None:
        """驱动活跃 feed 刷新（订阅制共享——同键任务一份拉取）。"""
        for key in self.registry.active_keys:
            feed = self.registry.get(*key)
            if feed is None or feed.subscriber_count == 0:
                continue
            try:
                feed.refresh()
            except Exception:
                logger.exception(
                    "feed 刷新失败 %s（续轮）", key)

    def _pickup_pending(self) -> None:
        """拾取：活跃数 < 上限时按 created_at 顺序拾取 pending。

        装载校验失败的执行器在 prepare 内直写 error（pending→error
        合法迁移——不进 running），线程随即结束由 _reap_finished 摘除，
        不占并发槽。
        """
        with self._lock:
            active = len(self._executors)
        capacity = self.max_running - active
        if capacity <= 0:
            return
        pending = paper_repo.list_tasks(
            self.db_path, status="pending", order="asc", limit=capacity)
        for row in pending:
            with self._lock:
                if row["task_id"] in self._executors:
                    continue
            self._launch(row)

    def _launch(self, row: Dict[str, Any]) -> None:
        """启动执行器工作线程（每任务一个线程+协作控制器）。"""
        task_id = row["task_id"]
        executor = PaperTaskExecutor(
            self.db_path, row,
            registry=self.registry,
            push=self.push,
            on_concluded=self._executor_concluded)
        thread = threading.Thread(
            target=executor.run, name=f"paper-{task_id}", daemon=True)
        with self._lock:
            self._executors[task_id] = executor
            self._threads[task_id] = thread
        thread.start()

    def _executor_concluded(self, task_id: str, status: str) -> None:
        """执行器终态回调：登记清理+客户端回调透传。"""
        with self._lock:
            self._executors.pop(task_id, None)
            self._threads.pop(task_id, None)
        if self._on_concluded_client is not None:
            self._on_concluded_client(task_id, status)

    def _run_backfill(self) -> None:
        """终态后资金费回补例程（Q-2 封账设计——注入面）。

        扫全部任务（含终态）的 pending 行：parquet 可达则回填 rate/
        amount（不动 balance）+status→settled_disclosure_only+触发
        报告修订版回调。缺省函数在 recovery 模块（延迟 import）。
        """
        backfill = self._backfill_fn or self._default_backfill
        try:
            backfill(self.db_path)
        except Exception:
            logger.exception("终态资金费回补例程异常（续轮）")

    def _default_backfill(self, db_path: Path) -> None:
        """缺省回补函数（data_dir 取 registry.data_dir 事实源——
        CARD-05：路径算术缺省曾指向不存在的 core/data/market，回补
        静默死链；registry 持有装配期真值消除推算）。"""
        from core.paper_trading.runtime.recovery import (
            backfill_terminal_funding,
        )

        backfill_terminal_funding(
            db_path, data_dir=self.registry.data_dir)

    # ── 状态机与请求面（REST 层的调度器入口） ────────────────

    def stop_task(self, task_id: str) -> str:
        """stop 请求分派（调度器写者面；REST 转译 HTTP 语义）。

        CAS 原子链（缺陷⑤吞没防线）：写 stopped 恒带前态门——命中即
        落笔（answer="stopped"=终局承诺，spec R1：随后任何执行器启动
        写被 run() 侧 CAS 拒绝）；零命中=前态已变（并发拾取置
        running）→重读按现态分流。

        Returns:
            str: "stopped"（CAS 命中，终局已落笔）｜"stopping"（本进程
            执行器协作停已请求——bar 边界收敛 stopped）｜"terminal"
            （终态拒绝）｜"not_found"。
        """
        row = paper_repo.get_task(self.db_path, task_id)
        if row is None:
            return "not_found"
        status = row["status"]
        if status == "pending":
            with self._lock:
                executor = self._executors.get(task_id)
            if executor is not None:
                # 拾取已登记（线程将置/已置 running）——协作停
                executor.controller.request_stop()
                return "stopping"
            hit = paper_repo.update_task(
                self.db_path, task_id, status="stopped",
                stopped_at=utc_now_iso(), stop_reason="user_stop",
                expected_status="pending")
            if hit > 0:
                return "stopped"
            # 零命中=并发拾取已置 running——按现态分流（孤儿 running
            # 的 CAS 由 _stop_running 收口）
            current = paper_repo.get_task(self.db_path, task_id)
            if current is not None and current["status"] == "running":
                return self._stop_running(task_id)
            return "terminal"
        if status == "running":
            return self._stop_running(task_id)
        return "terminal"

    def _stop_running(self, task_id: str) -> str:
        """running 态停：本进程执行器协作停；无执行器（移交中/启动
        遗留孤儿）CAS 原子直迁（执行器若随后启动，run() 侧 CAS 零命中
        放弃——终局不被覆写）。"""
        with self._lock:
            executor = self._executors.get(task_id)
        if executor is not None:
            executor.controller.request_stop()
            return "stopping"
        hit = paper_repo.update_task(
            self.db_path, task_id, status="stopped",
            stopped_at=utc_now_iso(), stop_reason="user_stop",
            expected_status="running")
        return "stopped" if hit > 0 else "terminal"

    def status(self) -> Dict[str, Any]:
        """runner-status 数据面（活跃清单/上限/运行数/pending 深度）。"""
        with self._lock:
            active_ids = sorted(self._executors)
        pending_count = paper_repo.count_tasks(
            self.db_path, status="pending")
        return {
            "max_running_tasks": self.max_running,
            "running_count": len(active_ids),
            "active_task_ids": active_ids,
            "pending_depth": pending_count,
        }

    # ── 内部 ────────────────────────────────────────────────

    def _now(self):
        """当前时刻（心跳超时判定；now_fn 注入——测试固定时刻）。"""
        if self._now_fn is not None:
            return self._now_fn()
        import pandas as pd

        return pd.Timestamp.now(tz="UTC")


def _parse_iso(value: str):
    """ISO 时刻字符串解析（调度器侧心跳判定输入）。"""
    import pandas as pd

    ts = pd.Timestamp(value)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts
