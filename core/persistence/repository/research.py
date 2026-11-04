"""research repository（add-ai-research-mcp 1.2）：研究域八表读写。

形制锚：core/persistence/repository 既有域文件（db_path + engine.begin 函数级
事务、_engine_for/_now 共享 helper）。设计卡（幂等/并发/事务）权威在
core/analysis/DESIGN.md（随 7.3 建立），本文件只落存储语义：

- 双写原子性经组合函数保证（一个 engine.begin 覆盖双表写入，附.6）
- 幂等键查询：假设 (conditions_hash,ranges_hash) WHERE active；
  验证 (hypothesis_id,strategy_id,range_hash)
- 假设迁移是条件更新（WHERE status='active'），影响行数由调用方判读
  （0 → 重查：同目标幂等返回 / 异终态 409，卡2）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import and_, case, func, insert, or_, select, update

from core.analysis.errors import load_stored_json  # 轻量定义点（errors.py 头部声明：任何模块可安全模块级 import）
from core.persistence.orm.models import (
    AnalysisTask,
    FeatureRequest,
    FormulaPlugin,
    ModelPlugin,
    ResearchHypothesis,
    ResearchLog,
    SignalRegistryEntry,
    Strategy,
    ValidationRecord,
    VrEquityCurve,
    VrSignalTrace,
    VrTradeDetail,
)
from core.persistence.orm.engine import engine_for as _engine_for
from core.persistence.timestamps import utc_now_iso as _now


def _new_id() -> str:
    """研究域行 id：uuid4 hex（日志 id 生成内聚于此，调用方不关心）。"""
    return uuid4().hex


def _apply_keyset(
    stmt: Any,
    created_at_col: Any,
    pk_col: Any,
    cursor: Optional[Tuple[Optional[str], str]],
    order: str,
) -> Any:
    """研究域列表的 keyset 过滤与稳定排序（四表同构收口）。

    cursor=(created_at, 主键) 按字典序严格比较（>cursor / <cursor 的
    and_/or_ 展开形，不依赖 SQLite 行值支持）；order 缺省 asc——
    offset/limit 翻页消费方（iter_all_pages）零改动，HTTP 列表端点
    传 desc（最新在前）并经 cursor 续页（深翻页漂移免疫）。

    NULL 语义（仅 desc：cards 的 recent_activity_at 标量子查询可
    NULL——无日志行假设；四实体表 created_at NOT NULL 不触发）：
    SQLite DESC 缺省 NULL 落末位，而 NULL<c 比较恒假会漏末位段，
    非 NULL cursor 须补 IS NULL 支；cursor 本身落在 NULL 段时该段
    内只余主键可比。asc 不处理：cards 恒 desc、四表 NOT NULL，
    组合不可达。
    """
    if cursor is not None:
        c, k = cursor
        if order == "desc":
            if c is None:
                stmt = stmt.where(and_(
                    created_at_col.is_(None), pk_col < k))
            else:
                stmt = stmt.where(or_(
                    created_at_col < c,
                    created_at_col.is_(None),
                    and_(created_at_col == c, pk_col < k)))
        else:
            stmt = stmt.where(or_(
                created_at_col > c,
                and_(created_at_col == c, pk_col > k)))
    if order == "desc":
        return stmt.order_by(created_at_col.desc(), pk_col.desc())
    return stmt.order_by(created_at_col, pk_col)


# ============================================================ 假设（双写组合）

def save_hypothesis_with_log(
    db_path: Path,
    *,
    hypothesis_id: str,
    conditions: str,
    conditions_hash: str,
    statement: str,
    data_ranges: str,
    ranges_hash: str,
    strategy_params: Optional[str] = None,
    log_id: Optional[str] = None,
    screen_use: Optional[Dict[str, str]] = None,
) -> None:
    """原子写：research_hypotheses + research_log(register) [+ screen_use]。

    log_id 可注入（测试原子性用）；任一后续 insert 失败时假设行一并
    回滚。screen_use（add-screen-patterns）：{ref_id, data_ranges}——
    登记引用扫描候选时同事务追加谱系日志（区间经判定并集机制自动
    纳入隔离，isolation.py 零改动）。strategy_params（0.4.2 R1）：
    声明块归一形 JSON 串；None=无声明（列 NULL，翻译走平台默认）。
    """
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(ResearchHypothesis).values(
            hypothesis_id=hypothesis_id,
            conditions=conditions, conditions_hash=conditions_hash,
            statement=statement,
            data_ranges=data_ranges, ranges_hash=ranges_hash,
            strategy_params=strategy_params,
            status="active",
            created_at=now, updated_at=now,
        ))
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="register",
            ref_id=hypothesis_id,
            data_ranges=data_ranges,
            summary=statement,
            created_at=now,
        ))
        if screen_use is not None:
            conn.execute(insert(ResearchLog).values(
                log_id=_new_id(),
                hypothesis_id=hypothesis_id,
                # GAP-10 泛化：entry_type/summary 随 screen_use dict 携带
                # （新路径 analysis_ref；缺省沿 screen_use 旧值——存量
                # 调用方与测试零适配）
                entry_type=screen_use.get("entry_type", "screen_use"),
                ref_id=screen_use["ref_id"],
                data_ranges=screen_use["data_ranges"],
                summary=screen_use.get(
                    "summary", "scan candidate referenced at registration"),
                created_at=now,
            ))


def get_hypothesis(db_path: Path, hypothesis_id: str) -> Optional[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ResearchHypothesis).where(
                ResearchHypothesis.hypothesis_id == hypothesis_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def find_active_hypothesis_by_identity(
    db_path: Path, conditions_hash: str, ranges_hash: str
) -> Optional[Dict[str, Any]]:
    """幂等键查询：同条件集+同区间集且 active 的既有假设（重复登记返回既有）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ResearchHypothesis).where(
                ResearchHypothesis.conditions_hash == conditions_hash,
                ResearchHypothesis.ranges_hash == ranges_hash,
                ResearchHypothesis.status == "active",
            ).order_by(ResearchHypothesis.created_at)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_hypotheses_by_identity(
    db_path: Path, conditions_hash: str, ranges_hash: str
) -> List[Dict[str, Any]]:
    """同身份全部假设实体（0.4.2 R4 身份族——全状态含终态，区别于
    find_active_hypothesis_by_identity 的 active 限定；终态后重登记产
    族内新实体，族=至多 1 active + 0..n 终态）。created_at 升序。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(ResearchHypothesis).where(
                ResearchHypothesis.conditions_hash == conditions_hash,
                ResearchHypothesis.ranges_hash == ranges_hash,
            ).order_by(ResearchHypothesis.created_at,
                       ResearchHypothesis.hypothesis_id)
        ).mappings().all()
    return [dict(r) for r in rows]


def parse_parent_ids(raw: Optional[str]) -> List[Any]:
    """parent_ids 列值 → id 列表（双形制归一，review-042 CARD-06 单点）。

    落库形制两种：JSON 数组（进化/手工来源，多父）与裸串（翻译器落库
    为单 id 串非 JSON 数组）；raw 空/None 归一为空列表。JSON 解析出非
    列表（单值形）包装为单元素列表。谱系查询（本文件）与验证档案
    _build_lineage 共用——两处各自实现曾是同一解析的第二份漂移源。
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [raw]  # 裸串形制——当单 id 处理
    if not isinstance(parsed, list):
        return [str(parsed)]  # 单值 JSON 形兼容
    return parsed


def list_strategies_by_parent_hypotheses(
    db_path: Path, hypothesis_ids: List[str]
) -> List[Dict[str, Any]]:
    """parent_ids JSON 数组成员精确匹配 id 集的策略（0.4.2 R4 谱系查询）。

    精确成员判定非子串匹配（进化/手工来源策略的 JSON 数组不误命中）；
    全量无 limit（谱系披露不截断）。created_at 升序。
    """
    if not hypothesis_ids:
        return []
    id_set = set(hypothesis_ids)
    # 显式列裁剪（review-042 CARD-07）：消费方仅需四列——select(Strategy)
    # 全表会把 dna_json（Text 大载荷）整体拖进内存；成员匹配保留在
    # Python 侧（正确性优先——JSON 数组的 SQL LIKE 有子串误命中）。
    # 行数上限/parent 关联表方案是 S-3 性能债（TD 登记），不在此卡
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                Strategy.strategy_id, Strategy.parent_ids,
                Strategy.status, Strategy.created_at,
            ).where(
                Strategy.parent_ids.isnot(None),
            ).order_by(Strategy.created_at, Strategy.strategy_id)
        ).mappings().all()
    out = []
    for row in rows:
        if id_set.intersection(str(p) for p in parse_parent_ids(row["parent_ids"])):
            out.append(dict(row))
    return out


def count_validation_records_by_strategy(
    db_path: Path, strategy_ids: List[str]
) -> Dict[str, Dict[str, Dict[str, int]]]:
    """按 strategy_id 的 level×verdict 聚合计数（0.4.2 R4/DC-8——SQL
    聚合不经列表截断；大谱系计数与记录实况一致）。

    Returns:
        {strategy_id: {level: {"count": n, "pass": n, "fail": n,
                               "inconclusive": n}}}
    """
    if not strategy_ids:
        return {}
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                ValidationRecord.strategy_id,
                ValidationRecord.level,
                ValidationRecord.verdict,
                func.count().label("n"),
            ).where(
                ValidationRecord.strategy_id.in_(strategy_ids),
            ).group_by(
                ValidationRecord.strategy_id,
                ValidationRecord.level,
                ValidationRecord.verdict,
            )
        ).all()
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for sid, level, verdict, n in rows:
        bucket = out.setdefault(sid, {}).setdefault(
            level, {"count": 0, "pass": 0, "fail": 0, "inconclusive": 0})
        bucket["count"] += n
        if verdict in bucket:
            bucket[verdict] += n
    return out


def list_validation_record_ids_by_strategy(
    db_path: Path, strategy_ids: List[str]
) -> Dict[str, Dict[str, List[str]]]:
    """按 strategy_id 的 level 分组 record_id 全量升序（变更一 B2 切片 b）。

    SQL 全量查询不经列表截断（spec「MUST NOT 截断扫描」——直插 201 条
    记录的实体编号明细须全量披露）；与 count_validation_records_by_strategy
    同文件同域保持单源（两函数同 strategy_id 域消费，防漂移约束一并保持）。

    Returns:
        {strategy_id: {level: [record_id, ...]}}（record_id 升序）
    """
    if not strategy_ids:
        return {}
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                ValidationRecord.strategy_id,
                ValidationRecord.level,
                ValidationRecord.record_id,
            ).where(
                ValidationRecord.strategy_id.in_(strategy_ids),
            ).order_by(
                ValidationRecord.strategy_id,
                ValidationRecord.level,
                ValidationRecord.record_id,
            )
        ).all()
    out: Dict[str, Dict[str, List[str]]] = {}
    for sid, level, record_id in rows:
        out.setdefault(sid, {}).setdefault(level, []).append(record_id)
    return out


def list_hypotheses(
    db_path: Path, *, status: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """假设列表（分页双轨：offset/limit 原样；cursor=(created_at,
    hypothesis_id) keyset 严格比较——HTTP 列表端点 desc+cursor 续页）。
    count 配套 count_hypotheses。"""
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(ResearchHypothesis),
        ResearchHypothesis.created_at, ResearchHypothesis.hypothesis_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if status is not None:
        stmt = stmt.where(ResearchHypothesis.status == status)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_hypotheses(
    db_path: Path, *, status: Optional[str] = None,
) -> int:
    """假设真总数（过滤口径与 list_hypotheses 一致）。"""
    stmt = select(func.count()).select_from(ResearchHypothesis)
    if status is not None:
        stmt = stmt.where(ResearchHypothesis.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


def transition_hypothesis_with_promotion(
    db_path: Path,
    hypothesis_id: str,
    *,
    target_status: str,
    terminal_ref: str,
    superseded_by: Optional[str] = None,
    promote: bool = False,
    log_id: Optional[str] = None,
) -> Dict[str, Any]:
    """条件迁移 + 迁移日志 +（promote 时）谱系 draft 原子转正，同一事务。

    转正经生命周期迁移服务 transit（build-strategy-lifecycle 3.1：
    draft→active 事件同事务落表，conn 透传；语义与直改等价）。
    refuted 定证后谱系 draft 经 transit 归档（build-strategy-lifecycle
    3.2 镜像形制：trigger=refute，谱系空跳过+留痕）。

    promote 独立证据条件（improve-mcp-agent-loop M4）：仅当存在挂
    (hypothesis_id, 该 draft) 的 verdict=pass 记录才转正——防"用别的
    策略的 pass 定证、转正未验证的 draft"的调包；无自身 pass 时假设
    照常定证、draft 保持 draft（日志记因）。draft 已被人工删除则跳过
    转正并落日志（显式清理不阻塞假设定案）。

    Returns:
        {"rows": 0|1, "promoted": bool, "promoted_strategy_id": Optional[str],
         "promotion_note": Optional[str]}
    """
    from core.persistence.orm.models import Strategy

    engine = _engine_for(db_path)
    now = _now()
    out: Dict[str, Any] = {"rows": 0, "promoted": False,
                           "promoted_strategy_id": None, "promotion_note": None}
    with engine.begin() as conn:
        result = conn.execute(
            update(ResearchHypothesis)
            .where(ResearchHypothesis.hypothesis_id == hypothesis_id,
                   ResearchHypothesis.status == "active")
            .values(
                status=target_status,
                terminal_ref=terminal_ref,
                superseded_by=superseded_by,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            return out
        out["rows"] = 1
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="transition",
            ref_id=terminal_ref,
            summary=f"active -> {target_status} (ref={terminal_ref})",
            created_at=now,
        ))

        if target_status == "refuted":
            # 证伪归档接入（build-strategy-lifecycle 3.2，promote 的
            # 镜像形制）：refuted 定证成功后谱系 draft 经 transit 归档
            # （trigger=refute，evidence=假设级 fail 记录 record_id=
            # terminal_ref——hypothesis_service 已强制其 verdict=fail），
            # conn 透传同事务；谱系空跳过+留痕。对象范围：refuted 时谱系
            # 恒 draft 或不存在（无「转正后事后证伪」路径），from 恒
            # draft；「任意→archived」完整 from 覆盖由 DELETE 人工边承载
            drafts = conn.execute(
                select(Strategy.strategy_id)
                .where(Strategy.parent_ids == hypothesis_id,
                       Strategy.status == "draft")
            ).scalars().all()
            if not drafts:
                out["promotion_note"] = "谱系无 draft，跳过证伪归档"
                conn.execute(insert(ResearchLog).values(
                    log_id=_new_id(), hypothesis_id=hypothesis_id,
                    entry_type="refutation", ref_id=terminal_ref,
                    summary="refutation skipped: no draft in lineage",
                    created_at=now))
                return out
            from core.strategy.lifecycle import transit as _transit
            for draft_id in drafts:
                _transit(
                    db_path, draft_id, target="archived", trigger="refute",
                    evidence={"hypothesis_id": hypothesis_id,
                              "record_id": terminal_ref},
                    conn=conn)
                conn.execute(insert(ResearchLog).values(
                    log_id=_new_id(), hypothesis_id=hypothesis_id,
                    entry_type="refutation", ref_id=draft_id,
                    summary=(f"draft archived <- refuted "
                             f"(evidence-bound fail {terminal_ref})"),
                    created_at=now))
            return out

        if not promote:
            return out

        drafts = conn.execute(
            select(Strategy.strategy_id)
            .where(Strategy.parent_ids == hypothesis_id,
                   Strategy.status == "draft")
        ).scalars().all()
        if not drafts:
            out["promotion_note"] = "谱系无 draft（已删除或未转化），跳过转正"
            conn.execute(insert(ResearchLog).values(
                log_id=_new_id(), hypothesis_id=hypothesis_id,
                entry_type="promotion", ref_id=terminal_ref,
                summary="promotion skipped: no draft in lineage",
                created_at=now))
            return out

        for draft_id in drafts:
            has_own_pass = conn.execute(
                select(ValidationRecord.record_id)
                .where(ValidationRecord.hypothesis_id == hypothesis_id,
                       ValidationRecord.strategy_id == draft_id,
                       ValidationRecord.verdict == "pass")
                .limit(1)
            ).first()
            if has_own_pass is None:
                out["promotion_note"] = (
                    f"draft {draft_id} 无自身 pass 记录，保持 draft（证据绑定未满足）")
                conn.execute(insert(ResearchLog).values(
                    log_id=_new_id(), hypothesis_id=hypothesis_id,
                    entry_type="promotion", ref_id=draft_id,
                    summary="promotion skipped: draft has no own pass record",
                    created_at=now))
                continue
            # build-strategy-lifecycle 3.1：转正改经迁移服务（status
            # 唯一合法写通道），conn 透传=与假设定案同事务（外层回滚
            # 两侧皆无）；draft→active 事件（trigger=credential）同事务
            # 落表。凭证判定与上方预检同式（服务单点纪律：内层再核一次）
            from core.strategy.lifecycle import transit as _transit
            _transit(
                db_path, draft_id, target="active", trigger="credential",
                evidence={"hypothesis_id": hypothesis_id,
                          "record_id": has_own_pass[0]},
                conn=conn)
            out["promoted"] = True
            out["promoted_strategy_id"] = draft_id
            conn.execute(insert(ResearchLog).values(
                log_id=_new_id(), hypothesis_id=hypothesis_id,
                entry_type="promotion", ref_id=draft_id,
                summary=f"draft promoted -> active (evidence-bound pass {has_own_pass[0]})",
                created_at=now))
        return out


# ============================================================ 研究日志

def append_research_log(
    db_path: Path,
    *,
    hypothesis_id: Optional[str],
    entry_type: str,
    ref_id: Optional[str] = None,
    data_ranges: Optional[str] = None,
    summary: Optional[str] = None,
) -> str:
    """追加一条研究日志（返回 log_id）。日志不可变（无 update 路径）。"""
    log_id = _new_id()
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(ResearchLog).values(
            log_id=log_id,
            hypothesis_id=hypothesis_id,
            entry_type=entry_type,
            ref_id=ref_id,
            data_ranges=data_ranges,
            summary=summary,
            created_at=_now(),
        ))
    return log_id


def list_research_log(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """按 hypothesis_id / entry_type 过滤的日志列表（稳定排序）。

    entry_type 过滤供反向核查（全局 screen 留痕）与审计查询通道
    （add-screen-patterns G6）使用；两参数均缺省时行为与既往一致
    （iter_all_pages 的 fetch_page(offset,limit) 契约不动）。分页
    双轨：cursor=(created_at, log_id) keyset 严格比较，order 缺省
    asc，HTTP 列表端点传 desc+cursor 续页。count 配套
    count_research_log。
    """
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(ResearchLog),
        ResearchLog.created_at, ResearchLog.log_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if hypothesis_id is not None:
        stmt = stmt.where(ResearchLog.hypothesis_id == hypothesis_id)
    if entry_type is not None:
        stmt = stmt.where(ResearchLog.entry_type == entry_type)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_research_log(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    entry_type: Optional[str] = None,
) -> int:
    """日志真总数（过滤口径与 list_research_log 一致）。"""
    stmt = select(func.count()).select_from(ResearchLog)
    if hypothesis_id is not None:
        stmt = stmt.where(ResearchLog.hypothesis_id == hypothesis_id)
    if entry_type is not None:
        stmt = stmt.where(ResearchLog.entry_type == entry_type)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


# ============================================================ 验证记录（双写组合）

def save_validation_with_log(
    db_path: Path,
    *,
    record_id: str,
    hypothesis_id: str,
    strategy_id: Optional[str],
    level: str,
    segmentation: str = "",
    params: str,
    result: Optional[str],
    verdict: str,
    range_hash: str,
    components: Optional[str] = None,
    log_id: Optional[str] = None,
    detail: Optional[List[Dict[str, Any]]] = None,
    trace: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """原子多写：validation_records + research_log(validate) + 明细两表+留痕表。

    segmentation：0.3.3 G7 幂等第五维（分段身份——默认空串=非分段，
    隔离/随机窗调用方可不传；分段调用方传 segmentation_identity 值）。
    detail（add-validation-detail B4）：执行份明细列表（_build_detail_payload
    产物，每份 {trades, equity}（行级含四标识列）），缺省 None 不写
    明细表（签名向后兼容——沙盒/既有调用方零适配）。携带时四写同一
    事务：任一失败整体回滚——「记录在而明细残缺」不可达（specs
    「验证明细原子落盘」）。
    trace（add-vr-signal-trace 任务 4.2）：信号留痕行列表（trace_
    resolver 产物，行级含 exec_ref/bar_time/seq/outcome/reason/
    event_code/size/price/capped/gene_hits）。缺省 None 行为逐位
    一致（沙盒/其他调用方零适配）；非 None（含空列表——管线通而
    事实空）时第五写：行批量 INSERT VrSignalTrace+记录三布尔打 1，
    与四写同一事务——「记录在而留痕残缺」不可达（打标与数据一致
    性由同事务结构性封死）。三布尔打标口径=「本批留痕管线执行成功」
    非事件有无。
    """
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(ValidationRecord).values(
            record_id=record_id,
            hypothesis_id=hypothesis_id,
            strategy_id=strategy_id,
            level=level,
            segmentation=segmentation,
            params=params,
            result=result,
            verdict=verdict,
            range_hash=range_hash,
            components=components,
            **(
                {"signal_trace": 1, "trade_semantics": 1, "stop_price": 1}
                if trace is not None else {}),
            created_at=now,
        ))
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=hypothesis_id,
            entry_type="validate",
            ref_id=record_id,
            summary=f"{level}: {verdict}",
            created_at=now,
        ))
        if detail:
            for exec_payload in detail:
                equity_row = dict(exec_payload["equity"])
                equity_row["record_id"] = record_id
                conn.execute(insert(VrEquityCurve).values(**equity_row))
                trade_rows = exec_payload["trades"]
                if trade_rows:
                    rows = [dict(t, record_id=record_id) for t in trade_rows]
                    conn.execute(insert(VrTradeDetail), rows)
        # 第五写（add-vr-signal-trace 任务 4.2）：留痕行批量落库——
        # trace 参数 None 即整块跳过（行为与现状逐位一致）；非 None
        # 且非空时行落库（record_id 由本层补——resolver 行不含）
        if trace:
            conn.execute(insert(VrSignalTrace), [
                dict(t, record_id=record_id) for t in trace])


def find_validation_by_identity(
    db_path: Path, hypothesis_id: str, strategy_id: Optional[str],
    range_hash: str, *, level: str, segmentation: str = "",
) -> Optional[Dict[str, Any]]:
    """幂等键查询（0.3.3 G7 五元）：同 (hypothesis_id, strategy_id,
    range_hash, level, segmentation) 的既有记录——level 等值过滤使
    幂等命中仅发生在同类记录间（隔离/分段/随机窗互不命中——跨类型
    误命中的现状缺陷随之修复）。level 无默认值：调用方必须显式声明
    查询的身份类型。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ValidationRecord).where(
                ValidationRecord.hypothesis_id == hypothesis_id,
                ValidationRecord.strategy_id == strategy_id,
                ValidationRecord.range_hash == range_hash,
                ValidationRecord.level == level,
                ValidationRecord.segmentation == segmentation,
            ).order_by(ValidationRecord.created_at)
        ).mappings().first()
    return dict(row) if row is not None else None


def get_validation_record_by_id(
    db_path: Path, record_id: str,
) -> Optional[Dict[str, Any]]:
    """单记录直读（add-validation-detail B2）：两态判定序第一态输入
    ——无行=记录本身不存在（404）；有行=元数据+params JSON 直读源。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(ValidationRecord).where(
                ValidationRecord.record_id == record_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def get_vr_signal_trace_rows(
    db_path: Path, record_id: str, exec_ref: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """留痕行读取（add-vr-signal-trace 任务 4.2）。

    按 (exec_ref, bar_time, seq) 升序；exec_ref 过滤可选（None=全份）。
    无行返回 []（判读协议：三布尔 true+全份空=W0 缺陷态；三布尔
    null+空=合法存量降级——判别在 detail 端点组合两源完成）。
    """
    engine = _engine_for(db_path)
    stmt = select(VrSignalTrace).where(
        VrSignalTrace.record_id == record_id)
    if exec_ref is not None:
        stmt = stmt.where(VrSignalTrace.exec_ref == exec_ref)
    stmt = stmt.order_by(
        VrSignalTrace.exec_ref, VrSignalTrace.bar_time, VrSignalTrace.seq)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [
        {"exec_ref": r["exec_ref"], "bar_time": r["bar_time"],
         "seq": r["seq"], "outcome": r["outcome"], "reason": r["reason"],
         "event_code": r["event_code"], "size": r["size"],
         "price": r["price"], "capped": r["capped"],
         "gene_hits": (load_stored_json(
                           r["gene_hits"],
                           source="vr_signal_trace.gene_hits",
                           detail={"record_id": record_id,
                                   "exec_ref": r["exec_ref"],
                                   "bar_time": r["bar_time"],
                                   "seq": r["seq"]})
                       if r["gene_hits"] else None)}
        for r in rows]


def get_validation_executions(
    db_path: Path, record_id: str,
) -> List[Dict[str, Any]]:
    """明细整份取回（B2）：两表读出按份组装（exec_index 排序、份内
    seq 笔序）；equity 行为份骨架（原子四写保证每份必有），trades
    挂靠 (exec_index, exec_ref)。无明细行返回 []（两态判定序第二态
    输入——历史记录/明细落盘前记录）。equity timestamps/values JSON
    解包为列表（紧凑形制 timestamps=None）；紧凑/降级双形制如实
    透传不重建（specs「明细读取端点」）。
    """
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        trade_rows = conn.execute(
            select(VrTradeDetail).where(
                VrTradeDetail.record_id == record_id)
            .order_by(VrTradeDetail.exec_index, VrTradeDetail.seq)
        ).mappings().all()
        equity_rows = conn.execute(
            select(VrEquityCurve).where(
                VrEquityCurve.record_id == record_id)
            .order_by(VrEquityCurve.exec_index)
        ).mappings().all()
    if not equity_rows and not trade_rows:
        return []

    trades_by_exec: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for t in trade_rows:
        trades_by_exec.setdefault(
            (t["exec_index"], t["exec_ref"]), []).append({
                "seq": t["seq"],
                "entry_time": t["entry_time"],
                "exit_time": t["exit_time"],
                "entry_price": t["entry_price"],
                "exit_price": t["exit_price"],
                "size": t["size"],
                "direction": t["direction"],
                "pnl": t["pnl"],
                # decision 键退役（retire-vr-decision-json 任务 2）：
                # 逐笔决策披露由 vr_signal_trace 承载（读侧不再组装）
            })

    executions = []
    for eq in equity_rows:
        compact = int(eq["grid_aligned"]) == 1
        equity: Dict[str, Any] = {
            "grid_aligned": compact,
            "timeframe": eq["timeframe"],
        }
        if compact:
            equity.update({"start": eq["start"], "length": eq["length"],
                           "timestamps": None})
        else:
            equity.update({"timestamps": json.loads(eq["timestamps"])})
        equity["values"] = json.loads(eq["values"])
        executions.append({
            "exec_ref": eq["exec_ref"],
            "exec_index": eq["exec_index"],
            "range_start": eq["range_start"],
            "range_end": eq["range_end"],
            "trades": trades_by_exec.get(
                (eq["exec_index"], eq["exec_ref"]), []),
            "equity": equity,
        })
    # 防御位：有 trade 行而无 equity 骨架的份（原子四写下不可达）——
    # 如实呈现空曲线骨架，不静默丢逐笔证据
    seen = {(e["exec_index"], e["exec_ref"]) for e in executions}
    for (exec_index, exec_ref), trades in trades_by_exec.items():
        if (exec_index, exec_ref) not in seen:
            executions.append({
                "exec_ref": exec_ref, "exec_index": exec_index,
                "range_start": "", "range_end": "",
                "trades": trades,
                "equity": {"grid_aligned": True, "timeframe": "",
                           "start": None, "length": 0,
                           "timestamps": None, "values": []},
            })
    executions.sort(key=lambda e: e["exec_index"])
    return executions


def list_validation_records(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
    q: Optional[str] = None,
    generalization: Optional[bool] = None,
    sort: Optional[str] = None,
    level: Optional[str] = None,
    verdict: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """验证记录列表（分页双轨：offset/limit 原样——iter_all_pages 的
    fetch_page 契约不动；cursor=(created_at, record_id) keyset 严格
    比较，order 缺省 asc）。count 配套 count_validation_records。

    B1 扩展形参（upgrade-validation-records-list 1.1——全部缺省 None
    行为与现状逐位一致，签名锚测试锁定四处调用点零适配）：
    - q：statement 关键词（LIKE %q%，%/_ 经 ESCAPE 字面化——搜索词
      含通配符按字面匹配不放大匹配面）；恒 LEFT JOIN research_
      hypotheses 取行级 statement 键（无匹配行 null——adhoc 如实）
    - generalization：true=仅泛化记录（记录标的≠策略原生标的——双路
      COALESCE+timeframe 相等断言，NULL 任侧保守漏判，B1.4 边界四条）
    - sort：四值封闭枚举（time_desc/time_asc/total_return_desc/
      verdict_quality——B1.3 worst 双路径 SQL+verdict 品质序；sort
      模式走 ORDER BY 表达式链，cursor keyset 无效忽略）
    - level/verdict：B1 期形参预置（仓储 WHERE 下推通道；端点 B2
      接线——B1 期不传）
    """
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(ValidationRecord, ResearchHypothesis.statement)
        .join(ResearchHypothesis, ValidationRecord.hypothesis_id
              == ResearchHypothesis.hypothesis_id, isouter=True),
        ValidationRecord.created_at, ValidationRecord.record_id,
        cursor, order,
    ).offset(offset).limit(limit)
    stmt = _apply_vr_filters(stmt, q=q, generalization=generalization,
                             level=level, verdict=verdict)
    if hypothesis_id is not None:
        stmt = stmt.where(ValidationRecord.hypothesis_id == hypothesis_id)
    if strategy_id is not None:
        stmt = stmt.where(ValidationRecord.strategy_id == strategy_id)
    if sort is not None:
        stmt = stmt.order_by(None).order_by(*_vr_sort_expressions(sort))
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_validation_records(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    q: Optional[str] = None,
    generalization: Optional[bool] = None,
    level: Optional[str] = None,
    verdict: Optional[str] = None,
) -> int:
    """验证记录真总数（过滤口径与 list_validation_records 一致——
    B1 四维：hypothesis_id+strategy_id+q+generalization；level/verdict
    形参 B2 接线后并入六维口径）。"""
    stmt = (
        select(func.count())
        .select_from(ValidationRecord)
        .join(ResearchHypothesis, ValidationRecord.hypothesis_id
              == ResearchHypothesis.hypothesis_id, isouter=True))
    stmt = _apply_vr_filters(stmt, q=q, generalization=generalization,
                             level=level, verdict=verdict)
    if hypothesis_id is not None:
        stmt = stmt.where(ValidationRecord.hypothesis_id == hypothesis_id)
    if strategy_id is not None:
        stmt = stmt.where(ValidationRecord.strategy_id == strategy_id)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


# ── B1 过滤/排序 SQL 表达式（upgrade-validation-records-list 1.1）──
# worst 双路径（B1.3）：三型 level 各自的 result/params JSON 键位路由
# （validation_service 实锚：isolated result $.metrics.total_return /
# random result $.summary.worst_window.metrics.total_return / staged
# result $.summary.worst_segment.total_return——composite/component
# 死值域落 NULL 计入 unsortable）。
_WORST_RETURN_EXPR = case(
    (ValidationRecord.level == "isolated_backtest",
     func.json_extract(ValidationRecord.result, "$.metrics.total_return")),
    (ValidationRecord.level == "random_window",
     func.json_extract(ValidationRecord.result,
                       "$.summary.worst_window.metrics.total_return")),
    (ValidationRecord.level == "staged_validation",
     func.json_extract(ValidationRecord.result,
                       "$.summary.worst_segment.total_return")),
    )

# 记录侧标的/周期（B1.4 泛化双路——三型键位路由）
_REC_SYMBOL_EXPR = case(
    (ValidationRecord.level == "isolated_backtest",
     func.json_extract(ValidationRecord.result, "$.symbol")),
    (ValidationRecord.level == "random_window",
     func.json_extract(ValidationRecord.params, "$.symbol")),
    (ValidationRecord.level == "staged_validation",
     func.json_extract(ValidationRecord.params,
                       "$.request_range.symbol")),
    )

_REC_TIMEFRAME_EXPR = case(
    (ValidationRecord.level == "isolated_backtest",
     func.json_extract(ValidationRecord.result, "$.timeframe")),
    (ValidationRecord.level == "random_window",
     func.json_extract(ValidationRecord.params, "$.timeframe")),
    (ValidationRecord.level == "staged_validation",
     func.json_extract(ValidationRecord.params,
                       "$.request_range.timeframe")),
    )

# 基准侧标的/周期（策略原生——strategy.parent_ids 首父假设的
# data_ranges 首段；裸串形制 parent_ids〔parse_parent_ids 先例〕经
# json_valid 分支归一；strategy.symbol/timeframe 兜底）。CASE 先验
# json_valid 而非 COALESCE 嵌套——SQLite COALESCE 首参先求值，裸串
# parent_ids 在 json_extract 即抛 malformed JSON，兜底分支不可达；
# parent_ids NULL 时 json_valid=NULL 两分支皆不中→NULL（等价旧形）
_PARENT_HYP_ID = case(
    (func.json_valid(Strategy.parent_ids) == 1,
     func.json_extract(Strategy.parent_ids, "$[0]")),
    (func.json_valid(Strategy.parent_ids) == 0, Strategy.parent_ids),
    )
_PARENT_DATA_RANGES = (
    select(ResearchHypothesis.data_ranges)
    .where(ResearchHypothesis.hypothesis_id == _PARENT_HYP_ID)
    # 显式只关联 strategy（parent_ids 来源经外层行引用）；research_
    # hypotheses 保持子查询独立 FROM——防 auto-correlate 在外层已
    # join hypotheses 时吸收 FROM 致空子查询
    .correlate(Strategy)
    .scalar_subquery())
# data_ranges 同为无机制保证恒 JSON 的列——同族先验 json_valid 防裸串
# 炸；与 _PARENT_HYP_ID 不同形之处：提取面全落 Strategy.symbol/
# timeframe 兜底（design 契约——data_ranges NULL/裸串/JSON 提取缺键
# 皆列兜底，NULL 非 parent_ids 的等 NULL 语义），故 else 而非双分支
_BASE_SYMBOL_EXPR = case(
    (func.json_valid(_PARENT_DATA_RANGES) == 1,
     func.coalesce(func.json_extract(_PARENT_DATA_RANGES, "$[0].symbol"),
                   Strategy.symbol)),
    else_=Strategy.symbol)
_BASE_TIMEFRAME_EXPR = case(
    (func.json_valid(_PARENT_DATA_RANGES) == 1,
     func.coalesce(func.json_extract(_PARENT_DATA_RANGES,
                                     "$[0].timeframe"),
                   Strategy.timeframe)),
    else_=Strategy.timeframe)
# 泛化断言面（B1.4 边界四条）：symbol 双侧非 NULL 且不等 ∧ timeframe
# 双侧非 NULL 且相等（NULL 任侧=保守漏判不判泛化；跨周期显式排除；
# 不限策略状态——标的事实非状态事实）
_GENERALIZATION_EXPR = and_(
    _REC_SYMBOL_EXPR.isnot(None),
    _BASE_SYMBOL_EXPR.isnot(None),
    _REC_SYMBOL_EXPR != _BASE_SYMBOL_EXPR,
    _REC_TIMEFRAME_EXPR.isnot(None),
    _BASE_TIMEFRAME_EXPR.isnot(None),
    _REC_TIMEFRAME_EXPR == _BASE_TIMEFRAME_EXPR,
)
# verdict 品质序（B1.3——pass→inconclusive→fail 好的在前；fail 与
# pass 时间序混排不沉底）
_VERDICT_QUALITY_EXPR = case(
    (ValidationRecord.verdict == "pass", 0),
    (ValidationRecord.verdict == "inconclusive", 1),
    (ValidationRecord.verdict == "fail", 2),
    else_=3,
)
_SORT_KEYS = {
    "time_desc": [ValidationRecord.created_at.desc(),
                  ValidationRecord.record_id.desc()],
    "time_asc": [ValidationRecord.created_at.asc(),
                 ValidationRecord.record_id.asc()],
    "total_return_desc": [
        case((_WORST_RETURN_EXPR.is_(None), 1), else_=0).asc(),
        _WORST_RETURN_EXPR.desc(),
        ValidationRecord.created_at.desc(),
        ValidationRecord.record_id.desc()],
    "verdict_quality": [
        _VERDICT_QUALITY_EXPR.asc(),
        ValidationRecord.created_at.desc(),
        ValidationRecord.record_id.desc()],
}


def _vr_sort_expressions(sort: str) -> list:
    """sort 枚举 → ORDER BY 表达式链（NULL 前缀键显式落尾——不依赖
    SQLite NULL 排序缺省，确定次序契约）。"""
    keys = _SORT_KEYS.get(sort)
    if keys is None:
        raise ValueError(f"sort 非法: {sort!r}（值域 {sorted(_SORT_KEYS)}）")
    return keys


def _apply_vr_filters(stmt, *, q=None, generalization=None,
                      level=None, verdict=None):
    """WHERE 下推（B1.4）：q LIKE ESCAPE 字面化+泛化断言+level/verdict
    等值（B2 接线）；全部缺省 None 零表达式=现状逐位一致。"""
    if level is not None:
        stmt = stmt.where(ValidationRecord.level == level)
    if verdict is not None:
        stmt = stmt.where(ValidationRecord.verdict == verdict)
    if q:
        # %/_/\\ 字面化（ESCAPE '\'）——搜索词含通配符按字面匹配
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace(
            "_", "\\_")
        stmt = stmt.where(ResearchHypothesis.statement.like(
            f"%{escaped}%", escape="\\"))
    if generalization:
        # 泛化断言需策略侧两表在位（LEFT JOIN strategies——非泛化查询
        # 不 join 零开销）
        stmt = stmt.join(
            Strategy, ValidationRecord.strategy_id == Strategy.strategy_id,
            isouter=True).where(_GENERALIZATION_EXPR)
    return stmt


def validation_records_aggregates(
    db_path: Path, *,
    hypothesis_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    q: Optional[str] = None,
    generalization: Optional[bool] = None,
    level: Optional[str] = None,
    verdict: Optional[str] = None,
) -> Dict[str, Any]:
    """聚合查询（B1.2）：verdict_counts 三键恒在场+unsortable_count。

    口径与 count 同基数（当前过滤维全集）；unsortable=worst 值缺失
    （NULL 键位或死值域 level）计数——非 total_return_desc 排序时
    端点恒报 0（语义键在场值退化），仓储如实计数两态均可判别。
    """
    from sqlalchemy import literal

    filters = []
    if hypothesis_id is not None:
        filters.append(ValidationRecord.hypothesis_id == hypothesis_id)
    if strategy_id is not None:
        filters.append(ValidationRecord.strategy_id == strategy_id)

    def _base(select_cols):
        stmt = select(*select_cols).select_from(ValidationRecord)
        stmt = stmt.join(
            ResearchHypothesis, ValidationRecord.hypothesis_id
            == ResearchHypothesis.hypothesis_id, isouter=True)
        stmt = _apply_vr_filters(stmt, q=q, generalization=generalization,
                                 level=level, verdict=verdict)
        for f in filters:
            stmt = stmt.where(f)
        return stmt

    engine = _engine_for(db_path)
    with engine.connect() as conn:
        v = ValidationRecord
        agg = _base([
            func.sum(case((v.verdict == "pass", 1), else_=0)),
            func.sum(case((v.verdict == "fail", 1), else_=0)),
            func.sum(case((v.verdict == "inconclusive", 1), else_=0)),
            func.count(literal(1)),
        ])
        row = conn.execute(agg).one()
        unsort = _base([func.count(literal(1))]).where(
            _WORST_RETURN_EXPR.is_(None))
        n_unsort = conn.execute(unsort).scalar_one()
    return {
        "pass": int(row[0] or 0), "fail": int(row[1] or 0),
        "inconclusive": int(row[2] or 0), "total": int(row[3] or 0),
        "unsortable_count": int(n_unsort or 0),
    }


# ============================================================ 主任务卡片（B1 只读聚合，add-hypothesis-cards）

# 卡片行排序/keyset 轴：recent_activity_at（research_log 最新
# created_at 标量子查询）降序 + hypothesis_id 次序键保证确定次序。
# NULL 语义：recent_activity_at 无日志行=NULL（SQLite DESC 排序
# NULL 落末位——正常路径每假设必有 register 日志，NULL 仅防御手工库）
_ACTIVITY_EXPR = (
    select(func.max(ResearchLog.created_at))
    .where(ResearchLog.hypothesis_id == ResearchHypothesis.hypothesis_id)
    .scalar_subquery())
_VALIDATION_COUNT_EXPR = (
    select(func.count())
    .select_from(ValidationRecord)
    .where(ValidationRecord.hypothesis_id == ResearchHypothesis.hypothesis_id)
    .scalar_subquery())
# 最新验证结论取一：created_at 降序+record_id 次序键（同秒并列确定）
_LATEST_VERDICT_EXPR = (
    select(ValidationRecord.verdict)
    .where(ValidationRecord.hypothesis_id == ResearchHypothesis.hypothesis_id)
    .order_by(ValidationRecord.created_at.desc(),
              ValidationRecord.record_id.desc())
    .limit(1).scalar_subquery())
_LATEST_RECORD_EXPR = (
    select(ValidationRecord.record_id)
    .where(ValidationRecord.hypothesis_id == ResearchHypothesis.hypothesis_id)
    .order_by(ValidationRecord.created_at.desc(),
              ValidationRecord.record_id.desc())
    .limit(1).scalar_subquery())


def _cards_filters(status: Optional[str], q: Optional[str]):
    """status 等值+q 子串过滤条件（%/_ 字面转义——LIKE 案义字符不作
    通配符；空/全空白 q 不过滤）。"""
    filters = []
    if status is not None:
        filters.append(ResearchHypothesis.status == status)
    if q is not None and q.strip():
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(ResearchHypothesis.statement.like(
            f"%{escaped}%", escape="\\"))
    return filters


def list_hypothesis_cards(
    db_path: Path, *,
    status: Optional[str] = None,
    q: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[Optional[str], str]] = None,
    order: str = "desc",
) -> List[Dict[str, Any]]:
    """主任务卡片行（B1）：hypotheses 主表+三标量子查询单 SQL 联查。

    卡片七字段为三源表派生视图（权威在各源表，本函数零持久化）；
    adhoc 记录（hypothesis_id='adhoc'）因假设表零父行被主表联查自然
    排除（结构事实非过滤特判）。cursor=(recent_activity_at,
    hypothesis_id) keyset 续页无漂移（沿 _apply_keyset 四表同构收口）。
    """
    stmt = select(
        ResearchHypothesis.hypothesis_id,
        ResearchHypothesis.statement,
        ResearchHypothesis.status,
        _VALIDATION_COUNT_EXPR.label("validation_count"),
        _LATEST_VERDICT_EXPR.label("latest_verdict"),
        _LATEST_RECORD_EXPR.label("latest_record_id"),
        _ACTIVITY_EXPR.label("recent_activity_at"),
    )
    for f in _cards_filters(status, q):
        stmt = stmt.where(f)
    stmt = _apply_keyset(
        stmt, _ACTIVITY_EXPR, ResearchHypothesis.hypothesis_id,
        cursor, order,
    ).offset(offset).limit(limit)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_hypothesis_cards(
    db_path: Path, *,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """卡片真总数（过滤口径与 list_hypothesis_cards 一致——status+q
    双过滤后的假设计数，非当页数）。"""
    stmt = select(func.count()).select_from(ResearchHypothesis)
    for f in _cards_filters(status, q):
        stmt = stmt.where(f)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()


# ============================================================ 插件（双写组合）

def save_plugin_with_registry(
    db_path: Path,
    *,
    source: str,  # formula | model
    plugin_id: str,
    name: str,
    version: int,
    expression: Optional[str] = None,      # formula
    output_columns: Optional[str] = None,  # formula
    config: Optional[str] = None,          # model
    signal_column: Optional[str] = None,   # model
    status: str = "valid",
    reject_reason: Optional[str] = None,
    entry_id: Optional[str] = None,        # None = rejected 只留记录不注册
    column_name: Optional[str] = None,     # 注册列名（entry 非 None 时必填）
    log_id: Optional[str] = None,
) -> Optional[str]:
    """原子双写：插件表 + signal_registry（valid 时）+ research_log(plugin)。

    Returns:
        注册 entry_id（valid）；rejected 时返回 None（不写注册表）。
    """
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        if source == "formula":
            conn.execute(insert(FormulaPlugin).values(
                plugin_id=plugin_id, name=name, version=version,
                expression=expression, output_columns=output_columns,
                status=status, reject_reason=reject_reason,
                strategy_visible=1, created_at=now,
            ))
        else:
            conn.execute(insert(ModelPlugin).values(
                plugin_id=plugin_id, name=name, version=version,
                config=config, signal_column=signal_column,
                status=status, reject_reason=reject_reason,
                strategy_visible=1, created_at=now,
            ))
        registered: Optional[str] = None
        if status == "valid" and entry_id is not None:
            conn.execute(insert(SignalRegistryEntry).values(
                entry_id=entry_id, source=source, plugin_id=plugin_id,
                column_name=column_name, version=version,
                strategy_visible=1, created_at=now,
            ))
            registered = entry_id
        conn.execute(insert(ResearchLog).values(
            log_id=log_id or _new_id(),
            hypothesis_id=None,
            entry_type="plugin",
            ref_id=plugin_id,
            summary=f"{source} plugin {name} v{version}: {status}"
                    + (f" ({reject_reason})" if reject_reason else ""),
            created_at=now,
        ))
    return registered


def list_signal_registry_entries(
    db_path: Path, *, only_visible: bool = False,
) -> List[Dict[str, Any]]:
    """插件信号列清单（内置指标不在此表——运行时与 INDICATOR_REGISTRY 合并）。"""
    engine = _engine_for(db_path)
    stmt = select(SignalRegistryEntry).order_by(
        SignalRegistryEntry.column_name, SignalRegistryEntry.version
    )
    if only_visible:
        stmt = stmt.where(SignalRegistryEntry.strategy_visible == 1)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def list_plugins(
    db_path: Path, *, source: str, name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """插件版本审计清单（formula/model 两表同构查询）。

    source="formula" 查 FormulaPlugin，否则 ModelPlugin（与
    save_plugin_with_registry 的 source 口径一致）；name 精确过滤
    同名版本族。排序 name+version（版本递增审计读序，原路由层
    内联 SQL 语义原样迁入）。
    """
    model = FormulaPlugin if source == "formula" else ModelPlugin
    engine = _engine_for(db_path)
    stmt = select(model).order_by(model.name, model.version)
    if name:
        stmt = stmt.where(model.name == name)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


# ============================================================ 分析任务句柄

def save_analysis_task(
    db_path: Path, *, task_id: str, template: str, params: str,
) -> None:
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(AnalysisTask).values(
            task_id=task_id, template=template, params=params,
            status="pending", created_at=_now(),
        ))


def update_analysis_task(
    db_path: Path, *, task_id: str,
    status: Optional[str] = None,
    reason: Optional[str] = None,
    result_ref: Optional[str] = None,
    result_truncated: Optional[int] = None,
) -> None:
    fields: Dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if reason is not None:
        fields["reason"] = reason
    if result_ref is not None:
        fields["result_ref"] = result_ref
    if result_truncated is not None:
        fields["result_truncated"] = result_truncated
    if status in ("succeeded", "failed"):
        fields["finished_at"] = _now()
    if not fields:
        return
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(update(AnalysisTask).where(
            AnalysisTask.task_id == task_id).values(**fields))


def list_analysis_tasks(
    db_path: Path, *,
    status: Optional[str] = None,
    template: Optional[str] = None,
    limit: int = 50, offset: int = 0,
) -> tuple[List[Dict[str, Any]], int]:
    """跨任务分析任务时间流（D8——analysis_tasks 表的列表视图：
    status/template SQL 过滤+created_at desc+count 配套）。空集=([], 0)。"""
    stmt = select(AnalysisTask)
    if status:
        stmt = stmt.where(AnalysisTask.status == status)
    if template:
        stmt = stmt.where(AnalysisTask.template == template)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = conn.execute(
            stmt.order_by(AnalysisTask.created_at.desc())
            .limit(limit).offset(offset)).mappings().all()
    return [dict(r) for r in rows], int(total)


def get_analysis_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(AnalysisTask).where(AnalysisTask.task_id == task_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def list_stale_running_tasks(
    db_path: Path, *, created_before: str,
) -> List[Dict[str, Any]]:
    """running 且 created_at 早于阈值的任务（API 启动中断回收用）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        rows = conn.execute(
            select(AnalysisTask).where(
                AnalysisTask.status == "running",
                AnalysisTask.created_at < created_before,
            )
        ).mappings()
        return [dict(r) for r in rows]


def register_existing_plugin_signal(
    db_path: Path,
    *,
    source: str,
    plugin_id: str,
    name: str,
    version: int,
    column_name: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """为已存在的插件行（如 pending→valid 的模型插件）补注册信号列。

    与 save_plugin_with_registry 的区别：不写插件表（行已存在），只做
    signal_registry 注册 + research_log 追加（同一事务）。
    """
    entry_id = _new_id()
    engine = _engine_for(db_path)
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(SignalRegistryEntry).values(
            entry_id=entry_id, source=source, plugin_id=plugin_id,
            column_name=column_name, version=version,
            strategy_visible=1, created_at=now))
        conn.execute(insert(ResearchLog).values(
            log_id=_new_id(), hypothesis_id=None, entry_type="plugin",
            ref_id=plugin_id,
            summary=f"{source} plugin {name} v{version}: valid"
                    + (f" ({json.dumps(extra, ensure_ascii=False)[:120]}" if extra else ""),
            created_at=now))
    return entry_id


# ============================================================ 功能需求单

def save_feature_request(
    db_path: Path,
    *,
    request_id: str,
    title: str,
    payload: str,
    status: str,
    created_at: str,
) -> None:
    """功能需求单落库（慢通道——只保证持久化与可查，不自动实现）。"""
    engine = _engine_for(db_path)
    with engine.begin() as conn:
        conn.execute(insert(FeatureRequest).values(
            request_id=request_id, title=title, payload=payload,
            status=status, created_at=created_at))


def get_feature_request(
    db_path: Path, request_id: str,
) -> Optional[Dict[str, Any]]:
    """单张需求单查询（回销 PATCH 的存在性先判——404 判定依据）。"""
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            select(FeatureRequest).where(
                FeatureRequest.request_id == request_id)
        ).mappings().first()
    return dict(row) if row is not None else None


def update_feature_request(
    db_path: Path,
    *,
    request_id: str,
    status: str,
    resolution: Optional[str] = None,
    touch_resolution: bool = True,
) -> Optional[Dict[str, Any]]:
    """需求单状态回销（变更二 4.2——部分更新语义的持久层投影）。

    touch_resolution=False 表示请求未携带 resolution 键——保留现值
    （部分更新规则①）；status→open 的联动清空由调用方以
    touch_resolution=True + resolution=None 表达（规则②）。值域与
    resolved 必填校验在路由层（本函数只承载写入）。

    Returns:
        更新后整行（dict）；request_id 不存在返回 None（404 由调用方
        判定）。
    """
    from sqlalchemy import update as sa_update

    engine = _engine_for(db_path)
    with engine.begin() as conn:
        row = conn.execute(
            select(FeatureRequest).where(
                FeatureRequest.request_id == request_id)
        ).mappings().first()
        if row is None:
            return None
        values: Dict[str, Any] = {"status": status}
        if touch_resolution:
            values["resolution"] = resolution
        conn.execute(sa_update(FeatureRequest).where(
            FeatureRequest.request_id == request_id).values(**values))
    out = dict(row)
    out["status"] = status
    if touch_resolution:
        out["resolution"] = resolution
    return out


def list_feature_requests(
    db_path: Path, *, status: Optional[str] = None,
    offset: int = 0, limit: int = 50,
    cursor: Optional[Tuple[str, str]] = None,
    order: str = "asc",
) -> List[Dict[str, Any]]:
    """功能需求单列表（分页双轨：offset/limit 原样；cursor=
    (created_at, request_id) keyset 严格比较——HTTP 列表端点 desc+
    cursor 续页；status 精确过滤）。count 配套 count_feature_requests。"""
    engine = _engine_for(db_path)
    stmt = _apply_keyset(
        select(FeatureRequest),
        FeatureRequest.created_at, FeatureRequest.request_id,
        cursor, order,
    ).offset(offset).limit(limit)
    if status is not None:
        stmt = stmt.where(FeatureRequest.status == status)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def count_feature_requests(
    db_path: Path, *, status: Optional[str] = None,
) -> int:
    """功能需求单真总数（过滤口径与 list_feature_requests 一致；
    HTTP 层 total=真总数消费，形制照 strategy.count_strategies）。"""
    stmt = select(func.count()).select_from(FeatureRequest)
    if status is not None:
        stmt = stmt.where(FeatureRequest.status == status)
    engine = _engine_for(db_path)
    with engine.connect() as conn:
        return conn.execute(stmt).scalar_one()
