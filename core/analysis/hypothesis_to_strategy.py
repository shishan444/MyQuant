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

其他翻译纪律不变：conditions JSON 为唯一翻译源；可表达子集与拒绝
原因（跨币种/基础列/插件列/分布形态）；谱系回链 parent_ids。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

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


def translate_hypothesis(
    db_path: Path,
    *,
    hypothesis_id: str,
    strategy_symbol: str = "BTCUSDT",
    strategy_timeframe: str = "4h",
) -> Dict[str, Any]:
    """把 active 假设转化为**草稿策略**（draft，验证对象；不要求 pass 前置）。

    Returns:
        {"strategy_id", "status": "draft", "duplicated": bool,
         "same_dna_existing": bool, "genes": [...],
         "risk_genes": "platform_default"}。
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

    # 幂等：谱系中 draft 产物存在即返回（转正后无 draft，可对新假设重译）
    existing_draft = _find_draft_by_parent(db_path, hypothesis_id)
    if existing_draft is not None:
        return {"strategy_id": existing_draft, "status": "draft",
                "duplicated": True, "same_dna_existing": False,
                "risk_genes": "platform_default"}

    conditions = json.loads(hyp["conditions"])
    data_ranges = json.loads(hyp["data_ranges"])

    # 跨币种拒绝
    symbols = {r.get("symbol") for r in data_ranges if isinstance(r, dict)}
    if len(symbols) > 1:
        raise ResearchError(
            "RESEARCH_NOT_EXPRESSIBLE",
            f"跨币种假设不可表达（WHEN/THEN 分属 {sorted(symbols)}）——"
            "策略表达为单品种信号源",
            detail={"symbols": sorted(symbols),
                    "slow_path": "提交 feature-requests 描述跨品种需求"})

    # 多周期假设拒绝（0.2.14 Q3=a）：timeframe 修饰整条件（掩码投影
    # 求值语义），TimeframeLayer 层映射的 role 归属与跨层组装语义待
    # 独立版本产品决策——显式拒绝，不产出静默降级为单周期的草稿。
    # 假设的登记、条件求值类分析与案例检查不受此边界影响；研究考试链
    # （隔离回测/随机窗口/分段）在翻译支持前不可用于混合周期假设。
    for group, key in (("when", "when_conditions"),
                       ("then", "then_conditions")):
        for i, c in enumerate(conditions.get(key, [])):
            if isinstance(c, dict) and c.get("timeframe") is not None:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{group}_conditions[{i}] 携带 timeframe 键——"
                    "多周期假设翻译暂不支持（TimeframeLayer 层映射语义"
                    "留独立版本决策）；假设的登记、条件求值类分析与案例"
                    "检查不受影响",
                    detail={
                        "field": f"{group}_conditions[{i}].timeframe",
                        "timeframe": c.get("timeframe"),
                        "slow_path": "多周期假设翻译支持前，此类假设"
                                     "生命周期上限为 active"
                                     "（仅可经 abandoned/superseded 退出）"})

    from core.strategy.dna import SignalRole
    when_genes, when_sources = _translate_conditions(
        conditions.get("when_conditions", []), db_path=db_path,
        label="when_conditions", role=SignalRole.ENTRY_TRIGGER)
    # THEN 按"预期兑现即离场"语义翻译为出场信号（与 WHEN 同规则拒绝；
    # 插件列出场源当前不可表达——组装器只组合入场，出场插件源会被
    # 静默丢弃，故显式拒绝而非误译为入场门控）
    then_genes, then_sources = _translate_conditions(
        conditions.get("then_conditions", []), db_path=db_path,
        label="then_conditions", role=SignalRole.EXIT_TRIGGER)
    genes = when_genes + then_genes
    plugin_sources = when_sources + then_sources

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

    dna = StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(
            symbol=strategy_symbol, timeframe=strategy_timeframe),
        risk_genes=RiskGenes(),  # 平台默认（返回中声明）
        parent_ids=[hypothesis_id],  # 谱系回链
        signal_sources=signal_sources,
        composition=composition,
    )

    # 同签名显式分流（MUST NOT 静默不落库）。签名用往返规范化后的对象算
    # ——构造态与库内（from_json）的 layers auto-wrap 形制不同，直接比对必失配
    dna = StrategyDNA.from_json(dna.to_json())
    same_dna = _find_by_signature(db_path, dna.gene_signature)
    active_hit = next((r for r in same_dna if r.get("status") != "draft"), None)
    if active_hit is not None:
        return {"strategy_id": active_hit["strategy_id"],
                "status": active_hit.get("status") or "active",
                "duplicated": False, "same_dna_existing": True,
                "risk_genes": "platform_default"}
    draft_hit = next((r for r in same_dna if r.get("status") == "draft"), None)
    if draft_hit is not None:
        return {"strategy_id": draft_hit["strategy_id"], "status": "draft",
                "duplicated": True, "same_dna_existing": False,
                "risk_genes": "platform_default"}

    strategy_id = f"st_{uuid.uuid4().hex[:12]}"
    from core.persistence.db_ext import save_strategy
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
            "risk_genes": "platform_default"}


def _translate_conditions(
    conditions: List[Dict[str, Any]], *, db_path: Path, label: str, role,
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

        # 结构化指标引用（0.2.13）：ColumnDef 直映射 SignalGene——
        # indicator/params 原样传递（多参指标天然支持），field 传语义名
        # （N4：executor 侧 resolve 的 field 值域是语义名 upper/signal/k…，
        # 编码前缀形在多输出指标非默认 field 会静默兜底取错列）
        if isinstance(subject, dict):
            from core.analysis.column_ref import ColumnRefError, resolve_column_ref
            try:
                cdef = resolve_column_ref(subject, db_path)
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
            if not isinstance(target, (int, float)):
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] target 须为数值（列名 target 不可表达）",
                    detail={"condition": cond})
            genes.append(SignalGene(
                indicator=cdef.indicator,
                params=dict(cdef.params),
                role=role,
                field_name=cdef.field,
                condition=_ACTION_MAP[action](target),
            ))
            continue

        # 插件列（注册表精确名）：翻译为 formula/model 信号源
        if subject in plugin_columns:
            source_type = plugin_columns[subject]
            if role != SignalRole.ENTRY_TRIGGER:
                raise ResearchError(
                    "RESEARCH_NOT_EXPRESSIBLE",
                    f"{label}[{i}] 不可表达: 插件列（{subject}）暂不支持"
                    "出场信号源（THEN 须为内置指标列；出场组合待后续版本）",
                    detail={"condition": cond,
                            "slow_path": "THEN 改用内置指标列 subject，"
                            "或提交 feature-requests 描述插件出场需求"})
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
            })
            continue

        # 字符串 subject（0.2.13 起）：参数化指标编码形已退役（入口关闭+
        # 存量迁移）——只可能是基础列 / 插件失效列 / 未知列，全部不可表达
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
    from core.persistence.db_ext import list_strategies

    rows = list_strategies(db_path, status="draft", limit=500)
    for row in rows:
        if row.get("parent_ids") and hypothesis_id in str(row["parent_ids"]):
            return row["strategy_id"]
    return None


def _find_by_signature(db_path: Path, gene_signature: str) -> List[Dict[str, Any]]:
    """同签名策略清单（含全部状态；translate 同签名分流用）。"""
    from core.persistence.repository.strategy import find_by_gene_signature

    return find_by_gene_signature(db_path, gene_signature)
