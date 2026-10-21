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
import logging
import operator as _operator
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.analysis.errors import ResearchError   # CARD-02 断链：轻量定义点

logger = logging.getLogger(__name__)


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
        # 退化形态：原路径原语义（行为逐位一致；事件化缺省生效——
        # dna_to_signal_set 内挂点承载）
        return dna_to_signal_set(dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe)

    # 掩码态 base（apply_trigger_policy=False——trigger-policy 卡5：
    # 插件组合在完整合并掩码上组合后统一变换；base 先变换再与插件
    # 布尔组合不等价——合并掩码段首可能落在段中部插件条件成立处）
    base = dna_to_signal_set(
        dna, enhanced_df, dfs_by_timeframe=dfs_by_timeframe,
        apply_trigger_policy=False)

    # 插件源求值（引用失败显式拒绝）。注册表一次查询建索引（column →
    # source）——逐源全表扫与 formula 过滤二次查统一为单次；formula 列
    # 一次整体 enrich 统一求值取列（叠层依赖闭包共享，不再逐列重复求值）
    from core.persistence.repository import research as rr
    source_types = {e["column_name"]: e["source"]
                    for e in rr.list_signal_registry_entries(db_path)}
    plugin_cols: Dict[str, pd.Series] = {}
    formula_cols: List[str] = []  # 声明序保序（失效报告序确定）
    for src in plugin_sources:
        col = src["ref"]
        source_type = source_types.get(col)
        if source_type is None:
            raise ResearchError(
                "RESEARCH_SIGNAL_SOURCE_INVALID",
                f"信号源引用失效（插件不存在或未注册）: {col}",
                detail={"ref": col})
        if source_type == "model":
            plugin_cols[col] = _model_column(db_path, col, enhanced_df)
        else:
            if col not in formula_cols:
                formula_cols.append(col)
    work = enhanced_df
    if formula_cols:
        from core.analysis.plugin_service import enrich_with_plugin_columns
        work = enrich_with_plugin_columns(db_path, enhanced_df, set(formula_cols))
        for col in formula_cols:
            if col not in work.columns:
                raise ResearchError(
                    "RESEARCH_SIGNAL_SOURCE_INVALID",
                    f"公式信号源求值失败: {col}",
                    detail={"ref": col})
            plugin_cols[col] = work[col]

    # 组合（composition.mode 缺省 = weighted_vote，threshold 0.5 交集语义）
    mode = (dna.composition or {}).get("mode", "weighted_vote")
    combined = _combine(base, dna, plugin_cols, mode)

    # 组合态可观测哨兵（fix-research-c4 C4.2）：有规则进场侧基因、
    # 规则侧基线全 False、且插件源在场——entries 完全由插件侧决定。
    # 该形态与历史缺陷五（规则基线静默作废纯插件进场）同形，固定
    # 前缀留痕供排查；警告不入 SignalSet 返回体。真实零触发市况下
    # 同样告警——按 spec 字面声明为正确行为契约（可观测优先，不改）。
    from core.strategy.dna import SignalRole as _SR
    _plugin_refs = sorted(s.get("ref", "") for s in plugin_sources)
    if (_has_rule_entry_side(dna) and plugin_cols
            and not bool(base.entries.fillna(False).astype(bool).any())):
        logger.warning(
            "[signal-assembly] 规则进场基线全 False 且插件源在场："
            "entries 完全由插件侧决定（若非预期请核查规则基因条件）"
            " plugin_refs=%s entry_genes=%s",
            _plugin_refs,
            [(g.indicator, getattr(g.role, "value", str(g.role)))
             for g in (dna.signal_genes or [])
             if g.role in (_SR.ENTRY_TRIGGER, _SR.ENTRY_GUARD)])

    # 事件化单次施加（trigger-policy 卡5）：组合后的 combined entries
    # 经唯一实现变换（防二次变换——suppress_bars 不幂等，二次抑制窗
    # 拉长 2N；base 已以掩码态取得）。CARD-03：改调 event_policy 公共
    # 面（消 core.analysis→core.strategy 跨层私有 import——executor 内
    # _apply_entry_trigger_policy 保留薄壳供回测/实盘/进化三链直调）
    from core.analysis.event_policy import apply_policy_to_series, validate_policy
    combined_entries = apply_policy_to_series(
        combined["entries"],
        validate_policy(getattr(dna, "entry_trigger_policy", None)),
        param_name="entry_trigger_policy")

    return SignalSet(
        entries=combined_entries,
        exits=base.exits,
        adds=base.adds,
        reduces=base.reduces,
        degraded_layers=base.degraded_layers,
        entry_direction=base.entry_direction,
        mtf_diagnostics=base.mtf_diagnostics,
        confidence=base.confidence,
    )


def _model_column(db_path: Path, column: str, df: pd.DataFrame) -> pd.Series:
    """模型源列：取插件配置，对当前 df 现算 walk-forward 信号。

    回测防作弊（spec）：模型信号必须 walk-forward 生成——不存在
    全样本拟合信号序列的路径（配置缓存 oos_summary 仅供参考）。
    """
    from sqlalchemy import select
    from core.persistence.orm.models import ModelPlugin
    from core.persistence.orm.engine import engine_for
    from core.signals.model import ModelPluginConfig, ModelPipelineError, fit_predict_walk_forward

    with engine_for(db_path).connect() as conn:
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
    # 库内 config 解析兜底（CARD-02）：损坏（外部改写/截断）与无
    # valid 版本/求值失败同族——信号源不可用，沿本函数既有码内联
    try:
        cfg = dict(json.loads(row["config"]))
    except json.JSONDecodeError as exc:
        raise ResearchError(
            "RESEARCH_SIGNAL_SOURCE_INVALID",
            f"模型信号源配置损坏（JSON 无法解析）: {column}",
            detail={"ref": column, "parse_error": str(exc)}) from exc
    cfg.pop("_config_hash", None)
    # 特征引用解析 + 装载 + 列名替换（与训练入口 register_model_plugin
    # 同源三步）：0.2.13 混合形制落库的 features 为 canonical 形
    # （指标 {indicator, params, field} dict / 基础列·插件列字符串），
    # 管线按精确列名取数——执行侧直喂 dict 形触发 unhashable
    # （模型列执行链全断，RR-034 沙盒首航实证）；字符串形（旧版
    # config）经同一解析口双形兼容
    from core.analysis.column_ref import ColumnRefError, resolve_column_ref
    from core.analysis.plugin_service import (
        _copy_config_with_columns, _enrich_feature_columns)
    feature_defs = []
    bad: list = []
    for f in cfg.get("features", []):
        try:
            feature_defs.append(resolve_column_ref(f, db_path))
        except ColumnRefError as exc:
            bad.append(f"{f!r}: {exc.message}")
    if bad:
        raise ResearchError(
            "RESEARCH_SIGNAL_SOURCE_INVALID",
            f"模型信号源特征引用不可用: {column}",
            detail={"ref": column, "bad_features": bad}) from None
    try:
        df = _enrich_feature_columns(db_path, df, feature_defs)
        run_cfg = _copy_config_with_columns(
            ModelPluginConfig(**cfg), feature_defs)
        res = fit_predict_walk_forward(df, run_cfg)
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


def _has_rule_entry_side(dna) -> bool:
    """规则进场侧基因在场判定（fix-research-c4 判定域·形态 B 提取——
    基线单位元选择与组合态哨兵两处共用同一判定，防单侧改动分叉）。

    判定域=引擎进场构造域（executor all_entry=entry_triggers+
    entry_guards 对齐）：任一 ENTRY_TRIGGER/ENTRY_GUARD 基因在场即真。
    """
    from core.strategy.dna import SignalRole
    return any(
        g.role in (SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD)
        for g in (dna.signal_genes or []))


def _to_float_or_reject(value, *, key: str):
    """组合参数数值键取值（0.3.7 H5：坏值结构化拒绝而非裸 float() 穿 500）。

    键缺省由调用方 ``get`` 的 default 承担（缺省是既有契约，本助手
    不触碰）；只拒显式坏值——坏串/不可解析类型 422 并回显键路径与
    原值（键路径带信号源列名，便于进化变异或手写 DNA 定位）。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"组合参数 {key} 须为数值: {value!r}",
            detail={"param": key, "value": value,
                    "hint": "修正 DNA 组合段该键为数值"})


def _combine(
    base, dna, plugin_cols: Dict[str, pd.Series], mode: str,
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
        threshold = _to_float_or_reject(
            src.get("threshold", 0.0), key=f"signal_sources[{col}].threshold")
        # 0.3.7 H5：未知算子显式拒绝（合法集=_SOURCE_COMPARE_OPS 键集
        # 单一事实源）——原 .get 兜底 ">" 会静默错比较方向且无提示；
        # 键缺省 ">"（不传 operator）仍走既有契约
        op = str(src.get("operator", ">"))
        if op not in _SOURCE_COMPARE_OPS:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"组合参数 signal_sources[{col}].operator 未知: {op!r}",
                detail={"param": f"signal_sources[{col}].operator",
                        "value": op,
                        "legal_values": sorted(_SOURCE_COMPARE_OPS),
                        "hint": "operator 须为封闭枚举集之一"})
        compare = _SOURCE_COMPARE_OPS[op]
        plugin_bools[col] = compare(
            series.astype(float), threshold).fillna(False)

    entries = base.entries.fillna(False).astype(bool)
    if not plugin_bools:
        return {"entries": entries}

    # 无规则源（纯插件条件策略，如翻译器纯插件列假设产物）：规则侧基线
    # 取组合语义单位元——AND 系（logic and / gate / weighted_vote 交集
    # 语义）恒真、logic OR 恒假——否则空 rule 基因的全 False entries 会
    # 把纯插件策略静默作废成永不入场
    #
    # 判定域=进场角色实质（fix-research-c4，D-13 定稿/TC-2 用户确认）：
    # 原「signal_sources 声明含 type=rule」与引擎进场构造域
    # （executor.py all_entry=entry_triggers+entry_guards）脱钩——
    # signal_genes 仅 exit_trigger+公式进场源的形制（st_f86b4295c58b）
    # 被误判「有规则源」，全 False 基线把纯插件进场静默作废（缺陷五
    # fr_1aee6a6e7e38）。现按基因角色实质判：任一 ENTRY_TRIGGER/
    # ENTRY_GUARD 基因在场即有规则进场侧。
    if not _has_rule_entry_side(dna):
        is_or = (mode == "logic"
                 and str((dna.composition or {}).get("op", "")).upper() == "OR")
        entries = pd.Series(not is_or, index=idx)

    comp = dna.composition or {}
    if mode == "weighted_vote":
        weights = comp.get("weights", {})
        threshold = _to_float_or_reject(
            comp.get("threshold", 0.5), key="composition.threshold")
        total = np.zeros(len(idx))
        w_sum = 0.0
        for col, b in plugin_bools.items():
            w = _to_float_or_reject(
                weights.get(col, 1.0), key=f"composition.weights[{col}]")
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
