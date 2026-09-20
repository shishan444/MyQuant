"""假设 → 策略转化器（add-ai-research-mcp 6.4）。

翻译纪律（spec strategy-expression）：
- **以登记的 conditions JSON 为唯一翻译来源**——不从 statement 解析
  （措辞差异不影响产物；结构化条件是假设身份的本质）；
- 可表达子集：WHEN 条件的 subject 为内置指标输出列形制
  （如 ema_50 / rsi_14）且 action ∈ 阈值/穿越类 → SignalGene；
- 不可表达显式拒绝（附原因 + 慢通道建议）：跨币种（data_ranges 多
  symbol）、基础列/插件列 subject、分布形态类 action；
- 谱系回链：产物 DNA 的 parent_ids 携带 hypothesis_id；
- 幂等（卡6）：同假设重复转化返回既有策略 id；
- 未验证通过不得转化（信任链不跳级，409）。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.analysis.hypothesis_service import ResearchError
from core.persistence.repository import research as rr

# WHEN action → SignalGene.condition 的映射（阈值/穿越类可表达）
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
    """把已验证假设转化为策略（登记入库，谱系回链）。

    Returns:
        {"strategy_id", "duplicated": bool, "genes": [...]}。
    """
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND",
                            f"假设不存在: {hypothesis_id}", status=404)

    # 信任链：须存在 verdict=pass 的验证记录
    records = rr.list_validation_records(
        db_path, hypothesis_id=hypothesis_id, limit=200)
    if not any(r["verdict"] == "pass" for r in records):
        raise ResearchError(
            "RESEARCH_HYPOTHESIS_UNVALIDATED",
            "假设无 verdict=pass 的验证记录——未验证通过不得转化（信任链不跳级）",
            status=409)

    # 幂等（卡6）：谱系回链的既有产物
    existing = _find_strategy_by_parent(db_path, hypothesis_id)
    if existing is not None:
        return {"strategy_id": existing, "duplicated": True}

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

    genes = _translate_when(conditions.get("when_conditions", []))

    # 组装 DNA（v2：signal_sources 显式声明规则源；插件绑定留给策略编辑）
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)

    dna = StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(
            symbol=strategy_symbol, timeframe=strategy_timeframe),
        risk_genes=RiskGenes(),
        parent_ids=[hypothesis_id],  # 谱系回链
        signal_sources=[{"type": "rule"}],
    )

    strategy_id = f"st_{uuid.uuid4().hex[:12]}"
    from core.persistence.db_ext import save_strategy
    save_strategy(
        db_path, strategy_id=strategy_id,
        dna_json=dna.to_json(),
        symbol=strategy_symbol, timeframe=strategy_timeframe,
        name=f"假设转化 {hypothesis_id[:12]}",
        source="hypothesis", parent_ids=hypothesis_id,
        notes=f"translated from hypothesis {hypothesis_id} (conditions 为唯一翻译源)")

    # 研究日志落转化事件
    rr.append_research_log(
        db_path, hypothesis_id=hypothesis_id, entry_type="transition",
        ref_id=strategy_id,
        summary=f"translated -> strategy {strategy_id}")

    return {"strategy_id": strategy_id, "duplicated": False,
            "genes": [f"{g.indicator}({g.field_name})" for g in genes]}


def _translate_when(when_conditions: List[Dict[str, Any]]):
    """WHEN 条件 → SignalGene 列表（不可表达即拒）。"""
    from core.strategy.dna import SignalGene, SignalRole
    from core.analysis.hypothesis_service import allowed_subject_columns

    genes = []
    for i, cond in enumerate(when_conditions):
        subject = cond.get("subject", "")
        action = cond.get("action", "")
        target = cond.get("target")

        parsed = _parse_indicator_column(subject)
        if parsed is None:
            # 区分拒绝原因（附慢通道建议）
            allowed, _ = allowed_subject_columns(Path("."))  # 基础列诊断
            reason = (
                f"基础列 subject（{subject}）不可表达为指标信号源"
                if subject in allowed
                else f"插件/未知列 subject（{subject}）须显式绑定信号源")
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"when_conditions[{i}] 不可表达: {reason}",
                detail={"condition": cond,
                        "slow_path": "提交 feature-requests 或改用指标列 subject"})
        indicator, field, param = parsed
        if action not in _ACTION_MAP:
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"when_conditions[{i}] action 不可表达: {action}"
                f"（可表达: {sorted(_ACTION_MAP)}）",
                detail={"condition": cond})
        if not isinstance(target, (int, float)):
            raise ResearchError(
                "RESEARCH_NOT_EXPRESSIBLE",
                f"when_conditions[{i}] target 须为数值（列名 target 不可表达）",
                detail={"condition": cond})

        # 指标参数名推断：单周期参数的指标用 period，其余用主参数名
        from core.features.registry import INDICATOR_REGISTRY
        defn = INDICATOR_REGISTRY[indicator]
        param_key = next(iter(defn.params)) if defn.params else "period"

        genes.append(SignalGene(
            indicator=indicator,
            params={param_key: int(param)},
            role=SignalRole.ENTRY_TRIGGER,
            field_name=f"{field}_{int(param)}",
            condition=_ACTION_MAP[action](target),
        ))
    if not genes:
        raise ResearchError("RESEARCH_NOT_EXPRESSIBLE", "无可表达条件")
    return genes


def _find_strategy_by_parent(db_path: Path, hypothesis_id: str) -> Optional[str]:
    """幂等查询：谱系 parent_ids 含该假设的既有策略。"""
    from core.persistence.db_ext import list_strategies

    rows = list_strategies(db_path, limit=500)
    for row in rows:
        if row.get("parent_ids") and hypothesis_id in str(row["parent_ids"]):
            return row["strategy_id"]
    return None
