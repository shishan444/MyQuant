#!/usr/bin/env python
"""历史污染回扫（fix-mtf-execution-consistency S 卡——一次性+幂等）。

清点在库带基因级周期漂移的策略与假设，关联其验证记录与工程回测
记录并标注 pollution_cause='gene_tf_drift'，输出清单报告。

清点判据（精筛实证 N=2——粗筛「含 layers 键」会误报）：
- strategy：dna_json.signal_genes 存在成员 timeframe 非 null 且
  ≠execution_genes.timeframe；
- research_hypotheses：conditions 的 when/add_trigger 组条目带
  timeframe 键且≠data_ranges[0].timeframe。

两表分界（design 卡 7）：validation_records（含 verdict 判定链）
=标注+重验取代通道；backtest_result（无 verdict 语义）=仅标注。
不触碰 verdict/结果数值；存量不回填（NULL=未标注）；幂等重跑
（已标实行跳过，不重复写）。

用法：
    venv/bin/python scripts/rescan_pollution.py --db data/quant.db
    venv/bin/python scripts/rescan_pollution.py --db data/quant.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

POLLUTION_CAUSE = "gene_tf_drift"

# 精筛判据（json_each 成员级——与 architect 独立复现 SQL 同形）
STRATEGY_DRIFT_SQL = """
SELECT strategy_id,
       json_extract(dna_json, '$.execution_genes.timeframe') AS exec_tf
FROM strategy
WHERE EXISTS (
    SELECT 1 FROM json_each(strategy.dna_json, '$.signal_genes') g
    WHERE json_extract(g.value, '$.timeframe') IS NOT NULL
      AND json_extract(g.value, '$.timeframe')
          != json_extract(strategy.dna_json,
                          '$.execution_genes.timeframe'))
"""

HYPOTHESIS_DRIFT_SQL = """
SELECT hypothesis_id, json_extract(data_ranges, '$[0].timeframe') AS base_tf
FROM research_hypotheses
WHERE EXISTS (
    SELECT 1 FROM json_each(conditions, '$.when_conditions') w
    WHERE json_extract(w.value, '$.timeframe') IS NOT NULL
      AND json_extract(w.value, '$.timeframe')
          != json_extract(data_ranges, '$[0].timeframe'))
   OR EXISTS (
    SELECT 1 FROM json_each(conditions, '$.add_trigger_conditions') a
    WHERE json_extract(a.value, '$.timeframe') IS NOT NULL
      AND json_extract(a.value, '$.timeframe')
          != json_extract(data_ranges, '$[0].timeframe'))
"""


def rescan(db_path: Path, *, dry_run: bool = False) -> dict:
    """执行回扫：清点 → 标注关联记录 → 清单报告。

    Returns:
        {drifted_strategies, drifted_hypotheses, marked_validation,
         marked_backtest, report}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        drifted_strategies = {
            r["strategy_id"]: r["exec_tf"] for r in conn.execute(
                STRATEGY_DRIFT_SQL)}
        drifted_hypotheses = {
            r["hypothesis_id"]: r["base_tf"] for r in conn.execute(
                HYPOTHESIS_DRIFT_SQL)}

        sid_placeholders = ",".join("?" * len(drifted_strategies)) or "''"
        hid_placeholders = ",".join("?" * len(drifted_hypotheses)) or "''"

        # 关联记录：validation_records（strategy 或 hypothesis 任一命中）
        val_rows = conn.execute(
            f"SELECT record_id FROM validation_records "
            f"WHERE strategy_id IN ({sid_placeholders}) "
            f"   OR hypothesis_id IN ({hid_placeholders}) "
            f"ORDER BY record_id",
            [*drifted_strategies, *drifted_hypotheses]).fetchall()
        # 工程回测：strategy_id 命中（无 verdict 语义，仅标注）
        bt_rows = conn.execute(
            f"SELECT result_id FROM backtest_result "
            f"WHERE strategy_id IN ({sid_placeholders}) ORDER BY result_id",
            list(drifted_strategies)).fetchall()

        val_ids = [r["record_id"] for r in val_rows]
        bt_ids = [r["result_id"] for r in bt_rows]

        marked_val = marked_bt = 0
        if not dry_run:
            # 幂等标注：只写未标行（pollution_cause IS NULL）
            if val_ids:
                marked_val = conn.execute(
                    f"UPDATE validation_records SET pollution_cause=? "
                    f"WHERE record_id IN "
                    f"({','.join('?' * len(val_ids))}) "
                    f"AND pollution_cause IS NULL",
                    [POLLUTION_CAUSE, *val_ids]).rowcount
            if bt_ids:
                marked_bt = conn.execute(
                    f"UPDATE backtest_result SET pollution_cause=? "
                    f"WHERE result_id IN ({','.join('?' * len(bt_ids))}) "
                    f"AND pollution_cause IS NULL",
                    [POLLUTION_CAUSE, *bt_ids]).rowcount
            conn.commit()

        report = {
            "cause": POLLUTION_CAUSE,
            "drifted_strategies": {
                sid: {"exec_tf": tf} for sid, tf in
                drifted_strategies.items()},
            "drifted_hypotheses": {
                hid: {"base_tf": tf} for hid, tf in
                drifted_hypotheses.items()},
            "linked_validation_records": val_ids,
            "linked_backtest_results": bt_ids,
            "marked": {"validation_records": marked_val,
                       "backtest_result": marked_bt},
            "dry_run": dry_run,
        }
        return report
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "quant.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="只清点不标注")
    args = parser.parse_args()
    report = rescan(args.db, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n清单：漂移策略 {len(report['drifted_strategies'])} 条 | "
          f"漂移假设 {len(report['drifted_hypotheses'])} 条 | "
          f"关联验证记录 {len(report['linked_validation_records'])} 条 | "
          f"关联工程回测 {len(report['linked_backtest_results'])} 条 | "
          f"本次标注 val={report['marked']['validation_records']} "
          f"bt={report['marked']['backtest_result']}"
          + ("（dry-run）" if args.dry_run else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
