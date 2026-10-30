#!/usr/bin/env python
"""污染记录重验通道（fix-mtf-execution-consistency S 卡 T4.3）。

对漂移假设执行四步重验：
①translate 重建（幂等收窄版——漂移 draft 放行重建，旧产物保留审计）；
②以新产物对旧验证活动区间重跑隔离验证（守卫放行+R3 warmup 正确+
  判定并集豁免——rerun 取代语义非重复判定）；
③新记录显式携带取代关系（result_json.supersedes + params_json.
  rerun_of = 旧 record_id）；
④旧记录与污染标注原样保留（不删不改 verdict）。

判定连锁（Q2 裁决）：不自动 transition——新 verdict 落记录，状态
迁移经研究者复核后走既有 MCP transition 通道。

用法：
    venv/bin/python scripts/rerun_polluted_validation.py \
        --db data/quant.db --data-dir data/market \
        --hypothesis hyp_3e1debcdb373
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


def rerun_hypothesis(db_path: Path, data_dir: Path, hypothesis_id: str,
                     *, request_range: dict | None = None) -> dict:
    """执行四步重验，返回摘要。

    request_range 缺省=旧污染记录首个 isolated 请求区间；无旧记录时
    =假设 data_ranges 区间（C1 活体锚形制——原验证活动区间）。
    """
    from core.analysis.hypothesis_to_strategy import translate_hypothesis
    from core.analysis.validation_service import run_isolated_backtest

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        hyp = conn.execute(
            "SELECT * FROM research_hypotheses WHERE hypothesis_id=?",
            (hypothesis_id,)).fetchone()
        if hyp is None:
            raise SystemExit(f"假设不存在: {hypothesis_id}")
        if hyp["status"] != "active":
            raise SystemExit(
                f"假设已终态（{hyp['status']}）——重验对象须 active")
        ranges = json.loads(hyp["data_ranges"])
        base_range = {"symbol": ranges[0]["symbol"],
                      "timeframe": ranges[0]["timeframe"],
                      "start": ranges[0]["start"], "end": ranges[0]["end"]}
        # 旧污染记录：假设关联且已标注——取代引用源
        old_records = [dict(r) for r in conn.execute(
            "SELECT record_id, params FROM validation_records "
            "WHERE (hypothesis_id=? OR strategy_id IN ("
            "  SELECT strategy_id FROM strategy WHERE parent_ids=?))"
            "AND pollution_cause=? ORDER BY created_at",
            (hypothesis_id, hypothesis_id, POLLUTION_CAUSE))]

    # ①重建产物（幂等收窄：漂移 draft 放行）
    translate_out = translate_hypothesis(
        db_path, hypothesis_id=hypothesis_id, data_dir=data_dir)
    new_sid = translate_out["strategy_id"]

    # ②区间选择：旧 isolated 污染记录的请求区间 > 假设 data_ranges
    # 重跑区间：时间范围沿用旧记录（原验证活动区间），周期换算假设
    # 基准——旧记录的 tf 是漂移产物时代值（守卫会拦异轴），新产物
    # exec=基准，重跑须同轴（守卫放行的自洽调用）
    rerun_range = request_range or base_range
    for rec in old_records:
        try:
            params = json.loads(rec["params"])
            rr = params.get("request_range")
            if rr and rr.get("start"):
                rerun_range = {"symbol": rr["symbol"],
                               "timeframe": base_range["timeframe"],
                               "start": rr["start"], "end": rr["end"]}
                break
        except (json.JSONDecodeError, TypeError):
            continue

    # ③重跑隔离验证（rerun 豁免并集；守卫保证 tf 一致——新产物
    # 执行周期=假设基准，与请求区间周期一致自然通过）
    primary_old = (old_records[0]["record_id"] if old_records else None)
    result = run_isolated_backtest(
        db_path, data_dir, hypothesis_id=hypothesis_id, strategy_id=new_sid,
        request_range=rerun_range, rerun_mode=True)
    new_rid = result["record_id"]

    # 取代关系落键（自描述面单记录自包含——A2 形制）
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result, params FROM validation_records "
            "WHERE record_id=?", (new_rid,)).fetchone()
        result_json = json.loads(row["result"]) if row["result"] else {}
        params_json = json.loads(row["params"])
        if primary_old is not None:
            result_json["supersedes"] = primary_old
            params_json["rerun_of"] = primary_old
        conn.execute(
            "UPDATE validation_records SET result=?, params=? "
            "WHERE record_id=?",
            (json.dumps(result_json, ensure_ascii=False),
             json.dumps(params_json, ensure_ascii=False), new_rid))
        conn.commit()

    return {
        "hypothesis_id": hypothesis_id,
        "new_strategy_id": new_sid,
        "new_record_id": new_rid,
        "rerun_range": rerun_range,
        "superseded_record": primary_old,
        "verdict": result["verdict"],
        "metrics": result.get("metrics"),
        "old_records_kept": [r["record_id"] for r in old_records],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "quant.db")
    parser.add_argument("--data-dir", type=Path,
                        default=ROOT / "data" / "market")
    parser.add_argument("--hypothesis", required=True,
                        help="重验假设 id（须 active）")
    args = parser.parse_args()
    summary = rerun_hypothesis(args.db, args.data_dir, args.hypothesis)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("\n判定连锁提醒：新 verdict 已落记录，状态迁移经研究者复核后"
          "走既有 transition 通道（不自动迁移）", file=sys.stderr)


if __name__ == "__main__":
    main()
