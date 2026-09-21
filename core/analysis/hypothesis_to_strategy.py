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


def _parse_indicator_column(subject: str) -> Optional[tuple[str, str, float]]:
    """subject 为内置指标输出列形制 → (indicator, field_prefix, param)。

    匹配注册表输出列前缀 + _数字（如 ema_50、rsi_14）。
    """
    from core.features.registry import INDICATOR_REGISTRY

    for name, defn in INDICATOR_REGISTRY.items():
        for field in defn.output_fields:
            m = re.fullmatch(rf"{re.escape(field)}_(\d+)", subject)
            if m:
                return name, field, float(m.group(1))
    return None


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

    from core.strategy.dna import SignalRole
    when_genes = _translate_conditions(
        conditions.get("when_conditions", []), db_path=db_path,
        label="when_conditions", role=SignalRole.ENTRY_TRIGGER)
    # THEN 按"预期兑现即离场"语义翻译为出场信号（与 WHEN 同规则拒绝）
    then_genes = _translate_conditions(
        conditions.get("then_conditions", []), db_path=db_path,
        label="then_conditions", role=SignalRole.EXIT_TRIGGER)
    genes = when_genes + then_genes

    # 组装 DNA（v2：signal_sources 显式声明规则源；插件绑定留给策略编辑）
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, StrategyDNA)

    dna = StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(
            symbol=strategy_symbol, timeframe=strategy_timeframe),
        risk_genes=RiskGenes(),  # 平台默认（返回中声明）
        parent_ids=[hypothesis_id],  # 谱系回链
        signal_sources=[{"type": "rule"}],
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
):
    """条件 → SignalGene 列表（不可表达即拒；WHEN/THEN 同规则）。"""
    from core.strategy.dna import SignalGene
    from core.analysis.hypothesis_service import allowed_subject_columns

    genes = []
    for i, cond in enumerate(conditions):
        subject = cond.get("subject", "")
        action = cond.get("action", "")
        target = cond.get("target")

        parsed = _parse_indicator_column(subject)
        if parsed is None:
            # 区分拒绝原因（附慢通道建议；基础列诊断需查插件注册表）
            allowed, _ = allowed_subject_columns(db_path)
            reason = (
                f"基础列 subject（{subject}）不可表达为指标信号源"
                if subject in allowed
                else f"插件/未知列 subject（{subject}）须显式绑定信号源")
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"{label}[{i}] 不可表达: {reason}",
                detail={"condition": cond,
                        "slow_path": "提交 feature-requests 或改用指标列 subject"})
        indicator, field, param = parsed
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

        # 指标参数名推断：单周期参数的指标用 period，其余用主参数名
        from core.features.registry import INDICATOR_REGISTRY
        defn = INDICATOR_REGISTRY[indicator]
        param_key = next(iter(defn.params)) if defn.params else "period"

        genes.append(SignalGene(
            indicator=indicator,
            params={param_key: int(param)},
            role=role,
            field_name=f"{field}_{int(param)}",
            condition=_ACTION_MAP[action](target),
        ))
    if not genes:
        raise ResearchError("RESEARCH_NOT_EXPRESSIBLE", "无可表达条件")
    return genes


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
