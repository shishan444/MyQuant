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
import time
from dataclasses import asdict, dataclass, replace
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

# 信号行 seq 编号（八步序位——spec 字段族明细）：即时类 1/2、pending
# 消费 3/4、解释器内拒绝 7、expired 收割 0
_SEQ_LIQUIDATION = 1
_SEQ_STOP_TAKE = 2
_SEQ_PENDING_EXIT = 3
_SEQ_PENDING_ENTRY = 4
_SEQ_INTERPRETER_REJECT = 7
_SEQ_EXPIRED = 0

# 意图 kind → 求值披露 hits 键（change paper-signal-log 任务 4）：
# open→entry；close 按意图 reason 分流（signal=exit 信号全平、
# reverse=entry 反向两步反手首步）；add/reduce 同名；防线族
# （exit_sl/tp/max_loss/max_hold/time/liquidation）非基因驱动不归键
def _hits_key_for_intent(intent: Intent) -> Optional[str]:
    if intent.kind == "open":
        return "entry"
    if intent.kind == "close":
        return "entry" if intent.reason == "reverse" else "exit"
    if intent.kind in ("add", "reduce"):
        return intent.kind
    return None


@dataclass(frozen=True)
class PendingIntent:
    """pending 意图+信号上下文（执行器私有词汇，任务 4）。

    Attributes:
        intent: 待执行意图（次根开盘消费）。
        signal_bar_time: 意图产生 bar（决策锚——信号行 bar_time）。
        gene_hits: 该 bar 命中基因项（SignalSet.hits 取键；恢复重建
            不可得——恢复场景空披露，不伪造归因）。
        signal_bar_index: 意图产生 bar 的帧内行号（指标快照取决策
            bar 值的定位锚；恢复重建语境无帧行号=-1→快照空 dict）。
    """

    intent: Intent
    signal_bar_time: str
    gene_hits: tuple
    signal_bar_index: int = -1


def intent_to_json(pendings: List[PendingIntent]) -> Optional[str]:
    """pending 列表 → pt_task.pending_intent_json（空列表 None=无待执行）。

    序列化面=Intent 字段（既有 json 格式不变——恢复链兼容）；信号
    上下文（signal_bar_time/gene_hits/行号锚）不序列化（恢复侧按
    last_bar_time 重建 bar 锚、基因归因空披露）。
    """
    if not pendings:
        return None
    return json.dumps(
        [asdict(p.intent) for p in pendings], ensure_ascii=False)


def intents_from_json(
    raw: Optional[str], *, signal_bar_time: Optional[str] = None,
) -> List[PendingIntent]:
    """pending_intent_json → PendingIntent 列表（解析失败上抛——恢复
    校验第四判据）。

    signal_bar_time=意图产生 bar 锚（恢复调用方传 last_bar_time——
    崩溃时点的未消费意图产生于最后处理完的 bar；缺省 None=无锚
    语境）。gene_hits 恒空、行号锚 -1（见 PendingIntent docstring）。

    Raises:
        ValueError: JSON 解析失败或结构不符（recovery 捕获转
            error=recovery_untrusted）。
    """
    if not raw:
        return []
    data = json.loads(raw)
    return [PendingIntent(
        intent=Intent(**d), signal_bar_time=signal_bar_time or "",
        gene_hits=()) for d in data]


def fill_row(event: ExecutionEvent) -> Dict[str, Any]:
    """ExecutionEvent → append_bar 的 fill 行（pt_fill 落库形制；
    核算明细三列随事件透传——任务 5）。"""
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
        "equity_before": event.equity_before,
        "available_before": event.available_before,
        "margin_used": event.margin_used,
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
    # 族 ISO 字符串，字典序=时间序）。字典序=时间序的前提=两列格式
    # 同构（生产写入面 isoformat 整点无微秒段；毫秒变体分歧实证与
    # 生产不可达论证见 change review-testing.md §五，前提锚用例
    # TestCardBTimeFormatPremise）。
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
        funding_lookup: Optional[Callable[[list], dict]] = None,
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
            funding_lookup: 资金费率查询注入（(结算点列表→{点:费率}
                映射），与 SymbolFeed.funding_rates 同签名同语义——
                回放驱动注入历史归档索引用；缺省 None=实时形态走
                registry feed 查询，行为零变更（change
                add-replay-engine 任务 1.3）。
        """
        self.db_path = Path(db_path)
        self.task_row = dict(task_row)
        self.task_id = task_row["task_id"]
        # 达标判定状态（paper-trading-revamp 组 B）：恢复场景沿 task 行
        # first_met_at 续判（旧库行缺键 None 兜底=从未满足）
        self._first_met_at = task_row.get("first_met_at")
        self.registry = registry
        self.controller = controller or TaskController()
        self._push = push or (lambda task_id, payload: None)
        self._on_concluded = on_concluded or (lambda task_id, status: None)
        self._data_dir = registry.data_dir
        self._lock = threading.Lock()
        self._funding_lookup = funding_lookup

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
        self.pending: List[PendingIntent] = []
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
        # 终态 reason 伴生字段（change paper-depletion-terminal 任务 3
        # 增量 3c：与 _concluded_status 同点置位/同点消费——防分派点把
        # depleted 覆写为 stop_condition；缺省 None=保持既有缺省行为）
        self._concluded_reason: Optional[str] = None
        # 枯竭计数器（证据连续模型：below_min_notional 开仓拒绝+1；
        # 开仓成交归零〔唯一物理反证〕；无开仓意图 bar 与非同类拒因
        # 不动；恢复不持久化重启归零重数——空仓枯竭态权益静止为吸收
        # 态，无信号 bar 是沉默不是反证）
        self._min_notional_streak = 0
        self._finalized = False
        # 账户级硬熔断状态（sandbox-and-report-supplement D3②）：
        # halt_drawdown_pct=None（NULL）=不启用（未配置任务行为逐位
        # 不变——判定短路）；峰值恢复=pt_equity 历史峰值（append-only
        # 权威，重启自愈）；halted_at 恢复读 task 行（首触发单次写，
        # 触发后单向不回落）。峰值初值兜底 initial_cash（零权益行=
        # 首跑任务）。
        self._halt_dd = task_row.get("halt_drawdown_pct")
        self._halted_at: Optional[str] = task_row.get("halted_at")
        self._halt_reason: Optional[str] = task_row.get("halt_reason")
        self._peak_equity = None
        if self._halt_dd is not None:
            self._peak_equity = (
                paper_repo.max_equity(self.db_path, self.task_id)
                or float(task_row["initial_cash"]))

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
        # 指标快照列集（信号行 indicator_snapshot 的取值面——任务 4：
        # DNA 引用指标列中主轴（执行周期帧）可解析者；跨轴基因
        # （gene.timeframe 标注/MTF 层基因）与直读派生列不进快照面
        # ——无「该 bar 单值」语义，披露边界在 _indicator_snapshot）
        self._snapshot_columns: List[str] = self._resolve_snapshot_columns()

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

    # ── 信号记录组装（change paper-signal-log 任务 4） ──────

    def _resolve_snapshot_columns(self) -> List[str]:
        """DNA 引用指标列 → 主轴快照列名集（A1 范围）。

        经注册表命名权威正向解析（resolve_indicator_column——与
        executor 列查找同约定单点）；解析失败（未注册/参数畸形）或
        跨轴基因（gene.timeframe 标注）跳过——快照面只承载「执行
        周期帧该 bar 单值」语义的列（MTF 层基因同属跨轴，不在本集；
        dna.signal_genes 为单周期展开面）。
        """
        from core.features.registry import (
            INDICATOR_REGISTRY, resolve_indicator_column)

        columns: List[str] = []
        seen = set()
        for gene in self.dna.signal_genes:
            if getattr(gene, "timeframe", None) is not None:
                continue  # 跨轴基因：无主轴单值语义
            reg = INDICATOR_REGISTRY.get(gene.indicator)
            if reg is None:
                continue  # 直读派生列/未注册指标不在快照面
            try:
                col = resolve_indicator_column(
                    gene.indicator, gene.params,
                    gene.field_name or "", reg.naming)
            except (KeyError, TypeError, ValueError):
                continue
            if col not in seen:
                seen.add(col)
                columns.append(col)
        return columns

    def _indicator_snapshot(
        self, df: pd.DataFrame, i: int,
    ) -> Dict[str, Any]:
        """快照列该 bar 值（NaN/inf→None——JSON 非法值转空〔SEC-1：
        oi_change_rate 等比率类指标可产 inf，裸落毒化查询面为非标
        JSON 字面量〕；列缺失跳过；行号越界/负值=无锚语境→空 dict，
        df.iloc[-1] 末行语义须防）。"""
        if not self._snapshot_columns or not 0 <= i < len(df):
            return {}
        row = df.iloc[i]
        out: Dict[str, Any] = {}
        for col in self._snapshot_columns:
            if col not in df.columns:
                continue
            v = row[col]
            out[col] = (
                None if (pd.isna(v) or not math.isfinite(v))
                else float(v))
        return out

    @staticmethod
    def _account_fields(snap: AccountSnapshot) -> Dict[str, Any]:
        """AccountSnapshot → 信号行账户快照六列（执行尝试前/求值时点）。"""
        pos = snap.position
        return {
            "equity": snap.equity, "balance": snap.balance,
            "unrealized_pnl": snap.unrealized_pnl,
            "position_side": pos.side if pos else None,
            "position_quantity": pos.quantity if pos else None,
            "position_margin": pos.margin if pos else None,
        }

    def _signal_row(
        self,
        *,
        bar_time: str,
        exec_bar_time: str,
        seq: int,
        kind: str,
        direction: Optional[str],
        size_fraction: Optional[float],
        gene_hits: List[Dict[str, Any]],
        indicator_snapshot: Dict[str, Any],
        outcome: str,
        account: Dict[str, Any],
        reject_reason: Optional[str] = None,
        fill_seq: Optional[int] = None,
        capped: Optional[int] = None,
    ) -> Dict[str, Any]:
        """信号行组装（pt_signal 落库形制——spec 字段族明细逐列）。"""
        row: Dict[str, Any] = {
            "strategy_id": self.task_row["strategy_id"],
            "bar_time": bar_time,
            "exec_bar_time": exec_bar_time,
            "seq": seq,
            "signal_kind": kind,
            "direction": direction or None,
            "size_fraction": size_fraction,
            "gene_hits": json.dumps(gene_hits, ensure_ascii=False),
            "indicator_snapshot": json.dumps(
                indicator_snapshot, ensure_ascii=False),
            "outcome": outcome,
            "reject_reason": reject_reason,
            "fill_seq": fill_seq,
            "capped": capped,
            "created_at": utc_now_iso(),
        }
        row.update(account)
        return row

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
        # 零 bar 语义（CARD-02）：刚挂载未收首根闭合 bar 的任务
        # balance 自然 NULL（首 bar 权益快照才写）——Ledger.__init__
        # 已从 initial_cash 构造初始账户态，跳过覆写即恢复初始账户
        # （零 bar 豁免见 validate_recovery 判据 2）；bars>0 而
        # balance NULL 是真损坏，由 validate_recovery 前置判据拦
        # （from_task_row 生产调用方唯一=recover_task，恒前置校验）。
        if row["balance"] is not None:
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
        # 意图产生 bar 锚=恢复游标（崩溃时点的未消费意图产生于最后
        # 处理完的 bar）；基因归因恢复不可得——空披露（见
        # PendingIntent docstring）
        executor.pending = intents_from_json(
            row["pending_intent_json"],
            signal_bar_time=row.get("last_bar_time"))
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
        from core.strategy.validator import (
            validate_dna, validate_trading_compatibility, has_plugin_sources)
        result = validate_dna(self.dna)
        if not result.is_valid:
            return self._reject(
                "invalid_dna: DNA 值域校验未过（"
                + "; ".join(result.errors) + "）")
        if has_plugin_sources(self.dna):
            return self._reject(
                "plugin_sources_unsupported: 执行环境不支持插件信号源"
                "（paper 链未接入插件信号组装）——请经研究验证链沙盒回测")
        # 纸盘链能力矩阵装载层（dynamic-stop-loss-series 4.3 D1-②
        # 纵深：受理层被绕过时的第二道——库内 dna 被改后重启装载场景；
        # 与受理层同判据单源 validate_trading_compatibility，link=
        # "paper"——series 档等纸盘不支持模式装载即拒，reasons 含
        # 档位名与「后续专项补齐」标注）
        _compat = validate_trading_compatibility(self.dna, "paper")
        if _compat:
            return self._reject(
                "trading_incompatible: " + "; ".join(_compat))

        symbol = self.task_row["symbol"]
        try:
            from core.data.validation import validate_symbol

            validate_symbol(symbol)  # 直拼前命名白名单（CARD-01②）
        except ValueError as exc:
            return self._reject(f"invalid_symbol: {exc}")

        # 挂载双白名单装载第二道（change paper-mount-and-concurrency
        # 任务 3：受理层被绕过〔库内行直插/dna 被改后重启〕时的库内
        # 行兜底——沿 D1-② 双层镜像先例；格式校验先行〔invalid_symbol
        # 是格式面〕，白名单枚举在后；拒绝 detail 双维分立可辨，值域
        # 单源 constants 白名单与受理层同判据）
        from core.paper_trading.constants import (
            PAPER_SYMBOL_WHITELIST, PAPER_TIMEFRAME_WHITELIST)
        if symbol not in PAPER_SYMBOL_WHITELIST:
            return self._reject(
                "symbol_not_supported_by_platform: 品种 "
                f"{symbol} 不在纸盘白名单"
                f"（{sorted(PAPER_SYMBOL_WHITELIST)}）——白名单外零数据"
                "接入，任务不可装载")
        if self.task_row["timeframe"] not in PAPER_TIMEFRAME_WHITELIST:
            return self._reject(
                "timeframe_not_supported_by_platform: 执行周期 "
                f"{self.task_row['timeframe']} 不在纸盘支持档位"
                f"（{sorted(PAPER_TIMEFRAME_WHITELIST)}——固定订阅制六档"
                "）；策略可在回测/研究链使用")
        path = self._data_dir / f"{symbol}_{self._timeframe}.parquet"
        if not path.exists():
            return self._reject("no_data: 执行周期 parquet 不存在")
        from core.data.storage import load_parquet

        raw = load_parquet(path)
        if raw is None or raw.empty:
            return self._reject("no_data: 执行周期 parquet 为空")

        # 衍生品数据注入（fix-paper-funding-indicator-merge 任务 A）：
        # 信号试算前注入——衍生列在场衍生品基因方可求值；语义与边界
        # 见共用面 _inject_derivatives docstring
        raw = self._inject_derivatives(raw)

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
                feed.reconcile()  # 装载校验期单次对账（尾戳缺口补拉）
                latest = feed.latest_df
                if latest is None or latest.empty:
                    return None
                frames[tf] = latest
            return frames
        finally:
            for tf, sub in temp_subs:
                self.registry.release(symbol, tf, sub)

    def _load_deriv_frame(self, filename_stem: str):
        """读衍生品归档 parquet（funding/OI），供装载期指标注入。

        缺失/为空/坏档一律返回 None（衍生品注入是增强路径——不因
        归档问题阻断纯 K 线策略装载；坏档 warning 留痕）。只读既有
        归档不做网络 IO。
        """
        from core.data.storage import load_parquet

        path = self._data_dir / f"{filename_stem}.parquet"
        if not path.exists():
            return None
        try:
            frame = load_parquet(path)
        except Exception:  # noqa: BLE001 —— 坏档降级为缺失，不阻断装载
            logger.warning("衍生品归档读取失败，按缺失跳过: %s", path)
            return None
        return frame if frame is not None and not frame.empty else None

    def _inject_derivatives(self, df: pd.DataFrame) -> pd.DataFrame:
        """衍生品列注入（装载期与运行期共用面）。

        funding/OI 归档可达时把 funding_rate / open_interest 列前向
        填充合并进 K 线帧——compute_all_indicators 见 funding_rate 列
        即产出 FundingZScore/FundingPressure（core/features/indicators
        衍生品族），衍生品基因随之可求值；归档缺失/为空/坏档时静默
        保持纯 K 线帧（纯价格策略路径零变化——merger 双 None 原样
        返回语义）。前向填充取最近一次已知值：归档末端之后的 bar
        沿用最后已知费率（stale 语义）。只读既有归档不做网络 IO
        （实时费率增量是 feed 侧后续增强，本处不越界）。

        调用面两处（fix-paper-funding-indicator-merge 任务 A/B）：
        装载期 prepare 信号试算前，与运行期 process_frames 的 MTF
        帧组装前——feed 帧只含 K 线列，主周期帧须先注入再组帧表，
        执行层衍生品基因运行期方可求值。
        """
        from core.data.derivatives_merger import merge_derivatives_into_ohlcv

        symbol = self.task_row["symbol"]
        return merge_derivatives_into_ohlcv(
            df,
            oi_df=self._load_deriv_frame(
                f"{symbol}_oi_{self._timeframe}"),
            funding_df=self._load_deriv_frame(f"{symbol}_funding"),
        )

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

    def process_frames(
        self, df: pd.DataFrame, from_idx: int,
        throttle_seconds: Optional[float] = None,
    ) -> None:
        """消费一帧闭合 bar（from_idx 起逐根八步序——回放驱动入口）。

        信号组装按整帧计算（dna_to_signal_set 全帧形制，沿旧 runner）；
        MTF 时辅周期取各 feed 最新引用组装。窗口到点/停止条件置位后
        本帧剩余 bar 不再处理（观察已结束）；stop 在 bar 边界协作退出
        （当前 bar 已完整落库）。

        throttle_seconds（add-replay-paced-mode）：逐 bar 节流尾参——
        bar 级 sleep（演示「逐根回放」形态），缺省 None 零开销、实时
        run() 路径恒不传；传入式观测参数不改执行判定语义。
        """
        # 运行期衍生品注入（fix-paper-funding-indicator-merge 任务 B
        # 最小版）：注入必须先于 MTF 帧组装——帧表主周期帧绑定注入
        # 后 df，执行层衍生品基因方可求值（MTF 层循环从帧表取层帧：
        # core/strategy/executor.py 与 mtf_engine.py 两条通道；装载期
        # 先注入后组装反序放行，注入晚于组装即帧表持纯 K 线帧、运行
        # 期首帧崩——CARD-01 修复）。stale 语义与不越界边界见
        # _inject_derivatives docstring。
        df = self._inject_derivatives(df)
        dfs_by_timeframe = self._assemble_mtf_frames(df)
        from core.strategy.executor import dna_to_signal_set

        # with_hits=True：求值披露开启点（change paper-signal-log
        # design §10——信号行的基因命中归因数据源；缺省关闭的零侵入
        # 通道仅纸盘执行器开启）
        sig_set = dna_to_signal_set(
            self.dna, df, dfs_by_timeframe, with_hits=True)
        try:
            for i in range(from_idx, len(df)):
                self.controller.check_stop()  # bar 边界协作退出锚
                if self._concluded_status is not None:
                    break  # 第 8 步已终态置位——后续 bar 不再感知
                self._process_bar(df, i, sig_set)
                # 逐 bar 节流（add-replay-paced-mode）：None 时零开销
                # （不进 time 分支）；>0 时 sleep——bar 处理完落库后再
                # 等待，节流不影响 bar 序与执行语义
                if throttle_seconds is not None:
                    time.sleep(throttle_seconds)
        except TaskStopRequested:
            self._finalize("stopped", stop_reason="user_stop")
            return
        if self._concluded_status == "completed":
            self._finalize("completed")
        elif self._concluded_status == "stopped":
            # reason 伴生字段消费（增量 3c：depleted 置位后不被本分派
            # 点覆写；缺省 None 保持既有 stop_condition 行为）
            self._finalize(
                "stopped",
                stop_reason=self._concluded_reason or "stop_condition")

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

        信号记录挂点（change paper-signal-log 任务 4）：第 3/4 步
        pending 消费（成交或被拒→信号行）、第 7 步解释器拒绝（seq=7
        行）、第 6 步落库（signals 与同 bar fills/equity/funding/
        task 游标同一原子事务——spec「信号与成交同批原子」）。第 7
        步的求值在本根成交后快照上进行（ledger 状态自第 5 步后不变，
        求值调用前移至第 6 步落库前使拒绝行与 pending 上下文并入同
        bar 事务——求值输入与产出语义等价，见 _persist_bar）。
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
        signal_rows: List[Dict[str, Any]] = []
        indicator_snapshot = self._indicator_snapshot(df, i)

        # ── 第 1/2 步：即时类判定（本根执行前快照）当根按触发价成交 ──
        # pre 求值只为取即时类意图（防线动作，非基因驱动）；pre 的
        # 非即时意图与拒绝不在消费路径（意图产出点=第 7 步 post），
        # 不落信号行
        pre = self.interpreter.interpret(
            bar, signals,
            self.ledger.snapshot(bar.close, bar.seq), self.params)
        for intent in pre.intents:
            if intent.kind == "exit_liquidation":
                self._execute(
                    intent, bar, events, signal_rows, seq=_SEQ_LIQUIDATION,
                    signal_bar_time=bar_time, gene_hits=[],
                    indicator_snapshot=indicator_snapshot)
            elif intent.kind in ("exit_sl", "exit_tp"):
                self._execute(
                    intent, bar, events, signal_rows, seq=_SEQ_STOP_TAKE,
                    signal_bar_time=bar_time, gene_hits=[],
                    indicator_snapshot=indicator_snapshot)

        # ── 第 3/4 步：上根 pending 消费（出场先于入场族）开盘价成交 ──
        # 成交或被拒均落信号行：bar_time=意图产生 bar（决策锚）、
        # exec_bar_time=本根（差一根）、fill_seq 关联同 seq 成交；
        # 指标快照取决策 bar 值（signal_bar_index 定位；恢复语境 -1
        # →空 dict——不伪造快照）
        for p in [x for x in self.pending
                  if x.intent.kind in _EXIT_PENDING_KINDS]:
            self._execute(
                p.intent, bar, events, signal_rows, seq=_SEQ_PENDING_EXIT,
                signal_bar_time=p.signal_bar_time,
                gene_hits=list(p.gene_hits),
                indicator_snapshot=(
                    self._indicator_snapshot(df, p.signal_bar_index)
                    if p.signal_bar_index >= 0 else {}))
        for p in [x for x in self.pending
                  if x.intent.kind not in _EXIT_PENDING_KINDS]:
            self._execute(
                p.intent, bar, events, signal_rows, seq=_SEQ_PENDING_ENTRY,
                signal_bar_time=p.signal_bar_time,
                gene_hits=list(p.gene_hits),
                indicator_snapshot=(
                    self._indicator_snapshot(df, p.signal_bar_index)
                    if p.signal_bar_index >= 0 else {}))
        self.pending = []

        # ── 第 5 步：资金费（先补旧账再记新账） ──
        funding_rows, backfills = self._settle_funding(bar, bar_time)

        # ── 第 7 步求值+第 6 步落库共用的成交后快照（单次求值收敛
        #    ——同输入同时点纯读，Q-4；ledger 状态自第 5 步后不变，
        #    求值前移使拒绝行与 pending 上下文并入第 6 步同 bar 事务） ──
        snap = self.ledger.snapshot(bar.close, bar.seq)
        # 熔断峰值跟踪与触发判定（D3②——bar 边界判：成交后快照权益
        # 参与峰值；回撤 (peak−equity)/peak ≥ 阈值〔含等号〕首触发
        # 置位〔单向不回落——权益修复不自动恢复开仓〕；触发事实随
        # 本 bar 第 6 步落库；halted_at 由 bar_time 携带）。None=未
        # 配置短路（存量逐位不变）
        if (self._halt_dd is not None and self._halted_at is None
                and self._peak_equity is not None):
            if snap.equity > self._peak_equity:
                self._peak_equity = snap.equity
            if self._peak_equity > 0.0 and (
                    (self._peak_equity - snap.equity) / self._peak_equity
                    >= float(self._halt_dd)):
                self._halted_at = bar_time
                self._halt_reason = (
                    "equity_drawdown_from_peak_ge_halt_drawdown_pct")
        post = self.interpreter.interpret(bar, signals, snap, self.params)
        hits = sig_set.hits
        for rej in post.rejections:
            # 解释器内拒绝（求值层拦截）：bar_time=exec_bar_time=本根
            # （拒绝发生 bar）、seq=7；账户快照=求值时点；基因归因按
            # 被拦 kind 取披露键（求值披露通道关闭时空数组）
            gene_hits: List[Dict[str, Any]] = []
            if hits is not None:
                gene_hits = list(hits.get(rej.kind, [])[i])
            signal_rows.append(self._signal_row(
                bar_time=bar_time, exec_bar_time=bar_time,
                seq=_SEQ_INTERPRETER_REJECT, kind=rej.kind,
                direction=rej.direction or None,
                size_fraction=self._rejected_size_fraction(rej.kind),
                gene_hits=gene_hits,
                indicator_snapshot=indicator_snapshot,
                outcome="rejected",
                account=self._account_fields(snap),
                reject_reason=rej.reason))
        new_pending = [
            PendingIntent(
                intent=x, signal_bar_time=bar_time,
                gene_hits=tuple(self._intent_hits(hits, x, i)),
                signal_bar_index=i)
            for x in post.intents
            if x.kind not in ("exit_liquidation", "exit_sl", "exit_tp")]

        # ── 第 6 步：权益快照（收盘价估值）+单 bar 原子落库 ──
        self.bars_processed += 1
        self.last_bar_time = bar_time
        self.pending = new_pending
        self._persist_bar(
            bar_time, events, snap, funding_rows, backfills,
            signals=signal_rows)

        # trail 峰值落账（持仓期逐 bar——判定自足，状态权威落点）
        self.ledger.update_trail_peak(bar)

        # 达标两维判定（paper-trading-revamp 组 B）：equity 取本 bar
        # 快照同源值（与 pt_equity 行同源，不自算第二种）；first_met_at
        # 幂等单次写（NULL→非 NULL 一次——下一 bar 起随 task_state 持恒）
        from core.paper_trading.qualification import (
            evaluate_two_dimensions, first_met_writable)
        _dims = evaluate_two_dimensions(
            snap.equity, float(self.task_row["initial_cash"]), self.ledger.closed_trades)
        if first_met_writable(self._first_met_at, _dims):
            self._first_met_at = bar_time

        # ── 第 8 步：窗口计数已并入第 6 步 task_state；停止条件判定 ──
        self._evaluate_conclusion(snap)

    def _intent_hits(
        self, hits: Optional[Dict[str, Any]], intent: Intent, i: int,
    ) -> List[Dict[str, Any]]:
        """意图 → 该 bar 命中基因项（求值披露通道；kind→hits 键映射
        见 _hits_key_for_intent——防线族/通道关闭时空数组）。"""
        if hits is None:
            return []
        key = _hits_key_for_intent(intent)
        if key is None:
            return []
        return list(hits.get(key, [])[i])

    def _rejected_size_fraction(self, kind: str) -> Optional[float]:
        """被拒意图的比例自描述（解释器 Rejection 不携带——按 kind
        从参数单源取：add→add_fraction；其余 None）。"""
        if kind == "add":
            return self.params.add_fraction
        return None

    def _execute(
        self,
        intent: Intent,
        bar: ClosedBar,
        events: List[ExecutionEvent],
        signal_rows: List[Dict[str, Any]],
        seq: int,
        *,
        signal_bar_time: str,
        gene_hits: List[Dict[str, Any]],
        indicator_snapshot: Dict[str, Any],
    ) -> None:
        """执行单条意图（FillOutcome 消费面）：成交落事件+filled 信号
        行（fill_seq=seq 关联成交）；被拦落 rejected 信号行（拒绝发生
        bar=承载 bar）。账户快照=执行尝试前时点（C7）。

        entry_atr 接线（修复卡：层间接线遗漏）：执行 bar 的 ATR 值
        （feed 指标列装载链）透传 kernel——open 意图的 risk_budget×atr
        档定仓距离消费（sl_mult×该 bar ATR）；其余意图不读（kernel
        签名语义）。漏接会使 atr 档距离恒 NaN 走公式单点 (0,0) 防御
        恒回退 fixed。
        """
        snap_before = self.ledger.snapshot(bar.close, bar.seq)
        # 熔断拦截（D3②：开仓前判定——halted 置位后 open 意图被拦，
        # 持仓与既有平仓逻辑照常〔close/exit 族不进本分支〕）：落
        # rejected 信号行（沿成交层拦截显式化形制，不入 ledger——
        # 不产生资金变动）。未配置/未触发短路零影响。
        if (intent.kind == "open" and self._halted_at is not None):
            exec_bar_time_bt = (bar.timestamp.isoformat()
                                if bar.timestamp is not None else str(bar.seq))
            signal_rows.append(self._signal_row(
                outcome="rejected",
                reject_reason="halted_drawdown",
                bar_time=signal_bar_time, exec_bar_time=exec_bar_time_bt,
                seq=seq, kind=intent.kind,
                direction=intent.direction or None,
                size_fraction=intent.size_fraction,
                gene_hits=gene_hits,
                indicator_snapshot=indicator_snapshot,
                account=self._account_fields(snap_before)))
            return
        # 最小名义值阈值注入（change paper-depletion-terminal 任务 2：
        # 启动装载表按任务 symbol 取——白名单币恒有值；表缺键 None=
        # 不判定防御形态）
        from core.paper_trading.min_notional import MIN_NOTIONAL

        outcome = self.ledger.execute(
            intent, bar, self.fill_model, entry_atr=bar.atr,
            min_notional=MIN_NOTIONAL.get(self.task_row["symbol"]))
        fill = outcome.fill
        # 承载 bar 时刻单算（信号行 exec_bar_time 与事件 bar_time 同式
        # ——Q-5 收敛单变量）
        exec_bar_time = (bar.timestamp.isoformat()
                         if bar.timestamp is not None else str(bar.seq))
        # 成交/被拒两段信号行的公共参数（Q-5 收敛）
        row_common = dict(
            bar_time=signal_bar_time, exec_bar_time=exec_bar_time,
            seq=seq, kind=intent.kind,
            direction=intent.direction or None,
            size_fraction=intent.size_fraction,
            gene_hits=gene_hits,
            indicator_snapshot=indicator_snapshot,
            account=self._account_fields(snap_before))
        if fill is None:
            # 枯竭计数四径之一（任务 3）：开仓被拒且拒因=below_min_
            # notional→+1（insufficient_funds/context_invalid 不动——
            # 前者是更深枯竭证据非反证、后者是语境防御不在计数语境）
            if intent.kind == "open" \
                    and outcome.reject_reason == "below_min_notional":
                self._min_notional_streak += 1
            signal_rows.append(self._signal_row(
                outcome="rejected",
                reject_reason=outcome.reject_reason, **row_common))
            return
        if intent.kind == "open":
            # 枯竭计数四径之二：开仓成交→归零（唯一物理反证=发得起）
            self._min_notional_streak = 0
        if fill.realized_pnl is not None:
            self.total_pnl += fill.realized_pnl
            if intent.kind not in ("open", "add", "reduce"):
                # 全平事件计数（与 ledger.closed_trades 同事件集——
                # win+loss=total 自洽口径；reduce 部分盈亏只进 total_pnl）
                if fill.realized_pnl > 0:
                    self.win_count += 1
                else:
                    self.loss_count += 1
        events.append(ExecutionEvent(
            fill=fill,
            bar_time=exec_bar_time,
            seq=seq,
            equity_before=outcome.equity_before,
            available_before=outcome.available_before,
            margin_used=outcome.margin_used))
        # 成交信号行：fill_seq=seq（C5 三元组 (task_id, exec_bar_time,
        # fill_seq) 定位成交）；capped=cap 截断披露（非拒绝）
        signal_rows.append(self._signal_row(
            outcome="filled", fill_seq=seq,
            capped=1 if fill.capped else None, **row_common))
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
        # 取值序：构造注入优先（回放历史归档）→ registry feed（实时）
        # → 全 None 回退（挂起语义）——change add-replay-engine 任务 1.3
        feed = self.registry.get(self.task_row["symbol"], self._timeframe)
        if self._funding_lookup is not None:
            rate_lookup = self._funding_lookup
        elif feed is not None:
            rate_lookup = feed.funding_rates
        else:
            rate_lookup = lambda points: {p: None for p in points}

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
        signals: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """单 bar 原子落库（fills+signals+equity+funding+task 游标与
        账户态一个事务）+task_update 推送。signals=该 bar 信号行
        （成交/被拒——spec「信号与成交同批原子」；缺省 None 兼容
        既有调用面）。"""
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
            signals=signals,
            equity={
                "equity": snap.equity, "balance": snap.balance,
                "unrealized_pnl": snap.unrealized_pnl,
                "position_side": position.side if position else None,
                "cumulative_funding": self.ledger.total_funding,
                # trail 档当 bar 止损价观测（paper-trading-revamp 组 E）：
                # 决策时点值（出场判定消费 ledger 内部 trail_peak，不读此列——
                # 观测面纪律，白名单=写入点+equity 查询面）
                "trail_sl_price": (
                    float(position.sl_price)
                    if (self.params.sl_mode == "trail" and position is not None
                        and position.sl_price is not None)
                    else None),
            },
            funding=funding_rows or None,
            **extra,
            task_state={
                "first_met_at": self._first_met_at,
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
                # 熔断触发运行态（D3②：首触发单次写——白名单键，值
                # None 时 update 侧按既有白名单语义跳过/写 NULL 均不
                # 破坏「首触发单次写」；显式传 None 保持未触发态）
                "halted_at": self._halted_at,
                "halt_reason": self._halt_reason,
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
        八步序完整落库。判定序固定：window→stop_loss_pct→depleted
        （同 bar 多条件命中时前者先——window 优先即本 bar 落库后结题
        的确定性序）。
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
                self._concluded_reason = "stop_condition"
                return
        # 枯竭终态（change paper-depletion-terminal 任务 3：证据连续
        # 计数≥N 且空仓→stopped+depleted；空仓前置=计数生于空仓开仓
        # 被拒、持仓出现必经开仓成交即清零——持仓中 streak 累积不触
        # 发的显式防御）
        from core.paper_trading.constants import DEPLETED_STREAK_N

        if (self._min_notional_streak >= DEPLETED_STREAK_N
                and self.ledger.position is None):
            self._concluded_status = "stopped"
            self._concluded_reason = "depleted"
            return  # C4-05：置位即止（零行为变化——防未来追加分支
            # 静默覆写已置位的终态信号，变异 D2 脆弱性封死）

    def _harvest_expired_signals(self) -> None:
        """终态收割：未消费 pending 意图逐条落 expired 行（spec 记录
        粒度与写入时机——窗口末根 pending 收割一次性路径）。

        - exec_bar_time=last_bar_time、seq=0（幂等键吸收收割防重入的
          重复落库——_finalized 标志外的库级第二防线）；
        - bar_time=意图产生 bar（恢复重建语境无锚=last_bar_time 兜底）；
        - 账户快照=最后 equity 行+当前任务行持仓组（终态时点账户；
          运行/恢复两场景统一从库取——内存态与库一致，恢复态无内存）；
        - indicator_snapshot 空 dict（无当根 bar 语境，不伪造快照）。
        """
        if not self.pending:
            return
        task = paper_repo.get_task(self.db_path, self.task_id) or {}
        snap_row = paper_repo.latest_equity_snapshot(
            self.db_path, self.task_id)
        last_bar = self.last_bar_time or task.get("last_bar_time") or ""
        account: Dict[str, Any] = {
            "position_side": task.get("position_side"),
            "position_quantity": task.get("position_quantity"),
            "position_margin": task.get("position_margin"),
        }
        if snap_row is not None:
            account["equity"] = snap_row["equity"]
            account["balance"] = snap_row["balance"]
            account["unrealized_pnl"] = snap_row["unrealized_pnl"]
        else:  # 防御：pending 非空必有已处理 bar（equity 行在场）
            account["equity"] = task.get("balance") or 0.0
            account["balance"] = task.get("balance") or 0.0
            account["unrealized_pnl"] = task.get("unrealized_pnl") or 0.0
        rows = [
            self._signal_row(
                bar_time=p.signal_bar_time or last_bar,
                exec_bar_time=last_bar, seq=_SEQ_EXPIRED,
                kind=p.intent.kind, direction=p.intent.direction or None,
                size_fraction=p.intent.size_fraction,
                gene_hits=list(p.gene_hits), indicator_snapshot={},
                outcome="expired", account=account)
            for p in self.pending
        ]
        paper_repo.save_signals(self.db_path, self.task_id, rows)
        logger.info(
            "纸盘任务 %s 终态收割 %d 条 expired 信号", self.task_id,
            len(rows))

    def _finalize(self, status: str, *, stop_reason: Optional[str] = None,
                  error_detail: Optional[str] = None) -> None:
        """终态迁移+WS 推送+结题回调（幂等；回调异常不回滚终态——
        报告是派生物）。"""
        if getattr(self, "_finalized", False):
            return
        self._finalized = True
        self._concluded_status = status
        # 终态收割（expired 行）：先于终态写落库（证据与终态同批语义；
        # 收割异常传播——不静默吞证据缺口）
        self._harvest_expired_signals()
        fields: Dict[str, Any] = {"status": status}
        if status in TERMINAL_STATUSES:
            fields["stopped_at"] = utc_now_iso()
        if stop_reason is not None:
            fields["stop_reason"] = stop_reason
        if error_detail is not None:
            fields["error_detail"] = error_detail
        # 达标首满足时点终局承载（paper-trading-revamp 组 B 修复）：首满足
        # bar 恰为终局 bar 时（窗口到点/stop 边界），_persist_bar 已过、无
        # 下一根承载——此处兜底写库；非 None 才带（从未满足保持 NULL，
        # 不覆写恢复场景装载值——幂等单次写语义）
        if self._first_met_at is not None:
            fields["first_met_at"] = self._first_met_at
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
