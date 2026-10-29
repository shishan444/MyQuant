"""历史策略产物清除脚本（用户裁决 2026-10-13：全部清除，不迁移不校正）。

背景：五缺陷修复（BB/KC 吞参等）后历史策略数字为旧语义，C5 曾走
重算回写路径；用户终局裁决改为**全部清除**——不为旧语义存量花迁移
/校正成本，工程能力以修复后代码为准（清除后新产物全部为正确语义）。

清除范围：strategy 全表（含 draft）+ backtest_result 全表（策略挂行）。
保留：research_hypotheses（假设不是策略）、validation_records（假设
证据链——状态机依赖）、evolution_task/generation_snapshot（过程档案
——champion 内存对象不消费存量行）、research_log（不可变审计）。
留痕：research_log entry_type='purge_legacy' 汇总条目。
回滚依据：清除前全表导出 JSON（scripts/purge_out/）。

用法：venv/bin/python scripts/purge_legacy_strategies.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB = REPO_ROOT / "data/quant.db"
OUT = REPO_ROOT / "scripts/purge_out"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    counts = {}
    for table in ("strategy", "backtest_result"):
        counts[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    draft_ids = [r[0] for r in conn.execute(
        "SELECT strategy_id FROM strategy WHERE status='draft'")]

    # 全量导出（回滚依据）
    export = {"generated_at": _now(), "strategy": [], "backtest_result": []}
    for r in conn.execute("SELECT * FROM strategy"):
        export["strategy"].append(dict(r))
    for r in conn.execute("SELECT * FROM backtest_result"):
        export["backtest_result"].append(dict(r))
    backup_path = OUT / "purge_backup.json"
    if not a.dry_run:
        if backup_path.exists():  # 保护写（R-2 同款纪律）
            backup_path.rename(backup_path.with_name(
                f"{backup_path.stem}.{_now().replace(':', '')}{backup_path.suffix}"))
        backup_path.write_text(json.dumps(export, ensure_ascii=False))
        conn.execute("DELETE FROM strategy")
        conn.execute("DELETE FROM backtest_result")
        conn.commit()
        # 留痕（research_log 追加不可变）
        from core.persistence.repository.research import append_research_log
        append_research_log(
            DB, hypothesis_id=None, entry_type="purge_legacy",
            ref_id="strategy:all",
            summary=json.dumps({
                "decision": "用户裁决 2026-10-13：历史策略全部清除，"
                            "不迁移不校正（工程能力以修复后代码为准）",
                "strategy_rows": counts["strategy"],
                "backtest_rows": counts["backtest_result"],
                "draft_ids": draft_ids,
                "backup": str(backup_path.relative_to(REPO_ROOT)),
            }, ensure_ascii=False))
    left = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("strategy", "backtest_result")}
    conn.close()
    print(f"[{'dry' if a.dry_run else 'ok'}] 清除 strategy "
          f"{counts['strategy']} 行 / backtest_result "
          f"{counts['backtest_result']} 行（含 draft {len(draft_ids)} 行）")
    print(f"清除后余量: {left}；备份: {backup_path}")


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    main()
