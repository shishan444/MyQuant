#!/usr/bin/env python
"""存量列引用一次性迁移（0.2.13 unify-column-references B5）。

两阶段原子形制：
- 阶段一预检（零写入）：全量扫描存量（假设全部状态 + 模型插件
  status=valid），每条 subject/feature 逐项 decode + 往返一致锁
  （encode(decode(x)) == x 才可迁），产出核对清单与阻塞清单；
  **有阻塞即整体退出零写入**（研究库小，原子性优于断点续跑）。
- 阶段二执行：单事务批量写——conditions canonical 化 + conditions_hash
  重算（与登记同函数路径）；ModelPlugin config features 迁移 + _config_hash
  重算（与注册同 hash 函数）。

用法：
    python scripts/migrate_column_refs.py <db_path> [--dry-run] [--out 清单路径]
历史留痕（research_log / 验证证据记录）不改写（Q1）；rejected 模型 config
为失败留痕不迁（清单列「不适用」）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.analysis.column_ref import (  # noqa: E402
    ColumnRefError, decode_column_ref, encode_column_ref, resolve_column_ref)


def _hash_json(obj) -> str:
    """与 hypothesis_service/_hash_json 同构（幂等键连续——同函数语义）。"""
    import hashlib
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _migrate_ref(ref, ctx: list):
    """单个引用迁移：字符串编码形 → 结构化；其他原样。

    ctx 收集阻塞项（id, 原值, 原因）。返回迁移后值（不可迁时返回 None
    并入 ctx——调用方按预检结果整体放弃）。
    """
    if not isinstance(ref, str):
        return ref                                    # 已结构化（幂等重跑）
    try:
        decoded = decode_column_ref(ref)
    except ColumnRefError as exc:
        ctx.append((exc.code, f"{exc.message}（歧义拒绝）"))
        return None
    if decoded is None:
        # 编码形特征（字母前缀+至少一段数字）但逆解失败 → 阻塞不猜测；
        # 纯名称串（基础列/插件列）直迁
        import re as _re
        if _re.fullmatch(r"[a-z][a-z0-9]*(?:_[\d.]+)+", ref):
            ctx.append(("UNPARSEABLE", f"形似指标编码但无法逆解: {ref!r}"))
            return None
        return ref
    try:
        if encode_column_ref(decoded["indicator"], decoded["params"],
                             decoded["field"]) != ref:
            ctx.append(("ROUNDTRIP", f"往返不一致: {ref!r}"))
            return None
        # canonical 化（与登记同函数——ref_dict 数值归一 + field 注入）
        return resolve_column_ref(decoded).ref_dict()
    except ColumnRefError as exc:
        ctx.append((exc.code, exc.message))
        return None


def _scan_hypotheses(db_path: Path):
    """阶段一：假设预检 → (迁移计划, 阻塞清单, 核对清单)。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT hypothesis_id, status, conditions, conditions_hash "
        "FROM research_hypotheses").fetchall()
    conn.close()

    plan, blocked, audit = [], [], []
    for hid, status, cond_json, old_hash in rows:
        try:
            conditions = json.loads(cond_json)
        except (json.JSONDecodeError, TypeError):
            blocked.append({"id": hid, "item": "<conditions>",
                            "reason": "conditions JSON 不可解析"})
            continue
        new_conditions = json.loads(json.dumps(conditions))   # 深拷贝
        item_ctx: list = []
        for group in ("when_conditions", "then_conditions"):
            for i, c in enumerate(new_conditions.get(group, [])
                                  if isinstance(new_conditions, dict) else []):
                if not isinstance(c, dict):
                    continue
                new_subject = _migrate_ref(c.get("subject"), item_ctx)
                if new_subject is None:
                    blocked.append({
                        "id": hid, "item": f"{group}[{i}].subject",
                        "reason": "; ".join(r for _, r in item_ctx)})
                    item_ctx = []
                    continue
                c["subject"] = new_subject
        if any(b["id"] == hid for b in blocked):
            continue
        new_hash = _hash_json(new_conditions)
        audit.append({"id": hid, "status": status,
                      "old_hash": old_hash, "new_hash": new_hash,
                      "subjects": [
                          c.get("subject")
                          for g in ("when_conditions", "then_conditions")
                          for c in (conditions.get(g, [])
                                    if isinstance(conditions, dict) else [])]})
        if new_conditions != conditions:
            plan.append((hid, json.dumps(new_conditions, ensure_ascii=False),
                         new_hash))
    return plan, blocked, audit


def _scan_model_plugins(db_path: Path):
    """阶段一：模型 config 预检（仅 status=valid；rejected=失败留痕不迁）。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT plugin_id, status, config FROM model_plugins").fetchall()
    conn.close()

    plan, blocked, audit, skipped = [], [], [], []
    for pid, status, cfg_json in rows:
        if status != "valid":
            skipped.append({"id": pid, "reason": f"不适用（{status}）"})
            continue
        try:
            cfg = json.loads(cfg_json)
        except (json.JSONDecodeError, TypeError):
            blocked.append({"id": pid, "item": "<config>",
                            "reason": "config JSON 不可解析"})
            continue
        features = cfg.get("features")
        if not isinstance(features, list):
            skipped.append({"id": pid, "reason": "不适用（无 features）"})
            continue
        item_ctx: list = []
        new_features = []
        for i, f in enumerate(features):
            nf = _migrate_ref(f, item_ctx)
            if nf is None:
                blocked.append({"id": pid, "item": f"features[{i}]",
                                "reason": "; ".join(r for _, r in item_ctx)})
                item_ctx = []
                continue
            new_features.append(nf)
        if any(b["id"] == pid for b in blocked):
            continue
        old_hash = cfg.get("_config_hash")
        cfg["features"] = new_features
        # _config_hash 重算：与注册同构（name+features+template+params+target
        # +symbol+timeframe+data——迁移只改 features，重算用同键集）
        cfg["_config_hash"] = _hash_json({
            k: cfg.get(k) for k in (
                "name", "features", "template", "params", "target",
                "symbol", "timeframe", "data")})
        audit.append({"id": pid, "old_hash": old_hash,
                      "new_hash": cfg["_config_hash"],
                      "features": new_features})
        if new_features != features:
            plan.append((pid, json.dumps(cfg, ensure_ascii=False)))
    return plan, blocked, audit, skipped


def _execute(db_path: Path, hyp_plan, model_plan) -> int:
    """阶段二：单事务批量写（预检零阻塞才可达）。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        with conn:                                  # 单事务（异常整体回滚）
            for hid, cond_json, new_hash in hyp_plan:
                conn.execute(
                    "UPDATE research_hypotheses SET conditions=?, "
                    "conditions_hash=? WHERE hypothesis_id=?",
                    (cond_json, new_hash, hid))
            for pid, cfg_json in model_plan:
                conn.execute(
                    "UPDATE model_plugins SET config=? WHERE plugin_id=?",
                    (cfg_json, pid))
    finally:
        conn.close()
    return len(hyp_plan) + len(model_plan)


def main() -> int:
    ap = argparse.ArgumentParser(description="存量列引用一次性迁移（0.2.13）")
    ap.add_argument("db_path", type=Path)
    ap.add_argument("--dry-run", action="store_true",
                    help="仅预检（零写入），输出核对与阻塞清单")
    ap.add_argument("--out", type=Path, default=None,
                    help="核对清单输出路径（JSON，默认 stdout 摘要）")
    args = ap.parse_args()

    if not args.db_path.exists():
        print(f"库不存在: {args.db_path}")
        return 2

    hyp_plan, hyp_blocked, hyp_audit = _scan_hypotheses(args.db_path)
    model_plan, model_blocked, model_audit, model_skipped = \
        _scan_model_plugins(args.db_path)
    blocked = hyp_blocked + model_blocked

    report = {
        "phase": "dry-run" if args.dry_run else "precheck",
        "hypotheses": {"total": len(hyp_audit), "to_migrate": len(hyp_plan),
                       "audit": hyp_audit, "blocked": hyp_blocked},
        "model_plugins": {"valid": len(model_audit),
                          "to_migrate": len(model_plan),
                          "audit": model_audit, "blocked": model_blocked,
                          "skipped_not_applicable": model_skipped},
    }
    if args.out:
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"预检：假设 {len(hyp_audit)} 条（待迁 {len(hyp_plan)}）、"
          f"模型 valid {len(model_audit)} 条（待迁 {len(model_plan)}）、"
          f"不适用 {len(model_skipped)} 条、阻塞 {len(blocked)} 条")
    for b in blocked:
        print(f"  BLOCKED {b['id']} {b.get('item', '')}: {b['reason']}")

    if blocked:
        print("存在阻塞——整体退出零写入（人工处理阻塞项后重跑，幂等安全）")
        return 1
    if args.dry_run:
        print("dry-run 完成（零写入）")
        return 0

    n = _execute(args.db_path, hyp_plan, model_plan)
    print(f"迁移完成：{n} 条写入（单事务）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
