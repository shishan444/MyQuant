"""假设 → 策略转化器（add-ai-research-mcp 6.4 / improve-mcp-agent-loop M4）。

草稿-转正两段生命周期（信任链不跳级保留在转正门）：
- translate 产 **draft**（不要求 pass 前置）——draft 即隔离验证对象
  （验证的就是将来出货的确切 DNA）；仅 active 假设可转化（终态拒绝）；
- WHEN 条件 → 入场信号（ENTRY_TRIGGER），THEN 条件按"预期兑现即离场"
  语义 → 出场信号（EXIT_TRIGGER）——两者同规则，不可表达均显式拒绝；
- 转正（draft→active）只经 transition(supported) 的原子路径，不经
  本转化器与 PUT（受保护字段锁死）；
- 幂等：谱系中 draft 产物存在即返回；同签名既有策略显式分流
  （active→same_dna_existing / draft→幂等），MUST NOT 静默不落库。

其他翻译纪律不变：conditions JSON + 策略层参数声明块为翻译源
（0.4.2 起「conditions 为唯一翻译源」显式扩展——声明块映射产物
risk_genes，NULL/空=平台默认逐位兼容）；可表达子集与拒绝原因
（跨币种/基础列/插件列/分布形态）；谱系回链 parent_ids。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.analysis.errors import ResearchError, load_stored_json  # 轻量定义点直取（errors.py 唯一权威）
from core.persistence.repository import research as rr

# risk_genes 完整键集（15 键，D2/DC-9 谱系）：TD-14 起分流判定已由
# _dna_equivalent 全量基因型复核承担（risk_genes 含于 to_dict 比对集，
# 比对职责被吸收），本常量的运行时消费随之退役；保留供测试锚消费
# ——test_max_hold_declaration/test_time_exit/test_dynamic_position_
# sizing 以它断言 risk_genes 键集完整性（声明块映射无丢键）
_D2_RISK_FIELDS = (
    "stop_loss", "take_profit", "sl_mode", "position_size", "leverage",
    "exit_fraction", "add_fraction", "max_exposure", "atr_period",
    "max_hold", "time_exit_utc", "position_mode", "risk_fraction",
    "initial_fraction", "max_position_loss",
)

# 条件 action → SignalGene.condition 的映射（阈值/穿越类可表达）
_ACTION_MAP = {
    "gt": lambda t: {"type": "gt", "threshold": float(t)},
    "lt": lambda t: {"type": "lt", "threshold": float(t)},
    "ge": lambda t: {"type": "ge", "threshold": float(t)},
    "le": lambda t: {"type": "le", "threshold": float(t)},
    "cross_above": lambda t: {"type": "cross_above", "threshold": float(t)},
    "cross_below": lambda t: {"type": "cross_below", "threshold": float(t)},
    "breakout": lambda t: {"type": "cross_above", "threshold": float(t)},
    "breakdown": lambda t: {"type": "cross_below", "threshold": float(t)},
    # 价格对指标列比较（fix-research-c2 TC-3，用户裁决 C2 附带补）：
    # 类型直通映射——回测面消费先例 executor.py evaluate_condition 的
    # price_above/price_below 分支（close vs indicator_series）现成。
    # target 为占位不进产物（与统计面占位契约对齐）、threshold 不参与。
    "price_above": lambda t: {"type": "price_above"},
    "price_below": lambda t: {"type": "price_below"},
}

# target 占位直通动作集：这两键的比较主体固定 close，target（数值/
# 数值串/列名）一律占位忽略不报错——下方「target 须为数值」校验对
# 其豁免（统计面同款占位契约；与 C6 序列形正交——price_* 不消费
# target）。
_PASSTHROUGH_ACTIONS = frozenset({"price_above", "price_below"})

# 列名 target（编码指标列）→ 序列对照类型的映射（fix-research-c6，
# D-14 八键）：数值比较四族同名 type；穿越族与 breakout/breakdown
# 别名归一到 executor 既有序列类型（cross_above_series/
# cross_below_series）；不在表内的 action+列名 target 维持不可表达。
_SERIES_TYPE_MAP = {
    "gt": "gt", "lt": "lt", "ge": "ge", "le": "le",
    "cross_above": "cross_above_series",
    "cross_below": "cross_below_series",
    "breakout": "cross_above_series",
    "breakdown": "cross_below_series",
}

# 插件列条件 → formula/model 信号源的比较算子（仅数值比较可表达；
# cross/break 类穿越语义不适用于插件连续值源，显式拒绝——见
# _translate_conditions 的插件分支）
_PLUGIN_ACTION_OPERATOR = {
    "gt": ">", "lt": "<", "ge": ">=", "le": "<=",
}


def _plugin_signal_columns(db_path: Path) -> Dict[str, str]:
    """注册表插件列 → source 类型（formula|model）；与登记域同源
    （allowed_subject_columns 的插件部分即 list_signal_registry_entries
    only_visible——同一查询，不复制第二份列清单）。

    插件列名为完整精确名（fx_xxx / fx_xxx_v2），无 ``_数字`` 参数后缀。
    """
    return {
        e["column_name"]: e["source"]
        for e in rr.list_signal_registry_entries(db_path, only_visible=True)
    }


def _guard_condition_timeframe(
    group: str, conds: List[Dict[str, Any]], base_tf: str,
) -> None:
    """放行组（when/add_trigger）timeframe 三步校验链（单点，两组共用）。

    unblock-research-validation A 段四组三放行域：
    ①键值 ∈ TF_SECONDS（TIMEFRAME_UNKNOWN——与登记面错误码族对齐）；
    ②高于或等于基准（assert_higher_tf 既有 >= 语义零改动）；
    ③tf==基准 → 条件键归一剥离（与登记面 canonical 化剥离同语义——
    主库明文「显式写基准周期与缺省语义完全相同（同指纹同求值）」，
    不写入基因=翻译面归一）；tf 严格高于基准 → 键保留在条件 dict，
    由 _translate_conditions 提取写入基因 timeframe 字段（执行链
    高周期轴求值+掩码投影）。
    翻译面校验为纵深防御（对绕过登记的库内/手工数据）——权威防线
    在登记面（TIMEFRAME_UNKNOWN/TIMEFRAME_NOT_HIGHER+canonical 化
    归一齐备）。
    """
    from core.data.timeframes import TF_SECONDS
    from core.data.mtf_alignment import assert_higher_tf

    for i, c in enumerate(conds):
        # null 值键=无标记（与登记面 c.get("timeframe") is None 同语义
        # ——显式 null 不视作标记，键保留但跳过校验）
        if not isinstance(c, dict) or c.get("timeframe") is None:
            continue
        tf_key = c["timeframe"]
        field_name = f"{group}_conditions[{i}].timeframe"
        if tf_key not in TF_SECONDS:
            raise ResearchError(
                "TIMEFRAME_UNKNOWN",
                f"未知周期: {tf_key!r}",
                detail={"field": field_name, "timeframe": tf_key,
                        "legal_range": sorted(TF_SECONDS)})
        try:
            assert_higher_tf(tf_key, base_tf)
        except ValueError as exc:
            raise ResearchError(
                "TIMEFRAME_NOT_HIGHER", str(exc),
                detail={"field": field_name, "timeframe": tf_key,
                        "base_timeframe": base_tf}) from exc
        if tf_key == base_tf:
            # 归一：显式基准=缺省语义（主库明文）——剥离键不写入基因
            c.pop("timeframe")


def _reject_condition_timeframe(group: str, conds: List[Dict[str, Any]]) -> None:
    """拒绝组（then/exit_partial/add_suppress）timeframe 键拒绝（单点）。"""
    for i, c in enumerate(conds):
        if isinstance(c, dict) and c.get("timeframe") is not None:
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"{group}_conditions[{i}] 携带 timeframe 键——"
                "THEN 条件为前向收益对照语义（forward_bars 以基准 bar "
                "计），跨周期前向窗口归属未定义（仅 when 与加仓触发组"
                "支持 timeframe 键）",
                detail={
                    "field": f"{group}_conditions[{i}].timeframe",
                    "timeframe": c.get("timeframe"),
                    "slow_path": "多周期标记仅进场侧（when 与加仓触发）"
                                 "支持；出场/减仓/抑制组请以基准周期条"
                                 "件表达"})


def build_signal_genes(
    when_conditions: List[Dict[str, Any]],
    then_conditions: List[Dict[str, Any]],
    db_path: Path,
    strategy_timeframe: str = "4h",
) -> tuple[List, List[Dict[str, Any]]]:
    """不落库纯翻译入口（0.4.1 S4）——条件组 → (内置基因, 插件源)。

    timeframe 防线与翻译能力同点权威单源（unblock-research-validation
    A 段四组三放行域改造）：when 组放行（_guard_condition_timeframe
    三步校验+归一）、then 组维持拒绝（_reject_condition_timeframe）。
    translate_hypothesis（落库产 draft）与 exit_sweep（构造内存 DNA
    不落库）为两消费方——禁止第二套条件→信号基因逻辑。

    不落库、不查假设、不产 draft；THEN 空=空基因列表放行（离场全靠
    risk_genes——translate_hypothesis 既有先例）；插件列条件自然
    透传（不被新拒绝面拦——timeframe 校验经条件顶层键不触碰插件源）。
    """
    from core.strategy.dna import SignalRole

    # 四组三放行域（unblock-research-validation）：when 放行（校验链
    # +tf==base 归一），then 拒绝；add_trigger/exit_partial/add_suppress
    # 在 translate_hypothesis 分批组循环处置（S4 入口只承 when/then）
    _guard_condition_timeframe("when", when_conditions, strategy_timeframe)
    _reject_condition_timeframe("then", then_conditions)

    # THEN 按"预期兑现即离场"语义翻译为出场信号（与 WHEN 同规则拒绝；
    # 插件列出场源当前不可表达——组装器只组合入场，出场插件源会被
    # 静默丢弃，故显式拒绝而非误译为入场门控）
    when_genes, when_sources = _translate_conditions(
        when_conditions, db_path=db_path,
        label="when_conditions", role=SignalRole.ENTRY_TRIGGER,
        base_tf=strategy_timeframe)
    then_genes, then_sources = _translate_conditions(
        then_conditions, db_path=db_path,
        label="then_conditions", role=SignalRole.EXIT_TRIGGER,
        base_tf=strategy_timeframe)
    return when_genes + then_genes, when_sources + then_sources


def _declared_risk_genes(declared: Optional[Dict[str, Any]]):
    """声明块 → RiskGenes（八键映射；None/空 → 平台默认）。

    0.4.2 R1 五参数 + R3 分批三键（组存在时登记路径已归一化字面化——
    恒具体值；None 键不写入=手工 DNA 缺省态语义）。值域已在登记 3.6
    步校验，此处只做映射（字面值直写）。
    """
    from core.strategy.dna import RiskGenes

    if not declared:
        return RiskGenes()  # platform_default
    keys = ("stop_loss", "take_profit", "sl_mode", "position_size",
            "leverage", "exit_fraction", "add_fraction", "max_exposure",
            "max_hold", "time_exit_utc", "position_mode", "risk_fraction",
            "initial_fraction", "max_position_loss")
    # null 值键跳过不写入（review-042 CARD-01 纵深）：翻译侧永不写 None
    # ——存量库手工 dna_json 或绕过登记链写入的 null 键按缺省态处理
    # （=手工 DNA 缺省语义），不穿透进 RiskGenes
    return RiskGenes(
        **{k: declared[k] for k in keys
           if k in declared and declared[k] is not None})


def _run_formula_trial(
    db_path: Path, formula_refs, rng, data_dir,
) -> int:
    """公式组件试算核心（任务 8/9 共用单点——三命中点补试算同源）。

    在 rng 区间最小装载（warmup 按组件 lookback）逐组件求值
    （enrich_with_plugin_columns 单元复用）；成功返回 evaluated_bars，
    失败抛 ResearchError（调用方决定拒绝或降级语义）。
    """
    from core.analysis.plugin_service import (
        enrich_with_plugin_columns, plugin_columns_lookback)
    from core.data.mtf_loader import find_parquet, safe_symbol
    from core.data.storage import load_parquet
    import pandas as pd

    path = find_parquet(Path(data_dir), safe_symbol(rng["symbol"]),
                        rng["timeframe"])
    if path is None:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"公式组件试算数据不可达: {rng['symbol']} {rng['timeframe']}",
            detail={"data_range": rng, "reason": "no_parquet"})
    df = load_parquet(path)
    if df is None or len(df) == 0:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"公式组件试算数据不可装载: {rng['symbol']} {rng['timeframe']}",
            detail={"data_range": rng, "reason": "empty_parquet"})
    warmup = plugin_columns_lookback(db_path, set(formula_refs))
    need_rows = min(len(df), max(warmup + 8, 32))
    head = df.iloc[:need_rows].copy()
    try:
        enriched = enrich_with_plugin_columns(
            db_path, head, set(formula_refs))
    except ResearchError:
        raise
    except Exception as exc:
        raise ResearchError(
            "RESEARCH_CONDITION_INVALID",
            f"公式组件试算失败: {sorted(formula_refs)}",
            detail={"columns": sorted(formula_refs),
                    "error": str(exc)[:200]}) from exc
    # 逐组件列在场且非全 NaN（evaluated_bars>0 语义）
    for col in formula_refs:
        series = enriched[col] if col in getattr(enriched, "columns", []) \
            else None
        evaluated = (int(series.notna().sum())
                     if series is not None else 0)
        if evaluated <= 0:
            raise ResearchError(
                "RESEARCH_CONDITION_INVALID",
                f"公式组件试算零产出: {col}",
                detail={"column": col, "reason": "all_nan"})
    return need_rows


def _write_component_eval(db_path, strategy_id, formula_refs, rng, bars):
    """component_eval 记录写入（任务 8/9 共用单点；逐组件一条）。

    记录形制按契约（tasks 任务 8）：每组件一条，summary 单键
    {"column": c, "source": "formula", "evaluated_bars": int}，
    data_ranges=json([试算区间]) 列表形（design 明文）——研究
    日志是 MCP 可查面，键名为契约（此前 columns 列表汇总一条为
    实现偏差，门禁读侧同步改 column 单键精确相等）。
    """
    from core.persistence.repository import research as rr
    for column in sorted(formula_refs):
        rr.append_research_log(
            db_path, hypothesis_id=None,
            entry_type="component_eval",
            ref_id=strategy_id,
            data_ranges=json.dumps([rng], ensure_ascii=False),
            summary=json.dumps(
                {"column": column, "source": "formula",
                 "evaluated_bars": bars},
                ensure_ascii=False))


def _backfill_component_eval(
    db_path: Path, strategy_id: str, row, data_ranges, data_dir,
) -> None:
    """三命中点补试算（任务 9：谱系幂等/active 分流/draft 分流）。

    「翻译过=有记录」不变式覆盖存量：命中既有策略时，其 formula
    组件若无 component_eval 记录（试算上线前入库的存量）则补试算
    写记录（ref_id=命中策略 id）。失败语义三处一致：不阻断返回 +
    logger.warning + 不写（门禁兜底拦截）。
    """
    try:
        from core.persistence.repository import research as rr
        # 精确判定：该策略 id 已有 component_eval 记录则跳过
        # （entry_type 过滤缩小扫描面 + iter_all_pages 分页取全后按
        # ref_id 精确匹配——与门禁读侧同查询形制；不取全则日志超量
        # 时 asc 尾部恒不含新 entry_type，幂等失效导致三命中点重复
        # 试算+重写）
        from core.analysis.isolation import iter_all_pages
        existing = [
            e for e in iter_all_pages(
                lambda off, lim: rr.list_research_log(
                    db_path, entry_type="component_eval",
                    offset=off, limit=lim))]
        if any(e.get("ref_id") == strategy_id for e in existing):
            return
        from core.strategy.dna import StrategyDNA
        dna_obj = StrategyDNA.from_json(row.get("dna_json") or "{}")
        refs = [s["ref"] for s in (dna_obj.signal_sources or [])
                if s.get("type") == "formula" and s.get("ref")]
        if not refs:
            return
        rng = next((r for r in (data_ranges or [])
                    if isinstance(r, dict) and r.get("symbol")
                    and r.get("timeframe")), None)
        if rng is None or data_dir is None:
            return  # 无区间可试——门禁兜底
        bars = _run_formula_trial(db_path, refs, rng, data_dir)
        _write_component_eval(db_path, strategy_id, refs, rng, bars)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "补试算失败（不阻断返回，门禁兜底）: strategy=%s",
            strategy_id, exc_info=True)


def _component_precheck(
    db_path: Path, dna, data_ranges, plugin_sources, data_dir,
) -> None:
    """组件装载试算（unblock-research-validation B 段任务 8）。

    仅含 formula 组件产物执行（model 组件不试算不写——walk-forward
    训练窗自理，门禁 model 线维持拦截为已知行为边界）。逐组件在
    data_ranges 首区间最小装载试算（_run_formula_trial 共用单点）：
    成功 → _write_component_eval（门禁双源判据 (a) 源的记录供给，
    「翻译过=有记录」不变式）；失败 → 翻译整体拒绝（不可求值的
    draft 是死策略）。
    """
    formula_refs = [s["ref"] for s in (plugin_sources or [])
                    if s.get("type") == "formula" and s.get("ref")]
    if not formula_refs:
        return
    rng = next((r for r in (data_ranges or [])
                if isinstance(r, dict) and r.get("symbol")
                and r.get("timeframe")), None)
    if rng is None or data_dir is None:
        raise ResearchError(
            "RESEARCH_CONDITION_INVALID",
            "公式组件试算不可执行（区间或数据目录不可得）",
            detail={"data_range": rng, "reason":
                    "no_valid_range" if rng is None else "data_dir_unavailable"})
    bars = _run_formula_trial(db_path, formula_refs, rng, data_dir)
    _write_component_eval(
        db_path, str(getattr(dna, "strategy_id", None) or ""),
        formula_refs, rng, bars)


def translate_hypothesis(
    db_path: Path,
    *,
    hypothesis_id: str,
    strategy_symbol: str = "BTCUSDT",
    strategy_timeframe: str = "4h",
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """把 active 假设转化为**草稿策略**（draft，验证对象；不要求 pass 前置）。

    risk_genes 映射（0.4.2 R1）：假设声明块（strategy_params 列）非空
    → 逐参数写入产物 risk_genes（"declared"）；NULL/空 → RiskGenes()
    平台默认（"platform_default"，现行为逐位兼容）。同签名分流增强：
    签名命中后还须九字段策略层参数全等才分流（参数异必建新实体）。

    Returns:
        {"strategy_id", "status": "draft", "duplicated": bool,
         "same_dna_existing": bool, "genes": [...],
         "risk_genes": "platform_default" | "declared",
         "direction": "long"|"short", "max_hold": int|null,
         "reuse_note": str|null}（身份三键规则②：方向/最长持仓/
         复用说明——same_dna_existing=true 时 reuse_note 为固定三
         要素文案）。
    """
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND",
                            f"假设不存在: {hypothesis_id}", status=404)

    # 仅 active 假设可转化（终态假设不产生新研究产物）
    if hyp["status"] != "active":
        raise ResearchError(
            "RESEARCH_HYPOTHESIS_TERMINAL",
            f"假设已终态（{hyp['status']}），拒绝转化；请登记新假设"
            "（可在新假设中引用旧假设）", status=409)

    # 声明块读入（0.4.2 R1）：NULL → None（平台默认路径逐位兼容）
    raw_params = hyp.get("strategy_params")
    try:
        declared_params = json.loads(raw_params) if raw_params else None
    except (json.JSONDecodeError, TypeError):
        declared_params = None
    risk_origin = "declared" if declared_params else "platform_default"

    # 幂等：谱系中 draft 产物存在即返回（转正后无 draft，可对新假设重译）
    existing_draft = _find_draft_by_parent(db_path, hypothesis_id)
    if existing_draft is not None:
        # 三命中点补试算①（谱系幂等）：存量 draft 组件无记录则补
        # （本分支在 data_ranges 解析前——就地解析补试算所需区间）
        from core.persistence.repository.strategy import get_strategy
        try:
            hit_ranges = load_stored_json(
                hyp["data_ranges"], source="research_hypotheses.data_ranges",
                detail={"hypothesis_id": hypothesis_id})
        except (ResearchError, TypeError, KeyError):
            hit_ranges = []
        _backfill_component_eval(
            db_path, existing_draft,
            get_strategy(db_path, existing_draft) or {},
            hit_ranges, data_dir)
        return {"strategy_id": existing_draft, "status": "draft",
                "duplicated": True, "same_dna_existing": False,
                "risk_genes": risk_origin,
                # 身份回读（规则②）：该分支只有产物 id 无行数据——
                # direction/max_hold 按收敛缺省（S2f 锚）
                **_translate_receipt(
                    "long", None, None, "fixed", None, False)}

    # 库内 JSON 解析兜底（CARD-02）：conditions/data_ranges 由登记侧
    # 序列化，损坏时结构化拒绝点名假设，不裸炸 500
    conditions = load_stored_json(
        hyp["conditions"], source="research_hypotheses.conditions",
        detail={"hypothesis_id": hypothesis_id})
    data_ranges = load_stored_json(
        hyp["data_ranges"], source="research_hypotheses.data_ranges",
        detail={"hypothesis_id": hypothesis_id})

    # 跨币种拒绝
    symbols = {r.get("symbol") for r in data_ranges if isinstance(r, dict)}
    if len(symbols) > 1:
        raise ResearchError(
            "RESEARCH_NOT_EXPRESSIBLE",
            f"跨币种假设不可表达（WHEN/THEN 分属 {sorted(symbols)}）——"
            "策略表达为单品种信号源",
            detail={"symbols": sorted(symbols),
                    "slow_path": "提交 feature-requests 描述跨品种需求",
                    "note": "此为有意设计边界（单品种信号源），非待修复缺陷"})

    # 多周期防线与双组翻译经 S4 公共入口（0.4.1）：防线循环原形在
    # build_signal_genes 内（与翻译能力同点权威单源）；跨币种防线
    # 基于多区间 data_ranges 不适用单区间请求，留在本函数。
    # unblock-research-validation：when 组放行（三步校验+归一）。
    genes, plugin_sources = build_signal_genes(
        conditions.get("when_conditions", []),
        conditions.get("then_conditions", []), db_path,
        strategy_timeframe=strategy_timeframe)

    # 分批三组翻译（0.4.2 R3，scaled-position-actions）：组内条目与
    # when/then 同构同拒（插件列 subject 在新组=不可表达——组装器只
    # 组合入场，插件源出场/加仓会被静默丢弃故显式拒绝）；组基因自然
    # 进 gene_signature（信号基因全集参与签名计算）
    # unblock-research-validation 四组三放行域（Q1 裁决=加仓组跟随
    # when 侧打通）：add_trigger 放行（校验链单点共用）、exit_partial/
    # add_suppress 维持拒绝——分批三组全部纳入防线（静默穿透洞封闭）
    from core.strategy.dna import SignalRole
    _SCALING_ROLE_MAP = (
        ("exit_partial_conditions", SignalRole.REDUCE_TRIGGER),
        ("add_trigger_conditions", SignalRole.ADD_TRIGGER),
        ("add_suppress_conditions", SignalRole.ADD_INHIBIT),
    )
    # 已知边界披露：adds 侧无 trigger_policy 机制（边沿加仓无表达，
    # 替代=基准周期条件）
    for group_key, group_role in _SCALING_ROLE_MAP:
        group_conditions = conditions.get(group_key)
        if not group_conditions:
            continue
        # field 定位键保留下划线完整键形（与登记面同位 field 及
        # _translate_conditions 插件 tf 拒绝的 field 同形制——结构化
        # 键不做自然语言化）
        group_short = group_key.rsplit("_conditions", 1)[0]
        if group_key == "add_trigger_conditions":
            _guard_condition_timeframe(
                "add_trigger", group_conditions, strategy_timeframe)
        else:
            _reject_condition_timeframe(group_short, group_conditions)
        g_genes, g_sources = _translate_conditions(
            group_conditions, db_path=db_path, base_tf=strategy_timeframe,
            label=group_key, role=group_role)
        if g_sources:  # 结构上不可达（插件列在非 ENTRY 角色已被拒），
            raise ResearchError(   # 防御性收口——不静默丢弃插件源
                "RESEARCH_NOT_EXPRESSIBLE",
                f"{group_key} 插件列条件不可表达（插件源仅入场）",
                detail={"field": group_key})
        genes = genes + g_genes

    # 组装 DNA（v2）：纯内置条件 = 退化形态零迁移（单 rule 源）；
    # 含插件条件 = rule 源（承载全部内置基因，有内置条件时）+ 各插件源，
    # composition 固定 logic AND（假设条件本就是合取清单，翻译器不发明
    # 优先级）；纯插件条件（无内置基因）不设 rule 源——组装器以插件源为
    # 全部条件（无规则源 = AND 单位元基线，见 signal_assembly._combine）
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA)

    if plugin_sources:
        signal_sources = ([{"type": "rule"}] if genes else []) + plugin_sources
        composition = {"mode": "logic", "op": "and"}
    else:
        signal_sources = [{"type": "rule"}]
        composition = None

    # 入场触发策略直接映射（trigger-policy 卡5 / Q3 裁决）：假设
    # conditions_json 顶层 trigger_policy 已是归一形（登记时
    # validate_policy 归一落库）；缺省/显式 none 不含键 → 不写字段 =
    # 既有策略形制零变化。不可静默降级：策略声明承载密度语义。
    entry_policy = conditions.get("trigger_policy")

    # 方向直通（add-direction-channel A3）：conditions 顶层 direction
    # （登记归一形——short 才写键）覆盖产物 risk_genes.direction；
    # 存量/long 假设无键 → or "long" 与 RiskGenes 默认同值（产物
    # dna_json 逐位不变）。方向唯一来源=假设声明（strategy_params
    # 白名单不扩 direction——无双源）；gene_signature 已编码 dir:
    # （dna.py），short 产物签名异于一切 long 策略，同签名分流不跨
    # 方向误命中；_D2_RISK_FIELDS 九键不扩（签名已编码方向，比对集
    # 管「签名未编码的策略层参数」，两机制正交）
    risk_genes = _declared_risk_genes(declared_params)  # 声明块映射（0.4.2 R1）
    risk_genes.direction = conditions.get("direction") or "long"

    dna = StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(
            symbol=strategy_symbol, timeframe=strategy_timeframe),
        risk_genes=risk_genes,
        parent_ids=[hypothesis_id],  # 谱系回链
        signal_sources=signal_sources,
        composition=composition,
        entry_trigger_policy=entry_policy,
    )

    # 同签名显式分流（MUST NOT 静默不落库）。签名用往返规范化后的对象算
    # ——构造态与库内（from_json）的 layers auto-wrap 形制不同，直接比对必失配。
    # TD-14 等价复核：签名命中后还须基因型全量等价（_dna_equivalent——
    # 两侧经 dna_json 往返规范化后逐键全等，排除过程字段四键）。
    # 九字段策略层参数比对被全量复核吸收（risk_genes 含于 to_dict）；
    # 签名与九字段都不覆盖的差异（condition.threshold、execution_genes
    # 的 symbol/timeframe）在复核面显形——变体与跨市场同名不复用。
    dna = StrategyDNA.from_json(dna.to_json())
    same_dna = _find_by_signature(db_path, dna.gene_signature)
    active_hit = next(
        (r for r in same_dna
         if r.get("status") != "draft"
         and _dna_equivalent(dna, r.get("dna_json") or "")),
        None)
    if active_hit is not None:
        # 来源标注统一用 risk_origin（declared_params 非空判据）——声明值
        # 恰等于平台默认时仍须标 declared（「有声明→declared」，review-042
        # CARD-03：按九字段与默认全等判会把同一声明标成 platform_default）
        _identity = _identity_reflection(active_hit)
        # 三命中点补试算②（active 分流）
        _backfill_component_eval(
            db_path, active_hit["strategy_id"], active_hit,
            data_ranges, data_dir)
        return {"strategy_id": active_hit["strategy_id"],
                "status": active_hit.get("status") or "active",
                "duplicated": False, "same_dna_existing": True,
                "risk_genes": risk_origin,
                **_translate_receipt(
                    _identity["direction"], _identity["max_hold"],
                    _identity["time_exit_utc"],
                    _identity["position_mode"],
                    _identity["risk_fraction"], True)}
    draft_hit = next(
        (r for r in same_dna
         if r.get("status") == "draft"
         and _dna_equivalent(dna, r.get("dna_json") or "")),
        None)
    if draft_hit is not None:
        _identity = _identity_reflection(draft_hit)
        # 三命中点补试算③（draft 分流）
        _backfill_component_eval(
            db_path, draft_hit["strategy_id"], draft_hit,
            data_ranges, data_dir)
        return {"strategy_id": draft_hit["strategy_id"], "status": "draft",
                "duplicated": True, "same_dna_existing": False,
                "risk_genes": risk_origin,
                **_translate_receipt(
                    _identity["direction"], _identity["max_hold"],
                    _identity["time_exit_utc"],
                    _identity["position_mode"],
                    _identity["risk_fraction"], False)}

    strategy_id = f"st_{uuid.uuid4().hex[:12]}"
    # 组件装载试算（unblock-research-validation B 段：DNA 组装后落库前，
    # 仅含 formula 组件产物执行）——成功写 component_eval 记录（门禁
    # 双源 (a) 源），失败整体拒绝；ref_id=产物策略 id（先定 id 再试算
    # 落库——试算拒绝时 id 不落库无副作用）
    dna.strategy_id = strategy_id
    _component_precheck(db_path, dna, data_ranges, plugin_sources, data_dir)
    from core.persistence.repository.strategy import save_strategy
    save_strategy(
        db_path, strategy_id=strategy_id,
        dna_json=dna.to_json(),
        symbol=strategy_symbol, timeframe=strategy_timeframe,
        name=f"假设转化 {hypothesis_id[:12]}",
        source="hypothesis", parent_ids=hypothesis_id,
        notes=f"translated from hypothesis {hypothesis_id} (conditions 为唯一翻译源)",
        status="draft",  # 草稿：验证对象，转正只经 transition 原子路径
    )

    # 研究日志落转化事件
    rr.append_research_log(
        db_path, hypothesis_id=hypothesis_id, entry_type="transition",
        ref_id=strategy_id,
        summary=f"translated -> draft strategy {strategy_id}")

    return {"strategy_id": strategy_id, "status": "draft",
            "duplicated": False, "same_dna_existing": False,
            "genes": [f"{g.indicator}({g.field_name})" for g in genes],
            "risk_genes": risk_origin,
            **_translate_receipt(
                risk_genes.direction, risk_genes.max_hold,
                risk_genes.time_exit_utc,
                risk_genes.position_mode, risk_genes.risk_fraction,
                False)}


def _translate_conditions(
    conditions: List[Dict[str, Any]], *, db_path: Path, label: str, role,
    base_tf: str = "",
) -> tuple[List, List[Dict[str, Any]]]:
    """条件 → (内置 SignalGene 列表, 插件信号源列表)。

    插件列条件不进入 rule 基因（signal_genes 只收内置条件），按条件
    阈值翻译为 formula/model 源；不可表达即拒（WHEN/THEN 同规则）。
    """
    from core.strategy.dna import SignalGene, SignalRole
    from core.analysis.column_ref import BASE_SUBJECT_COLUMNS

    genes = []
    plugin_sources: List[Dict[str, Any]] = []
    plugin_columns = _plugin_signal_columns(db_path)
    for i, cond in enumerate(conditions):
        subject = cond.get("subject", "")
        action = cond.get("action", "")
        target = cond.get("target")
        # 基因级周期提取（unblock-research-validation）：放行组条件顶层
        # timeframe 键经防线校验后写入基因第六属性（tf==base 已在防线
        # 归一剥离——到这里的非 None 值恒为严格高于基准）；基因 condition
        # dict 为重建产物不含该键（键剥离语义天然成立）
        cond_tf = cond.get("timeframe")

        # 结构化指标引用（0.2.13）：ColumnDef 直映射 SignalGene——
        # indicator/params 原样传递（多参指标天然支持），field 传语义名
        # （N4：executor 侧 resolve 的 field 值域是语义名 upper/signal/k…，
        # 编码前缀形在多输出指标非默认 field 会静默兜底取错列）
        if isinstance(subject, dict):
            from core.analysis.column_ref import ColumnRefError, resolve_column_ref
            try:
                # base_tf/ref_tf 供给（任务 7 三面之二 + D-6②——翻译面
                # 域校验与登记面同源；条件带 timeframe 时该 tf 为求值轴）
                cdef = resolve_column_ref(
                    subject, db_path, base_tf=base_tf or None,
                    ref_tf=cond_tf)
            except ColumnRefError as exc:
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{label}[{i}] subject 非法: {exc.message}",
                    detail={"condition": cond, "code": exc.code,
                            **exc.detail}) from exc
            if action not in _ACTION_MAP:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] action 不可表达: {action}"
                    f"（可表达: {sorted(_ACTION_MAP)}）",
                    detail={"condition": cond})
            # fix-research-c6：target 分类复用 _classify_target_ref 单点
            # ——数值/数值串→threshold 形（现状逐字节不变）；编码列名
            # →序列形（target_* 三键，无 threshold）；基础列/unknown/
            # 非法形→维持既有拒绝边界。price_above/price_below 豁免
            # （TC-3：target 一律占位忽略不报错——与统计面占位契约对齐）
            from core.analysis.condition_kernel import _classify_target_ref
            t_kind, t_ref = _classify_target_ref(target)
            if action in _PASSTHROUGH_ACTIONS:
                condition = _ACTION_MAP[action](target)
            elif t_kind in ("non_str", "numeric"):
                if (not isinstance(target, (int, float, str))
                        or isinstance(target, bool)):
                    # bool 排除与插件分支对称（review-042 CARD-14）：
                    # True/False 是布尔断言非阈值；list/None 等非法形
                    # 维持既有拒绝
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] target 须为数值（列名 target 仅"
                        "编码指标列可表达）",
                        detail={"condition": cond})
                condition = _ACTION_MAP[action](target)
            elif t_kind == "decoded":
                series_type = _SERIES_TYPE_MAP.get(action)
                if series_type is None:
                    # 当前不可达（_ACTION_MAP−passthrough=恰 8 键=本表
                    # 键集）——保留为 _ACTION_MAP 未来扩键的防御纵深
                    # （扩键未同步本表时显式拒绝优于静默漏翻译）
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] action 不可表达列名 target: {action}"
                        f"（列名 target 可表达: {sorted(_SERIES_TYPE_MAP)}）",
                        detail={"condition": cond})
                # F3：直用 _classify 的 payload（其返回即 try_decode
                # 产物，含 indicator/params/field——消同 target 二次解码）
                condition = {
                    "type": series_type,
                    "target_indicator": t_ref["indicator"],
                    "target_params": dict(t_ref["params"]),
                    "target_field": t_ref["field"],
                }
            else:
                # base_column（基础列无指标结构化形）与 unknown：
                # 「真正不可表达」边界保留（D-14 范围外）
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] target 须为数值或编码指标列名"
                    f"（{t_kind} 形不可表达）",
                    detail={"condition": cond})
            genes.append(SignalGene(
                indicator=cdef.indicator,
                params=dict(cdef.params),
                role=role,
                field_name=cdef.field,
                condition=condition,
                timeframe=cond_tf,
            ))
            continue

        # 插件列（注册表精确名）：翻译为 formula/model 信号源
        if subject in plugin_columns:
            if cond_tf is not None:
                # 插件源无 timeframe 槽位（signal_sources 结构不带周期
                # 维）——放行组内携带即拒（与组别拒绝同族显式面）
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列条件暂不支持 "
                    "timeframe 键（插件信号源无周期槽位）",
                    detail={"field": f"{label}[{i}].timeframe",
                            "timeframe": cond_tf,
                            "condition": cond})
            source_type = plugin_columns[subject]
            if role != SignalRole.ENTRY_TRIGGER:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列（{subject}）仅支持"
                    "入场信号源（THEN 与分批组须为内置指标列；出场/"
                    "加仓组合待后续版本）",
                    detail={"condition": cond,
                            "slow_path": "改用内置指标列 subject，"
                            "或提交 feature-requests 描述插件出场/"
                            "加仓需求"})
            operator = _PLUGIN_ACTION_OPERATOR.get(action)
            if operator is None:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列暂不支持该 action"
                    f"（需数值比较: {sorted(_PLUGIN_ACTION_OPERATOR)}）",
                    detail={"condition": cond,
                            "slow_path": "插件列条件改用数值比较 action"
                            "（gt/lt/ge/le）"})
            if not isinstance(target, (int, float)) or isinstance(target, bool):
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列条件 target 须为数值",
                    detail={"condition": cond})
            plugin_sources.append({
                "type": source_type,
                "ref": subject,
                "threshold": float(target),
                "operator": operator,
                # 角色标记（recognize-plugin-source-dna T1）：翻译链产出的
                # 插件源显式写 entry；读取侧对无 role 键存量条目同按 entry
                # 解释（缺省=entry 全链契约，存量 11 行零迁移）。
                # "exit" 值域前向声明（翻译器拒绝插件出场——无生产者），
                # 无消费逻辑，语义留未来。
                "role": "entry",
            })
            continue

        # 价格主体表达力（变更一 B1）：close 基础列 subject × lt/gt ×
        # 编码列名 target——target 解码为结构化引用（复用 C6 的
        # _classify_target_ref 单点），以解码结果为基因主体产出既有
        # 价格形态（lt→price_below / gt→price_above：收盘价对该指标
        # 序列的严格比较，executor 既有求值分支零改动）。语义恒等式：
        # close lt X ≡ price_below(X 基因) ≡ close < X。对 role 无特判
        # （WHEN/THEN/分批三组同规则）；指标挂基因主体（非 condition 内
        # target_* 键），warmup 经 _dna_indicator_names 主路径天然收集。
        # ge/le 含等号无对应严格形态——显式拒绝（禁静默近似映射
        # price_above，错误语义完整性优先于表面覆盖，完整四条理由见
        # openspec 变更一 design.md 第 3 节）。
        # hour_utc 放行域（add-session-structure-expression 任务 7，
        # Q2=基础列基因通道终稿）：hour_utc × gt/lt/ge/le × 数值
        # target（[0,24) 小数域阈值——U7b 值形制）→ 基因承载
        # （indicator="hour_utc"/params={}/field=None——执行链直读
        # 白名单）；其余 action（cross/price 族）拒绝——小时列无穿越
        # 语义闭环必要（周期回绕比较需取模，当前以 gt/lt 组合可表达）
        if subject == "hour_utc":
            if action in ("gt", "lt", "ge", "le") \
                    and isinstance(target, (int, float)) \
                    and not isinstance(target, bool):
                # target 值域硬校验（security F-1）：[0,24) 小时域
                # （U7b 值形制——开盘 UTC 小时+分钟/60，23:59 → 23.9833）
                # ——域外阈值（如 25 的 lt）比较恒 False 无语义，软放行
                # 会产出永不/恒触发的基因；落地兜底文案自述的 [0,24) 域
                if not (0 <= target < 24):
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] 不可表达: hour_utc target {target}"
                        " 超出 [0,24) 小时域（UTC 开盘时刻小数）",
                        detail={"condition": cond,
                                "slow_path": "target 取 [0,24) 小数"
                                             "（bar 开盘 UTC 小时"
                                             "+分钟/60）"})
                # 引用周期交互校验（D-6② 翻译面防线——登记面为权威，
                # 此处拦库内/手工路径）：hour_utc 求值轴（条件 tf 优先，
                # 无修饰回退基准）须日内，日线及以上值恒定无意义
                from core.analysis.column_ref import is_intraday_timeframe
                _eff_tf = cond_tf or (base_tf or None)
                if _eff_tf and not is_intraday_timeframe(_eff_tf):
                    raise ResearchError(
                        "RESEARCH_CONDITION_INVALID",
                        f"{label}[{i}] 不可表达: hour_utc 求值轴 "
                        f"{_eff_tf} 为日线及以上——每日一 bar 下小时列"
                        "值恒定无意义",
                        detail={"condition": cond, "ref_tf": _eff_tf,
                                "hint": "时刻列的求值轴必须是日内周期"
                                        "（12h 及以下），或去掉 timeframe"
                                        " 修饰在日内基准上求值"})
                genes.append(SignalGene(
                    indicator="hour_utc",
                    params={},
                    role=role,
                    field_name=None,
                    condition=_ACTION_MAP[action](target),
                    timeframe=cond_tf,
                ))
                continue
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"{label}[{i}] 不可表达: hour_utc 仅支持数值比较 "
                "gt/lt/ge/le × 数值 target（[0,24) 小时域）",
                detail={"condition": cond,
                        "slow_path": "cross/price 族对小时列不支持"
                                     "（周期回绕语义未定义）"})

        if subject == "close":
            from core.analysis.condition_kernel import _classify_target_ref
            t_kind, t_ref = _classify_target_ref(target)
            if t_kind == "decoded":
                if action in ("lt", "gt"):
                    genes.append(SignalGene(
                        indicator=t_ref["indicator"],
                        params=dict(t_ref["params"]),
                        role=role,
                        field_name=t_ref["field"],
                        condition={"type":
                                   "price_below" if action == "lt"
                                   else "price_above"},
                        timeframe=cond_tf,
                    ))
                    continue
                if action in ("ge", "le"):
                    raise ResearchError(
                        "RESEARCH_NOT_EXPRESSIBLE",
                        f"{label}[{i}] 不可表达: close 基础列 subject 支持"
                        " lt/gt 与编码列名 target 组合（价格与指标线的"
                        "严格比较）；ge/le 含等号形态暂不可表达",
                        detail={"condition": cond,
                                "slow_path": "改用 gt/lt 或提交 "
                                             "feature-requests"})
                # 其余动作（cross/breakout 族等）：穿下方既有基础列
                # 拒绝段——price_* 无穿越形态为真边界，文案逐字节不变
            elif action in ("lt", "gt"):
                # lt/gt 但 target 非 decoded（数值/基础列/未知串）：
                # close 主体仅支持编码列名 target——引导改写而非笼统拒绝
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: close 基础列主体现仅支持"
                    "编码列名 target——数值 target 组合不在本能力"
                    f"（target 分类: {t_kind}）",
                    detail={"condition": cond,
                            "slow_path": "target 改用编码指标列名"
                                         "（如 ema_20），或提交 "
                                         "feature-requests"})
            # close+ge/le+非 decoded 及其余组合：穿既有基础列拒绝段
            # （维持现状文案，与三段格局边界一致）

        # 字符串 subject（0.2.13 起）：参数化指标编码形已退役（入口关闭+
        # 存量迁移）——只可能是基础列 / 插件失效列 / 未知列，全部不可表达
        # （close 的 lt/gt+decoded 组合已在上方分支放行，不会落到这里）
        if subject in BASE_SUBJECT_COLUMNS:
            reason = (f"内置基础列 subject（{subject}）不可表达为"
                      "指标信号源（参数化指标请用结构化引用 "
                      "{indicator, params, field}）")
        elif subject.startswith(("fx_", "model_")):
            reason = (f"插件列 subject（{subject}）已失效"
                      "（信号注册表无此列——插件被删除或未注册）")
        else:
            reason = (f"字符串 subject（{subject}）不可表达——参数化指标"
                      "统一结构化引用 {indicator, params, field}")
        raise ResearchError(
            "RESEARCH_NOT_EXPRESSIBLE",
            f"{label}[{i}] 不可表达: {reason}",
            detail={"condition": cond,
                    "slow_path": "提交 feature-requests 或改用结构化指标引用"})
    # WHEN 组至少一个可表达条件（无入场条件的策略无意义）；THEN 组空
    # = 无显式出场（出场基因可为零，不拒——此前空 then 被"无可表达条件"
    # 连坐拒绝，纯插件列假设的最小形态 when=[插件条件]/then=[] 需放行）
    if label == "when_conditions" and not genes and not plugin_sources:
        raise ResearchError("RESEARCH_NOT_EXPRESSIBLE", "无可表达条件")
    return genes, plugin_sources


def _find_draft_by_parent(db_path: Path, hypothesis_id: str) -> Optional[str]:
    """幂等查询：谱系 parent_ids 含该假设的 draft 产物。"""
    from core.persistence.repository.strategy import list_strategies

    rows = list_strategies(db_path, status="draft", limit=500)
    for row in rows:
        if row.get("parent_ids") and hypothesis_id in str(row["parent_ids"]):
            return row["strategy_id"]
    return None


def _find_by_signature(db_path: Path, gene_signature: str) -> List[Dict[str, Any]]:
    """同签名策略清单（含全部状态；translate 同签名分流用）。"""
    from core.persistence.repository.strategy import find_by_gene_signature

    return find_by_gene_signature(db_path, gene_signature)


# 等价复核排除集（TD-14）：strategy_id/parent_ids/generation/mutation_ops
# 是实例溯源信息而非基因型语义——比对含它们则一切复用判定恒假
# （每次翻译 parent_ids 都指向不同假设）
_EQUIV_EXCLUDE_FIELDS = frozenset(
    {"strategy_id", "parent_ids", "generation", "mutation_ops"})


def _dna_equivalent(new_dna, row_dna_json: str) -> bool:
    """同签名分流的基因型等价复核（TD-14 方向 b）。

    两侧均经 from_json→to_dict 往返规范化后逐键全等（构造态与库内
    auto-wrap 形制差异被吸收；显式 None 键与缺键经往返同为缺省态，
    不产生假差异），比对集覆盖 risk_genes 全键、execution_genes
    （symbol/timeframe）、信号基因 condition.threshold 等签名未编码
    的语义差异——签名同而复核不等 ⇒ 变体，穿透建新实体不复用。
    行侧 dna_json 缺失/畸形不可证 = 不等价（保守穿透，不复用）。

    空容器归一：to_dict 的「非 None 才写」会把显式空容器写出
    （translate 纯 rule 产物 signal_sources=[]）、缺省 None 不写——
    基因型语义上空信号源集=无插件源=缺省，比对前 []/{} 归一为
    缺键，不产生假差异。纯 rule 源形 [{"type":"rule"},…]（translate
    产物的显式缺省形，:522/:525 组装）与手工 DNA 缺省 None 同为
    「无插件源」——同归一；含 formula/model 源的列表是真实基因型
    差异，不归一。
    """
    from core.strategy.dna import StrategyDNA

    def _norm(v):
        if v in ([], {}):
            return None
        if (isinstance(v, list) and v and all(
                isinstance(s, dict) and s == {"type": "rule"}
                for s in v)):
            return None
        return v

    try:
        ours = StrategyDNA.from_json(new_dna.to_json()).to_dict()
        theirs = StrategyDNA.from_json(row_dna_json).to_dict()
    except (ValueError, TypeError, KeyError):
        return False
    keys = (set(ours) | set(theirs)) - _EQUIV_EXCLUDE_FIELDS
    return all(_norm(ours.get(k)) == _norm(theirs.get(k)) for k in keys)


def _risk_genes_of_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """策略行 dna_json → risk_genes dict（解析失败/缺段返回空 dict——
    _identity_reflection 身份回读消费：缺键取 None 走收敛缺省，不炸
    回执；非分流消费——分流判定已由 _dna_equivalent 全量复核承担）。"""
    try:
        dna = json.loads(row.get("dna_json") or "{}")
        rg = dna.get("risk_genes")
        return rg if isinstance(rg, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _identity_reflection(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """幂等/分流分支的身份回读（unify-tool-return-contract 规则②）。

    从库内策略行 dna_json 提取 direction/max_hold——direction 值域
    收敛（脏值/null 恒回显 "long"）；解析失败同收敛（缺行/畸形存量
    不炸回执）。position_mode/risk_fraction（complete-research-
    tooling 任务 4）：仓位模式属身份语义键（参数变体族——回执须
    可见）；值域收敛同款（脏值回显缺省）。"""
    rg = _risk_genes_of_row(row) if row else {}
    direction = rg.get("direction")
    max_hold = rg.get("max_hold")
    time_exit = rg.get("time_exit_utc")
    position_mode = rg.get("position_mode")
    risk_fraction = rg.get("risk_fraction")
    return {
        "direction": direction if direction in ("long", "short") else "long",
        # bool 排除（security F-5）：bool 是 int 子类，True 会穿透
        # isinstance(int) 回显为 1（time_exit_utc 即 0:01 合法时刻）——
        # 与 validator 侧 S-2 类型防护同族
        "max_hold": int(max_hold)
        if isinstance(max_hold, int) and not isinstance(max_hold, bool)
        else None,
        "time_exit_utc": int(time_exit)
        if isinstance(time_exit, int) and not isinstance(time_exit, bool)
        else None,
        "position_mode": position_mode
        if position_mode in ("fixed", "risk_budget") else "fixed",
        "risk_fraction": float(risk_fraction)
        if isinstance(risk_fraction, (int, float))
        and not isinstance(risk_fraction, bool) else None,
    }


# reuse_note 固定文案（规则②：same_dna_existing=true 时的复用三要素
# 披露——命中既有同基因策略实体/共享语义/验证挂载依赖；不新增
# duplicated_by_same_dna 近义键）
_REUSE_NOTE = (
    "命中既有同基因策略实体：该策略可被多假设共享复用；其验证记录"
    "挂载依赖调用方显式携带 hypothesis_id；执行周期由验证请求参数指定")


def _translate_receipt(direction: str, max_hold, time_exit,
                        position_mode, risk_fraction,
                        reuse: bool) -> Dict[str, Any]:
    """翻译回执身份六键（四返回点统一形制——time_exit_utc 任务 6
    增键、position_mode/risk_fraction 任务 4 增键（K1 回执接线补齐），
    回执面与声明面同步；值域收敛+bool 排除与 _identity_reflection
    同族同款）。"""
    return {
        "direction": direction if direction in ("long", "short") else "long",
        # bool 排除（security F-5）：与 _identity_reflection 同族——
        # bool 穿透会以 1/0 进回执冒充合法 bar 数/时刻
        "max_hold": max_hold if isinstance(max_hold, int)
        and not isinstance(max_hold, bool) else None,
        "time_exit_utc": time_exit if isinstance(time_exit, int)
        and not isinstance(time_exit, bool) else None,
        # 仓位模式属身份语义键（参数变体族——回执须可见，K1）：
        # 非法/缺省收敛 "fixed"；risk_fraction bool 排除（True 穿透
        # 冒充 1.0 比例）
        "position_mode": position_mode
        if position_mode in ("fixed", "risk_budget") else "fixed",
        "risk_fraction": float(risk_fraction)
        if isinstance(risk_fraction, (int, float))
        and not isinstance(risk_fraction, bool) else None,
        "reuse_note": _REUSE_NOTE if reuse else None,
    }
