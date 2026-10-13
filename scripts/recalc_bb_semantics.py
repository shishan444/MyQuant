"""布林带历史存量全量重算脚本（fix-research-c5-bb-legacy-recalc）。

修复背景：C1 批次修复 BB std 吞参后，历史存量数字仍为旧语义（std≠2
实按 2.0 计算）。本脚本对五类资产执行「枚举→筛选→重算→对照→分层
处置→导出」：策略进化行与关联回测行回写正确数字（状态字段/结论
不动），快照与扫描存档只对照不回写，处置全程 research_log 留痕
（entry_type=recalc_bb，不可变追加）。

用法（仓库根执行）：
  venv/bin/python scripts/recalc_bb_semantics.py audit   --db data/quant.db --out scripts/recalc_out
  venv/bin/python scripts/recalc_bb_semantics.py recalc  --db data/quant.db --data-dir data/market --out scripts/recalc_out [--dry-run]
  venv/bin/python scripts/recalc_bb_semantics.py export  --db data/quant.db --out scripts/recalc_out

三层保障：回写前全量旧值备份（JSON，回滚依据）；幂等断点续跑
（research_log 已有该资产 recalc_bb 行即跳过）；单资产单事务
（重算→对照→回写同事务，异常整体回滚）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BB_GENE_RE = re.compile(r"bb_(upper|middle|lower)_(\d+)_([0-9.]+)")
NUMERIC_COLS = ("total_return", "sharpe_ratio", "max_drawdown",
                "win_rate", "total_trades", "total_score", "fitness")
STATE_COLS = ("qualified", "run_source", "session_id", "created_at",
              "strategy_id", "result_id", "template_name")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _iter_genes(dna: dict) -> list:
    """结构化解析策略全部基因（signal_genes ∪ layers——枚举证据质量
    要求：结构化字段精确匹配，禁止字符串模糊匹配）。"""
    genes = list(dna.get("signal_genes") or [])
    for layer in dna.get("layers") or []:
        genes += list(layer.get("signal_genes") or [])
    return genes


def _bb_std_values(genes: list) -> list:
    """BB 基因的 std 全集（float 归一）。"""
    out = []
    for g in genes:
        if g.get("indicator") == "BB":
            s = g.get("params", {}).get("std")
            if s is not None:
                out.append(float(s))
    return out


def _encoded_std_values(blob: str) -> list:
    """编码列名通道：捕获组转 float（字符串负向前瞻形在 std=2.5 漏网
    ——DB 实见 2.5，必须捕获+数值比较）。"""
    if not blob:
        return []
    return [float(m.group(3)) for m in BB_GENE_RE.finditer(blob)]


def _dna_blob_affected(blob) -> bool:
    """DNA JSON 载体判定：解析成功走结构化通道（signal_genes/layers/
    population 数组三种形态），失败回退编码列名正则。"""
    if not blob:
        return False
    try:
        d = json.loads(blob)
    except (TypeError, json.JSONDecodeError):
        return _is_affected(blob, structured=False)
    gene_lists = []
    if isinstance(d, dict):
        gene_lists.append(_iter_genes(d))
    elif isinstance(d, list):  # population_json 为 DNA 数组
        for item in d:
            if isinstance(item, dict):
                gene_lists.append(_iter_genes(item))
    for genes in gene_lists:
        if any(s != 2.0 for s in _bb_std_values(genes)):
            return True
    return False


def _is_affected(dna_or_blob, *, structured: bool) -> bool:
    """双通道判定：任一 BB std ≠ 2.0 即涉事（两通道同一判定语义）。"""
    if structured:
        stds = _bb_std_values(_iter_genes(dna_or_blob))
    else:
        stds = _encoded_std_values(str(dna_or_blob))
    return any(s != 2.0 for s in stds)


# ---------------------------------------------------------------- 枚举段

def audit(db: Path, out_dir: Path) -> dict:
    """五类资产枚举+筛选——范围清单 manifest.json 落盘。"""
    conn = _connect(db)
    manifest = {"generated_at": _now(), "db": str(db), "classes": {}}

    # ① strategy 进化库存（active 面；draft 归④——D-2 边界）
    strategies, affected, bb_only_2 = [], [], []
    for r in conn.execute(
            "SELECT strategy_id, status, dna_json FROM strategy").fetchall():
        try:
            dna = json.loads(r["dna_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        genes = _iter_genes(dna)
        if not any(g.get("indicator") == "BB" for g in genes):
            continue
        if r["status"] == "draft":
            continue  # ④类边界：draft 生命周期资产
        bb_only_2.append(r["strategy_id"])  # 含 BB 待 std 判
        if _is_affected(dna, structured=True):
            affected.append(r["strategy_id"])
            strategies.append({
                "strategy_id": r["strategy_id"],
                "bb_stds": _bb_std_values(genes),
            })
    manifest["classes"]["strategy_evolution"] = {
        "total_with_bb": len(bb_only_2),
        "affected_std_ne_2": len(affected),
        "strategies": strategies,
        "note_std2_excluded": len(bb_only_2) - len(affected),
    }

    # 关联回测行（重算回写主面）
    ph = ",".join("?" * len(affected))
    bt_rows = [dict(r) for r in conn.execute(
        f"SELECT result_id, strategy_id, symbol, timeframe, data_start, "
        f"data_end, init_cash, fee, slippage FROM backtest_result "
        f"WHERE strategy_id IN ({ph})", affected)] if affected else []
    manifest["classes"]["strategy_evolution"]["backtest_rows"] = bt_rows

    # ② analysis_tasks 扫描存档 + research_log screen 审计行
    at_hits = []
    for r in conn.execute(
            "SELECT task_id, params, result_ref FROM analysis_tasks"
    ).fetchall():
        pj = r["params"] or ""
        rr_ = r["result_ref"] or ""
        if _is_affected(pj, structured=False) or _is_affected(rr_, structured=False):
            at_hits.append({
                "task_id": r["task_id"],
                "result_ref_completeness": _ref_completeness(r["result_ref"]),
            })
    screen_logs = [dict(r) for r in conn.execute(
        "SELECT log_id, summary FROM research_log WHERE entry_type='screen'"
    ).fetchall() if _is_affected(r["summary"], structured=False)]
    manifest["classes"]["scan_archive"] = {
        "analysis_tasks": at_hits, "research_log_screen": screen_logs,
        "count": len(at_hits) + len(screen_logs)}

    # ③ 历史假设（全状态含 abandoned）
    hyp_hits = [dict(r) for r in conn.execute(
        "SELECT hypothesis_id, status, conditions FROM research_hypotheses"
    ).fetchall() if _is_affected(r["conditions"], structured=False)]
    manifest["classes"]["hypotheses"] = {"items": hyp_hits,
                                         "count": len(hyp_hits)}

    # ④ draft 策略及其挂行（validation_records/backtest_result）
    drafts = []
    for r in conn.execute(
            "SELECT strategy_id, dna_json FROM strategy WHERE status='draft'"
    ).fetchall():
        try:
            dna = json.loads(r["dna_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if _is_affected(dna, structured=True):
            drafts.append(r["strategy_id"])
    vr_hits = []
    for r in conn.execute(
            "SELECT record_id, result FROM validation_records").fetchall():
        if _is_affected(r["result"], structured=False):
            vr_hits.append(r["record_id"])
    manifest["classes"]["draft_assets"] = {
        "draft_strategies": drafts, "validation_records": vr_hits,
        "count": len(drafts) + len(vr_hits)}

    # ⑤ evolution_task（champion 载体）与 generation_snapshot（只对照）
    et_hits = [r["task_id"] for r in conn.execute(
        "SELECT task_id, champion_dna, initial_dna FROM evolution_task"
    ).fetchall()
        if _is_affected(r["champion_dna"], structured=False)
        or _is_affected(r["initial_dna"], structured=False)]
    snap_rows = []
    for r in conn.execute(
            "SELECT task_id, generation, best_dna, population_json "
            "FROM generation_snapshot").fetchall():
        # 快照载体是 DNA JSON（结构化 {indicator, params}）——先结构化
        # 通道，解析失败回退编码列名正则（双通道同一判定语义）
        hit_b = _dna_blob_affected(r["best_dna"])
        hit_p = _dna_blob_affected(r["population_json"])
        if hit_b or hit_p:
            snap_rows.append({"task_id": r["task_id"],
                              "generation": r["generation"],
                              "in_best": hit_b, "in_population": hit_p})
    manifest["classes"]["evolution"] = {
        "evolution_task_affected": et_hits,
        "snapshot_rows_total": len(snap_rows),
        "snapshot_rows": snap_rows,
        "champion_metrics_column": "查证无此项（表无 champion_metrics 列）"}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))
    conn.close()
    return manifest


def _ref_completeness(ref: str | None) -> str:
    """result_ref 完整性三态（截断探测——载体 [:200000] 截断）。"""
    if not ref:
        return "absent"
    try:
        json.loads(ref)
        return "complete"
    except (json.JSONDecodeError, TypeError):
        return "truncated"


# ---------------------------------------------------------------- 重算段

def _recalc_backtest_row(db: Path, data_dir: Path, row: dict) -> dict | None:
    """单回测行同参重算（init_cash/fee/slippage/区间全部回读）。"""
    from core.analysis.backtest_bridge import run_backtest_on_range
    from core.analysis.validation_service import _derive_dna_warmup
    from core.strategy.dna import StrategyDNA

    conn = _connect(db)
    dj = conn.execute(
        "SELECT dna_json FROM strategy WHERE strategy_id=?",
        (row["strategy_id"],)).fetchone()["dna_json"]
    conn.close()
    dna = StrategyDNA.from_json(dj)
    warmup, _ = _derive_dna_warmup(db, dna)
    metrics, trades, actual_warmup, bars_used = run_backtest_on_range(
        db, data_dir, dna, row["symbol"], row["timeframe"],
        row["data_start"], row["data_end"], warmup,
        init_cash=float(row["init_cash"]), fee=float(row["fee"]),
        slippage=float(row["slippage"]))
    return {"metrics": metrics, "trade_returns": trades,
            "actual_warmup": actual_warmup, "bars_used": bars_used}


def _diff_level(old: dict, new: dict) -> str:
    """对照三档：数字变化/结论敏感变化（盈亏翻转）/无变化。"""
    keys = [k for k in NUMERIC_COLS if k in old and k in new]
    changed = [k for k in keys if abs(
        float(old[k] or 0) - float(new[k] or 0)) > 1e-9]
    if not changed:
        return "no_change"
    sign_flip = any(
        (float(old[k] or 0)) * (float(new[k] or 0)) < 0
        for k in ("total_return", "sharpe_ratio") if k in keys)
    return "conclusion_sensitive" if sign_flip else "numeric_change"


def recalc(db: Path, data_dir: Path, out_dir: Path, dry_run: bool = False):
    """重算+对照+分层处置（幂等断点续跑+备份+单资产单事务）。"""
    manifest = audit(db, out_dir)
    affected = [s["strategy_id"]
                for s in manifest["classes"]["strategy_evolution"]["strategies"]]
    bt_rows = manifest["classes"]["strategy_evolution"]["backtest_rows"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # 备份：全部涉事行旧值全量导出（回滚依据）
    backup = {"generated_at": _now(), "backtest_rows": [], "strategies": []}
    conn = _connect(db)
    for row in bt_rows:
        full = conn.execute(
            "SELECT * FROM backtest_result WHERE result_id=?",
            (row["result_id"],)).fetchone()
        backup["backtest_rows"].append(dict(full))
    sid_ph = ",".join("?" * len(affected))
    for r in conn.execute(
            f"SELECT strategy_id, best_score, metrics_json FROM strategy "
            f"WHERE strategy_id IN ({sid_ph})", affected):
        backup["strategies"].append(dict(r))
    conn.close()
    if not dry_run:
        (out_dir / "backup_pre_recalc.json").write_text(
            json.dumps(backup, ensure_ascii=False, indent=1))

    # 幂等：已有该策略 recalc_bb 条目即跳过
    conn = _connect(db)
    # 幂等键形制对齐：done 存裸 sid（剥 ref_id 的 "strategy:" 前缀——
    # 与循环比较形制一致；原存全前缀形致恒不命中，断点续跑幂等失效
    # 【tester6 6ae138b 锤出】）
    done = {r["ref_id"].replace("strategy:", "", 1) for r in conn.execute(
        "SELECT ref_id FROM research_log WHERE entry_type='recalc_bb' "
        "AND ref_id LIKE 'strategy:%'").fetchall()}
    conn.close()

    cmp_rows = []
    from core.persistence.repository.research import append_research_log
    for sid in affected:
        if sid in done:
            print(f"[skip] {sid[:12]} 已有 recalc_bb 条目（幂等）")
            continue
        rows = [r for r in bt_rows if r["strategy_id"] == sid]
        results = []
        for row in rows:
            try:
                new = _recalc_backtest_row(db, data_dir, row)
            except Exception as exc:
                if ("warmup" in str(exc).lower()
                        or "数据不足" in str(exc)):
                    # 历史行产生时数据起点更早（2026-09-20 数据集重建后
                    # 起点后移）——现 warmup 纪律下不可重算：保留旧值，
                    # 记 skipped（环境差异如实披露，不猜测重放）。
                    # 消息判定（非类身份）：工程内 ResearchError 有两个
                    # 轻量定义点，跨类 except 不可靠
                    old_row = next(b for b in backup["backtest_rows"]
                                   if b["result_id"] == row["result_id"])
                    results.append({
                        "result_id": row["result_id"],
                        "old": {k: old_row.get(k) for k in NUMERIC_COLS},
                        "new": None,
                        "level": "not_recalculable_data_env"})
                    print(f"[skip-warmup] {row['result_id'][:10]}")
                    continue
                print(f"[fail] {sid[:12]} {row['result_id'][:8]}: {exc}")
                results = None
                break
            old_row = next(b for b in backup["backtest_rows"]
                           if b["result_id"] == row["result_id"])
            level = _diff_level(old_row, new["metrics"])
            results.append({"result_id": row["result_id"],
                            "old": {k: old_row.get(k) for k in NUMERIC_COLS},
                            "new": {k: new["metrics"].get(k)
                                    for k in NUMERIC_COLS},
                            "level": level,
                            "_full_metrics": new["metrics"]})
        if results is None:
            continue
        if not dry_run:
            conn = _connect(db)
            try:
                conn.execute("BEGIN")
                recalcable = [r for r in results
                              if r["level"] != "not_recalculable_data_env"]
                for row, res in zip(rows, results):
                    if res["level"] == "not_recalculable_data_env":
                        continue
                    m = res["_full_metrics"]
                    # 动态列集：只写重算 metrics 实际提供且非 None 的
                    # 数字列（total_score 等键不在 run_backtest_on_range
                    # 产物内——写 None 违反 NOT NULL，旧值保留）
                    sets, vals = [], []
                    for col in NUMERIC_COLS:
                        v = m.get(col)
                        if v is not None:
                            sets.append(f"{col}=?")
                            vals.append(v)
                    if sets:
                        vals.append(row["result_id"])
                        conn.execute(
                            f"UPDATE backtest_result SET "
                            f"{', '.join(sets)} WHERE result_id=?", vals)
                if not recalcable:
                    conn.execute("COMMIT")
                    conn.close()
                    append_research_log(
                        db, hypothesis_id=None, entry_type="recalc_bb",
                        ref_id=f"strategy:{sid}",
                        summary=json.dumps({
                            "backtest_rows": len(results),
                            "levels": sorted({r["level"] for r in results})},
                            ensure_ascii=False, default=list))
                    cmp_rows.extend(results)
                    continue
                # strategy 行：metrics_json 同名键同步最新可重算值
                latest = recalcable[-1]["new"]
                old_s = next(s for s in backup["strategies"]
                             if s["strategy_id"] == sid)
                mj = json.loads(old_s["metrics_json"] or "{}")
                for k, v in latest.items():
                    if k in mj and v is not None:
                        mj[k] = v
                conn.execute(
                    "UPDATE strategy SET metrics_json=? WHERE strategy_id=?",
                    (json.dumps(mj), sid))
                conn.commit()
            except Exception:
                conn.rollback()
                conn.close()
                print(f"[tx-fail] {sid[:12]} 事务回滚")
                continue
            conn.close()
            append_research_log(
                db, hypothesis_id=None, entry_type="recalc_bb",
                ref_id=f"strategy:{sid}",
                summary=json.dumps({
                    "backtest_rows": len(results),
                    "levels": sorted({r["level"] for r in results})},
                    ensure_ascii=False))
        for r in results:
            cmp_rows.append({k: v for k, v in r.items()
                             if k != "_full_metrics"})
        lv = {r["level"] for r in results}
        print(f"[{'dry' if dry_run else 'ok'}] {sid[:12]} "
              f"{len(results)} 行 {sorted(lv)}")

    (out_dir / "comparison_backtest.json").write_text(
        json.dumps(cmp_rows, ensure_ascii=False, indent=1))

    # 快照只对照：载体分流（明细文件+汇总条目）
    snaps = manifest["classes"]["evolution"]["snapshot_rows"]
    (out_dir / "snapshot_comparison.jsonl").write_text("\n".join(
        json.dumps(s, ensure_ascii=False) for s in snaps))
    if not dry_run:
        append_research_log(
            db, hypothesis_id=None, entry_type="recalc_bb",
            ref_id="generation_snapshot:summary",
            summary=json.dumps({
                "rows_total": len(snaps), "disposition": "compare_only"},
                ensure_ascii=False))
        # 空集正式条目（pm 底线 1）
        for cls, key in (("scan_archive", "②类扫描存档枚举"),
                         ("hypotheses", "③类历史假设枚举"),
                         ("draft_assets", "④类 draft 资产枚举")):
            n = manifest["classes"][cls]["count"]
            append_research_log(
                db, hypothesis_id=None, entry_type="recalc_bb",
                ref_id=f"enumeration:{key}",
                summary=json.dumps({"count": n}, ensure_ascii=False))
        append_research_log(
            db, hypothesis_id=None, entry_type="recalc_bb",
            ref_id="enumeration:evolution_task",
            summary=json.dumps({
                "count": len(manifest["classes"]["evolution"]
                             ["evolution_task_affected"])},
                ensure_ascii=False))
    print(f"完成：策略 {len(affected)} 行、回测行 {len(cmp_rows)} 条对照"
          f"（明细 {out_dir / 'comparison_backtest.json'}）")


# ---------------------------------------------------------------- 导出段

def export(db: Path, out_dir: Path):
    """RR 档案对应关系表：研究档案涉 BB(std≠2) 统计数字 ↔ 重算正确值。"""
    cmp_path = out_dir / "comparison_backtest.json"
    mapping = []
    if cmp_path.exists():
        rows = json.loads(cmp_path.read_text())
        # RR 档案不写死 std≠2 数字时为空表（空集记录）
        mapping = [r for r in rows if r["level"] != "no_change"]
    out = {
        "generated_at": _now(),
        "note": "RR-002/003/005 档案无写死的 BB(std≠2) 统计数字时为空表"
                "（空集记录）；数字对照以 comparison_backtest.json 为准",
        "rows": mapping,
    }
    (out_dir / "rr_archive_mapping.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(f"对应关系表：{out_dir / 'rr_archive_mapping.json'}"
          f"（{len(mapping)} 行）")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=["audit", "recalc", "export"])
    ap.add_argument("--db", type=Path, default=REPO_ROOT / "data/quant.db")
    ap.add_argument("--data-dir", type=Path,
                    default=REPO_ROOT / "data/market")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "scripts/recalc_out")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.phase == "audit":
        m = audit(a.db, a.out)
        c1 = m["classes"]["strategy_evolution"]
        print(f"①strategy: 含BB {c1['total_with_bb']} / std≠2 {c1['affected_std_ne_2']}"
              f" / 回测行 {len(c1['backtest_rows'])}")
        for k in ("scan_archive", "hypotheses", "draft_assets"):
            print(f"{k}: {m['classes'][k]['count']}")
        print(f"evolution_task 涉事: "
              f"{len(m['classes']['evolution']['evolution_task_affected'])}；"
              f"快照行 {m['classes']['evolution']['snapshot_rows_total']}")
    elif a.phase == "recalc":
        recalc(a.db, a.data_dir, a.out, a.dry_run)
    else:
        export(a.db, a.out)


if __name__ == "__main__":
    main()
