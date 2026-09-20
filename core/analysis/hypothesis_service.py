"""假设登记与迁移用例（add-ai-research-mcp 3.1）。

登记校验链（顺序即拒绝优先级）：
1. conditions 结构（WHEN/THEN 形制，逐条 {subject, action, target}）
   → 422 RESEARCH_CONDITION_INVALID（附位置）
2. subject 引用必须落在可用信号集（OHLCV/衍生品基础列 ∪ 内置指标
   输出列形制 ∪ 插件列）→ 422 RESEARCH_CONDITION_INVALID
3. data_ranges 非空且每条 {symbol, timeframe, start, end} 半开
   → 422 RESEARCH_RANGE_INVALID；symbol/timeframe 在数据目录
   → 422 RESEARCH_SYMBOL_NOT_FOUND
4. 幂等键 (conditions_hash, ranges_hash) WHERE active：重复登记
   返回既有 id（duplicated 标记，不重复写）

迁移（transition）：active --证据/接替ref--> supported|refuted|superseded；
终态拒绝（409 RESEARCH_HYPOTHESIS_TERMINAL）；条件更新先到者胜
（rowcount 0 → 重查判读：同目标幂等 / 异终态 409）。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.persistence.repository import research as rr
from core.persistence.timestamps import utc_now_iso


class ResearchError(Exception):
    """研究域错误：code + http_status + message + detail（路由层转 HTTPException）。"""

    def __init__(self, code: str, message: str, *, status: int = 422, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


def _hash_json(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------- 信号集

# 条件 subject 可引用的基础列（假设引擎 _resolve_subject 的列语义）
_BASE_SUBJECT_COLUMNS = frozenset({
    "open", "high", "low", "close", "volume", "trades",
    "open_interest", "funding_rate",
})


def allowed_subject_columns(db_path: Path) -> Tuple[set, set]:
    """subject 可引用列：(精确列全集, 内置指标输出列前缀集)。

    内置指标输出列是参数化形制（如 ema_50）——不枚举全部参数组合，
    以前缀集合 + 「前缀_数字」正则判定。插件列来自 signal_registry
    （精确名，入精确列全集）。
    """
    from core.features.registry import INDICATOR_REGISTRY

    cols = set(_BASE_SUBJECT_COLUMNS)
    builtin_prefixes = set()
    for defn in INDICATOR_REGISTRY.values():
        for f in defn.output_fields:
            builtin_prefixes.add(f)
    cols.update(builtin_prefixes)

    for entry in rr.list_signal_registry_entries(db_path, only_visible=True):
        cols.add(entry["column_name"])
    return cols, builtin_prefixes


def _subject_exists(subject: str, allowed: set[str], builtin_prefixes: set[str]) -> bool:
    """subject 合法性：精确命中，或「内置前缀_数字」形制（指标输出列）。"""
    if subject in allowed:
        return True
    for prefix in builtin_prefixes:
        if re.fullmatch(rf"{re.escape(prefix)}_\d+", subject):
            return True
    return False


# ---------------------------------------------------------------- 登记

def register_hypothesis(
    db_path: Path,
    *,
    conditions: Dict[str, Any],
    statement: str,
    data_ranges: List[Dict[str, str]],
    hypothesis_id: Optional[str] = None,
) -> Dict[str, Any]:
    """登记假设（校验链 + 幂等 + 原子双写）。

    Returns:
        {"hypothesis_id", "duplicated": bool}——duplicated=True 表示命中
        幂等键返回既有假设。
    """
    # 1. conditions 结构
    when = conditions.get("when_conditions")
    then = conditions.get("then_conditions")
    if not isinstance(when, list) or not when:
        raise ResearchError("RESEARCH_CONDITION_INVALID", "when_conditions 须为非空列表",
                            detail={"field": "when_conditions"})
    if not isinstance(then, list) or not then:
        raise ResearchError("RESEARCH_CONDITION_INVALID", "then_conditions 须为非空列表",
                            detail={"field": "then_conditions"})
    for group, conds in (("when", when), ("then", then)):
        for i, c in enumerate(conds):
            field_name = f"{group}_conditions[{i}]"
            if not isinstance(c, dict) or not all(k in c for k in ("subject", "action", "target")):
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{field_name} 须含 subject/action/target",
                    detail={"field": field_name})

    # 2. subject 引用
    allowed, builtin_prefixes = allowed_subject_columns(db_path)
    for group, conds in (("when", when), ("then", then)):
        for i, c in enumerate(conds):
            field_name = f"{group}_conditions[{i}].subject"
            if not _subject_exists(c["subject"], allowed, builtin_prefixes):
                raise ResearchError(
                    "RESEARCH_CONDITION_INVALID",
                    f"{field_name} 引用不存在的信号列: {c['subject']}",
                    detail={"field": field_name,
                            "subject": c["subject"],
                            "hint": "可用列经 GET /api/research/signals 查询"})

    # 3. 区间
    if not isinstance(data_ranges, list) or not data_ranges:
        raise ResearchError("RESEARCH_RANGE_INVALID", "data_ranges 须为非空列表")
    for i, r in enumerate(data_ranges):
        field_name = f"data_ranges[{i}]"
        if not isinstance(r, dict) or not all(k in r for k in ("symbol", "timeframe", "start", "end")):
            raise ResearchError("RESEARCH_RANGE_INVALID",
                                f"{field_name} 须含 symbol/timeframe/start/end",
                                detail={"field": field_name})
        if not (r["start"] < r["end"]):
            raise ResearchError("RESEARCH_RANGE_INVALID",
                                f"{field_name} 非半开区间（start >= end）",
                                detail={"field": field_name})
    # 数据目录可达性在路由层校验（需 data_dir；此处只做结构）——见路由

    # 4. 幂等
    conditions_hash = _hash_json(conditions)
    ranges_hash = _hash_json(data_ranges)
    existing = rr.find_active_hypothesis_by_identity(db_path, conditions_hash, ranges_hash)
    if existing is not None:
        return {"hypothesis_id": existing["hypothesis_id"], "duplicated": True}

    # 5. 原子双写
    import uuid
    hid = hypothesis_id or f"hyp_{uuid.uuid4().hex[:12]}"
    rr.save_hypothesis_with_log(
        db_path,
        hypothesis_id=hid,
        conditions=json.dumps(conditions, ensure_ascii=False),
        conditions_hash=conditions_hash,
        statement=statement,
        data_ranges=json.dumps(data_ranges, ensure_ascii=False),
        ranges_hash=ranges_hash,
    )
    return {"hypothesis_id": hid, "duplicated": False}


# ---------------------------------------------------------------- 迁移

_TERMINAL = ("supported", "refuted", "superseded")
_VALID_TARGETS = _TERMINAL


def transition_hypothesis(
    db_path: Path,
    hypothesis_id: str,
    *,
    target_status: str,
    evidence_ref: str,
) -> Dict[str, Any]:
    """状态迁移：证据/接替引用校验 + 条件更新 + 幂等语义。

    Returns:
        {"hypothesis_id", "status", "idempotent": bool}
    """
    if target_status not in _VALID_TARGETS:
        raise ResearchError("RESEARCH_TRANSITION_INVALID",
                            f"非法目标态: {target_status}（合法: {_VALID_TARGETS}）")
    if not evidence_ref:
        raise ResearchError("RESEARCH_EVIDENCE_NOT_FOUND", "迁移必带证据引用")

    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is None:
        raise ResearchError("RESEARCH_HYPOTHESIS_NOT_FOUND", f"假设不存在: {hypothesis_id}",
                            status=404)

    # 证据引用真实性：superseded 指接替假设；supported/refuted 指验证记录
    if target_status == "superseded":
        if rr.get_hypothesis(db_path, evidence_ref) is None:
            raise ResearchError("RESEARCH_EVIDENCE_NOT_FOUND",
                                f"接替假设不存在: {evidence_ref}")
    else:
        records = rr.list_validation_records(
            db_path, hypothesis_id=hypothesis_id, limit=200)
        if not any(r["record_id"] == evidence_ref for r in records):
            raise ResearchError(
                "RESEARCH_EVIDENCE_NOT_FOUND",
                f"证据验证记录不存在或不属于该假设: {evidence_ref}")

    if hyp["status"] in _TERMINAL:
        if hyp["status"] == target_status and hyp["terminal_ref"] == evidence_ref:
            return {"hypothesis_id": hypothesis_id, "status": target_status, "idempotent": True}
        raise ResearchError("RESEARCH_HYPOTHESIS_TERMINAL",
                            f"假设已终态（{hyp['status']}），不可再迁移",
                            status=409)

    n = rr.transition_hypothesis(
        db_path, hypothesis_id,
        target_status=target_status,
        terminal_ref=evidence_ref,
        superseded_by=evidence_ref if target_status == "superseded" else None,
    )
    if n == 0:
        # 并发竞争：另一请求先迁移——重查判读
        fresh = rr.get_hypothesis(db_path, hypothesis_id)
        if fresh is not None and fresh["status"] == target_status:
            return {"hypothesis_id": hypothesis_id, "status": target_status, "idempotent": True}
        raise ResearchError("RESEARCH_HYPOTHESIS_TERMINAL",
                            f"并发迁移冲突：假设现为 {fresh['status'] if fresh else '缺失'}",
                            status=409)
    return {"hypothesis_id": hypothesis_id, "status": target_status, "idempotent": False}
