"""sandbox 回测编排与判定并集查询（旧三编排 isolated/random/staged 已随
retire-legacy-subsystems 退役删除——快速试错统一流 run_sandbox_backtest
为验证记录唯一生产写入通道，判定权收敛人工 Web 审核）。

run_sandbox_backtest 纪律内嵌：每次执行强制落研究日志（挂 hypothesis_id
时区间进判定并集，封堵「先偷看后验证」）；区间 bar 数超上限拒绝；
不判门槛、verdict 恒空串。K1（D-3.7，2026-11-16 用户终裁「新记录必须
保留明细」）：经桥 with_detail 取明细载荷，两组装函数（_build_detail_
payload/_resolve_trace_rows）零 diff 复用，save_sandbox_validation_record
同事务原子落三明细表——sandbox 新记录 detail_available=true 可复核。

query_union_balance/load_data_bounds：判定并集余量只读查询（workbench
共享流内部消费——union-balance REST/MCP 端点已退役，实现保留）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.analysis.backtest_bridge import run_backtest_on_range
from core.analysis.errors import ResearchError, require_finite_values
from core.analysis.isolation import judged_ranges_union
from core.analysis.registry_meta import derive_warmup_full
from core.analysis.warmup_loader import TF_MINUTES, to_utc
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)

# 沙盒单次 bar 上限（M6：防爆超时——须在 MCP write_timeout=60s 内
# 留足余量；50k 为默认起点，按回测速度实施校准）
SANDBOX_MAX_BARS = 50_000


def _range_hash(request_range: Dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(request_range, sort_keys=True).encode()).hexdigest()


def dna_indicator_names(dna) -> List[str]:
    """提取 DNA 实际用到的指标名（signal_genes + 各 MTF layer +
    序列形条件的 target_indicator——fix-research-c6 warmup 并集：
    列名 target 指标的 lookback 不计入则 target 列前段 NaN，进场
    信号前段缺失）。"""
    names = set()
    # 派生基础列（hour_utc）非注册表成员——跳过（derive_warmup 对
    # 未知指标 KeyError；装载注入零收敛成本，防炸点）；import 提至
    # 闭包外层（逐基因重复 import 无意义）
    from core.strategy.executor import _BASE_DIRECT_READ_COLUMNS

    def _collect_gene(gene) -> None:
        if getattr(gene, "indicator", None):
            if gene.indicator in _BASE_DIRECT_READ_COLUMNS:
                return
            names.add(gene.indicator)
        # 序列形条件（gt/lt/ge/le/cross_*_series 带 target_indicator）：
        # target 侧指标并入 warmup 并集
        cond = getattr(gene, "condition", None)
        if isinstance(cond, dict):
            t_ind = cond.get("target_indicator")
            if t_ind:
                names.add(t_ind)

    for gene in dna.signal_genes or []:
        _collect_gene(gene)
    for layer in dna.layers or []:
        for gene in getattr(layer, "signal_genes", []) or []:
            _collect_gene(gene)
    return sorted(names)


def _load_strategy_dna(db_path: Path, strategy_id: str):
    """读库装载策略 DNA（四入口共用单点：隔离回测/随机窗口/沙盒
    回测/分段验证经调用自动全覆盖）。

    装载即校验（signal-sources-structure-gate 卡 R，Q-1 裁决=挂
    全量 validate_dna）：from_json 成功后、return 前执行全量结构/
    值域校验——不过即 ResearchError(RESEARCH_DNA_INVALID) 结构化
    拒绝（detail 携 strategy_id+errors 判据明细）。防线边界：「可
    解析但校验不过」；from_json 解析失败的更重损毁沿既有边界
    （挂账 TECH-DEBT，与装载校验分属两层）。
    """
    from core.persistence.repository.strategy import get_strategy
    from core.strategy.dna import StrategyDNA

    row = get_strategy(db_path, strategy_id)
    if row is None:
        raise ResearchError("RESEARCH_STRATEGY_NOT_FOUND",
                            f"策略不存在: {strategy_id}", status=404)
    dna = StrategyDNA.from_json(row["dna_json"])
    from core.strategy.validator import validate_dna
    vr = validate_dna(dna)
    if not vr.is_valid:
        raise ResearchError(
            "RESEARCH_DNA_INVALID",
            f"库内策略 DNA 结构/值域非法: {strategy_id}",
            detail={"strategy_id": strategy_id, "errors": vr.errors})
    return dna, row


def gene_tf_axis_refs(dna) -> Dict[str, List[tuple]]:
    """基因级高周期轴引用分组：带基因级 tf 的基因按轴归入
    (indicator, params) 引用清单（含序列对照类条件的 target_indicator
    并集——design 2.5 契约；收敛粒度按条件实际参数推导）。基准轴
    指标不在此（仍在 warmup 主清单）。warmup 折算与短窗预检两
    消费方单源（fix-review B8 抽取）。"""
    axis_map: Dict[str, List[tuple]] = {}
    for gene in (getattr(dna, "signal_genes", None) or []):
        gene_tf = getattr(gene, "timeframe", None)
        if gene_tf is not None and gene_tf != dna.execution_genes.timeframe:
            refs = [(gene.indicator, dict(gene.params or {}))]
            cond = gene.condition if isinstance(gene.condition, dict) else {}
            if cond.get("target_indicator"):
                refs.append((cond["target_indicator"],
                             dict(cond.get("target_params") or {})))
            axis_map.setdefault(gene_tf, []).extend(refs)
    return axis_map


def _derive_dna_warmup(db_path: Path, dna, base_tf: str) -> Tuple[int, List[str]]:
    """DNA warmup 推导薄壳：提取引用 → derive_warmup_full 两维同式。

    双产出保持（(warmup, indicator_names)——后者被 warmup.indicators
    披露字段消费）：只迁计算式不动产出面。插件 lookback 按依赖闭包
    递归取 max（fx_b 引用 fx_a → 两窗口都计入）；model 源
    walk-forward 训练窗在求值端自理，不占 warmup。插件列不存在时
    贡献 0（求值端显式拒绝兜底）。仅含插件源时 derive_warmup([])
    的 floor 200 保底。

    Raises:
        KeyError: 内置指标未知（透传给调用方决定拒绝语义）。
    """
    indicator_names = dna_indicator_names(dna)
    formula_refs = {s["ref"] for s in (dna.signal_sources or [])
                    if s.get("type") == "formula" and s.get("ref")}
    # series 档止损线来源 warmup 两轨并入（dynamic-stop-loss-series
    # 4.2——warmup 覆盖面=求值面）：指标形式并入内置指标清单（周期×3
    # 规则自动推导）；fx 形式并入插件列集（plugin_columns_lookback
    # 依赖闭包同点扩展）
    _sl_src = getattr(dna.risk_genes, "sl_series_source", None)
    if isinstance(_sl_src, dict):
        if "indicator" in _sl_src and _sl_src["indicator"] not in \
                indicator_names:
            indicator_names = indicator_names + [_sl_src["indicator"]]
        elif "column" in _sl_src:
            formula_refs.add(str(_sl_src["column"]))
    # 基因级周期分组（unblock-research-validation 预热第三维）：
    # 分组提取单源 _gene_tf_axis_refs（B8 抽取——与短窗预检共用）
    indicator_tfs = gene_tf_axis_refs(dna)
    # R3 接线（fix-mtf-execution-consistency）：base_tf 必填消费轴——
    # 推导折算分母与回测装载消费轴显式一致（守卫在前保证
    # base_tf==execution_genes.timeframe，折算分母无歧义）；移除
    # dna.execution_genes.timeframe 内部兜底（显式契约防复发——漂移
    # DNA 的执行周期≠请求周期时兜底产 8 倍欠账）
    warmup = derive_warmup_full(
        db_path, indicator_names, formula_refs,
        indicator_tfs=indicator_tfs or None,
        base_tf=base_tf)
    return warmup, indicator_names




def _resolve_trace_rows(
    detail: Dict[str, Any], dna, exec_ref: str,
) -> List[Dict[str, Any]]:
    """份级留痕行解析（add-vr-signal-trace 任务 5.1——resolver 接线）。

    桥 detail 载荷（equity 索引=trace_buf 行轴〔切回请求区间后〕/
    trades_df/swallowed/trace_buf/value_curve）+DNA 风控参数 →
    vr_signal_trace 行列表（record_id 由落库层补）。三入口共用——
    漏一处=该类型新记录三布尔 NULL 被验收锚打红。
    """
    from core.backtest.trace_resolver import resolve_signal_trace

    rg = dna.risk_genes
    # size_pct 物化对齐装配单源（positioning/resolve.py
    # _materialize_legacy：initial_fraction>0 优先否则 position_size；
    # position_size 恒非 None 无第三分支——CARD-01 线索 7）
    size_pct = (
        float(rg.initial_fraction)
        if rg.initial_fraction is not None and rg.initial_fraction > 0
        else float(rg.position_size))
    risk_params = {
        "position_mode": rg.position_mode or "fixed",
        "size_pct": size_pct,
        # 加仓物化缺省 0.1（同 engine 装载段——11 码期望量重放消费；
        # 装配单点在此，不进 positioning/resolve.py）
        "add_fraction": (
            float(rg.add_fraction)
            if rg.add_fraction is not None else 0.1),
        "sl_mode": rg.sl_mode or "pct",
        "sl_stop": float(rg.stop_loss) if rg.stop_loss else 0.0,
        "risk_fraction": (
            float(rg.risk_fraction)
            if rg.risk_fraction is not None else None),
    }
    return resolve_signal_trace(
        detail.get("trace_buf"), detail["equity"].index,
        detail.get("swallowed"), detail.get("trades_df"),
        exec_ref=exec_ref, init_cash=detail.get("init_cash", 100000.0),
        risk_params=risk_params,
        value_curve=detail.get("value_curve"))


def _build_detail_payload(
    detail: Dict[str, Any], *,
    exec_ref: str, exec_index: int,
    range_start: str, range_end: str, timeframe: str,
) -> Dict[str, Any]:
    """明细组装（B4）：桥 detail 载荷 → 两表行结构。

    网格同构守卫（R-1 定案）：理想网格 start 锚定=equity 索引自身首
    元素（自锚定形制——断言语义=序列自身等距性，MUST NOT 锚定数据
    装载首 bar；equity 索引是请求区间切回后的轴，与装载区间起点无关
    ——「等距但与请求区间错位」判紧凑是该契约语义下的正确行为）；
    相等→紧凑形制（grid_aligned=1, start/timeframe/length），不等→
    降级形制（grid_aligned=0, timestamps 显式数组——禁静默网格重建，
    洞数据集错位防线；降级不拒绝：执行证据保真优先）。

    Returns:
        {"trades": [逐笔行], "equity": 曲线行}——顶层仅两键，四标识
        （exec_ref/exec_index/range_start/range_end）只随行级携带；
        trades 行含除 record_id 外全部列（落库层补 record_id）；
        equity 行同。
    """
    equity = detail["equity"]
    n = int(len(equity))
    values_json = json.dumps([float(v) for v in equity.values],
                             ensure_ascii=False)
    if n == 0:
        # 空曲线（防御位——正常路径桥对零 bar 区间前置拒绝）：
        # 平凡对齐，紧凑形制 start=None
        equity_row: Dict[str, Any] = {
            "grid_aligned": 1, "start": None, "length": 0,
            "timestamps": None, "values": "[]",
        }
    else:
        freq = pd.Timedelta(minutes=TF_MINUTES[timeframe])
        ideal = pd.date_range(equity.index[0], periods=n, freq=freq)
        if equity.index.equals(ideal):
            equity_row = {
                "grid_aligned": 1, "start": str(equity.index[0]),
                "length": n, "timestamps": None, "values": values_json,
            }
        else:
            equity_row = {
                "grid_aligned": 0, "start": None, "length": None,
                "timestamps": json.dumps(
                    [str(t) for t in equity.index], ensure_ascii=False),
                "values": values_json,
            }
    equity_row.update({
        "exec_ref": exec_ref, "exec_index": exec_index,
        "range_start": str(range_start), "range_end": str(range_end),
        "timeframe": timeframe,
    })

    trades: List[Dict[str, Any]] = []
    tdf = detail.get("trades_df")
    if tdf is not None and len(tdf) > 0:
        # vbt 0.28.5 records_readable 列族（实测）：Entry Timestamp/
        # Exit Timestamp/Avg Entry Price/Avg Exit Price/Size/PnL/
        # Direction——Status=Open 行 Exit Timestamp=NaT（exit_time
        # 存 NULL，保真不伪造平仓时点）
        for k in range(len(tdf)):
            row = tdf.iloc[k]
            exit_ts = row["Exit Timestamp"]
            pnl_val = row["PnL"]
            trades.append({
                "exec_ref": exec_ref, "exec_index": exec_index,
                "range_start": str(range_start), "range_end": str(range_end),
                "seq": k,
                "entry_time": str(row["Entry Timestamp"]),
                "exit_time": (None if pd.isna(exit_ts) else str(exit_ts)),
                "entry_price": float(row["Avg Entry Price"]),
                "exit_price": float(row["Avg Exit Price"]),
                "size": abs(float(row["Size"])),
                "direction": str(row["Direction"]),
                "pnl": None if pd.isna(pnl_val) else float(pnl_val),
                # decision_json 退役（retire-vr-decision-json 任务 1）：
                # 逐笔决策过程披露由 vr_signal_trace 事件级行承载
                # （add-vr-signal-trace 兑现——reason/capped/事件码全量
                # 在场，本键 {"trigger": null} 极简形零信息量已撤）
            })

    return {"trades": trades, "equity": equity_row}


def run_sandbox_backtest(
    db_path: Path,
    data_dir: Path,
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    fee: float = 0.001,
    slippage: float = 0.0005,
    init_cash: float = 100000.0,
    hypothesis_id: Optional[str] = None,
    leverage: Optional[int] = None,
    margin_cap: Optional[float] = None,
    halt_drawdown_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """沙盒回测（improve-mcp-agent-loop M6）：快速试错，不判门槛、不构成验证证据。

    纪律内嵌：每次沙盒强制落研究日志——挂 hypothesis_id 时区间进判定
    并集（封堵"先偷看后验证"），不挂时以 adhoc 日志留痕。
    区间 bar 数超上限拒绝（防爆超时，引导分多次小区间 run_backtest）。

    验证记录统一流（add-sandbox-records-unified-flow R1）：每次执行
    同步写入 validation_records（level=sandbox_backtest 行——verdict 恒
    空串不参与三态判定；hypothesis_id 未挂以 'adhoc' 字面量入行），
    响应回显 record_id；沙盒记录不可作 ruler/ab 对照源。

    任务级封顶两参（sandbox-and-report-supplement D1 ③层——路由层
    已完成值域校验）：leverage/margin_cap 透传引擎；两参都传时③层
    生效（缩量至顶＋capped_by_task_cap 计数＋task_cap 生效面披露），
    未传逐位不变。

    Returns:
        {"strategy_id", "symbol", "timeframe", "metrics", "bars_used",
         "warmup_bars", "hypothesis_id", "record_id", "sandbox": True,
         "task_params"?}
    """
    # bars 上限（MCP write_timeout=60s 内留余量；50k 起点实施时校准）
    tf_minutes = TF_MINUTES.get(timeframe, 240)
    start_ts = to_utc(pd.Timestamp(start))
    end_ts = to_utc(pd.Timestamp(end))
    bars = int((end_ts - start_ts).total_seconds() / 60 / tf_minutes)
    if bars > SANDBOX_MAX_BARS:
        raise ResearchError(
            "RESEARCH_SANDBOX_LIMIT",
            f"沙盒区间 {bars} bar 超上限 {SANDBOX_MAX_BARS}——分多次"
            "小区间 run_backtest（单次上限内验证后并读结果）",
            status=422,
            detail={"bars": bars, "max": SANDBOX_MAX_BARS,
                    "slow_path": "分多次小区间 run_backtest"})

    # 假设存在性前置（ND1——三入口对称先例）：沙盒入表后假 id 会成为
    # 挂不存在假设的「证据」行，必须拦；None（adhoc 路径）跳过校验
    # 直接以 'adhoc' 字面量入行
    if hypothesis_id is not None:
        if rr.get_hypothesis(db_path, hypothesis_id) is None:
            raise ResearchError(
                "RESEARCH_HYPOTHESIS_NOT_FOUND",
                f"假设不存在: {hypothesis_id}", status=404,
                detail={"hypothesis_id": hypothesis_id})

    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    symbol = symbol or strat_row.get("symbol", "BTCUSDT")
    timeframe = timeframe or strat_row.get("timeframe", "4h")
    warmup_bars, _ = _derive_dna_warmup(db_path, dna, timeframe)  # R3：消费轴

    # K1 明细接线（retire-legacy D-3.7 ①②）：with_detail=True 取桥明细
    # 载荷（equity/trades_df/init_cash/fee/slippage/swallowed/trace_buf/
    # mtf_gated_bars/value_curve——桥与引擎零改动）；trade_returns 不进
    # 沙盒响应（不判门槛不披露 CI——_ 占位解包）
    metrics, _trade_returns, actual_warmup, bars_used, detail = (
        run_backtest_on_range(
            db_path, data_dir, dna, symbol, timeframe, start, end,
            warmup_bars,
            init_cash=init_cash, fee=fee, slippage=slippage,
            strategy_id=strategy_id, with_detail=True,
            task_leverage=leverage, task_margin_cap=margin_cap,
            task_halt_drawdown_pct=halt_drawdown_pct))

    # 强制落研究日志（挂假设 → 区间进判定并集；机制：judged_ranges_union
    # 取该假设全部日志条目 data_ranges，现成无需改动）
    range_entry = {"symbol": symbol, "timeframe": timeframe,
                   "start": start, "end": end}
    rr.append_research_log(
        db_path,
        hypothesis_id=hypothesis_id,  # None → adhoc 留痕
        entry_type="sandbox_backtest",
        ref_id=strategy_id,
        data_ranges=json.dumps([range_entry], ensure_ascii=False),
        summary=f"sandbox backtest {symbol} {timeframe} [{start},{end}) "
                f"fee={fee} slippage={slippage}")

    # 验证记录统一流写入（R1——编排步骤 5）：日志先行（步骤 4，并集
    # 唯一事实源）记录随后，两独立事务；写失败裸穿 fail-loud（最坏
    # 状态=「并集已计、记录缺」，重跑同区间即补——并集对同区间幂等）。
    # params 七恒键+三可选键传了才写（无 request_range——ruler/ab 对照
    # 形制校验天然拒绝）；result 携带完整 metrics（ND2）；range_hash
    # 掺参数全集+record_id（D5——每次执行恒新，五元唯一约束永不冲突）
    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    # K1 ③（D-3.7）：params 三键值源切桥 detail 回显（本调用点两值源
    # 恒等——显式传参与桥生效值同源；执行参数以桥实际生效值为单源，
    # 防双源漂移；七恒键形制不变不五键化，键序保持原形——params 与
    # range_hash 逐字节不变）
    record_params: Dict[str, Any] = {
        "symbol": symbol, "timeframe": timeframe, "start": start,
        "end": end, "fee": detail["fee"], "slippage": detail["slippage"],
        "init_cash": detail["init_cash"]}
    if detail.get("leverage") is not None:
        record_params["leverage"] = detail["leverage"]
    if detail.get("margin_cap") is not None:
        record_params["margin_cap"] = detail["margin_cap"]
    if halt_drawdown_pct is not None:
        record_params["halt_drawdown_pct"] = halt_drawdown_pct
    # K1 ④：result_json 纯增 mtf_gated_bars 顶层键（null=无门控面与
    # 0=门控全过严格区分——单份直取，不经旧三份聚合函数）
    # 引擎浮点溢出守卫（fix-replay-list-channel CARD-03/D-2 同构面）：
    # metrics 数值来自引擎计算（schema le 上界管不到产出面），非有限
    # 值结构化拒绝不落坏行；params 侧数值已过 schema 上界恒有限免守卫
    result_payload = {
        "metrics": metrics, "symbol": symbol, "timeframe": timeframe,
        "bars_used": bars_used, "warmup_bars": actual_warmup,
        "mtf_gated_bars": detail.get("mtf_gated_bars")}
    require_finite_values(
        result_payload, source="validation_records.result",
        detail={"record_id": record_id})
    rr.save_sandbox_validation_record(
        db_path,
        record_id=record_id,
        hypothesis_id=hypothesis_id or "adhoc",
        strategy_id=strategy_id,
        params=json.dumps(record_params, ensure_ascii=False),
        result=json.dumps(result_payload, ensure_ascii=False),
        range_hash=_range_hash({**record_params, "record_id": record_id}),
        detail=[_build_detail_payload(
            detail, exec_ref="main", exec_index=0,
            range_start=start, range_end=end, timeframe=timeframe)],
        trace=_resolve_trace_rows(detail, dna, "main"))

    # 任务级参数回显（A1：实际生效值——请求实参逐键传了才写；
    # 有效杠杆=min(DNA 声明, 任务级) 从 metrics.position_sizing.task_cap
    # 提取（引擎装配段 min 链单源），两参都传才生成）
    response = {"strategy_id": strategy_id, "symbol": symbol,
                "timeframe": timeframe,
                "metrics": metrics, "bars_used": bars_used,
                "warmup_bars": actual_warmup,
                "hypothesis_id": hypothesis_id, "record_id": record_id,
                "sandbox": True,
                "note": "沙盒结果不构成验证证据；关联假设的区间已计入判定并集"}
    if leverage is not None or margin_cap is not None:
        task_params: Dict[str, Any] = {}
        if leverage is not None:
            task_params["leverage"] = leverage
        if margin_cap is not None:
            task_params["margin_cap"] = margin_cap
        task_cap_face = (metrics.get("position_sizing") or {}).get(
            "task_cap")
        if task_cap_face is not None:
            task_params["effective_leverage"] = task_cap_face[
                "effective_leverage"]
        response["task_params"] = task_params
    # 熔断三态回显（D3①：halt_drawdown_pct 传入时写 halt_info——
    # 未触发 {halted: false}／触发附 halted_at/halt_reason；未配置
    # 无键。halted 记录不构成验证证据（证据地位待验证协议侧裁决））
    if halt_drawdown_pct is not None:
        response["halt_info"] = metrics.get("halt_info") or {
            "halted": False}
    return response


def load_data_bounds(
    data_dir: Path, symbol: str, timeframe: str,
) -> tuple:
    """数据范围边界（workbench 审查 CARD-02 提取）：[首 bar, 尾 bar+1×TF)
    半开——原 query_union_balance 内联段公开化（workbench 请求内
    bounds_cache 复用，消逐假设重复装载）；含原 raise 语义（无 parquet/
    不可装载=RESEARCH_SYMBOL_NOT_FOUND）。

    find_parquet 定位+首尾 bar 完整时间戳直读（不走 HTTP
    available-sources——其时间边界日级 [:10] 截断精度不足）。
    """
    from core.data.mtf_loader import find_parquet, safe_symbol
    from core.data.storage import load_parquet
    from core.data.timeframes import TF_SECONDS

    path = find_parquet(data_dir, safe_symbol(symbol), timeframe)
    if path is None:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"无数据: {symbol} {timeframe}",
            detail={"symbol": symbol, "timeframe": timeframe})
    ts = load_parquet(path, columns=["timestamp"])
    if ts is None or len(ts) == 0:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"数据不可装载: {symbol} {timeframe}",
            detail={"symbol": symbol, "timeframe": timeframe})
    data_start, data_end = ts.index[0], ts.index[-1]
    # 数据范围半开 [首 bar, 尾 bar+1×TF)——尾 bar 自身占一个完整周期
    span_end = data_end + pd.Timedelta(seconds=TF_SECONDS[timeframe])
    return data_start, span_end


def query_union_balance(
    db_path: Path, data_dir: Path, *,
    symbol: str, timeframe: str, hypothesis_id: str,
    data_bounds: Optional[tuple] = None,
) -> Dict[str, Any]:
    """判定并集余量只读查询（0.4.1 R4）——账本全貌/剩余区间/保守折算。

    前置判定 ``get_hypothesis`` None → 404（judged_ranges_union 对不存在
    假设静默返回空——查询方必须自判，不得以空清单伪装成功）。账本=
    ``judged_ranges_union`` 直调（单一事实源，禁第二套汇总）。数据范围=
    ``load_data_bounds``（无 data_bounds 入参时现场装载）；范围=
    ``[首 bar, 尾 bar+1×TF)`` 半开与 judged 对齐。remaining=
    数据范围−judged（先合并重叠/相邻再逐段差）；remaining_bars=Σ 每段
    ``floor(段时长/TF_SECONDS)``（不足整 bar 残余丢弃——保守计数）。

    data_bounds 可选（workbench 审查 CARD-02）：调用方预装载的
    (data_start, span_end) 直传——请求内缓存复用，缺省 None 现场装载
    （零破坏）。零副作用：不落日志不写库；GET 语义天然幂等。两出口
    一致锚：查询 remaining 段发起隔离验证不被 RESEARCH_RANGE_OVERLAP
    拒（同一账本装配）。
    """
    from core.data.timeframes import TF_SECONDS

    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError(
            "RESEARCH_HYPOTHESIS_NOT_FOUND",
            f"假设不存在: {hypothesis_id}",
            status=404, detail={"hypothesis_id": hypothesis_id})
    judged = judged_ranges_union(db_path, hypothesis_id)

    if data_bounds is not None:
        data_start, span_end = data_bounds
    else:
        data_start, span_end = load_data_bounds(data_dir, symbol, timeframe)
    tf_seconds = TF_SECONDS[timeframe]

    def _parse(s: str) -> pd.Timestamp:
        # to_utc 恒返 aware（naive 补 UTC / aware 转 UTC）——原
        # tz_localize 分支恒假，收敛单行
        return to_utc(pd.Timestamp(s))

    # judged 并集（clamp 到数据范围后合并重叠/相邻，再逐段差集）
    segs = sorted(
        (max(_parse(r["start"]), data_start),
         min(_parse(r["end"]), span_end))
        for r in judged)
    merged: list = []
    for s, e in segs:
        if e <= s:
            continue  # 与数据范围无交（clamp 后空段）
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    remaining: list = []
    cursor = data_start
    for s, e in merged:
        if s > cursor:
            remaining.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < span_end:
        remaining.append((cursor, span_end))

    remaining_bars = sum(
        int((e - s).total_seconds() // tf_seconds)
        for s, e in remaining)
    return {
        "hypothesis_id": hypothesis_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "judged": judged,
        "remaining": [
            {"start": s.isoformat(), "end": e.isoformat()}
            for s, e in remaining],
        "remaining_bars": remaining_bars,
    }
