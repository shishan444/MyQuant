"""隔离回测与随机窗口编排（add-ai-research-mcp 3.2 / 3.3）。

隔离回测判定流（附.3）：
终态拒验 → 幂等查询 → 判定并集求交（重叠 409 附 judged_ranges）→
warmup 推导（max(200, 跨指标取最大的(周期类参数注册表上限之和×3))，
含公式插件时取 max(内置推导, plugin_formula_lookback)——口径见
registry_meta.WARMUP_RULE_TEXT）→
数据起点不足 422 → [warmup 起点装载算指标/组装插件信号 → 切回请求
区间回测]（指标与插件列记忆保留、结果只统计请求区间）→ 门槛表机械判定 → validation_records+research_log 原子双落。

随机窗口（3.3）：同种子可复现（seed 进 params 持久化）；每窗独立
warmup 前扩（与隔离回测同语义）。

0.4.1 S3：_run_backtest_on_range（含 _collect_needed_tfs/
_slice_signal_set 两私有助手）逐字保真提升至 core/analysis/
backtest_bridge.run_backtest_on_range——本模块四处调用点改 import，
行为零变化。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.analysis.backtest_bridge import run_backtest_on_range
from core.analysis.errors import ResearchError, load_stored_json  # 轻量定义点直取（errors.py 唯一权威）
from core.analysis.isolation import (
    _parse_ts,
    iter_all_pages,
    judged_ranges_union,
    overlaps_with_request,
)
from core.analysis.registry_meta import WARMUP_RULE_TEXT, derive_warmup_full
from core.analysis.thresholds import (
    DEFAULT_PROFILE,
    _aggregate_verdicts,
    judge,
    tighten_profile,
)
from core.analysis.warmup_loader import TF_MINUTES, to_utc
from core.persistence.repository import research as rr

logger = logging.getLogger(__name__)

# 幂等数据源权威键黑名单（fix-review CARD-03）：base 的身份与幂等
# 三键由记录列/调用方权威产生，库内 result_json 内嵌同名键（损坏
# 或外部改写）不得覆盖权威值——命中跳过并告警留痕（与 direction
# 白名单二值收敛特判同构：一个是值域收敛，一个是整键拒绝）
_PAYLOAD_AUTHORITY_KEYS = frozenset({"record_id", "verdict", "duplicated"})

# 沙盒单次 bar 上限（M6：防爆超时——须在 MCP write_timeout=60s 内
# 留足余量；50k 为默认起点，按回测速度实施校准）
SANDBOX_MAX_BARS = 50_000


def _range_hash(request_range: Dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(request_range, sort_keys=True).encode()).hexdigest()


def _screen_reuse_check(
    db_path: Path, hypothesis_id: str, request_range: Dict[str, str],
) -> None:
    """扫描候选精确复用反向核查（add-screen-patterns 四件套之④）。

    在既有判定并集拦截未命中后执行（screen_use 谱系已被并集拦下，
    本步只覆盖不挂假设的 screen 日志）：全局 screen 留痕中「同品种
    同周期且时间重叠」的任务，其候选指纹清单与假设条件指纹集求交，
    交非空即 409 拒绝——不依赖任何声明，只拦精确复用；实质修改条件
    后指纹不同，天然放行。

    解析容错（D8/G4）：单条日志 summary 损坏跳过并记 warning
    （可用性优先，不因单条坏记录挡住全部验证）；指纹按请求 timeframe
    现算（timeframe 参与指纹——跨周期复用不算精确复用，D4）。
    """
    import logging

    from core.analysis.condition_identity import canonical_condition_hash
    from core.analysis.isolation import _parse_ts

    logger = logging.getLogger(__name__)
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        return
    try:
        conditions = json.loads(hyp["conditions"])
    except (json.JSONDecodeError, TypeError):
        return  # 结构异常由后续验证流程自然失败，本步不越权拒绝

    tf = str(request_range.get("timeframe", ""))
    cond_fps: set = set()
    for group in ("when_conditions", "then_conditions"):
        for c in conditions.get(group, []) if isinstance(conditions, dict) else []:
            try:
                cond_fps.add(canonical_condition_hash(c, tf))
            except (ValueError, TypeError):
                continue
            # F1 影子（0.2.13）：结构化指标 subject 追加旧算法影子指纹——
            # 存量 screen 留痕（旧算法指纹快照）× 迁移后假设的命中保全。
            # 迁移一次性原子切无混合态，单向补影即完备；比对与 409 零改动。
            if isinstance(c.get("subject"), dict):
                try:
                    from core.analysis.condition_identity import legacy_compatible_hash
                    cond_fps.add(legacy_compatible_hash(c, tf))
                except (ValueError, TypeError):
                    continue
    if not cond_fps:
        return

    rs = _parse_ts(request_range["start"])
    re_ = _parse_ts(request_range["end"])
    # screen 留痕分页取全量：单页 500 截断会漏更早的扫描任务（CARD-01）
    for entry in iter_all_pages(
        lambda off, lim: rr.list_research_log(
            db_path, entry_type="screen", offset=off, limit=lim),
        page_size=500,
    ):
        try:
            entry_ranges = json.loads(entry.get("data_ranges") or "[]")
            summary = json.loads(entry.get("summary") or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.warning("screen 日志解析失败，反向核查跳过该条: %s",
                           entry.get("log_id"))
            continue
        if not isinstance(summary, dict):
            continue
        # 同品种同周期 + 时间重叠（半开区间相交）
        overlap = False
        for r in entry_ranges if isinstance(entry_ranges, list) else []:
            if not isinstance(r, dict):
                continue
            if (r.get("symbol") != request_range.get("symbol")
                    or str(r.get("timeframe")) != tf):
                continue
            try:
                s, e = _parse_ts(r["start"]), _parse_ts(r["end"])
            except (KeyError, ValueError):
                continue
            if max(rs, s) < min(re_, e):
                overlap = True
                break
        if not overlap:
            continue
        task_fps = set(summary.get("top_fingerprints") or [])
        hits = sorted(cond_fps & task_fps)
        if hits:
            raise ResearchError(
                "SCREEN_CANDIDATE_REUSE",
                "验证区间与已扫描区间重叠且条件精确复用扫描候选"
                "（该区间数据已被扫描任务看过）",
                status=409,
                detail={"screen_task_id": entry.get("ref_id"),
                        "hit_fingerprints": hits,
                        "hint": "实质修改条件后登记新假设，或换未扫描区间验证"})


def _dna_indicator_names(dna) -> List[str]:
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


def _gene_tf_axis_refs(dna) -> Dict[str, List[tuple]]:
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
    indicator_names = _dna_indicator_names(dna)
    formula_refs = {s["ref"] for s in (dna.signal_sources or [])
                    if s.get("type") == "formula" and s.get("ref")}
    # 基因级周期分组（unblock-research-validation 预热第三维）：
    # 分组提取单源 _gene_tf_axis_refs（B8 抽取——与短窗预检共用）
    indicator_tfs = _gene_tf_axis_refs(dna)
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


# 基因级高周期轴装载下限（fix-review B8 / DEV-5 裁决 B）：值与
# load_mtf_data 的 min_bars 缺省同源——预检拦截集合=装载层会跳过的
# 集合，不扩大拒绝面
_GENE_TF_MIN_BARS = 50


def _reject_short_window_for_gene_tfs(
    dna, window_bars: int, base_tf: str, *, scope: str,
) -> None:
    """短窗×基因级高周期入口预检（统计无意义窗的诚实拒绝）。

    边界披露（裁决 B）：窗内高周期采样物理稀薄的组合（如 30 bar@4h
    对 1d 基因=窗内仅 5 根）对随机窗口/分段抽查无统计意义——判据
    与装载层同源（窗内采样 bar 保守向上取整 + 轴收敛 bar
    high_tf_warmup_span 单源 < min_bars 即拒，RESEARCH_DATA_
    INSUFFICIENT 族纪律），拒绝提前到入口并附可行动 detail
    （window_bars/gene_tf/need_hint）。保守取整保证只拦装载必拒的
    组合；边缘不一致落回既有基因级 fail-loud 兜底（_evaluate_gene_
    signal 轴缺失拒绝）。无基因级高周期基因零行为；显式分段不在
    预检面（用户自担段设计，fail-loud 兜底）。
    """
    axis_refs = _gene_tf_axis_refs(dna)
    if not axis_refs:
        return
    import math
    from core.data.timeframes import TF_SECONDS
    from core.data.mtf_alignment import high_tf_warmup_span
    base_sec = TF_SECONDS.get(base_tf)
    if not base_sec:
        return
    for gene_tf, refs in axis_refs.items():
        gene_sec = TF_SECONDS.get(gene_tf)
        if not gene_sec:
            continue
        in_window = math.ceil(window_bars * base_sec / gene_sec)
        warmup_bars = math.ceil(
            high_tf_warmup_span(refs, gene_tf).total_seconds() / gene_sec)
        if in_window + warmup_bars < _GENE_TF_MIN_BARS:
            need = math.ceil(
                (_GENE_TF_MIN_BARS - warmup_bars) * gene_sec / base_sec)
            raise ResearchError(
                "RESEARCH_DATA_INSUFFICIENT",
                f"{scope}对基因级周期 {gene_tf} 不可构成有效样本：窗 "
                f"{window_bars} bar 折算轴内约 {in_window} bar + 收敛 "
                f"{warmup_bars} bar < 装载下限 {_GENE_TF_MIN_BARS}"
                "（统计无意义窗诚实拒绝）",
                detail={
                    "window_bars": window_bars, "gene_tf": gene_tf,
                    "in_window_bars": in_window,
                    "warmup_bars": warmup_bars,
                    "min_bars": _GENE_TF_MIN_BARS,
                    "need_hint": f"加宽窗口至 ≥{need} bar（基准 {base_tf}）"
                                 " 或降低基因周期"})


# threshold_profile 按名白名单（安全关 S-3，方向 B）：预定义名 → 实际
# profile 的解析入口——门槛规则由本域定义，全量 rules dict 透传等于
# 调用方操纵判定门槛
_THRESHOLD_PROFILE_NAMES: tuple[str, ...] = ("default", "tightened")


def _resolve_threshold_profile(
    threshold_profile: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """threshold_profile 按名白名单解析（两验证入口共用）。

    {"name": "default"} → DEFAULT_PROFILE；{"name": "tightened"} →
    tighten_profile()（组合加严档）；None → None（judge 侧落
    DEFAULT_PROFILE）。未知名 / 含 rules 等全量形制 / 非法结构一律
    422——判定门槛不可由调用方注入。
    """
    if threshold_profile is None:
        return None
    if (not isinstance(threshold_profile, dict)
            or set(threshold_profile) != {"name"}
            or threshold_profile["name"] not in _THRESHOLD_PROFILE_NAMES):
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "threshold_profile 只认按名形制 "
            '{"name": "default"|"tightened"}（门槛规则由本域预定义，'
            "不接受全量透传）",
            detail={"param": "threshold_profile",
                    "legal_names": list(_THRESHOLD_PROFILE_NAMES)})
    if threshold_profile["name"] == "tightened":
        return tighten_profile()
    return DEFAULT_PROFILE


def segmentation_identity(mode: str, segments=None) -> str:
    """0.3.3 G7：幂等身份第五维——分段身份（非模式串）。

    auto → "auto"；explicit → 规范化段清单内容摘要（等价清单必同
    摘要：段序按 (start,end,label) 显式排序、键序固定、非 ASCII label
    稳定——不同段清单摘要不同，同区间分段方案对比各自独立）；其余
    （隔离/随机窗）→ 空串。输入形制统一为与 params 落库同形的字符串化
    段清单（Timestamp/str 双兼容——写入侧对象先同形转换，回填侧
    params 字符串直取）。alembic 迁移内联同逻辑副本（自包含防重放
    漂移）——改动须双向同步（test 断言两实现同输出）。
    """
    if mode == "auto":
        return "auto"
    if mode == "explicit":
        norm = [
            {"label": s["label"],
             "start": s["start"].isoformat()
             if hasattr(s["start"], "isoformat") else str(s["start"]),
             "end": s["end"].isoformat()
             if hasattr(s["end"], "isoformat") else str(s["end"])}
            for s in (segments or [])]
        canon = json.dumps(
            sorted(norm, key=lambda s: (s["start"], s["end"], s["label"])),
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    return ""


def _validation_payload(
    base: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    *,
    db_path: Optional[Path] = None,
    hypothesis_id: Optional[str] = None,
    judged: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """验证返回体单一映射（unify-tool-return-contract 规则①）。

    统一形制=首次/幂等两分支顶层键集并集全顶层、result 嵌套退役：
    payload = base（身份与入口专有键）∪ result 内嵌键顶层化 ∪
    judged_ranges。isolated 首次/幂等与 staged 首次/幂等四点共用
    （staged 专有键由 base 参数化，禁一套键集硬套两入口）——键集
    一致性由本函数结构保证，契约测试断言两分支顶层键集合相等。

    - 权威键黑名单（CARD-03）：result 内嵌 record_id/verdict/
      duplicated 不覆盖 base（权威值来自记录列与调用方），命中
      跳过并 warning。
    - direction 值域收敛（security S-1）：脏值/null 恒回显合法二值。
    - judged_ranges 同源现算 judged_ranges_union（语义=响应时点
      判定并集，落库后含本记录——首次与幂等同口径）；judged 显式
      传入时直用（staged 首次已现算，避免双算）。
    """
    payload = dict(base)
    if isinstance(result, dict):
        for key, value in result.items():
            if key in _PAYLOAD_AUTHORITY_KEYS:
                logger.warning(
                    "result_json 内嵌权威键 %r，跳过不覆盖（base 权威值保留）",
                    key)
                continue
            if key != "direction":
                payload[key] = value
    direction = result.get("direction") if isinstance(result, dict) else None
    payload["direction"] = direction if direction in ("long", "short") else "long"
    if judged is None:
        judged = judged_ranges_union(db_path, hypothesis_id) \
            if (db_path is not None and hypothesis_id) else []
    payload.setdefault("judged_ranges", judged)
    return payload


def _dup_payload(
    existing: Dict[str, Any],
    *,
    db_path: Optional[Path] = None,
    hypothesis_id: Optional[str] = None,
    entry_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """幂等命中返回形（隔离/分段共用；经 _validation_payload 与首次同构）。

    幂等数据源=result_json（写入侧保证内嵌完整首次 payload）；entry
    专有键（staged 的入口参数）经 entry_ctx 参数化。direction 读
    result_json 顶层键（add-direction-channel A4），存量记录无键按
    "long" 收敛。
    """
    # 幂等数据源解析兜底（CARD-02）：result_json 由写入侧序列化，
    # 损坏（外部改写/截断）时结构化拒绝点名记录，不裸炸 500
    result = load_stored_json(
        existing["result"], source="validation_records.result",
        detail={"record_id": existing["record_id"]},
    ) if existing["result"] else None
    base = {
        "record_id": existing["record_id"],
        "verdict": existing["verdict"],
        "duplicated": True,
    }
    if entry_ctx:
        base.update(entry_ctx)
    return _validation_payload(
        base, result, db_path=db_path, hypothesis_id=hypothesis_id)


def _hypothesis_precheck(
    db_path: Path, hypothesis_id: str, strategy_id: str,
    rh: str, request_range: Dict[str, str],
    *, idem_level: Optional[str] = None, idem_segmentation: str = "",
    rerun_mode: bool = False,
) -> Optional[Dict[str, Any]]:
    """验证族前置检查四步（isolated/staged 同构提取）。

    终态拒验（404/409）→ 幂等查询 → 判定并集求交（重叠 409 附
    judged_ranges）→ 扫描候选精确复用反向核查。幂等命中返回既有
    记录 payload（duplicated=True），干净返回 None（调用方继续
    验证主流程）；Raises 语义与提取前逐字一致。
    0.3.3 G7：幂等查询按五元身份（level+segmentation 等值——类型
    隔离）。idem_level=None 时跳过原位幂等（分段路径的 explicit 摘要
    依赖构造后的段清单——幂等查询后置到段清单就绪处，precheck 只做
    其余三步）。
    """
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND",
                            f"假设不存在: {hypothesis_id}", status=404)
    if hyp["status"] != "active":
        raise ResearchError("RESEARCH_HYPOTHESIS_TERMINAL",
                            f"假设已终态（{hyp['status']}），拒绝验证；请登记新假设",
                            status=409)
    if idem_level is not None:
        existing = rr.find_validation_by_identity(
            db_path, hypothesis_id, strategy_id, rh,
            level=idem_level, segmentation=idem_segmentation)
        if existing is not None:
            return _dup_payload(
                existing, db_path=db_path, hypothesis_id=hypothesis_id)
    judged = judged_ranges_union(db_path, hypothesis_id)
    overlaps = overlaps_with_request(
        judged, request_range["start"], request_range["end"])
    if overlaps and not rerun_mode:
        # 重验取代豁免（fix-mtf-execution-consistency S）：rerun 模式
        # 跳过并集面——重跑旧区间不是「同区间反复判定刷 verdict」而是
        # 污染修正取代（新记录 supersedes 引用旧记录；无旧记录的漂移
        # 假设=取代未落记录的原验证活动，同属取代语义）；幂等键与终态
        # 检查照常生效（新 strategy_id 不命中幂等）
        raise ResearchError(
            "RESEARCH_RANGE_OVERLAP",
            "请求区间与判定区间重叠（判定并集=自报∪日志，漏报被日志拦截）",
            status=409,
            detail={"judged_ranges": judged, "overlaps": overlaps})
    _screen_reuse_check(db_path, hypothesis_id, request_range)
    return None


def run_isolated_backtest(
    db_path: Path,
    data_dir: Path,
    *,
    hypothesis_id: str,
    strategy_id: str,
    request_range: Dict[str, str],
    threshold_profile: Optional[Dict[str, Any]] = None,
    rerun_mode: bool = False,
) -> Dict[str, Any]:
    """隔离回测（判定流见模块 docstring）。

    request_range: {symbol, timeframe, start, end}（半开、UTC）。
    Returns:
        {record_id, verdict, metrics, threshold_details, warmup,
         duplicated, judged_ranges}
    """
    # threshold_profile 按名解析（结构校验前置——先于终态/幂等）
    threshold_profile = _resolve_threshold_profile(threshold_profile)

    # 验证族前置检查四步（终态/幂等/并集求交/扫描复用——同构提取，
    # 幂等命中即返）
    rh = _range_hash(request_range)
    dup = _hypothesis_precheck(db_path, hypothesis_id, strategy_id, rh,
                               request_range,
                               idem_level="isolated_backtest",
                               rerun_mode=rerun_mode)
    if dup is not None:
        return dup

    # 策略与 DNA
    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    symbol = request_range.get("symbol") or strat_row.get("symbol", "BTCUSDT")
    timeframe = request_range.get("timeframe") or strat_row.get("timeframe", "4h")

    # warmup 推导（内置 ×3 与插件公式 lookback 取大，floor 200）
    try:
        warmup_bars, indicator_names = _derive_dna_warmup(
            db_path, dna, timeframe)  # R3：消费轴（请求周期）
    except KeyError as exc:
        raise ResearchError("RESEARCH_CONDITION_INVALID", str(exc)) from exc

    # 组合策略加严门之一（6.5）：分量证据前置检查（先于回测执行——
    # 缺证据不浪费计算；模型分量 walk-forward 由组装器强制）
    plugin_sources = [s for s in (dna.signal_sources or [])
                      if s.get("type") in ("formula", "model")]
    components_json = None
    effective_profile = threshold_profile
    if plugin_sources:
        missing = _missing_component_evidence(db_path, plugin_sources)
        if missing:
            raise ResearchError(
                "RESEARCH_COMPONENT_EVIDENCE_MISSING",
                "多信号源策略缺分量级证据（每分量须独立验证记录）",
                status=409,
                detail={"missing": missing})
        from core.analysis.thresholds import tighten_profile
        # 0.3.7 C1（Q1=A 已裁）：组合分支恒收紧——原 truthy 短路使显式
        # default 档绕开收紧，与 R5「组合 MUST 加严」意图背离；上方
        # :470 非组合行的 threshold_profile 直传语义不变
        effective_profile = tighten_profile()
        components_json = json.dumps(
            [s["ref"] for s in plugin_sources], ensure_ascii=False)

    # 回测（warmup 前扩装载）
    metrics, trade_returns, actual_warmup, bars_used = run_backtest_on_range(
        db_path, data_dir, dna, symbol, timeframe,
        request_range["start"], request_range["end"], warmup_bars,
        strategy_id=strategy_id)

    # 门槛机械判定（组合用收紧 profile；三段闸门含证据闸门/CI）
    verdict, details = judge(metrics, effective_profile,
                             trade_returns=trade_returns)

    # 双落（原子）
    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    result_json = json.dumps({
        "metrics": metrics,
        "threshold_details": details,
        "warmup": {"bars": actual_warmup, "derived": warmup_bars,
                   "basis": WARMUP_RULE_TEXT,
                   "indicators": indicator_names,
                   # R3：推导轴披露（消费轴=请求周期——研究者可查轴单位）
                   "base_timeframe": timeframe},
        "bars_used": bars_used,
        "symbol": symbol, "timeframe": timeframe,
        # 方向披露（add-direction-channel A4）：纯增量键，来源=draft
        # DNA——幂等重放经 _dup_payload 同构回显；旧消费方零适配
        "direction": dna.risk_genes.direction,
    }, ensure_ascii=False)
    params_json = json.dumps({
        "request_range": request_range, "range_hash": rh,
        "threshold_profile": (effective_profile or DEFAULT_PROFILE)["name"],
    }, ensure_ascii=False)
    rr.save_validation_with_log(
        db_path, record_id=record_id, hypothesis_id=hypothesis_id,
        strategy_id=strategy_id, level="isolated_backtest", segmentation="",
        params=params_json, result=result_json, verdict=verdict, range_hash=rh,
        components=components_json)

    # 统一返回体（unify-tool-return-contract 规则①）：经单一映射函数
    # 与幂等分支同构（11 键全顶层；warmup 与 result_json 同 4 子键；
    # judged_ranges 响应时点现算=落库后含本记录，与幂等重放同口径）
    return _validation_payload(
        {"record_id": record_id, "verdict": verdict, "duplicated": False},
        json.loads(result_json),
        db_path=db_path, hypothesis_id=hypothesis_id)


def random_window_test(
    db_path: Path,
    data_dir: Path,
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    data_start: str,
    data_end: str,
    window_bars: int = 200,
    n_windows: int = 8,
    seed: int = 42,
    threshold_profile: Optional[Dict[str, Any]] = None,
    hypothesis_id: Optional[str] = None,
) -> Dict[str, Any]:
    """随机窗口抽查（种子可复现）。

    每窗独立 warmup 前扩（与隔离回测同语义）；窗口在
    [data_start, data_end] 内随机抽样（同 rng 种子 → 同窗口集）。

    物理边界（fix-review B8 裁决 B）：短窗×基因级高周期组合（窗内
    采样+收敛 < 装载轴下限 50 bar）入口预检显式拒绝（detail 附
    window_bars/gene_tf/need_hint——统计无意义窗诚实拒绝；fail-loud
    为第二道兜底；见 _reject_short_window_for_gene_tfs）。
    """
    # threshold_profile 按名解析（结构校验前置；每窗 judge 消费）
    threshold_profile = _resolve_threshold_profile(threshold_profile)

    import numpy as np

    dna, _ = _load_strategy_dna(db_path, strategy_id)
    warmup_bars, _ = _derive_dna_warmup(db_path, dna, timeframe)  # R3：消费轴
    tf_minutes = TF_MINUTES.get(timeframe, 240)
    bar_delta = pd.Timedelta(minutes=tf_minutes)

    # 可抽窗的候选区间：[data_start, data_end - window]
    ds = to_utc(pd.Timestamp(data_start))
    de = to_utc(pd.Timestamp(data_end))
    span_bars = int((de - ds) / bar_delta)
    usable = span_bars - window_bars
    if usable < n_windows:
        raise ResearchError("RESEARCH_DATA_INSUFFICIENT",
                            f"区间仅容 {usable} 个不重叠窗，需 {n_windows}")

    # 幂等：同 (strategy, range_hash) 二次请求返回既有记录（复现审计）
    rh = _range_hash({"symbol": symbol, "timeframe": timeframe,
                      "start": data_start, "end": data_end,
                      "window_bars": window_bars, "n": n_windows, "seed": seed})
    existing = rr.find_validation_by_identity(
        db_path, hypothesis_id or "adhoc", strategy_id, rh,
        level="random_window")  # G7 五元：随机窗身份（segmentation 空串）
    if existing is not None:
        # 幂等数据源解析兜底（CARD-02）：同 _dup_payload 形制
        _rw_result = load_stored_json(
            existing["result"], source="validation_records.result",
            detail={"record_id": existing["record_id"]},
        ) if existing["result"] else None
        # 方向同构回显（add-direction-channel A4）：存量无键/脏值按 long
        # 补（security S-1 值域收敛——恒回显合法二值）
        _rw_direction = _rw_result.get("direction") \
            if isinstance(_rw_result, dict) else None
        if _rw_direction not in ("long", "short"):
            _rw_direction = "long"
        return {"record_id": existing["record_id"], "seed": seed,
                "duplicated": True,
                # 0.3.3 G6：幂等重放与首次请求同形——顶层 verdict 取
                # 记录 verdict 列（:360 先例同源；worst_window.verdict 是
                # 收益最差窗的窗口判定，不等于三态聚合，禁作提取源）
                "verdict": existing["verdict"],
                "direction": _rw_direction or "long",
                "result": _rw_result}

    # 短窗×基因级高周期入口预检（fix-review B8 / 裁决 B）：装载层
    # 跳过→fail-loud 的两段式拒绝提前为入口显式拒绝（幂等命中不受
    # 影响——预检在幂等查询后）
    _reject_short_window_for_gene_tfs(
        dna, window_bars, timeframe, scope="随机窗口")

    rng = np.random.default_rng(seed)
    # 顺序抽样保证不重叠且可复现
    offsets = sorted(rng.choice(usable, size=n_windows, replace=False).tolist())

    windows = []
    for off in offsets:
        w_start = ds + off * bar_delta
        w_end = w_start + window_bars * bar_delta
        metrics, w_returns, actual_warmup, bars = run_backtest_on_range(
            db_path, data_dir, dna, symbol, timeframe,
            str(w_start.isoformat()), str(w_end.isoformat()), warmup_bars,
            strategy_id=strategy_id)
        verdict, details = judge(metrics, threshold_profile,
                                 trade_returns=w_returns)
        windows.append({
            "start": w_start.isoformat(), "end": w_end.isoformat(),
            "metrics": metrics, "verdict": verdict,
        })

    returns = sorted(w["metrics"]["total_return"] for w in windows)
    worst = min(windows, key=lambda w: w["metrics"]["total_return"])

    # 总判定最差聚合（0.2.11 三态）：任一 fail → fail；无 fail 有
    # inconclusive → inconclusive；全 pass → pass（保守性不低于原两态
    # 聚合）；all_pass 收紧为严格全 pass（A1）
    agg = _aggregate_verdicts(w["verdict"] for w in windows)
    summary = {
        "n_windows": n_windows,
        "window_bars": window_bars,
        "returns_q25": float(np.quantile(returns, 0.25)),
        "returns_median": float(np.quantile(returns, 0.50)),
        "returns_q75": float(np.quantile(returns, 0.75)),
        "worst_window": worst,
        "all_pass": agg == "pass",
    }

    # 落验证记录（level=random_window；种子在 params 中持久化 → 可复现审计）
    record_id = f"rec_{uuid.uuid4().hex[:12]}"
    rr.save_validation_with_log(
        db_path, record_id=record_id, hypothesis_id=hypothesis_id or "adhoc",
        strategy_id=strategy_id, level="random_window", segmentation="",
        params=json.dumps({"seed": seed, "window_bars": window_bars,
                           "n_windows": n_windows, "data_start": data_start,
                           "data_end": data_end, "symbol": symbol,
                           "timeframe": timeframe}, ensure_ascii=False),
        result=json.dumps({"windows": windows, "summary": summary,
                           # 方向披露（add-direction-channel A4）：幂等
                           # 重放提取源；纯增量键
                           "direction": dna.risk_genes.direction},
                          ensure_ascii=False),
        verdict=agg, range_hash=rh)

    return {"record_id": record_id, "seed": seed,
            # 0.3.3 G6：顶层 verdict（值=在场 agg 聚合判定）——与幂等
            # 命中路径同形同值，程序化判定链单点取值
            "verdict": agg,
            "direction": dna.risk_genes.direction,
            "windows": windows, "summary": summary}


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
) -> Dict[str, Any]:
    """沙盒回测（improve-mcp-agent-loop M6）：快速试错，不判门槛、不入验证记录。

    纪律内嵌：每次沙盒强制落研究日志——挂 hypothesis_id 时区间进判定
    并集（封堵"先偷看后验证"），不挂时以 adhoc 日志留痕。
    区间 bar 数超上限拒绝（防爆超时，引导走隔离验证协议）。

    Returns:
        {"strategy_id", "symbol", "timeframe", "metrics", "bars_used",
         "hypothesis_id", "sandbox": True}
    """
    # bars 上限（MCP write_timeout=60s 内留余量；50k 起点实施时校准）
    tf_minutes = TF_MINUTES.get(timeframe, 240)
    start_ts = to_utc(pd.Timestamp(start))
    end_ts = to_utc(pd.Timestamp(end))
    bars = int((end_ts - start_ts).total_seconds() / 60 / tf_minutes)
    if bars > SANDBOX_MAX_BARS:
        raise ResearchError(
            "RESEARCH_SANDBOX_LIMIT",
            f"沙盒区间 {bars} bar 超上限 {SANDBOX_MAX_BARS}——隔离验证"
            "协议支持任意区间（分区间验证）",
            status=422,
            detail={"bars": bars, "max": SANDBOX_MAX_BARS,
                    "slow_path": "run_isolated_validation 支持大区间"})

    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    symbol = symbol or strat_row.get("symbol", "BTCUSDT")
    timeframe = timeframe or strat_row.get("timeframe", "4h")
    warmup_bars, _ = _derive_dna_warmup(db_path, dna, timeframe)  # R3：消费轴

    # trade_returns 不进沙盒响应（不判门槛不披露 CI——_ 占位解包）
    metrics, _trade_returns, actual_warmup, bars_used = run_backtest_on_range(
        db_path, data_dir, dna, symbol, timeframe, start, end, warmup_bars,
        init_cash=init_cash, fee=fee, slippage=slippage,
        strategy_id=strategy_id)

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

    return {"strategy_id": strategy_id, "symbol": symbol, "timeframe": timeframe,
            "metrics": metrics, "bars_used": bars_used,
            "warmup_bars": actual_warmup,
            "hypothesis_id": hypothesis_id, "sandbox": True,
            "note": "沙盒结果不构成验证证据；关联假设的区间已计入判定并集"}


def _missing_component_evidence(
    db_path: Path, plugin_sources: List[Dict[str, Any]],
) -> List[str]:
    """缺分量证据的源清单（双源判据——Q3 用户裁决 (a)∪(b)）。

    (a) 研究日志 component_eval 记录（逐组件一条，summary.column
        精确字符串相等——unblock-research-validation B 段翻译试算
        的供给面，「翻译过=有记录」不变式）；
    (b) 既有 validation_records level=component 且 verdict=pass
    （params.column 匹配——查询面原样保留；verdict=pass 过滤保持
    0.2.11 D3 收紧语义）。
    放行=任一源命中；拦截=双无（全新未执行列）。「或活跃且被引用」
    恒真分支不复活（语义锚——design 明文移除）。model 列组件维持
    拦截（本包解禁范围=formula 线：试算不写 model 记录，双源皆无
    恒拦——已知行为边界）。
    """
    # (b) 源：component 级 pass 记录
    component_records = [
        r for r in iter_all_pages(
            lambda off, lim: rr.list_validation_records(
                db_path, offset=off, limit=lim))
        if r["level"] == "component" and r["verdict"] == "pass"
    ]
    evidenced = set()
    for r in component_records:
        try:
            params = json.loads(r["params"])
            evidenced.add(params.get("column"))
        except (json.JSONDecodeError, TypeError):
            continue
    # (a) 源：component_eval 研究日志（翻译试算写入）
    for entry in iter_all_pages(
            lambda off, lim: rr.list_research_log(
                db_path, entry_type="component_eval",
                offset=off, limit=lim)):
        try:
            summary = json.loads(entry.get("summary") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        col = summary.get("column")
        if isinstance(col, str):
            evidenced.add(col)
    return [s["ref"] for s in plugin_sources if s["ref"] not in evidenced]


# ---------------------------------------------------------------- 分段验证
# （add-staged-validation；MCP 0.2.10「考试完整」开门件）
#
# 时段稳健性证据第二形态：与 random_window_test（盲切抽查）互补——
# 分段按市场状态归因（哪类行情不行）。九步编排全部复用既有权威：
# build_segments 切段（第二消费者）/ _run_backtest_on_range 逐段回测
# （每段独立装载=零状态渗透）/ judge 段级门槛（同一把尺量每类行情）。
#
# 关键裁决（七环节评估链冻结）：
# - Q1 可引用·叠加：挂假设且全段达标产生 level="staged_validation" 的
#   pass 记录（transition 沿用 record_id+verdict 引用语义，无 level 过滤
#   ——hypothesis_service 查证）；与隔离回测并列不替代。
# - Q2 段级门槛：judge 前置预判 total_trades<5 → insufficient_sample
#   （judge 是通用规则引擎无 insufficient 语义，不预判短段会被误判
#   fail——thresholds.py 查证）；「标注不是第三 verdict」不动全局两态。
# - Q3 双模互斥：auto（机械判读 gates 内建）与 segments 同传拒。
# - 写入双路径：挂假设 → save_validation_with_log 一体（记录+日志+
#   入并集）；adhoc → append_research_log 纯日志（validation_records.
#   hypothesis_id NOT NULL+FK——无挂靠物理不产生记录，verdict 照报、
#   record_id=None 无可引用凭据）。
# - 字段映射：build_segments 输出的段类型键 type 映射统一 label
#   （explicit 段天然 label——输出单键结构稳定）；判读统计三键随
#   auto 段透传，explicit 段天然无。

_SEGMENTATION_MODES = frozenset({"auto", "explicit"})
_REGIME_HELPER_NAMES = ("ADX", "ATR")


def _validate_explicit_segments(
    segments: List[Dict[str, Any]],
    req_start: str, req_end: str,
) -> List[Dict[str, Any]]:
    """显式段列表校验（恰好切分语义：段在请求区间内、并集恰覆盖、
    相邻相接不重叠、时间升序；三键齐备）——违反拒第一违规段。
    """
    if not segments:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "explicit 模式 segments 不能为空",
            detail={"param": "segments"})
    rs, re_ = _parse_ts(req_start), _parse_ts(req_end)
    prev_end: Optional[datetime] = None
    out: List[Dict[str, Any]] = []
    for i, seg in enumerate(segments):
        missing = [k for k in ("label", "start", "end") if not seg.get(k)]
        if missing:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 缺键: {missing}（须三键 {{label, start, end}}）",
                detail={"param": f"segments[{i}]", "missing": missing})
        try:
            s, e = _parse_ts(seg["start"]), _parse_ts(seg["end"])
        except Exception as exc:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 时间不可解析: {seg['start']}..{seg['end']}",
                detail={"param": f"segments[{i}]"}) from exc
        if not s < e:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 起点不早于终点",
                detail={"param": f"segments[{i}]", "start": seg["start"],
                        "end": seg["end"]})
        if prev_end is not None and s < prev_end:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 与前段重叠或乱序（本段起点早于前段终点）",
                detail={"param": f"segments[{i}]",
                        "prev_end": prev_end.isoformat(),
                        "start": seg["start"]})
        out.append({"label": str(seg["label"]), "start": s, "end": e})
        prev_end = e
    # 恰好覆盖：首段起于请求起点、末段止于请求终点、相邻相接（缝隙
    # 表现为某段起点晚于前段终点——上面重叠检查放行 s >= prev_end，
    # 缝隙在此汇总核：并集须恰等于请求区间）
    if out[0]["start"] > rs or out[-1]["end"] < re_:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "段列表未完整覆盖请求区间（首段起点/末段终点越界）",
            detail={"param": "segments",
                    "request": [req_start, req_end],
                    "covered": [out[0]["start"].isoformat(),
                                out[-1]["end"].isoformat()]})
    for i in range(1, len(out)):
        if out[i]["start"] > out[i - 1]["end"]:
            raise ResearchError(
                "RESEARCH_PARAM_INVALID",
                f"段 #{i} 与前段之间存在缝隙",
                detail={"param": f"segments[{i}]",
                        "gap": [out[i - 1]["end"].isoformat(),
                                out[i]["start"].isoformat()]})
    return out


def _auto_segments(
    data_dir: Path, symbol: str, timeframe: str,
    start: str, end: str,
) -> List[Dict[str, Any]]:
    """auto 切段：装载请求区间 → ADX/ATR default 现算 → build_segments
    → type→label 映射（判读统计三键透传；bar 数由位置差重算）。

    物理边界（fix-review B8 裁决 B）：短段×基因级高周期组合由
    run_staged_validation 在段清单就绪后逐段预检（判据与装载层
    同源，见 _reject_short_window_for_gene_tfs；explicit 段不在
    预检面——调用方自担段内采样充分性）。"""
    from core.analysis.regime_segments import build_segments
    from core.analysis.registry_meta import default_params_for
    from core.data.mtf_loader import (
        find_parquet, load_and_prepare_df, safe_symbol)

    if find_parquet(data_dir, safe_symbol(symbol), timeframe) is None:
        raise ResearchError(
            "RESEARCH_SYMBOL_NOT_FOUND",
            f"无数据: {symbol} {timeframe}",
            detail={"symbol": symbol, "timeframe": timeframe})
    df = load_and_prepare_df(
        data_dir, symbol, timeframe,
        data_start=start, data_end=end, compute_indicators=False)
    if df is None or df.empty:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            f"区间数据不足: {symbol} {timeframe} [{start}, {end})")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    from core.features.indicators import _compute_indicator
    for helper in _REGIME_HELPER_NAMES:
        for col, series in _compute_indicator(
                df, helper, default_params_for(helper)).items():
            df[col] = series
    adx_col = next(c for c in df.columns if c.startswith("adx_"))
    atr_col = next(c for c in df.columns if c.startswith("atr_"))
    raw = build_segments(df, adx_col, atr_col)
    if not raw:
        raise ResearchError(
            "RESEARCH_DATA_INSUFFICIENT",
            "区间太短切不出段（机械判读未产出任何段）",
            detail={"symbol": symbol, "timeframe": timeframe})
    return [{"label": seg["type"], "start": seg["start"], "end": seg["end"],
             "bars": int(seg["bars"]),
             "adx_mean": seg.get("adx_mean"),
             "atr_pct_mean": seg.get("atr_pct_mean"),
             "atr_pct_pct_mean": seg.get("atr_pct_pct_mean")}
            for seg in raw]


def run_staged_validation(
    db_path: Path,
    data_dir: Path,
    *,
    strategy_id: str,
    hypothesis_id: Optional[str] = None,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    segmentation: str = "auto",
    segments: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """分段验证（检查⑤的分题型成绩单）。

    Args:
        strategy_id: 策略（不在册拒 STRATEGY_NOT_FOUND）。
        hypothesis_id: 可缺省——挂假设走完整证据路径（终态拒验/幂等/
            并集/扫描复用 + verdict 记录）；缺省 adhoc 纯日志留痕。
        symbol / timeframe / start / end: 请求区间（半开 UTC）。
        segmentation: "auto"（机械判读）或 "explicit"；与 segments
            同传拒（双模互斥）。
        segments: explicit 段列表 [{label, start, end}]（恰好覆盖请求
            区间、相接不重叠、升序）。

    Returns:
        {record_id, verdict, segments[], summary{worst_segment,
        spread, all_pass}, segmentation, judged_ranges}——verdict 三态
        {pass, fail, inconclusive}（最差聚合；全段样本不足=inconclusive，
        None 特例已退役）；record_id 挂假设时存在（inconclusive 记录
        record_id 照常但不可被 transition 引用——证据不足不可定论）。

    Raises:
        ResearchError: RESEARCH_STRATEGY_NOT_FOUND /
        RESEARCH_HYPOTHESIS_NOT_FOUND / RESEARCH_HYPOTHESIS_TERMINAL /
        RESEARCH_RANGE_OVERLAP / SCREEN_CANDIDATE_REUSE /
        RESEARCH_PARAM_INVALID（段列表非法/双模互斥/枚举）/
        RESEARCH_DATA_INSUFFICIENT（数据不足/切不出段）。
    """
    # ---- 校验链（固定顺序：枚举/互斥 → 挂假设前置 → 策略 → 段） ----
    if segmentation not in _SEGMENTATION_MODES:
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            f"segmentation 须为 auto 或 explicit: {segmentation!r}",
            detail={"param": "segmentation", "value": segmentation,
                    "legal_values": sorted(_SEGMENTATION_MODES)})
    if segments is not None and segmentation == "auto":
        raise ResearchError(
            "RESEARCH_PARAM_INVALID",
            "双模互斥：auto 模式（或缺省）不得同时传 segments",
            detail={"param": "segments",
                    "hint": "显式分段须 segmentation='explicit'"})

    request_range = {"symbol": symbol, "timeframe": timeframe,
                     "start": start, "end": end}
    rh = _range_hash(request_range)

    judged: List[Dict[str, str]] = []
    if hypothesis_id is not None:
        # 验证族前置检查四步（isolated 同款——同构提取，幂等命中即返）
        # G7 五元幂等：explicit 摘要依赖构造后的段清单——原位幂等
        # 后置（idem_level=None 跳过），段清单就绪后按下述查询补做
        dup = _hypothesis_precheck(db_path, hypothesis_id, strategy_id, rh,
                                   request_range, idem_level=None)
        if dup is not None:
            return dup

    # ---- 策略与 warmup ----
    dna, strat_row = _load_strategy_dna(db_path, strategy_id)
    try:
        warmup_bars, _indicator_names = _derive_dna_warmup(
            db_path, dna, timeframe)  # R3：消费轴（请求周期）
    except KeyError as exc:
        raise ResearchError("RESEARCH_CONDITION_INVALID", str(exc)) from exc

    # ---- 段清单（双模统一结构） ----
    if segmentation == "explicit":
        seg_list = _validate_explicit_segments(
            segments, start, end)
    else:
        seg_list = _auto_segments(data_dir, symbol, timeframe, start, end)
        # 短段×基因级高周期预检（fix-review B8 / 裁决 B）：auto 短段
        # 与随机短窗同根因，段清单就绪后逐段预检（不足段整体拒绝；
        # explicit 段用户自担设计走既有 fail-loud 兜底）
        import math as _math
        from core.data.timeframes import TF_SECONDS as _TF_SEC
        _base_sec = _TF_SEC.get(timeframe)
        for seg in seg_list:
            seg_bars = seg.get("bars")
            if (seg_bars is None and _base_sec
                    and seg.get("start") and seg.get("end")):
                _span_sec = (
                    to_utc(pd.Timestamp(seg["end"]))
                    - to_utc(pd.Timestamp(seg["start"]))).total_seconds()
                seg_bars = _math.ceil(_span_sec / _base_sec)
            if seg_bars:
                _reject_short_window_for_gene_tfs(
                    dna, int(seg_bars), timeframe,
                    scope=f"分段 {seg.get('label', '?')}")

    # G7 五元幂等（后置补做）：段清单就绪后按分段身份查询——同区间
    # 不同分段方案（auto / 不同 explicit 清单）各自独立命中
    if hypothesis_id is not None:
        existing = rr.find_validation_by_identity(
            db_path, hypothesis_id, strategy_id, rh,
            level="staged_validation",
            segmentation=segmentation_identity(segmentation, seg_list))
        if existing is not None:
            # staged 幂等：专有键经入口参数化（与首次同构；result 内
            # 嵌键由写入侧补齐保证——存量记录缺键时入口值兜底）
            return _dup_payload(
                existing, db_path=db_path, hypothesis_id=hypothesis_id,
                entry_ctx={"strategy_id": strategy_id,
                           "hypothesis_id": hypothesis_id,
                           "segmentation": segmentation,
                           "request_range": request_range,
                           "warmup_bars": int(warmup_bars)})

    # ---- 逐段回测与段级门槛 ----
    seg_results: List[Dict[str, Any]] = []
    for seg in seg_list:
        seg_start = seg["start"].isoformat()
        seg_end = seg["end"].isoformat()
        entry: Dict[str, Any] = {
            "label": seg["label"], "start": seg_start, "end": seg_end,
            "bars": seg.get("bars"),
        }
        for stat_key in ("adx_mean", "atr_pct_mean", "atr_pct_pct_mean"):
            if seg.get(stat_key) is not None:
                entry[stat_key] = seg[stat_key]
        # 段回测可行性由 warmup 前扩自然判定（前缀不足=请求区间整体
        # 前缀不足，第一段即暴露——isolated 同款语义）；段级判定统一
        # 走 judge 三段闸门（0.2.11 Q1a：样本不足段判 inconclusive，
        # 原前置预判与闸门同条件重叠已删；threshold_details 恒带——
        # 三态都有明细供归因）
        metrics, seg_returns, _actual_warmup, bars_used = run_backtest_on_range(
            db_path, data_dir, dna, symbol, timeframe,
            seg_start, seg_end, warmup_bars, strategy_id=strategy_id)
        entry["metrics"] = metrics
        entry["bars_used"] = int(bars_used)
        seg_verdict, seg_details = judge(metrics,
                                         trade_returns=seg_returns)
        entry["segment_verdict"] = seg_verdict
        entry["threshold_details"] = seg_details
        seg_results.append(entry)

    # ---- 汇总与 verdict 映射（最差聚合三态，与随机窗口同族） ----
    # worst/spread=描述统计（全部段含 inconclusive 段按 total_return 取）；
    # all_pass=判定统计（严格全 pass）——两口径并存各有其义，无 None 特例
    verdict = _aggregate_verdicts(r["segment_verdict"] for r in seg_results)
    worst = min(seg_results, key=lambda r: r["metrics"]["total_return"])
    best = max(seg_results, key=lambda r: r["metrics"]["total_return"])
    summary = {
        "worst_segment": {"label": worst["label"],
                          "total_return": worst["metrics"]["total_return"]},
        "spread": best["metrics"]["total_return"]
        - worst["metrics"]["total_return"],
        "all_pass": verdict == "pass",
    }

    # ---- 写入双路径 ----
    record_id: Optional[str] = None
    if hypothesis_id is not None:
        record_id = f"rec_{uuid.uuid4().hex[:12]}"
        rr.save_validation_with_log(
            db_path, record_id=record_id, hypothesis_id=hypothesis_id,
            strategy_id=strategy_id, level="staged_validation",
            segmentation=segmentation_identity(segmentation, seg_list),
            params=json.dumps({
                "request_range": request_range, "range_hash": rh,
                "segmentation": segmentation, "segments": [
                    {"label": seg["label"],
                     "start": seg["start"].isoformat()
                     if hasattr(seg["start"], "isoformat") else seg["start"],
                     "end": seg["end"].isoformat()
                     if hasattr(seg["end"], "isoformat") else seg["end"]}
                    for seg in seg_list]},
                ensure_ascii=False),
            result=json.dumps({"segments": seg_results, "summary": summary,
                               # 方向披露（add-direction-channel A4）：
                               # 纯增量键，幂等重放提取源
                               "direction": dna.risk_genes.direction,
                               # 写入侧补齐不变量（unify-tool-return-
                               # contract）：result_json 内嵌完整首次
                               # payload 键——幂等重放数据源完备
                               "request_range": request_range,
                               "warmup_bars": int(warmup_bars)},
                              ensure_ascii=False),
            verdict=verdict, range_hash=rh)
    else:
        # adhoc（或无 verdict）纯日志留痕——验证记录 hypothesis_id 为
        # 非空外键，无挂靠物理不产生记录；verdict 照报、record_id=None
        rr.append_research_log(
            db_path, hypothesis_id=hypothesis_id,
            entry_type="staged_validation",
            data_ranges=json.dumps(request_range, ensure_ascii=False),
            summary=json.dumps({"segments": [
                {"label": r["label"], "verdict": r["segment_verdict"]}
                for r in seg_results], "summary": summary},
                ensure_ascii=False))
    if hypothesis_id is not None:
        judged = judged_ranges_union(db_path, hypothesis_id)

    # 统一返回体（unify-tool-return-contract 规则①）：经单一映射函数
    # 与幂等分支同构（staged 专有键由 base 参数化；duplicated 纯增量
    # 键使两入口返回体形制一致）
    return _validation_payload(
        {"record_id": record_id, "verdict": verdict, "duplicated": False,
         "strategy_id": strategy_id, "hypothesis_id": hypothesis_id,
         "segmentation": segmentation, "request_range": request_range,
         "warmup_bars": int(warmup_bars)},
        {"segments": seg_results, "summary": summary,
         "direction": dna.risk_genes.direction},
        judged=judged)


def query_union_balance(
    db_path: Path, data_dir: Path, *,
    symbol: str, timeframe: str, hypothesis_id: str,
) -> Dict[str, Any]:
    """判定并集余量只读查询（0.4.1 R4）——账本全貌/剩余区间/保守折算。

    前置判定 ``get_hypothesis`` None → 404（judged_ranges_union 对不存在
    假设静默返回空——查询方必须自判，不得以空清单伪装成功）。账本=
    ``judged_ranges_union`` 直调（单一事实源，禁第二套汇总）。数据范围=
    ``find_parquet`` 定位 + 首尾 bar 完整时间戳直读（不走 HTTP
    available-sources——其时间边界日级 ``[:10]`` 截断精度不足以做区间
    差集）；范围=``[首 bar, 尾 bar+1×TF)`` 半开与 judged 对齐。remaining=
    数据范围−judged（先合并重叠/相邻再逐段差）；remaining_bars=Σ 每段
    ``floor(段时长/TF_SECONDS)``（不足整 bar 残余丢弃——保守计数）。

    零副作用：不落日志不写库（查询不是研究决策证据，与元数据查询
    同级）；GET 语义天然幂等。两出口一致锚：查询 remaining 段发起隔离
    验证不被 RESEARCH_RANGE_OVERLAP 拒（同一账本装配）。
    """
    from core.data.mtf_loader import find_parquet, safe_symbol
    from core.data.storage import load_parquet
    from core.data.timeframes import TF_SECONDS

    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError(
            "RESEARCH_HYPOTHESIS_NOT_FOUND",
            f"假设不存在: {hypothesis_id}",
            status=404, detail={"hypothesis_id": hypothesis_id})
    judged = judged_ranges_union(db_path, hypothesis_id)

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

    tf_seconds = TF_SECONDS[timeframe]
    # 数据范围半开 [首 bar, 尾 bar+1×TF)——尾 bar 自身占一个完整周期
    span_end = data_end + pd.Timedelta(seconds=tf_seconds)

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
