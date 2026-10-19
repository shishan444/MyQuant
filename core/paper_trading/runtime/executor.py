"""任务执行器（PaperTaskExecutor——design §5.3，八步 bar 处理序的编排侧）。

每任务实例：装载 dna_snapshot → validate_dna 值域校验 → materialize
物化 RiskParams（任务 leverage/费滑覆写）→ 重建 Ledger（恢复场景从
pt_task 行）→ 订阅 feed。

bar 驱动（design §3.5 八步序，顺序固定；两时点两次 interpret 沿
ChainHarness 参考切片——tests/README 十二区守护行）：
1. 清算判定（本根执行前快照，当根按触发价成交，seq=1）；
2. SL/TP 判定（同上，seq=2）；
3. 金额防线/时间出场+信号出场意图（bar N−1 所产）按本根开盘价
   成交（seq=3；exit_max_loss 属 pending 出场族——次根开盘执行）；
4. 入场→减仓→加仓意图（bar N−1 所产）按本根开盘价成交（seq=4）；
5. 资金费结算（先补旧账再记新账——Q-2，tasks 3.5：bar 区间 (open,
   close] 跨过的每个 8h 结算点逐点扣/挂起；扣款同步归集持仓级
   funding_paid——金额防线判定的资金费分量）；
6. 权益快照（本根收盘价估值）落库；
7. 解释器评估本根信号（本根成交后快照）→ 新意图挂 pending（次根执行）；
8. 窗口计数（bars_processed+1）+停止条件判定（权益破线停观察——
   不干预任何一笔交易）。

单 bar 原子落库（repository.append_bar）+WS 推送。stop 语义：
controller.request_stop → bar 边界协作退出（当前 bar 完整落库后）→
stopped+结题回调。

结构性前提（测试基建）：闭合 bar 事件入口可注入——process_frames
直接消费 DataFrame（合成/历史回放逐根驱动，不经真实时钟与 feed）。
"""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from core.concurrency.task_control import (
    TaskController,
    TaskStopRequested,
)
from core.data.timeframes import TF_SECONDS
from core.paper_trading.constants import POLL_INTERVAL_SECONDS
from core.paper_trading.kernel.account import AccountSnapshot
from core.paper_trading.kernel.fills import MarketFill
from core.paper_trading.kernel.intents import SignalInterpreter
from core.paper_trading.kernel.ledger import Ledger, Position, funding_amount
from core.paper_trading.kernel.types import (
    BarSignals,
    ClosedBar,
    ExecutionEvent,
    Intent,
    RiskParams,
    materialize,
)
from core.paper_trading.runtime.feed import (
    FeedRegistry,
    FeedSubscription,
    settlement_points_between,
)
from core.persistence.repository import paper as paper_repo
from core.persistence.timestamps import utc_now_iso

logger = logging.getLogger("paper_runtime")

# 出场族 kind（第 3 步先于入场族第 4 步消费；exit_max_loss 收盘判定
# 次根开盘全平——pending 出场族，非即时类三元组）
_EXIT_PENDING_KINDS = (
    "exit_max_hold", "exit_time", "exit_max_loss", "close")

# ATR 默认档（compute_all_indicators 默认指标集产出列）
_DEFAULT_ATR_PERIOD = 14

# 状态机终态集（迁移拒绝面消费）
TERMINAL_STATUSES = frozenset({"completed", "stopped", "error"})


def intent_to_json(intents: List[Intent]) -> Optional[str]:
    """意图列表 → pt_task.pending_intent_json（空列表 None=无待执行）。"""
    if not intents:
        return None
    return json.dumps([asdict(i) for i in intents], ensure_ascii=False)


def intents_from_json(raw: Optional[str]) -> List[Intent]:
    """pending_intent_json → 意图列表（解析失败上抛——恢复校验第四判据）。

    Raises:
        ValueError: JSON 解析失败或结构不符（recovery 捕获转
            error=recovery_untrusted）。
    """
    if not raw:
        return []
    data = json.loads(raw)
    return [Intent(**d) for d in data]


def fill_row(event: ExecutionEvent) -> Dict[str, Any]:
    """ExecutionEvent → append_bar 的 fill 行（pt_fill 落库形制）。"""
    fill = event.fill
    return {
        "seq": event.seq,
        "kind": fill.intent.kind,
        "side": fill.intent.direction or None,
        "price": fill.price,
        "quantity": fill.quantity,
        "fee": fill.fee,
        "realized_pnl": fill.realized_pnl,
        "reason": fill.intent.reason or None,
    }


def _rebuild_funding_paid(db_path: Path, task_id: str) -> float:
    """崩溃恢复的持仓级资金费重建（design 层 5 恢复式）。

    pt_funding 行按 status='settled' AND settle_time≥当前持仓开始
    bar 时刻（最近一次 open 成交 bar——反手 open 重建锚，与报告
    position_bars 同口径）聚合 amount：pending 未扣款不计入浮亏；
    settled_disclosure_only 是终态后形态，运行中恢复不出现。
    防线判定逐 bar 纯函数式（快照+params 现算），无需持久化触发
    标记——重建后从最新收盘重判即恢复防线。
    """
    # 聚合下推（B①②，refactor-paper-runtime-data-access）：max(open
    # bar_time) 与 settled 合计由数据库直接产出——原两处全量拉取
    # （fills limit=100_000+funding limit=100_000）+Python 聚合拆除，
    # 窗口假设（超限锚点偏早/累计偏大）从机理消除；NULL 语义等价点：
    # COALESCE(SUM(amount),0.0) ≡ Python 逐行 ``or 0.0`` 兜零，
    # settle_time>=since 闭区间两侧同判（bar_time/settle_time 同列
    # 族 ISO 字符串，字典序=时间序）。
    since = paper_repo.max_open_fill_bar_time(db_path, task_id)
    if since is None:
        return 0.0
    return paper_repo.sum_settled_funding_since(db_path, task_id, since)


class PaperTaskExecutor:
    """纸盘任务执行器（每任务实例；状态机写者之一）。

    生命周期：prepare()（装载三就绪校验——失败 error 不进 running）→
    run()（拾取后置 running+主循环）→ 终态（completed/stopped/error）。
    恢复场景经 from_task_row 重建（校验归 recovery 模块）。
    """

    def __init__(
        self,
        db_path: Path,
        task_row: Dict[str, Any],
        *,
        registry: FeedRegistry,
        controller: Optional[TaskController] = None,
        push: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_concluded: Optional[Callable[[str, str], None]] = None,
        start_after: Optional[str] = None,
    ):
        """初始化执行器（不触碰任务行——状态写发生在 prepare/run）。

        Args:
            db_path: research.db 库路径。
            task_row: pt_task 行（pending 或恢复场景 running）。
            registry: feed 注册表（(symbol,timeframe) 共享单例）。
            controller: 协作取消控制器（缺省自建——测试可注入）。
            push: WS 推送回调 (task_id, payload)；None=无推送。
            on_concluded: 终态回调 (task_id, status)——结题固化挂点
                （组 4 reporting 接入；报告是派生物，回调异常不回滚
                终态）。
            start_after: bar 起始边界（bar_time 严格小于它的历史 bar
                不感知不计数——生产=last_bar_time 或 created_at；测试
                注入 None 全回放）。
        """
        self.db_path = Path(db_path)
        self.task_row = dict(task_row)
        self.task_id = task_row["task_id"]
        self.registry = registry
        self.controller = controller or TaskController()
        self._push = push or (lambda task_id, payload: None)
        self._on_concluded = on_concluded or (lambda task_id, status: None)
        self._data_dir = registry.data_dir
        self._lock = threading.Lock()

        dna = self._load_dna(task_row["dna_snapshot_json"])
        params = materialize(dna.risk_genes)
        # 任务覆写面：leverage/fee/slippage 以任务行为准（spec 任务配置
        # 边界——语义参数不可覆写，费滑杠杆可）
        params = replace(
            params,
            leverage=int(task_row["leverage"]),
        )
        self.dna = dna
        self.params = params
        self.fill_model = MarketFill(
            float(task_row["fee"]), float(task_row["slippage"]))
        self.interpreter = SignalInterpreter()

        # bar 间工作副本（design §5.5：内存 Ledger，bar 边界回写）
        self.ledger = Ledger(float(task_row["initial_cash"]), params)
        self.pending: List[Intent] = []
        self.bars_processed = int(task_row.get("bars_processed") or 0)
        self.last_bar_time: Optional[str] = task_row.get("last_bar_time")
        self.win_count = int(task_row.get("win_count") or 0)
        self.loss_count = int(task_row.get("loss_count") or 0)
        self.total_pnl = float(task_row.get("total_pnl") or 0.0)
        # 挂起资金费行内存镜像（先补旧账的消费面；恢复时从库装载）
        self._pending_funding: List[Dict[str, Any]] = []

        # 起始边界：恢复游标 > 创建时刻（首跑跳过创建前的历史 bar——
        # 任务从创建时点开始感知市场，沿旧 runner 语义；测试传 None
        # 全回放旁路）
        if start_after is not None:
            self._start_after = start_after
        else:
            self._start_after = (
                task_row.get("last_bar_time") or task_row.get("created_at"))
        # 终态信号（第 8 步判定置位——_loop 出口消费）
        self._concluded_status: Optional[str] = None
        self._finalized = False

        # feed 订阅（主周期+MTF 辅周期；forming 过滤在 feed 侧）
        self._main_sub: Optional[FeedSubscription] = None
        self._aux_subs: Dict[str, FeedSubscription] = {}
        self._aux_frames: Dict[str, pd.DataFrame] = {}
        self._frame_queue: "queue.Queue[tuple[pd.DataFrame, int]]" = (
            queue.Queue())

        self._timeframe = task_row["timeframe"]
        self._tf_seconds = TF_SECONDS.get(self._timeframe, 86400)
        atr_need = (
            {int(params.atr_period)}
            if params.sl_mode == "atr" else set())
        self._atr_need = atr_need

    # ── DNA 装载 ────────────────────────────────────────────

    @staticmethod
    def _load_dna(dna_snapshot_json: str):
        """dna_snapshot_json → StrategyDNA（快照自包含，D-6）。

        Raises:
            ValueError: JSON 解析/结构不符（prepare 前的构造期上抛——
            上盘流转已保证快照合法，运行期到达即库内脏数据）。
        """
        from core.strategy.dna import StrategyDNA

        return StrategyDNA.from_json(dna_snapshot_json)

    # ── 恢复重建（校验归 recovery；本方法只做可信重建） ──────

    @classmethod
    def from_task_row(
        cls,
        db_path: Path,
        task_row: Dict[str, Any],
        **kwargs: Any,
    ) -> "PaperTaskExecutor":
        """从持久化行重建执行器（可信恢复——校验由 recovery 前置）。

        重建面：Ledger（balance/position 字段组/closed_trades/累计
        资金费←最后 equity 行 cumulative_funding/持仓级 funding_paid
        ←pt_funding settled 行聚合重建）+pending 意图+游标+统计组
        +挂起资金费行。
        """
        executor = cls(db_path, task_row, **kwargs)
        row = task_row
        ledger = executor.ledger
        ledger.balance = float(row["balance"])
        if row["position_side"] is not None:
            ledger.position = Position(
                side=row["position_side"],
                entry_price=float(row["position_entry"]),
                quantity=float(row["position_quantity"]),
                margin=float(row["position_margin"]),
                open_cost=float(row["position_open_cost"]),
                sl_price=row["position_sl_price"],
                tp_price=row["position_tp_price"],
                trail_peak=float(row["position_trail_peak"]),
                entry_bar_seq=int(row["position_entry_bar_seq"]),
            )
            ledger.position.funding_paid = _rebuild_funding_paid(
                db_path, row["task_id"])
        ledger.closed_trades = int(row["total_trades"])
        executor.pending = intents_from_json(row["pending_intent_json"])
        # 行查询下推（B④）：pending 资金费重建带 status 过滤+settle_time
        # 升序（查询内置排序，行集=真正要处理的对象，零到几行——过滤
        # 下推不聚合）；累计资金费 B③：末行快照由数据库直出（原
        # limit=1_000_000 全量拉取拆除），空集→None 语义保持
        executor._pending_funding = paper_repo.list_funding_records(
            db_path, row["task_id"], limit=100_000, status="pending")
        snap = paper_repo.latest_equity_snapshot(db_path, row["task_id"])
        if snap is not None:
            ledger.total_funding = float(snap["cumulative_funding"])
        return executor

    # ── 装载三就绪校验（design §5.3：不可就绪→error 不进 running） ──

    def _prepare_guarded(self) -> bool:
        """prepare 防逃逸包装（CARD-02）：裸异常（坏 parquet/refresh
        抛错/指标现算崩溃等）若逃出 run() 会击穿工作线程——任务永留
        pending 被守护循环每 2s 无限重拾。此处兜底转 prepare_failed
        error（pending→error 合法迁移，_reject 通道）。

        TaskStopRequested 透传（prepare 期间 stop 协作语义保留——
        run() 的 except 分支按 user_stop 收口）。
        """
        try:
            return self.prepare()
        except TaskStopRequested:
            raise
        except Exception as exc:
            logger.exception("纸盘任务 %s 装载异常", self.task_id)
            return self._reject(
                f"prepare_failed: {type(exc).__name__}: {exc}")

    def prepare(self) -> bool:
        """装载校验（执行周期 parquet 存在/指标可算/atr 列可产出+辅周期
        完整性+DNA 值域）。

        Returns:
            bool: True=就绪（可进 running）；False=已写 error（不进
            running——「载入即拒」纪律）。
        """
        # DNA 校验（T4 改造：validate_dna_quiet 布尔包装退役，直调明细
        # 形制）+插件源能力边界判据（第一问·方向二——结构合法但 paper
        # 链未接入插件信号组装，在场即拒；错误码分立〔补丁①〕：这是
        # 「执行环境不支持」而非「策略无效」，detail 指明能力边界与
        # 替代通道）
        from core.strategy.validator import validate_dna, has_plugin_sources
        result = validate_dna(self.dna)
        if not result.is_valid:
            return self._reject(
                "invalid_dna: DNA 值域校验未过（"
                + "; ".join(result.errors) + "）")
        if has_plugin_sources(self.dna):
            return self._reject(
                "plugin_sources_unsupported: 执行环境不支持插件信号源"
                "（paper 链未接入插件信号组装）——请经研究验证链沙盒回测")

        symbol = self.task_row["symbol"]
        try:
            from core.data.validation import validate_symbol

            validate_symbol(symbol)  # 直拼前命名白名单（CARD-01②）
        except ValueError as exc:
            return self._reject(f"invalid_symbol: {exc}")
        path = self._data_dir / f"{symbol}_{self._timeframe}.parquet"
        if not path.exists():
            return self._reject("no_data: 执行周期 parquet 不存在")
        from core.data.storage import load_parquet

        raw = load_parquet(path)
        if raw is None or raw.empty:
            return self._reject("no_data: 执行周期 parquet 为空")

        from core.features.indicators import compute_all_indicators

        try:
            enhanced = compute_all_indicators(raw)
        except Exception as exc:  # 指标计算崩溃=不可算
            return self._reject(f"indicators_unavailable: {exc}")

        # MTF：先组装再试算（校验序——修复卡 P0②）：辅周期经 feed
        # 装载组装 dfs_by_timeframe（沿旧 load_mtf_data+runner 完整性
        # 纪律：DNA 所需周期缺任一即拒 mtf_data_missing 语义），信号
        # 试算传真实组装（试算传 None 会被 dna_to_signal_set 的 MTF
        # 防御恒拒——8.2 实测缺陷）
        dfs_by_timeframe = None
        if self.dna.is_mtf:
            dfs_by_timeframe = self._assemble_prepare_frames(enhanced)
            if dfs_by_timeframe is None:
                aux = [tf for tf in self.dna.timeframes
                       if tf != self._timeframe]
                return self._reject(
                    f"mtf_data_missing: {','.join(sorted(aux))}"
                    f"（所需周期 {sorted(self.dna.timeframes)}）")

        # 指标可算=DNA 声明指标可产出且信号可评估（装载期信号试算——
        # 残缺数据下指标列缺失，试算即失败，不待运行期炸）
        from core.strategy.executor import dna_to_signal_set

        try:
            dna_to_signal_set(self.dna, enhanced, dfs_by_timeframe)
        except Exception as exc:
            return self._reject(f"indicators_unavailable: {exc}")

        if self.params.sl_mode == "atr":
            period = int(self.params.atr_period)
            col = f"atr_{period}"
            if col not in enhanced.columns:
                from core.features.indicators import _compute_indicator

                computed = _compute_indicator(
                    enhanced, "ATR", {"period": period})
                col_series = computed.get(col)
            else:
                col_series = enhanced[col]
            if col_series is None or not pd.Series(
                    col_series).notna().any():
                return self._reject(
                    f"atr_missing: ATR({period}) 列缺失且现算不可得")
        return True

    def _assemble_prepare_frames(
        self, enhanced: pd.DataFrame,
    ) -> Optional[Dict[str, pd.DataFrame]]:
        """MTF 装载校验期帧组装（辅周期临时订阅+刷新，校验后释放——
        正式订阅归 run() 的 subscribe_feeds）。

        沿旧装载纪律（design §5.2/§5.3）：DNA 所需辅周期 parquet 缺失
        或刷新后无产物（拉取失败/空）即完整性不满足。返回
        dfs_by_timeframe（主周期=试算帧）；不可得返回 None。

        临时订阅的意义：装载校验只证明「数据就绪」，校验通过即退订
        （引用计数归零回收）——运行期由 subscribe_feeds 正式订阅共享
        feed，不持有校验期资源。
        """
        symbol = self.task_row["symbol"]
        frames: Dict[str, pd.DataFrame] = {self._timeframe: enhanced}
        temp_subs: list[tuple[str, FeedSubscription]] = []
        try:
            for tf in self.dna.timeframes:
                if tf == self._timeframe:
                    continue
                # 快速拒绝面：parquet 缺失直接拒（不触发全量回拉）
                from core.data.validation import validate_symbol

                validate_symbol(symbol)  # 直拼前命名白名单（CARD-01②）
                aux_path = self._data_dir / f"{symbol}_{tf}.parquet"
                if not aux_path.exists():
                    return None
                sub = FeedSubscription(on_frames=lambda df, idx: None)
                temp_subs.append((tf, sub))
                feed = self.registry.acquire(symbol, tf, sub)
                feed.refresh()  # 装载校验期单次刷新（增量形制）
                latest = feed.latest_df
                if latest is None or latest.empty:
                    return None
                frames[tf] = latest
            return frames
        finally:
            for tf, sub in temp_subs:
                self.registry.release(symbol, tf, sub)

    def _reject(self, detail: str) -> bool:
        """装载拒绝：pending→error（不进 running），保留数据。"""
        paper_repo.update_task(
            self.db_path, self.task_id, status="error",
            error_detail=detail)
        logger.error("纸盘任务 %s 装载拒绝: %s", self.task_id, detail)
        self._push(self.task_id, {
            "type": "task_error", "task_id": self.task_id,
            "error_detail": detail})
        return False

    # ── 主循环 ──────────────────────────────────────────────

    def run(self) -> None:
        """执行主入口（调度器拾取后在工作线程调用）。

        prepare 失败直接返回（error 已写——不进 running）；成功置
        running（写 started_at）后进主循环，直到终态（窗口到点/
        stop 协作退出/异常）。
        """
        if not self._prepare_guarded():
            return
        # CAS 前态门（缺陷⑤吞没防线）：pending→running 合法迁移原子写；
        # 集合含自身覆盖恢复续跑场景（恢复行本就 running，同态幂等
        # 覆写 started_at）。零命中=并发 stop 已直迁终态（拾取后启动
        # 前窗口）——放弃启动（终局承诺不被覆写，spec R1）。
        hit = paper_repo.update_task(
            self.db_path, self.task_id, status="running",
            started_at=utc_now_iso(),
            expected_status={"pending", "running"})
        if hit == 0:
            logger.info(
                "纸盘任务 %s 启动前已终态（并发 stop/他写）——放弃启动",
                self.task_id)
            return
        self.subscribe_feeds()
        self._push(self.task_id, {
            "type": "task_started", "task_id": self.task_id})
        logger.info("纸盘任务 %s 进入 running", self.task_id)
        try:
            self._loop()
        except TaskStopRequested:
            self._finalize("stopped", stop_reason="user_stop")
        except Exception as exc:
            logger.exception("纸盘任务 %s 执行异常", self.task_id)
            self._finalize("error", error_detail=f"{type(exc).__name__}: {exc}")
        finally:
            self.unsubscribe_feeds()

    def _loop(self) -> None:
        """轮询主循环：等 feed 帧（poll 间隔兜底唤醒）逐帧推进。

        bar 边界 stop 由 process_frames 内协作退出并 finalize；空闲期
        （无帧等待中）stop 在本层捕获 finalize。
        """
        try:
            while self._concluded_status is None:
                try:
                    df, from_idx = self._frame_queue.get(
                        timeout=POLL_INTERVAL_SECONDS)
                except queue.Empty:
                    self.controller.check_stop()  # 空闲期协作退出锚
                    continue
                self.process_frames(df, from_idx)
        except TaskStopRequested:
            self._finalize("stopped", stop_reason="user_stop")

    # ── feed 订阅面 ─────────────────────────────────────────

    def subscribe_feeds(self) -> None:
        """订阅主周期 feed（bar 驱动）+MTF 辅周期 feed（信号组装供数）。"""
        symbol = self.task_row["symbol"]
        on_funding = self._on_funding_arrived
        self._main_sub = FeedSubscription(
            on_frames=lambda df, idx: self._frame_queue.put((df, idx)),
            on_funding_arrived=on_funding,
            atr_periods=self._atr_need)
        self.registry.acquire(symbol, self._timeframe, self._main_sub)
        if self.dna.is_mtf:
            for tf in self.dna.timeframes:
                if tf == self._timeframe:
                    continue
                sub = FeedSubscription(
                    on_frames=self._make_aux_handler(tf),
                    on_funding_arrived=on_funding)
                self._aux_subs[tf] = sub
                self.registry.acquire(symbol, tf, sub)

    def unsubscribe_feeds(self) -> None:
        """退订全部 feed（引用计数回收协作面；幂等）。"""
        symbol = self.task_row["symbol"]
        if self._main_sub is not None:
            self.registry.release(symbol, self._timeframe, self._main_sub)
            self._main_sub = None
        for tf, sub in list(self._aux_subs.items()):
            self.registry.release(symbol, tf, sub)
        self._aux_subs.clear()

    def _make_aux_handler(
        self, tf: str,
    ) -> Callable[[pd.DataFrame, int], None]:
        """辅周期帧回调：仅更新最新引用（信号组装时取用）。"""

        def on_aux(df: pd.DataFrame, from_idx: int) -> None:
            self._aux_frames[tf] = df

        return on_aux

    def _on_funding_arrived(self, points: List[pd.Timestamp]) -> None:
        """funding 新覆盖通知（及时性事件——补记实际发生在次 bar
        第 5 步「先补旧账」，此处仅记日志）。"""
        logger.debug(
            "任务 %s 收到 funding_arrived: %s 个结算点",
            self.task_id, len(points))

    # ── 八步序（测试注入通道——直接喂帧，不经真实时钟/feed） ──

    def process_frames(self, df: pd.DataFrame, from_idx: int) -> None:
        """消费一帧闭合 bar（from_idx 起逐根八步序——回放驱动入口）。

        信号组装按整帧计算（dna_to_signal_set 全帧形制，沿旧 runner）；
        MTF 时辅周期取各 feed 最新引用组装。窗口到点/停止条件置位后
        本帧剩余 bar 不再处理（观察已结束）；stop 在 bar 边界协作退出
        （当前 bar 已完整落库）。
        """
        dfs_by_timeframe = self._assemble_mtf_frames(df)
        from core.strategy.executor import dna_to_signal_set

        sig_set = dna_to_signal_set(self.dna, df, dfs_by_timeframe)
        try:
            for i in range(from_idx, len(df)):
                self.controller.check_stop()  # bar 边界协作退出锚
                if self._concluded_status is not None:
                    break  # 第 8 步已终态置位——后续 bar 不再感知
                self._process_bar(df, i, sig_set)
        except TaskStopRequested:
            self._finalize("stopped", stop_reason="user_stop")
            return
        if self._concluded_status == "completed":
            self._finalize("completed")
        elif self._concluded_status == "stopped":
            self._finalize("stopped", stop_reason="stop_condition")

    def _assemble_mtf_frames(
        self, main_df: pd.DataFrame,
    ) -> Optional[Dict[str, pd.DataFrame]]:
        """MTF 帧组装（辅周期引用快照；单周期 None——dna_to_signal_set
        单周期路径不消费该参数）。"""
        if not self.dna.is_mtf:
            return None
        frames: Dict[str, pd.DataFrame] = {self._timeframe: main_df}
        for tf in self.dna.timeframes:
            if tf == self._timeframe:
                continue
            aux = self._aux_frames.get(tf)
            if aux is None:
                continue
            frames[tf] = aux
        return frames

    def _bar_signals(self, sig_set, i: int) -> BarSignals:
        """SignalSet 第 i 行切片 → BarSignals（解释器 bar 级输入）。"""
        direction = sig_set.entry_direction
        entry_dir = (
            float(direction.iloc[i]) if direction is not None else 0.0)
        return BarSignals(
            entry=bool(sig_set.entries.iloc[i]),
            exit=bool(sig_set.exits.iloc[i]),
            add=bool(sig_set.adds.iloc[i]),
            reduce=bool(sig_set.reduces.iloc[i]),
            entry_direction=entry_dir,
        )

    def _closed_bar(self, df: pd.DataFrame,
                    i: int) -> Optional[ClosedBar]:
        """df 第 i 行 → ClosedBar（ATR 列按 DNA 周期读；缺列 None）。

        装载点守卫（NaN bar 单点拦截）：open/high/low/close 任一
        NaN/inf 判该 bar 无效返回 None——OHLC 是下游全链（判定→定仓
        →成交→落库）的唯一价格入口，此处单点覆盖后 intents/fills/
        ledger 均不再各自防御。ATR 不在校验面（无效 ATR 语义由
        risk_budget 距离拒绝链与 _initial_stop 防御覆盖）。
        """
        row = df.iloc[i]
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        if not (math.isfinite(open_) and math.isfinite(high)
                and math.isfinite(low) and math.isfinite(close)):
            return None
        atr_value: Optional[float] = None
        if self.params.sl_mode == "atr":
            col = f"atr_{int(self.params.atr_period)}"
            if col in df.columns:
                atr_value = float(row[col])
        ts = df.index[i]
        return ClosedBar(
            seq=i, open=open_, high=high,
            low=low, close=close,
            timestamp=ts, tf_minutes=self._tf_seconds / 60.0,
            atr=atr_value)

    def _process_bar(self, df: pd.DataFrame, i: int, sig_set) -> None:
        """八步 bar 处理序（design §3.5——顺序固定）。

        NaN bar 跳过（装载点守卫命中）：不成交、不落库、
        last_bar_time 不推进、pending 顺延至下一根有效 bar 消费
        ——对齐回测「NaN bar 无成交无判定生效」的静默容忍。
        """
        bar = self._closed_bar(df, i)
        if bar is None:
            return
        signals = self._bar_signals(sig_set, i)
        bar_time = df.index[i].isoformat()

        # 游标判重（恢复重放幂等）+起始边界（历史 bar 不感知）
        if self.last_bar_time is not None and bar_time <= self.last_bar_time:
            return
        if self._start_after is not None and bar_time < self._start_after:
            return

        events: List[ExecutionEvent] = []

        # ── 第 1/2 步：即时类判定（本根执行前快照）当根按触发价成交 ──
        pre = self.interpreter.interpret(
            bar, signals,
            self.ledger.snapshot(bar.close, bar.seq), self.params)
        for intent in pre:
            if intent.kind == "exit_liquidation":
                self._execute(intent, bar, events, seq=1)
            elif intent.kind in ("exit_sl", "exit_tp"):
                self._execute(intent, bar, events, seq=2)

        # ── 第 3/4 步：上根 pending 消费（出场先于入场族）开盘价成交 ──
        for intent in [x for x in self.pending
                       if x.kind in _EXIT_PENDING_KINDS]:
            self._execute(intent, bar, events, seq=3)
        for intent in [x for x in self.pending
                       if x.kind not in _EXIT_PENDING_KINDS]:
            self._execute(intent, bar, events, seq=4)
        self.pending = []

        # ── 第 5 步：资金费（先补旧账再记新账） ──
        funding_rows, backfills = self._settle_funding(bar, bar_time)

        # ── 第 6 步：权益快照（收盘价估值）+单 bar 原子落库 ──
        snap = self.ledger.snapshot(bar.close, bar.seq)
        self.bars_processed += 1
        self.last_bar_time = bar_time
        self._persist_bar(
            bar_time, events, snap, funding_rows, backfills)

        # ── 第 7 步：解释器评估本根信号（本根成交后快照）挂 pending ──
        post = self.interpreter.interpret(
            bar, signals,
            self.ledger.snapshot(bar.close, bar.seq), self.params)
        self.pending = [
            x for x in post
            if x.kind not in ("exit_liquidation", "exit_sl", "exit_tp")]

        # trail 峰值落账（持仓期逐 bar——判定自足，状态权威落点）
        self.ledger.update_trail_peak(bar)

        # ── 第 8 步：窗口计数已并入第 6 步 task_state；停止条件判定 ──
        self._evaluate_conclusion(snap)

    def _execute(self, intent: Intent, bar: ClosedBar,
                 events: List[ExecutionEvent], seq: int) -> None:
        """执行单条意图（Ledger.execute 语境防御面：None 丢弃）。

        entry_atr 接线（修复卡：层间接线遗漏）：执行 bar 的 ATR 值
        （feed 指标列装载链）透传 kernel——open 意图的 risk_budget×atr
        档定仓距离消费（sl_mult×该 bar ATR）；其余意图不读（kernel
        签名语义）。漏接会使 atr 档距离恒 NaN 走公式单点 (0,0) 防御
        恒回退 fixed。
        """
        fill = self.ledger.execute(
            intent, bar, self.fill_model, entry_atr=bar.atr)
        if fill is None:
            return
        if fill.realized_pnl is not None:
            self.total_pnl += fill.realized_pnl
            if intent.kind not in ("open", "add", "reduce"):
                # 全平事件计数（与 ledger.closed_trades 同事件集——
                # win+loss=total 自洽口径；reduce 部分盈亏只进 total_pnl）
                if fill.realized_pnl > 0:
                    self.win_count += 1
                else:
                    self.loss_count += 1
        events.append(ExecutionEvent(fill=fill, bar_time=bar.timestamp.isoformat()
                                     if bar.timestamp is not None else str(bar.seq),
                                     seq=seq))
        self._push(self.task_id, {
            "type": "fill_event", "task_id": self.task_id,
            "bar_time": events[-1].bar_time, "seq": seq,
            "kind": fill.intent.kind, "side": fill.intent.direction or None,
            "price": fill.price, "quantity": fill.quantity,
            "fee": fill.fee, "realized_pnl": fill.realized_pnl,
            "reason": fill.intent.reason or None})

    # ── 资金费（Q-2：先补旧账再记新账） ──────────────────────

    def _settle_funding(
        self, bar: ClosedBar, bar_time: str,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """资金费结算：返回 (本 bar 新结算行, 旧账回填行)。

        先补旧账：内存 pending 镜像按 settle_time 升序逐点查索引，
        可达则回填（rate←真实值、amount=快照公式、status→settled、
        balance 扣减——与该 bar 新结算同事务落库，见 _persist_bar）。
        记新账：bar 区间 (open, close] 跨过的每个 8h 结算点，当时有仓
        则逐点结算（金额=quantity×bar_close×rate×方向符号）；索引未达
        落 pending 行（rate/amount NULL+快照 settle_price/
        position_quantity——带符号数量编码方向，补记侧无 side 列可恢复）。
        两分支扣款均经 ledger.record_funding 单点（balance/total_funding/
        持仓级 funding_paid 三键——金额防线判定的资金费分量；补旧账
        金额按快照数量重算不读当前仓位，当时有仓则归集当前持仓——
        跨持仓段的挂起补扣归新仓，方向保守=防线偏敏感，重启恢复按
        settle_time≥持仓开始时刻过滤自愈；对质①裁决）。
        """
        backfills: List[Dict[str, Any]] = []
        feed = self.registry.get(self.task_row["symbol"], self._timeframe)
        rate_lookup = (
            feed.funding_rates if feed is not None
            else lambda points: {p: None for p in points})

        # 先补旧账（升序逐点——feed 已刷新则多数可达）
        still_pending: List[Dict[str, Any]] = []
        for row in self._pending_funding:
            point = pd.Timestamp(row["settle_time"])
            rate = rate_lookup([point])[point]
            if rate is None:
                still_pending.append(row)
                continue
            amount = funding_amount(
                row["position_quantity"], row["settle_price"], rate)
            self.ledger.record_funding(amount)
            # rows 形制 [{settle_time, rate, amount}]——目标态恒
            # 'settled' 由仓库接口定死（对质②形制收口：不传 status）
            backfills.append({
                "settle_time": row["settle_time"], "rate": rate,
                "amount": amount})
        self._pending_funding = still_pending

        # 记新账（当时有仓才结算——与杠杆无关，对质六裁决）
        pos = self.ledger.position
        new_rows: List[Dict[str, Any]] = []
        if pos is not None and bar.timestamp is not None:
            close_t = bar.timestamp + pd.Timedelta(
                seconds=self._tf_seconds)
            signed_qty = (
                pos.quantity if pos.side == "long" else -pos.quantity)
            points = settlement_points_between(bar.timestamp, close_t)
            rates = rate_lookup(points)
            for point in points:
                rate = rates[point]
                if rate is None:
                    new_rows.append({
                        "settle_time": point.isoformat(),
                        "status": "pending", "rate": None, "amount": None,
                        "position_quantity": signed_qty,
                        "settle_price": float(bar.close)})
                    continue
                amount = funding_amount(signed_qty, float(bar.close), rate)
                self.ledger.record_funding(amount)
                new_rows.append({
                    "settle_time": point.isoformat(),
                    "status": "settled", "rate": rate, "amount": amount,
                    "position_quantity": signed_qty,
                    "settle_price": float(bar.close)})
        if new_rows:
            self._pending_funding.extend(
                r for r in new_rows if r["status"] == "pending")
        return new_rows, backfills

    # ── 落库与推送 ───────────────────────────────────────────

    def _persist_bar(
        self,
        bar_time: str,
        events: List[ExecutionEvent],
        snap: AccountSnapshot,
        funding_rows: List[Dict[str, Any]],
        backfills: List[Dict[str, Any]],
    ) -> None:
        """单 bar 原子落库（fills+equity+funding+task 游标与账户态一个
        事务）+task_update 推送。"""
        position = snap.position
        # 旧账回填走 append_bar 的 funding_backfills 面（对质中组 2 接口
        # 增量）——仅在非空时传参：正常路径零依赖；回填发生而接口未
        # 到位时 TypeError 显式暴露（不静默丢账）
        extra: Dict[str, Any] = {}
        if backfills:
            extra["funding_backfills"] = backfills
        paper_repo.append_bar(
            self.db_path, self.task_id,
            bar_time=bar_time,
            fills=[fill_row(e) for e in events],
            equity={
                "equity": snap.equity, "balance": snap.balance,
                "unrealized_pnl": snap.unrealized_pnl,
                "position_side": position.side if position else None,
                "cumulative_funding": self.ledger.total_funding,
            },
            funding=funding_rows or None,
            **extra,
            task_state={
                "bars_processed": self.bars_processed,
                "last_bar_time": self.last_bar_time,
                "balance": self.ledger.balance,
                "position_side": position.side if position else None,
                "position_entry": position.entry_price if position else None,
                "position_quantity": (
                    position.quantity if position else None),
                "position_margin": position.margin if position else None,
                "position_open_cost": (
                    position.open_cost if position else None),
                "position_sl_price": position.sl_price if position else None,
                "position_tp_price": position.tp_price if position else None,
                "position_trail_peak": (
                    position.trail_peak if position else None),
                "position_entry_bar_seq": (
                    position.entry_bar_seq if position else None),
                "unrealized_pnl": snap.unrealized_pnl,
                "pending_intent_json": intent_to_json(self.pending),
                "total_trades": self.ledger.closed_trades,
                "win_count": self.win_count,
                "loss_count": self.loss_count,
                "total_pnl": self.total_pnl,
                "heartbeat_at": utc_now_iso(),
            },
        )
        self._push(self.task_id, {
            "type": "task_update", "task_id": self.task_id,
            "status": "running", "equity": snap.equity,
            "balance": snap.balance,
            "unrealized_pnl": snap.unrealized_pnl,
            "bars_processed": self.bars_processed,
            "window_bars": self.task_row.get("window_bars")})

    # ── 终态 ────────────────────────────────────────────────

    def _evaluate_conclusion(self, snap: AccountSnapshot) -> None:
        """八步序第 8 步后半：停止条件判定（窗口到点在此判定、_loop 出口消费）。

        停止条件=运行控制不是行为干预：只结束观察，本 bar 的交易已按
        八步序完整落库。
        """
        window = self.task_row.get("window_bars")
        if window is not None and self.bars_processed >= int(window):
            self._concluded_status = "completed"
            return
        pct = self.task_row.get("stop_loss_pct")
        if pct is not None:
            threshold = float(self.task_row["initial_cash"]) * (
                1.0 - float(pct))
            if snap.equity <= threshold:
                self._concluded_status = "stopped"

    def _finalize(self, status: str, *, stop_reason: Optional[str] = None,
                  error_detail: Optional[str] = None) -> None:
        """终态迁移+WS 推送+结题回调（幂等；回调异常不回滚终态——
        报告是派生物）。"""
        if getattr(self, "_finalized", False):
            return
        self._finalized = True
        self._concluded_status = status
        fields: Dict[str, Any] = {"status": status}
        if status in TERMINAL_STATUSES:
            fields["stopped_at"] = utc_now_iso()
        if stop_reason is not None:
            fields["stop_reason"] = stop_reason
        if error_detail is not None:
            fields["error_detail"] = error_detail
        paper_repo.update_task(self.db_path, self.task_id, **fields)
        logger.info("纸盘任务 %s 终态 %s（%s）", self.task_id, status,
                    stop_reason or error_detail or "")
        if status == "completed":
            self._push(self.task_id, {
                "type": "task_completed", "task_id": self.task_id,
                "final_equity": self.ledger.balance, "total_trades":
                    self.ledger.closed_trades})
        elif status == "stopped":
            self._push(self.task_id, {
                "type": "task_stopped", "task_id": self.task_id,
                "stop_reason": stop_reason})
        else:
            self._push(self.task_id, {
                "type": "task_error", "task_id": self.task_id,
                "error_detail": error_detail})
        try:
            self._on_concluded(self.task_id, status)
        except Exception:
            logger.exception(
                "纸盘任务 %s 结题回调失败（不回滚终态）", self.task_id)


# （validate_dna_quiet 原定义位于此处——recognize-plugin-source-dna
# T4 改造中随消费点（prepare 装载校验直调 validate_dna 明细形制）
# 一并退役删除，调用面全域清零经 grep 核验）
