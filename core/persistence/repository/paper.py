"""paper repository（rebuild-paper-trading 2.2）：纸盘五表新仓库。

与旧仓库 paper_trading.py 物理分开（design §4.6——旧三表留档不删
不改不写，无跨新旧外键；旧仓库随组 7 退役）。行为锚点：
- 单 bar 原子落库 append_bar：fills+equity+funding+task 游标与账户态
  一个事务（engine.begin()），三明细表 INSERT OR IGNORE 幂等（唯一键
  吸收恢复重放）；
- 级联删 delete_task 五表单事务；
- clone 新建 pending 行（research_status_snapshot 按 clone 创建时点
  重新快照——「创建时快照」语义一致性，design §5.1）；
- 游标/账户/统计组仅经 append_bar 原子更新，update_task 白名单收敛
  为状态机字段（写者隔离，design §5.5——运行态权威=pt_task 行）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union
from uuid import uuid4

from sqlalchemy import (
    Connection, delete, desc, func, insert, select, update,
)

from core.persistence.orm.models import PtEquity, PtFill, PtFunding, PtReport, PtTask
from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.timestamps import utc_now_iso as _now

# 状态机字段（调度器/执行器/恢复器的任务行唯一外写面；账户/持仓/
# 统计/游标组是 append_bar 的原子专属面，不开放外写）
_ALLOWED_UPDATE_COLUMNS = frozenset({
    "status", "started_at", "stopped_at", "stop_reason", "heartbeat_at",
    "error_detail", "updated_at",
})

# append_bar 的 task 行更新面（八步序第 6-8 步产出的绝对状态快照：
# 游标+账户+统计+pending 意图+心跳——绝对值非增量，重放收敛）
_BAR_STATE_COLUMNS = frozenset({
    "bars_processed", "last_bar_time", "balance", "position_side",
    "position_entry", "position_quantity", "position_margin",
    "position_open_cost", "position_sl_price", "position_tp_price",
    "position_trail_peak", "position_entry_bar_seq", "unrealized_pnl",
    "pending_intent_json", "total_trades", "win_count", "loss_count",
    "total_pnl", "heartbeat_at", "updated_at",
})

# pt_task 状态机合法前态单源（design §5.1 五态+§7.1 端点 4 未拾取撤销
# pending→stopped+tasks 3.2 装载校验拒绝 pending→error 不进 running）：
# by-target 索引——update_task 的 status 写默认守门直接消费。scheduler
# 侧迁移对集（by-source 索引）经 PT_STATUS_TRANSITIONS 派生引用，不另
# 立第二逻辑源（命名单源纪律；import 方向=scheduler→persistence 正向，
# persistence 不引 core.paper_trading——infra 零域依赖）
PT_STATUS_PREDECESSORS: Dict[str, frozenset] = {
    "pending": frozenset(),  # 无迁移入 pending（clone=新建行非迁移）
    "running": frozenset({"pending"}),
    "completed": frozenset({"running"}),
    "stopped": frozenset({"pending", "running"}),
    "error": frozenset({"pending", "running"}),
}

# (from, to) 迁移对派生视图（scheduler 状态机测试消费——数据单源在
# 上映射，两视图不可能漂移）
PT_STATUS_TRANSITIONS: frozenset = frozenset(
    (src, tgt)
    for tgt, srcs in PT_STATUS_PREDECESSORS.items()
    for src in srcs
)

# 终态集派生视图（状态图无出边态=keys−union(values)——与
# credential._active_statuses 同数据单源互补；task_end 回流扫描与
# 结题门槛消费，reporting.FINAL_STATUSES 语义等价但独立声明避免
# 调度器拖 reporting 聚合链）
PT_STATUS_TERMINAL: frozenset = frozenset(PT_STATUS_PREDECESSORS) - set().union(
    *PT_STATUS_PREDECESSORS.values())


def _as_status_tuple(expected_status: Union[str, Iterable[str]]) -> tuple:
    """expected_status 归一（str→单元组；可迭代→元组）——显式前态集
    的两种传形统一为 IN 参数（迁移门与纯前置两分支共用微单点）。"""
    return (
        (expected_status,) if isinstance(expected_status, str)
        else tuple(expected_status)
    )


def _resolve_status_gate(
    target: str, expected_status: Optional[Union[str, Iterable[str]]],
) -> tuple:
    """status 写的前态门集合：显式 expected_status 优先（调用方自定义
    收紧）；缺省=合法前态 ∪ 目标态自身（同态幂等覆写放行——恢复续跑
    场景依赖）。未知目标态按仅同态放行（保守：五态外取值本就不该出现）。"""
    if expected_status is not None:
        return _as_status_tuple(expected_status)
    return tuple(PT_STATUS_PREDECESSORS.get(target, frozenset()) | {target})


def new_task_id() -> str:
    """纸盘任务 id：uuid4 hex 12 位（沿旧轨 api/routes/trading.py:111 形制）。"""
    return uuid4().hex[:12]


def resolve_research_status(strategy_row: Dict[str, Any]) -> str:
    """研究验证状态四值映射（design §9.2 单源；创建/clone 快照共用）：
    status=draft→"draft"；qualified=NULL→"unverified"；1→"verified"；
    0→"verification_failed"。"""
    if strategy_row.get("status") == "draft":
        return "draft"
    qualified = strategy_row.get("qualified")
    if qualified is None:
        return "unverified"
    return "verified" if qualified == 1 else "verification_failed"


def create_task(
    db_path: Path,
    *,
    strategy_id: str,
    dna_snapshot_json: str,
    symbol: str,
    timeframe: str,
    research_status_snapshot: str,
    initial_cash: float = 100_000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    leverage: int = 1,
    window_bars: Optional[int] = None,
    stop_loss_pct: Optional[float] = None,
    task_id: Optional[str] = None,
    conn: Optional[Connection] = None,
) -> str:
    """创建纸盘任务（status 硬编码 pending、bars_processed=0、账户组
    NULL、统计组 0）。测试直建通道（design §11 偏差 2）：全参数直收
    不查 strategy 表——REST 层负责读 strategy 组装快照后调用。

    conn 可选透传（build-strategy-lifecycle 5.1 挂盘同事务）：传入则
    插入走外层事务（挂盘迁移+建任务两侧原子成败——外层回滚则两侧
    皆无）；缺省自开单事务（既有直建行为不变）。
    """
    now = _now()
    tid = task_id or new_task_id()
    values = dict(
        task_id=tid, status="pending", strategy_id=strategy_id,
        dna_snapshot_json=dna_snapshot_json, symbol=symbol,
        timeframe=timeframe, initial_cash=initial_cash, fee=fee,
        slippage=slippage, leverage=leverage, window_bars=window_bars,
        stop_loss_pct=stop_loss_pct, bars_processed=0,
        research_status_snapshot=research_status_snapshot,
        created_at=now, updated_at=now,
    )
    if conn is not None:
        conn.execute(insert(PtTask).values(**values))
        return tid
    engine = _engine_for(db_path)
    with engine.begin() as own:
        own.execute(insert(PtTask).values(**values))
    return tid


def get_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 取纸盘任务（不存在返回 None）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(PtTask).where(PtTask.task_id == task_id)
        ).mappings().first()
    return dict(row) if row else None


def update_task(
    db_path: Path, task_id: str, *,
    expected_status: Optional[Union[str, Iterable[str]]] = None,
    **kwargs,
) -> int:
    """更新任务状态机字段（白名单过滤+updated_at 刷新，空集 no-op）。

    游标/账户/统计组不在白名单——仅经 append_bar 原子更新（模块
    docstring 写者隔离锚点）。

    status 写的前态守门（缺陷⑤ CAS 修订版：默认内建，spec R1「应答
    stopped 即终局承诺」要求写点原子——无条件直调也过不了门）：
    - 写 status 且不提供 expected_status：默认守门 WHERE status IN
      (合法前态 ∪ {目标态自身})——同态幂等覆写放行（恢复续跑场景
      依赖），非法迁移零行命中拒写（返回 0 由调用方分流，不静默）；
      前态单源=PT_STATUS_PREDECESSORS；先例 evolution.update_task
      expected_statuses 条件更新同构语义；
    - 提供 expected_status（str 或集合）：以显式前态集替换默认门
      （自定义收紧，如 stop 的 pending 分支拒 running 态）；
    - 不写 status 的更新不受守门影响（无 expected_status 时无条件；
      有则按显式集前置）；
    - expected_status 是 WHERE 条件不是新写面，与六字段白名单写者
      隔离并存；
    - 返回受影响行数（旧调用面不接收返回值，零破坏）。
    """
    if not kwargs:
        return 0
    filtered = {k: v for k, v in kwargs.items() if k in _ALLOWED_UPDATE_COLUMNS}
    if not filtered:
        return 0
    filtered["updated_at"] = _now()
    stmt = update(PtTask).where(PtTask.task_id == task_id)
    if "status" in filtered:
        stmt = stmt.where(
            PtTask.status.in_(_resolve_status_gate(filtered["status"],
                                                  expected_status))
        )
    elif expected_status is not None:
        stmt = stmt.where(PtTask.status.in_(_as_status_tuple(expected_status)))
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        result = conn.execute(stmt.values(**filtered))
        return result.rowcount


def list_tasks(
    db_path: Path, status: Optional[str] = None, limit: int = 50,
    offset: int = 0, order: str = "desc",
) -> List[Dict[str, Any]]:
    """列纸盘任务（status 过滤+created_at 排序+分页；desc=REST 列表
    倒序缺省，asc=调度器拾取最早优先序）。"""
    stmt = select(PtTask)
    if status:
        stmt = stmt.where(PtTask.status == status)
    col = PtTask.created_at.desc() if order == "desc" else PtTask.created_at.asc()
    stmt = stmt.order_by(col).limit(limit).offset(offset)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def count_tasks(db_path: Path, status: Optional[str] = None) -> int:
    """计纸盘任务数（可按 status 过滤）。"""
    stmt = select(func.count()).select_from(PtTask)
    if status:
        stmt = stmt.where(PtTask.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def append_bar(
    db_path: Path,
    task_id: str,
    *,
    bar_time: str,
    fills: List[Dict[str, Any]],
    equity: Dict[str, Any],
    funding: Optional[List[Dict[str, Any]]] = None,
    task_state: Optional[Dict[str, Any]] = None,
    funding_backfills: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """单 bar 原子落库：fills+equity+funding+task 游标与账户态一个事务。

    - fills 逐条 INSERT OR IGNORE——唯一键 (task_id, bar_time, seq)
      吸收恢复重放（首写为准）；
    - equity 一行 OR IGNORE——唯一键 (task_id, bar_time)；
    - funding 逐条 OR IGNORE——唯一键 (task_id, settle_time)（一结算点
      一行，补记回填不产生新行）；
    - funding_backfills（Q-2 运行中补旧账，tasks 3.5「先补旧账再记
      新账」）：逐条条件 UPDATE 置 rate/amount/status='settled'，WHERE
      含 status='pending' 前置——已 settled 行不命中（影响行数 0 静默
      跳过），这是崩溃重放后二次扣款路径的幂等防线；
    - task_state 为该 bar 终态的绝对快照（白名单 _BAR_STATE_COLUMNS），
      其 UPDATE 附 status='running' 前置（CARD-06）：终态后零行命中
      静默接受——明细照落（append-only 披露面，spec「保留已产出
      数据」）、运行态组冻结（终态封账语义延伸；僵尸执行器迟到写的
      旧 bar 明细经唯一键幂等吸收零污染，运行态权威不被终态后覆写）；
    - 任一写失败整体回滚（原子性）。
    """
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        for f in fills:
            conn.execute(
                insert(PtFill).prefix_with("OR IGNORE").values(
                    task_id=task_id, bar_time=bar_time, seq=f["seq"],
                    kind=f["kind"], side=f.get("side"), price=f.get("price"),
                    quantity=f.get("quantity"), fee=f.get("fee"),
                    realized_pnl=f.get("realized_pnl"), reason=f.get("reason"),
                )
            )
        conn.execute(
            insert(PtEquity).prefix_with("OR IGNORE").values(
                task_id=task_id, bar_time=bar_time,
                equity=equity["equity"], balance=equity["balance"],
                unrealized_pnl=equity["unrealized_pnl"],
                position_side=equity.get("position_side"),
                cumulative_funding=equity.get("cumulative_funding", 0.0),
            )
        )
        # 先补旧账（backfill 已有 pending 行）再记新账（新结算 INSERT）
        for rb in funding_backfills or ():
            conn.execute(
                update(PtFunding)
                .where(PtFunding.task_id == task_id,
                       PtFunding.settle_time == rb["settle_time"],
                       PtFunding.status == "pending")
                .values(rate=rb["rate"], amount=rb["amount"],
                        status="settled")
            )
        for fd in funding or ():
            conn.execute(
                insert(PtFunding).prefix_with("OR IGNORE").values(
                    task_id=task_id, settle_time=fd["settle_time"],
                    bar_time=bar_time, status=fd.get("status", "settled"),
                    rate=fd.get("rate"), amount=fd.get("amount"),
                    position_quantity=fd["position_quantity"],
                    settle_price=fd["settle_price"],
                )
            )
        if task_state:
            filtered = {
                k: v for k, v in task_state.items() if k in _BAR_STATE_COLUMNS
            }
            if filtered:
                filtered["updated_at"] = _now()
                # CARD-06：running 前置——终态后零行命中静默接受
                # （明细照落、运行态组冻结，见函数 docstring）
                conn.execute(
                    update(PtTask)
                    .where(PtTask.task_id == task_id,
                           PtTask.status == "running")
                    .values(**filtered)
                )


def backfill_funding_rows(
    db_path: Path,
    task_id: str,
    rows: List[Dict[str, Any]],
) -> int:
    """终态后回补（Q-2 终态封账，tasks 3.5 调度器守护例程专用）：
    独立事务只 UPDATE pt_funding 行——SET rate/amount、status
    'pending'→'settled_disclosure_only'（三态披露位）。

    - MUST NOT 触碰 pt_task 行与任何 balance（终态封账 append-only：
      结题时点账户是可得最好事实，回补只补「真实费率是多少」这条
      证据，不重写已固化成绩）；
    - WHERE 含 status='pending' 前置：已处理行不命中，重放幂等；
    - 返回实际回填行数（供回补例程披露计数）。
    """
    engine = _engine_for(db_path)
    total = 0
    with engine.begin() as conn:
        for rb in rows:
            result = conn.execute(
                update(PtFunding)
                .where(PtFunding.task_id == task_id,
                       PtFunding.settle_time == rb["settle_time"],
                       PtFunding.status == "pending")
                .values(rate=rb["rate"], amount=rb["amount"],
                        status="settled_disclosure_only")
            )
            total += result.rowcount
    return total


def list_fills(
    db_path: Path, task_id: str, limit: int = 100, offset: int = 0,
) -> List[Dict[str, Any]]:
    """列任务成交流水（bar_time 升序+seq 升序——八步序审计读序）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(PtFill).where(PtFill.task_id == task_id)
            .order_by(PtFill.bar_time.asc(), PtFill.seq.asc())
            .limit(limit).offset(offset)
        ).mappings().all()
    return [dict(r) for r in rows]


def list_equity_snapshots(
    db_path: Path, task_id: str, limit: int = 100, offset: int = 0,
) -> List[Dict[str, Any]]:
    """取任务权益曲线（bar_time 升序+分页）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(PtEquity).where(PtEquity.task_id == task_id)
            .order_by(PtEquity.bar_time.asc())
            .limit(limit).offset(offset)
        ).mappings().all()
    return [dict(r) for r in rows]


def list_funding_records(
    db_path: Path, task_id: str, limit: int = 100, offset: int = 0,
) -> List[Dict[str, Any]]:
    """取任务资金费明细（settle_time 升序+分页——对账披露面）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(PtFunding).where(PtFunding.task_id == task_id)
            .order_by(PtFunding.settle_time.asc())
            .limit(limit).offset(offset)
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_task(db_path: Path, task_id: str) -> bool:
    """删除纸盘任务及其全部关联数据（五表级联单事务；不存在返回
    False）。终态门槛（仅终态可删）是路由层语义，仓库纯数据操作。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        exists = conn.execute(
            select(PtTask.task_id).where(PtTask.task_id == task_id)
        ).first()
        if exists is None:
            return False
        conn.execute(delete(PtFill).where(PtFill.task_id == task_id))
        conn.execute(delete(PtEquity).where(PtEquity.task_id == task_id))
        conn.execute(delete(PtFunding).where(PtFunding.task_id == task_id))
        conn.execute(delete(PtReport).where(PtReport.task_id == task_id))
        conn.execute(delete(PtTask).where(PtTask.task_id == task_id))
        return True


def clone_task(
    db_path: Path, task_id: str, *, new_task_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """克隆重跑：同 dna_snapshot+同任务参数新建 pending 行，进度/账户/
    统计组归零；research_status_snapshot 按 clone 创建时点重新快照
    （读当前 strategy 行重算——strategy 已删则 ValueError 上抛调用方
    转业务错误）。原任务不存在返回 None。

    字段装配委托 create_task 单点（B4 收敛）：clone 只传复制面+重算
    快照，归零硬编码（pending/bars_processed=0/账户 NULL/统计 0/时间
    戳 now）复用 create_task——pt_task 加列时装配面单处维护，clone
    侧新增必填参数显式暴露而非静默缺省。
    """
    from core.persistence.repository.strategy import get_strategy

    source = get_task(db_path, task_id)
    if source is None:
        return None
    strategy = get_strategy(db_path, source["strategy_id"])
    if strategy is None:
        raise ValueError(
            f"strategy {source['strategy_id']!r} 不存在，无法重算快照")

    tid = create_task(
        db_path,
        task_id=new_task_id,
        strategy_id=source["strategy_id"],
        dna_snapshot_json=source["dna_snapshot_json"],
        symbol=source["symbol"], timeframe=source["timeframe"],
        research_status_snapshot=resolve_research_status(strategy),
        initial_cash=source["initial_cash"], fee=source["fee"],
        slippage=source["slippage"], leverage=source["leverage"],
        window_bars=source["window_bars"],
        stop_loss_pct=source["stop_loss_pct"],
    )
    return get_task(db_path, tid)


def save_report(
    db_path: Path,
    task_id: str,
    *,
    report_version: int,
    generated_at: str,
    as_of: str,
    report_json: str,
) -> None:
    """落结题报告（INSERT OR REPLACE——(task_id, report_version) 复合 PK
    上重算覆盖：结题初版 v1 恒新写、终态后补记修订版恒 v2 重算覆盖，
    design §4.5 版本机制）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(
            insert(PtReport).prefix_with("OR REPLACE").values(
                task_id=task_id, report_version=report_version,
                generated_at=generated_at, as_of=as_of,
                report_json=report_json,
            )
        )


def get_report(
    db_path: Path, task_id: str, version: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """取结题报告：version 指定取该版；缺省最新版（report_version
    倒序首行）。无报告返回 None。"""
    engine = _engine_for(db_path)
    stmt = select(PtReport).where(PtReport.task_id == task_id)
    if version is None:
        stmt = stmt.order_by(desc(PtReport.report_version)).limit(1)
    else:
        stmt = stmt.where(PtReport.report_version == version).limit(1)
    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None
