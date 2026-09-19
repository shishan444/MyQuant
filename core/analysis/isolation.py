"""验证区间隔离判定（add-ai-research-mcp 3.2 核心）。

判定区间 = 假设自报 data_ranges ∪ 该假设关联的全部 research_log
.data_ranges（并集）——强制自动记录的日志拦截漏报，宁可多拒不可
漏拦（spec research-validation-protocol）。请求区间与判定并集求交，
非空即拒并列重叠段。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.persistence.repository import research as rr


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iter_ranges(raw: str | None) -> List[Tuple[datetime, datetime, str]]:
    """解析 data_ranges JSON（容忍两种条目形：{start,end[,...]}）。"""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and "start" in it and "end" in it:
            try:
                out.append((_parse_ts(it["start"]), _parse_ts(it["end"]), str(it)))
            except (ValueError, TypeError):
                continue
    return out


def judged_ranges_union(db_path: Path, hypothesis_id: str) -> List[Dict[str, str]]:
    """判定并集：自报区间 ∪ 关联日志区间（去重合并相邻可不做——并集语义）。"""
    merged: List[Tuple[datetime, datetime, str]] = []
    hyp = rr.get_hypothesis(db_path, hypothesis_id)
    if hyp is not None:
        merged.extend(_iter_ranges(hyp["data_ranges"]))
    for entry in rr.list_research_log(db_path, hypothesis_id=hypothesis_id, limit=200):
        merged.extend(_iter_ranges(entry.get("data_ranges")))
    # 稳定排序去重
    seen = set()
    out = []
    for start, end, raw in sorted(merged, key=lambda x: (x[0], x[1])):
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        out.append({"start": start.isoformat(), "end": end.isoformat()})
    return out


def overlaps_with_request(
    judged: List[Dict[str, str]], request_start: str, request_end: str,
) -> List[Dict[str, str]]:
    """请求半开区间与判定并集的交（重叠段列表；空 = 干净放行）。"""
    rs, re_ = _parse_ts(request_start), _parse_ts(request_end)
    hits = []
    for r in judged:
        s, e = _parse_ts(r["start"]), _parse_ts(r["end"])
        lo, hi = max(rs, s), min(re_, e)
        if lo < hi:  # 半开区间相交
            hits.append({"start": lo.isoformat(), "end": hi.isoformat(),
                         "judged_range": r})
    return hits
