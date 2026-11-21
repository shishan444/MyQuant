"""验证报告器（rebuild-paper-trading 4.1）：三层组装聚合函数。

报告=数据视图（派生物，非第二事实源——design §5.5；补记历史的权威
事实在 pt_funding 行）：

- 绝对绩效层——compute_metrics 单源引用（core/scoring/metrics.py 回测
  族，与回测报告同族可横向比较，零新公式），六输入由 pt_equity/pt_fill
  组装；「出场类」判据=realized_pnl 非 NULL（kernel 对 close/exit_*/reduce
  全部回填 realized_pnl，与回测引擎 vbt trades 含部分出场记录的口径
  对齐——受控等价层 8.1 前置）；
- 偏差对照层——同策略同任务参数在任务运行区间计算预期成绩，按报告
  形态分形对照源（fix-paper-deviation-contrast）：final（结题固化/
  修订）走 ReplayRunner 同参数直跑（临时库隔离回放行；与执行核心
  同构——diff=纯执行环境差，REDUCE 族策略不混入对照引擎缺陷差）；
  rolling（滚动实时计算）维持 run_backtest_on_range 桥（legacy VBT
  秒级快算——滚动「随时取」承诺 × replay 分钟级不可行）+第五注记
  披露缺陷差计入 diff。两形均带 contrast_source 字段（replay/
  legacy_vbt）。origin=replay 任务行在计算入口判域排除（回放即对照
  工具无对照轴——语义裁决）。dna_override=deepcopy 快照后写入
  task.leverage——两通道均无 leverage 参数，任务覆写经 DNA 副本传入
  （bridge/replay 零改动）；对照计算性失败（ResearchError/ReplayError
  ）该层标 unavailable 而不使整份报告失败；
- 证据强度层——60/5 双阈值（constants 单源）+research_status 查询期
  现算（§9.2 四值，build-strategy-lifecycle 5.3：三序解析迁移后随动；
  策略行已删回退任务行快照列=创建时点审计事实兜底）+funding 三态两
  计数。

报告时机：滚动=实时计算（running/pending/终态均可取，不落库）；结题
=completed/stopped/error 固化落 pt_report（error 态保留已产出数据同样
出报告）。终态后补记触发修订版恒 version=2 重算覆盖（design §4.5 版本
机制：不做版本链，补记历史的权威在 pt_funding）。报告结构无
verdict/feasible/recommendation 自动判定字段（考场无观点，D3/D10）。
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.errors import ResearchError, load_stored_json
from core.data.timeframes import TF_SECONDS
from core.paper_trading.constants import PAPER_MIN_BARS, PAPER_MIN_TRADES
from core.persistence.repository import paper
from core.persistence.timestamps import utc_now_iso
from core.scoring.metrics import compute_metrics

logger = logging.getLogger("paper_runtime")

# 结题终态（固化/修订/取 final 的统一门槛；未结题 → TaskNotConcludedError，
# REST 层组 5 转 409 "task not concluded"）——paper 单点常量别名
# （refactor-paper-runtime-data-access C 卡：原独立字面声明归一为
# 同一对象，三处恒等由静态保证；reporting 已 import paper 零新增依赖）
FINAL_STATUSES: frozenset[str] = paper.PT_STATUS_TERMINAL

# 偏差对照 warmup 前扩根数（design §6.4 契约值）
DEVIATION_WARMUP_BARS = 200

# diff 关键指标差值键集（design §6.1；差值=纸盘−回测，None 侧透传 None）
_DIFF_KEYS: tuple[str, ...] = (
    "total_return", "sharpe_ratio", "max_drawdown", "win_rate")

# funding「已结算」侧两态（design §6.3 三态：settled=正常/运行中补记已
# 扣款｜pending=挂起｜settled_disclosure_only=终态后回填未扣款——两态
# 证据均已补齐，合计计入 funding_settled_points）
_SETTLED_STATUSES: frozenset[str] = frozenset(
    {"settled", "settled_disclosure_only"})

# 聚合全量拉取上限（窗口级数据量——分页是 REST 流水面，聚合不适用）
_NO_LIMIT = 10**9


class TaskNotConcludedError(ValueError):
    """任务未结题（pending/running）而请求结题固化版——组 5 转 409。"""

    def __init__(self, task_id: str, status: str) -> None:
        super().__init__(
            f"paper task {task_id!r} not concluded (status={status!r})")
        self.task_id = task_id
        self.status = status


def build_report(
    db_path: Path, data_dir: Path, task_id: str, *,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """滚动报告（实时计算不落库；任意状态可取——running 中随时取当前版）。

    偏差对照层为 rolling 形态：legacy VBT 桥秒级快算+第五注记披露
    （架构约束：滚动「随时取」承诺 × replay 分钟级不可行）。

    Args:
        db_path: 工程数据库路径。
        data_dir: 行情数据目录（偏差对照层装载回测区间用）。
        task_id: 纸盘任务 ID。
        generated_at: 报告生成时点（缺省当前 UTC；测试注入定值）。

    Returns:
        三层结构报告 dict（design §6.1 schema）。

    Raises:
        ValueError: 任务不存在。
    """
    task = _require_task(db_path, task_id)
    return _assemble_report(
        db_path, data_dir, task, generated_at or utc_now_iso(),
        form="rolling")


def finalize_report(
    db_path: Path, data_dir: Path, task_id: str, *,
    generated_at: Optional[str] = None,
    contrast_runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """结题固化（version=1）：三终态时生成完整报告落 pt_report。

    调用方=app 装配的终态回调（app.py lifespan 注入 on_concluded——
    executor 三终态经 _finalize→调度器透传触发；不经回调的终态路径
    〔stop 的 CAS 直写/恢复清场转 error〕不固化）。error 态保留已产出
    数据同样出报告。

    Args:
        contrast_runner: final 对照计算器（replay 直跑能力——
            core.replay.runner.run_contrast_replay；域方向 L2-H3：实时
            域不 import 回放域，经装配点注入到达。缺省 None 时 final
            对照 unavailable〔原因明示非静默〕）。

    Raises:
        ValueError: 任务不存在。
        TaskNotConcludedError: 任务未结题。
    """
    task = _require_final(_require_task(db_path, task_id))
    ts = generated_at or utc_now_iso()
    report = _assemble_report(
        db_path, data_dir, task, ts, form="final",
        contrast_runner=contrast_runner)
    paper.save_report(
        db_path, task_id, report_version=1, generated_at=ts, as_of=ts,
        report_json=json.dumps(report, ensure_ascii=False))
    return report


def regenerate_final_report(
    db_path: Path, data_dir: Path, task_id: str, *,
    generated_at: Optional[str] = None,
    contrast_runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """终态后补记触发的修订版（design §4.5：恒 version=2 重算覆盖）。

    纯聚合重跑零副作用：全量重查三明细表（内容自然累积全部已补记点），
    OR REPLACE 覆盖 version=2 行（多次触发不增版）；as_of=重算时点；
    终态后补记披露段=evidence.funding_post_terminal_backfill（初版恒
    None）。终态后补记不改成绩（不动 balance/曲线），v1/v2 的
    performance 层逐键相同——报告由此自解释「为何存在 v2 而数字未变」。

    Args:
        contrast_runner: final 对照计算器（同 finalize_report——装配点
            注入；本函数当前生产零调用，终态后补记修订路径接线属既有
            缺口、不属本 change）。

    Raises:
        ValueError: 任务不存在。
        TaskNotConcludedError: 任务未结题。
    """
    task = _require_final(_require_task(db_path, task_id))
    ts = generated_at or utc_now_iso()
    report = _assemble_report(
        db_path, data_dir, task, ts, form="final",
        contrast_runner=contrast_runner)
    paper.save_report(
        db_path, task_id, report_version=2, generated_at=ts, as_of=ts,
        report_json=json.dumps(report, ensure_ascii=False))
    return report


def get_final_report(
    db_path: Path, task_id: str, version: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """取结题固化版：version 缺省最新版、显式取该版（?version=1 初版）。

    Returns:
        {task_id, report_version, generated_at, as_of, report}；终态而
        无固化版返回 None（固化由终态回调异步落库——不经回调的终态
        路径可能无固化版，此处不代偿补算）。

    Raises:
        ValueError: 任务不存在。
        TaskNotConcludedError: 任务未结题（组 5 转 409）。
    """
    task = _require_final(_require_task(db_path, task_id))
    row = paper.get_report(db_path, task["task_id"], version)
    if row is None:
        return None
    return {
        "task_id": row["task_id"],
        "report_version": row["report_version"],
        "generated_at": row["generated_at"],
        "as_of": row["as_of"],
        # TD-02 点位 7：固化 report_json 库内 JSON 列结构化解析（损坏
        # 422 PAPER_STORED_DATA_INVALID——防未来接线裸穿；本函数生产
        # 零调用已核验）
        "report": load_stored_json(
            row["report_json"], source="pt_report.report_json",
            detail={"task_id": row["task_id"],
                    "report_version": row["report_version"]},
            code="PAPER_STORED_DATA_INVALID"),
    }


# ── 内部组装 ──────────────────────────────────────────────


def _require_task(db_path: Path, task_id: str) -> Dict[str, Any]:
    task = paper.get_task(db_path, task_id)
    if task is None:
        raise ValueError(f"paper task not found: {task_id!r}")
    return task


def _require_final(task: Dict[str, Any]) -> Dict[str, Any]:
    if task["status"] not in FINAL_STATUSES:
        raise TaskNotConcludedError(task["task_id"], task["status"])
    return task


def _assemble_report(
    db_path: Path, data_dir: Path, task: Dict[str, Any],
    generated_at: str, *, form: str,
    contrast_runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """三层组装（全量读三明细表→三层→顶层 schema）。

    Args:
        form: 报告形态（"rolling"=build_report 滚动版；"final"=
            finalize/regenerate 终态固化版——偏差对照层分形依据）。
        contrast_runner: final 对照计算器（replay 直跑能力，装配点
            注入；rolling 形态不用）。
    """
    tid = task["task_id"]
    equity_rows = paper.list_equity_snapshots(db_path, tid, limit=_NO_LIMIT)
    fill_rows = paper.list_fills(db_path, tid, limit=_NO_LIMIT)
    funding_rows = paper.list_funding_records(db_path, tid, limit=_NO_LIMIT)
    # 出场类=realized_pnl 已回填行（list_fills 按 bar_time+seq 升序，
    # trade_returns 顺序稳定）
    exit_fills = [f for f in fill_rows if f["realized_pnl"] is not None]

    performance, paper_side = _performance_layer(
        task, equity_rows, exit_fills)
    evidence = _evidence_layer(
        db_path, task, equity_rows, fill_rows, exit_fills, funding_rows)
    deviation = _deviation_layer(
        db_path, data_dir, task, equity_rows, paper_side, evidence,
        form=form, contrast_runner=contrast_runner)
    # 第四键 qualification（paper-trading-revamp 组 C：三维终判段——
    # 既有三键不动；旧报告无此键合法）
    from core.paper_trading.qualification import build_qualification_section
    qualification = build_qualification_section(db_path, data_dir, task)
    return {
        "task_id": tid,
        "strategy_id": task["strategy_id"],
        "status": task["status"],
        "generated_at": generated_at,
        "performance": performance,
        "deviation": deviation,
        "evidence": evidence,
        "qualification": qualification,
    }


def _bars_per_year(timeframe: str) -> int:
    """年化换算（design §6.2）：365×86400/tf_seconds，TF_SECONDS 单源。

    4h=2190 恰为 compute_metrics 缺省档（等值锚）；非 4h 档由本函数显式
    换算传入（1h=8760），否则年化错周期。timeframe 从 DNA 抄录（上游
    validate_dna 把关），非法档 KeyError 直炸不设回退。
    """
    return 365 * 86400 // TF_SECONDS[timeframe]


def _performance_layer(
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    exit_fills: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """绝对绩效层：compute_metrics 六输入组装（公式单源，零新公式）。

    Returns:
        (performance, paper_side)——paper_side 供偏差对照层 diff 计算
        （total_return 为区间口径直算，compute_metrics 不返回该键）。
    """
    # DatetimeIndex 对齐回测口径（monthly_consistency 的 resample 需要）
    equity_curve = pd.Series(
        [float(r["equity"]) for r in equity_rows],
        index=pd.to_datetime([r["bar_time"] for r in equity_rows]),
        dtype=float,
    )
    n_closed = len(exit_fills)
    pnls = [float(f["realized_pnl"]) for f in exit_fills]
    trade_win_rate = (
        sum(1 for p in pnls if p > 0) / n_closed if n_closed else None)
    trade_returns = (
        np.array([p / task["initial_cash"] for p in pnls], dtype=float)
        if n_closed else None)
    metrics = compute_metrics(
        equity_curve,
        total_trades=n_closed,
        bars_per_year=_bars_per_year(task["timeframe"]),
        trade_win_rate=trade_win_rate,
        trade_returns=trade_returns,
        # benchmark 由偏差对照层供——本层不填（design §6.2）
    )
    total_return = (
        float(equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
        if len(equity_curve) >= 2 else None)
    paper_side = {
        "total_return": total_return,
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
    }
    return metrics, paper_side


def _deviation_layer(
    db_path: Path,
    data_dir: Path,
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    paper_side: Dict[str, Any],
    evidence: Dict[str, Any],
    *,
    form: str,
    contrast_runner: Optional[Any] = None,
) -> Dict[str, Any]:
    """偏差对照层（design §6.4）：任务区间对照预期成绩+diff+归因披露。

    两形对照源（fix-paper-deviation-contrast）：final（结题固化/修订）
    走 ReplayRunner 同参数直跑（与执行核心同构——diff=纯执行环境差）；
    rolling 维持 legacy VBT 桥+第五注记披露缺陷差。对照计算性失败
    （ResearchError/ReplayError——数据不足/warmup 不足/装载拒绝）该层
    标 unavailable（backtest=null+unavailable_reason），整份报告不失败。

    Args:
        form: 报告形态（"final"|"rolling"——对照源分形依据）。
    """
    contrast_source = "replay" if form == "final" else "legacy_vbt"
    notes = _attribution_notes(
        evidence, legacy_contrast=form == "rolling")
    # 计算入口判域（fix-paper-deviation-contrast 1.0）：origin=replay
    # 任务行不进 deviation 计算——回放任务本身即对照工具，「预期成绩」
    # 无定义良好的对照轴（语义裁决）；判据用 origin 域标记单源常量
    # （C1 落地后形态；此前 started_at is None 代理判据退役——回放行
    # 走 process_frames 不经 executor.run() 恒 NULL，与域标记恒等）
    if task.get("origin") == paper.PT_ORIGIN_REPLAY:
        return _unavailable(
            "回放任务不适用实时偏差对照（回放无实时对照语义）",
            notes, contrast_source=None)
    started_at = task["started_at"]
    if started_at is None or not equity_rows:
        return _unavailable(
            "任务尚未开始或无权益数据，无对照区间", notes,
            contrast_source=contrast_source)

    # start=任务 started_at 所在 bar（floor 到周期网格——对照按
    # index >= start 切区间，对齐到所在 bar 开盘时刻才包含该 bar）；
    # end=当前最后 bar（滚动）/结题 bar（终态）——同为 pt_equity 末行
    tf_seconds = TF_SECONDS[task["timeframe"]]
    start = pd.Timestamp(started_at).floor(f"{tf_seconds}s").isoformat()
    end = equity_rows[-1]["bar_time"]

    from core.strategy.dna import StrategyDNA

    # 对照 DNA 副本：各通道只把自己消费的 DNA 键对齐任务行（写入位
    # 即文档）。rolling：VBT 桥无 leverage 参数、真读 DNA
    # risk_genes.leverage——副本写入任务覆写值（通道零改动）；symbol
    # 经参数位显式传任务行值。final：run() 有 leverage 参数位，任务
    # 行值经 _final_deviation 直传（executor 以任务行为准）；symbol
    # 从 DNA execution_genes 派生（runner.run 单源汇聚点）——副本对
    # 齐任务行 symbol，挂盘 body.symbol 覆写场景下对照轴不混入品种
    # 差、两通道基准同源
    # TD-02 点位 8：dna_snapshot_json 损坏属工程错误——结构化拒绝使
    # 报告生成显式失败，不走 unavailable 降级（工程错误≠计算性失败）
    dna_override = deepcopy(StrategyDNA.from_dict(load_stored_json(
        task["dna_snapshot_json"], source="pt_task.dna_snapshot_json",
        detail={"task_id": task["task_id"]},
        code="PAPER_STORED_DATA_INVALID")))

    if form == "final":
        # final 消费 execution_genes.symbol（leverage 经参数位直传）
        dna_override.execution_genes.symbol = task["symbol"]
        return _final_deviation(
            data_dir, task, start, end, dna_override, paper_side, notes,
            contrast_runner=contrast_runner)

    # rolling：legacy VBT 桥（秒级快算——架构约束：滚动实时计算承诺）
    from core.analysis.backtest_bridge import run_backtest_on_range

    # 杠杆缝隙处理（design §6.4）：VBT 桥无 leverage 参数，读 DNA
    # risk_genes.leverage——副本写入任务覆写值（通道零改动）。symbol
    # 经参数位显式传任务行值
    dna_override.risk_genes.leverage = int(task["leverage"])

    try:
        bt_metrics, _returns, _warmup, _bars = run_backtest_on_range(
            db_path, data_dir, dna_override,
            task["symbol"], task["timeframe"], start, end,
            DEVIATION_WARMUP_BARS,
            init_cash=task["initial_cash"], fee=task["fee"],
            slippage=task["slippage"],
        )
    except ResearchError as exc:
        return _unavailable(
            f"{exc.code}: {exc.message}", notes,
            contrast_source=contrast_source)

    diff = {
        key: (
            None if paper_side[key] is None or bt_metrics.get(key) is None
            else float(paper_side[key] - bt_metrics[key]))
        for key in _DIFF_KEYS
    }
    return {
        "backtest": bt_metrics,
        "diff": diff,
        "contrast_source": contrast_source,
        "unavailable_reason": None,
        "attribution_notes": notes,
    }


def _unavailable(
    reason: str, notes: List[str], *,
    contrast_source: Optional[str],
) -> Dict[str, Any]:
    """对照降级构造器（backtest=null+diff 全 None+原因）。

    contrast_source 语义：本报告形态的对照引擎（按 form 恒定——
    rolling=legacy_vbt/final=replay，降级时同样披露「本形态会用哪个
    引擎」）；仅 replay 判域排除（无对照轴）为 None。
    """
    return {
        "backtest": None,
        "diff": {k: None for k in _DIFF_KEYS},
        "contrast_source": contrast_source,
        "unavailable_reason": reason,
        "attribution_notes": notes,
    }


def _final_deviation(
    data_dir: Path,
    task: Dict[str, Any],
    start: str,
    end: str,
    dna_override: Any,
    paper_side: Dict[str, Any],
    notes: List[str],
    *,
    contrast_runner: Optional[Any],
) -> Dict[str, Any]:
    """final 对照（fix-paper-deviation-contrast 1.1）：replay 直跑
    经注入到达（DNA 快照+leverage/费滑/资金覆写+任务区间）。

    contrast_runner=core.replay.runner.run_contrast_replay（同参数
    直跑，回放行落临时库不污染生产任务域，跑毕读回明细三件套——
    域方向 L2-H3：实时域不 import 回放域，经装配点注入）。对照成绩
    组装复用 _performance_layer（compute_metrics 单源+区间口径
    total_return——与纸盘侧同公式同口径，diff=纯执行环境差）。
    注入函数错误族对报告器不可知——宽 Exception 捕获降级
    unavailable（计算性失败语义——不炸报告，原因含异常类名）。
    """
    if contrast_runner is None:
        return _unavailable(
            "final 对照计算器未装配（contrast_runner=None——装配点 "
            "app.py 终态回调注入）", notes, contrast_source="replay")
    try:
        replay_task, replay_equity, replay_fills = contrast_runner(
            data_dir, dna_override, start, end,
            fee=task["fee"], slippage=task["slippage"],
            initial_cash=task["initial_cash"],
            leverage=int(task["leverage"]),
            window_bars=task["window_bars"],
            stop_loss_pct=task["stop_loss_pct"],
            halt_drawdown_pct=task["halt_drawdown_pct"])
    except Exception as exc:  # noqa: BLE001——注入边界，错误族不可知
        # 完整异常本层落日志后丢弃原文（app 回调层只接 finalize_report
        # 上抛，本分支降级不上抛——留痕须在丢弃前）。reason：受控族
        # （带 code——构造点全为固定中文文案+参数值）保留「类名: 原
        # 文」；非受控只记类名——原文可能携临时目录绝对路径/系统用
        # 户名，将永久固化进报告并经 MCP 透传/导出外泄。code 属性鸭
        # 子判据——L2-H3 域隔离下 reporting 不 import ReplayError
        logger.exception("final 对照计算失败（task=%s）",
                         task.get("task_id"))
        reason = (f"{type(exc).__name__}: {exc}"
                  if getattr(exc, "code", None) is not None
                  else type(exc).__name__)
        return _unavailable(reason, notes, contrast_source="replay")
    replay_exits = [f for f in replay_fills
                    if f["realized_pnl"] is not None]
    metrics, replay_side = _performance_layer(
        replay_task, replay_equity, replay_exits)

    diff = {
        key: (None if paper_side[key] is None or replay_side[key] is None
              else float(paper_side[key] - replay_side[key]))
        for key in _DIFF_KEYS
    }
    # backtest 键=对照成绩（compute_metrics 输出+区间口径 total_return
    # 并入——diff 四键与 backtest 键可对拍，与 VBT 形制消费面兼容）
    backtest = dict(metrics)
    backtest["total_return"] = replay_side["total_return"]
    return {
        "backtest": backtest,
        "diff": diff,
        "contrast_source": "replay",
        "unavailable_reason": None,
        "attribution_notes": notes,
    }


def _attribution_notes(
    evidence: Dict[str, Any], *, legacy_contrast: bool,
) -> List[str]:
    """归因披露：两侧已知口径差+挂起补记披露（按对照源分形）。

    legacy（rolling）四项+第五项（对照引擎缺陷差计入 diff——spec
    披露义务）；replay（final）与执行核心同构，清算无已知口径差不
    列，资金费为数据面差（实时 vs 历史归档）——「如实列明」按形态
    取实（VBT 时代的「固定 0.001 估算」「无杠杆曲线等效线」对回放
    对照不成立，照抄即失实陈述）。
    """
    settled = evidence["funding_settled_points"]
    pending = evidence["funding_pending_points"]
    backfill_note = (f"资金费挂起补记：{settled} 点已补记、"
                     f"{pending if pending is not None else 0} 点待补"
                     "（补记时点前权益略高）")
    if legacy_contrast:
        return [
            "资金费：纸盘按真实资金费率逐点结算，回测按固定 0.001 估算",
            "清算：纸盘按仓位口径逐 bar 分层判定，回测为无杠杆曲线"
            "等效线",
            "对照区间边界：回测按 bar 闭合序列取区间，纸盘按任务起止"
            " bar",
            backfill_note,
            "对照引擎为 legacy VBT——其已知缺陷差（如减仓不终止）"
            "计入 diff",
        ]
    return [
        "资金费：纸盘按实时资金费率逐点结算，对照回放按历史归档费率"
        "（缺档挂起不扣）",
        "对照区间边界：回放按 bar 闭合序列取区间，纸盘按任务起止 bar",
        backfill_note,
    ]


def _evidence_layer(
    db_path: Path,
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    fill_rows: List[Dict[str, Any]],
    exit_fills: List[Dict[str, Any]],
    funding_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """证据强度层：60/5 双阈值+研究状态现算+funding 三态计数+出场
    原因分布。

    research_status 查询期现算（build-strategy-lifecycle 5.3 读点①：
    resolve_research_status 三序——迁移后随动非快照）；策略行已删
    （draft 真删等场景）回退任务行快照列（创建时点审计事实兜底，
    信息不丢失不虚构）。

    insufficient_evidence = bars_run<60 OR closed_trades<5（任一命中即
    整份报告标注；position_bars 共同披露但不参与判定）。

    exit_reasons（add-position-sizing-params 层 6，对质②裁决）＝出场
    原因分布（归因统计）：出场类=close+exit_* 全出场族七种（close 类
    按 reason 细分为 "close:signal"/"close:reverse"）按 Intent kind
    直接作键聚合；reduce 是仓位管理动作非出场，不进本键——两层口径
    显式分工：reduce 的部分实现收益计入 performance 层（「出场类=
    realized_pnl 非 NULL」是 trade_returns 的组装口径，含 reduce），
    evidence 层归因不含它。无出场任务该键={}（键恒在场，与「无数据」
    区分）。权威源=pt_fill（聚合派生视图不另立第二事实源）。
    """
    from core.persistence.repository.strategy import get_strategy

    bars_run = len(equity_rows)
    closed_trades = len(exit_fills)
    position_bars = _position_bars(task, equity_rows, fill_rows)
    settled = sum(
        1 for r in funding_rows if r["status"] in _SETTLED_STATUSES)
    pending = sum(1 for r in funding_rows if r["status"] == "pending")
    # 研究状态读点现算：策略行在→三序解析；行已删→快照列审计值兜底
    strategy = get_strategy(db_path, task["strategy_id"])
    research_status = (
        paper.resolve_research_status(db_path, strategy)
        if strategy is not None
        else task["research_status_snapshot"])
    # 出场原因分布：kind 直接作键（close 细分 reason）；reduce 跳过
    # （仓位管理动作非出场——归因口径，收益面归 performance 层）
    exit_reasons: Dict[str, int] = {}
    for f in exit_fills:
        if f["kind"] == "reduce":
            continue
        key = (f"close:{f['reason']}" if f["kind"] == "close"
               else f["kind"])
        exit_reasons[key] = exit_reasons.get(key, 0) + 1
    # 终态后补记披露段（design §6.3）：N 点、合计金额 M——未计入账户余额
    disclosure = [
        r for r in funding_rows if r["status"] == "settled_disclosure_only"]
    backfill = (
        {"points": len(disclosure),
         "total_amount": float(sum(r["amount"] or 0.0 for r in disclosure))}
        if disclosure else None)
    return {
        "bars_run": bars_run,
        "closed_trades": closed_trades,
        "position_bars": position_bars,
        "insufficient_evidence": (
            bars_run < PAPER_MIN_BARS or closed_trades < PAPER_MIN_TRADES),
        "research_status": research_status,
        "funding_settled_points": settled,
        # 无 funding 行=None（「无资金费数据」与「0 笔挂起」语义区分）
        "funding_pending_points": pending if funding_rows else None,
        "funding_post_terminal_backfill": backfill,
        "exit_reasons": exit_reasons,
    }


def _position_bars(
    task: Dict[str, Any],
    equity_rows: List[Dict[str, Any]],
    fill_rows: List[Dict[str, Any]],
) -> int:
    """当前持仓已持有根数（kernel.bars_held 同域等价口径，修复卡 A1）。

    kernel 口径=当前 bar seq−入场 bar seq+1（ClosedBar.seq 域=feed 全量
    帧行号，account.py bars_held）；报告器只读库拿不到当前 bar 的全量
    帧 seq，等价同基=最近一次 open 成交 bar 起的权益行数（每 bar 落库
    契约下=seq 差+1 含入场当根；减仓/加仓不引入 open 故不重置、反手
    open 重建锚——与 kernel「持仓期连续」语义一致）。禁用 bars_processed
    参与算术：它是任务感知计数（start_after 跳过的历史段不+1），与
    全量帧行号混域相减在生产场景恒得大负数。无 open 记录而有持仓=
    状态损坏，按 0 披露不炸报告（数据视图哲学）。
    """
    if task["position_side"] is None or not equity_rows:
        return 0
    open_bars = [f["bar_time"] for f in fill_rows if f["kind"] == "open"]
    if not open_bars:
        return 0
    last_open = max(open_bars)
    return sum(1 for r in equity_rows if r["bar_time"] >= last_open)
