"""退役写入通道后的验证记录 SQL 直插工厂（retire-legacy 任务 5.6 口径）。

retire-legacy-subsystems 终波扩展：假设/策略构造改 SQL 直插/仓储直插
（register_hypothesis/translate_hypothesis 已退役，仅作夹具手段的
构造链随之改造——守护对象存活的用例改走本工厂，断言零改动）。
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from core.persistence.orm.engine import engine_for

_HYP_FIXED_TIME = "2026-11-01T00:00:00"


def _dump(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True)


def _hash16(hypothesis_id: str, payload: str) -> str:
    """hash 列填值：掺 hypothesis_id 保证夹具行间不撞 active 唯一索引。"""
    return hashlib.sha256(
        f"{hypothesis_id}:{payload}".encode("utf-8")).hexdigest()[:16]


def insert_hypothesis(
    db: Path, *,
    hypothesis_id: Optional[str] = None,
    status: str = "active",
    conditions: Optional[Dict[str, Any]] = None,
    data_ranges: Optional[List[Dict[str, Any]]] = None,
    strategy_params: Optional[Dict[str, Any]] = None,
    statement: str = "s",
    created_at: str = _HYP_FIXED_TIME,
    updated_at: str = _HYP_FIXED_TIME,
    **_retired_kwargs: Any,
) -> str:
    """SQL 直插 research_hypotheses（register_hypothesis 已退役的夹具替代）。

    conditions 缺省形制对齐登记面冻结值（when/then 组键）；conditions/
    data_ranges/strategy_params 传 dict/list 自动 json.dumps，传 str 原样
    直通。conditions_hash/ranges_hash 仅填 NOT NULL 列（掺 id 的 sha256
    前 16 位），不承载身份判定语义。

    ``_retired_kwargs``：登记面专属顶层键（direction/exit_logic/
    trigger_policy 等）随登记域退役无消费方——静默吞掉保持旧夹具
    调用兼容，需要这些语义进产物 DNA 的用例自行手构 StrategyDNA。

    hypothesis_id 缺省 None=自动生成（对齐原 register 自动 id 形制，
    同库多次插入不撞主键）。
    """
    if hypothesis_id is None:
        import uuid as _uuid
        hypothesis_id = f"hyp_{_uuid.uuid4().hex[:12]}"
    if conditions is None:
        conditions = {"when": [], "then": []}
    if data_ranges is None:
        data_ranges = [{
            "symbol": "BTCUSDT", "timeframe": "1h",
            "start": "2026-01-01T00:00:00", "end": "2026-02-01T00:00:00",
        }]
    cond_str = _dump(conditions)
    ranges_str = _dump(data_ranges)
    params_str = (
        _dump(strategy_params)
        if strategy_params is not None else None)
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
            " conditions_hash, statement, data_ranges, ranges_hash, status,"
            " created_at, updated_at, strategy_params)"
            " VALUES (:h, :c, :ch, :st, :dr, :rh, :s, :ca, :ua, :sp)"),
            {"h": hypothesis_id, "c": cond_str,
             "ch": _hash16(hypothesis_id, cond_str), "st": statement,
             "dr": ranges_str,
             "rh": _hash16(hypothesis_id, ranges_str), "s": status,
             "ca": created_at, "ua": updated_at, "sp": params_str})
    return hypothesis_id


def insert_strategy_from_hypothesis(
    db: Path, sid: str, hypothesis_id: str, dna: Any, *,
    status: str = "draft",
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
) -> str:
    """按假设血缘直插策略行（translate_hypothesis 退役后的夹具替代）。

    走 repository save_strategy 正规通道（含 gene_signature 推导与
    draft 免去重语义）；dna 传 dict 自动序列化，传 str 原样直通。
    """
    from core.persistence.repository.strategy import save_strategy
    dna_json = dna if isinstance(dna, str) else json.dumps(
        dna, ensure_ascii=False, sort_keys=True)
    save_strategy(
        db, strategy_id=sid, dna_json=dna_json, symbol=symbol,
        timeframe=timeframe, parent_ids=hypothesis_id, status=status)
    return sid


def insert_validation_record(
    db: Path, *,
    record_id: str,
    hypothesis_id: str,
    strategy_id: Optional[str],
    level: str,
    params: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    verdict: str = "",
    segmentation: str = "",
    range_hash: str = "",
    created_at: str = "2026-11-01T00:00:00",
) -> str:
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, params, result, verdict,"
            " range_hash, created_at) VALUES (:r, :h, :s, :l, :g, :p,"
            " :v, :w, :x, :c)"),
            {"r": record_id, "h": hypothesis_id, "s": strategy_id,
             "l": level, "g": segmentation,
             "p": json.dumps(params, ensure_ascii=False)
             if isinstance(params, dict) else params,
             "v": json.dumps(result, ensure_ascii=False)
             if isinstance(result, dict) else result,
             "w": verdict, "x": range_hash, "c": created_at})
    return record_id
