"""信号源组装器（add-ai-research-mcp 6.2）：v2 策略的 signal_set 注入路径。

依赖纪律（D13）：backtest 引擎零新增依赖——插件信号源由本用例层
（core/analysis）求值并组装为 SignalSet，经引擎现成注入点
``engine.run(signal_set=...)`` 传入（evaluation.py:132 先例）。

时序契约（spec strategy-expression）：插件产 t 时刻原始值，防前视
平移由引擎独占负责（engine.py 统一 shift(1)）——组装层不平移，
双层平移会造成隐性偏差。

引用失败显式拒绝（不走 executor 静默 continue 路径）：插件不存在/
已失效/列缺失 → ResearchError，不产生"缺一路信号"的结果。
"""
from __future__ import annotations

import json
import operator as _operator
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError


def assemble_signal_set(
    db_path: Path,
    dna,
    enhanced_df: pd.DataFrame,
    dfs_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None,
):
    """组装 v2 策略的 SignalSet；退化形态（无 signal_sources）走原路径。

    v2 流程：规则源（dna_to_signal_set 原路径）→ 插件源求值（formula
    现算 / model walk-forward 现算）→ composition 组合三模式 → SignalSet。
    """
    from core.strategy.executor import SignalSet, dna_to_signal_set

    plugin_sources = [
        s for s in (dna.signal_sources or [])
        if s.get("type") in ("formula", "model")
    ]
    if not plugin_sources and not dna.composition:
        # 退化形态：原路径原语义（行为逐位一致）
        return dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    base = dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    # 插件源求值（引用失败显式拒绝）
    work = enhanced_df
    plugin_cols: Dict[str, pd.Series] = {}
    for src in plugin_sources:
        col = src["ref"]
        plugin_cols[col] = _resolve_plugin_column(db_path, src, work)
    if plugin_cols:
        from core.analysis.plugin_service import enrich_with_plugin_columns
        formula_cols = {c for c in plugin_cols
                        if _source_type_of(db_path, c) == "formula"}
        if formula_cols:
            work = enrich_with_plugin_columns(db_path, enhanced_df, formula_cols)

    # 组合（composition.mode 缺省 = weighted_vote，threshold 0.5 交集语义）
    mode = (dna.composition or {}).get("mode", "weighted_vote")
    combined = _combine(base, dna, plugin_cols, work, mode)

    return SignalSet(
        entries=combined["entries"],
        exits=base.exits,
        adds=base.adds,
        reduces=base.reduces,
        degraded_layers=base.degraded_layers,
        entry_direction=base.entry_direction,
        mtf_diagnostics=base.mtf_diagnostics,
        confidence=base.confidence,
    )


def _source_type_of(db_path: Path, column: str) -> Optional[str]:
    from core.persistence.repository import research as rr
    for e in rr.list_signal_registry_entries(db_path):
        if e["column_name"] == column:
            return e["source"]
    return None


def _resolve_plugin_column(
    db_path: Path, src: Dict[str, Any], df: pd.DataFrame,
) -> pd.Series:
    """求值一个插件源列（formula 现算 / model walk-forward 现算）。"""
    col = src["ref"]
    source_type = _source_type_of(db_path, col)
    if source_type is None:
        raise ResearchError(
            "RESEARCH_SIGNAL_SOURCE_INVALID",
            f"信号源引用失效（插件不存在或未注册）: {col}",
            detail={"ref": col})
    if source_type == "model":
        return _model_column(db_path, col, df)
    # formula 列由上层 enrich 统一求值；此处仅校验存在性
    from core.analysis.plugin_service import enrich_with_plugin_columns
    enriched = enrich_with_plugin_columns(db_path, df, {col})
    if col not in enriched.columns:
        raise ResearchError(
            "RESEARCH_SIGNAL_SOURCE_INVALID",
            f"公式信号源求值失败: {col}",
            detail={"ref": col})
    return enriched[col]


def _model_column(db_path: Path, column: str, df: pd.DataFrame) -> pd.Series:
    """模型源列：取插件配置，对当前 df 现算 walk-forward 信号。

    回测防作弊（spec）：模型信号必须 walk-forward 生成——不存在
    全样本拟合信号序列的路径（配置缓存 oos_summary 仅供参考）。
    """
    from sqlalchemy import select
    from core.persistence.orm.models import ModelPlugin
    from core.persistence.repository.dataset_meta import _engine_for
    from core.signals.model import ModelPluginConfig, ModelPipelineError, fit_predict_walk_forward

    with _engine_for(db_path).connect() as conn:
        rows = conn.execute(
            select(ModelPlugin).where(ModelPlugin.signal_column == column)
            .order_by(ModelPlugin.version.desc())
        ).mappings().all()
    row = next((r for r in rows if r["status"] == "valid"), None)
    if row is None:
        raise ResearchError(
            "RESEARCH_SIGNAL_SOURCE_INVALID",
            f"模型信号源无 valid 版本: {column}",
            detail={"ref": column})
    cfg = dict(json.loads(row["config"]))
    cfg.pop("_config_hash", None)
    try:
        res = fit_predict_walk_forward(df, ModelPluginConfig(**cfg))
    except ModelPipelineError as exc:
        raise ResearchError(
            "RESEARCH_SIGNAL_SOURCE_INVALID",
            f"模型信号源求值失败: {exc}",
            detail={"ref": column}) from exc
    return res.signal


#: 信号源比较算子（formula/model 源可选 operator 字段；缺省 ">" =
#: 既有行为，向后兼容——翻译器产物四比较保真用）
_SOURCE_COMPARE_OPS = {
    ">": _operator.gt, "<": _operator.lt,
    ">=": _operator.ge, "<=": _operator.le,
}


def _combine(
    base, dna, plugin_cols: Dict[str, pd.Series],
    work: pd.DataFrame, mode: str,
) -> Dict[str, pd.Series]:
    """组合决策层三模式（插件源转布尔：按源 operator 与阈值比较，
    缺省 ">" 对 0 阈值——既有行为）。"""
    idx = base.entries.index
    plugin_bools = {}
    for src in (dna.signal_sources or []):
        if src.get("type") not in ("formula", "model"):
            continue
        col = src["ref"]
        series = plugin_cols.get(col)
        if series is None:
            continue
        series = series.reindex(idx) if hasattr(series, "reindex") else series
        threshold = float(src.get("threshold", 0.0))
        compare = _SOURCE_COMPARE_OPS.get(
            str(src.get("operator", ">")), _operator.gt)
        plugin_bools[col] = compare(
            series.astype(float), threshold).fillna(False)

    entries = base.entries.fillna(False).astype(bool)
    if not plugin_bools:
        return {"entries": entries}

    # 无规则源（纯插件条件策略，如翻译器纯插件列假设产物）：规则侧基线
    # 取组合语义单位元——AND 系（logic and / gate / weighted_vote 交集
    # 语义）恒真、logic OR 恒假——否则空 rule 基因的全 False entries 会
    # 把纯插件策略静默作废成永不入场
    has_rule_source = any(
        s.get("type") == "rule" for s in (dna.signal_sources or []))
    if not has_rule_source:
        is_or = (mode == "logic"
                 and str((dna.composition or {}).get("op", "")).upper() == "OR")
        entries = pd.Series(not is_or, index=idx)

    comp = dna.composition or {}
    if mode == "weighted_vote":
        weights = comp.get("weights", {})
        threshold = float(comp.get("threshold", 0.5))
        total = np.zeros(len(idx))
        w_sum = 0.0
        for col, b in plugin_bools.items():
            w = float(weights.get(col, 1.0))
            total += b.astype(float).values * w
            w_sum += w
        vote = pd.Series(total / max(w_sum, 1e-9), index=idx) >= threshold
        return {"entries": entries & vote}

    if mode == "gate":
        # 门控：全部 gate 源为真时放行基础 entries
        gate_mask = pd.Series(True, index=idx)
        for b in plugin_bools.values():
            gate_mask &= b
        return {"entries": entries & gate_mask}

    if mode == "logic":
        op = comp.get("op", "AND").upper()
        plugin_any = pd.Series(False, index=idx)
        plugin_all = pd.Series(True, index=idx)
        for b in plugin_bools.values():
            plugin_any |= b
            plugin_all &= b
        if op == "OR":
            return {"entries": entries | plugin_any}
        return {"entries": entries & plugin_all}

    raise ResearchError(
        "RESEARCH_COMPOSITION_INVALID",
        f"未知组合模式: {mode}（weighted_vote|gate|logic）")
