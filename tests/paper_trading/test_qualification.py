"""qualification 判定式与对齐谓词单测（paper-trading-revamp 组 B/C）。

判定式等号侧精确断言（验收总纲）：收益>0 严格（==0 为 false）/
笔数≥5 含等（==5 为 true）/差异≤0.10 含等（==0.10 为 passed）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.qualification import (
    QUALIFICATION_PROFILE,
    evaluate_two_dimensions,
    first_met_writable,
    paper_trigger_set,
    replay_trigger_set,
    trigger_consistency,
    final_judgment,
    qualification_progress_payload,
    SIGNAL_WHITELIST,
    EXECUTION_EXIT_KINDS,
)


class TestTwoDimensions:
    def test_pnl_strict_positive(self):
        assert evaluate_two_dimensions(100.0, 100.0, 0)["pnl_positive"] is False  # ==0 为 false
        assert evaluate_two_dimensions(100.001, 100.0, 0)["pnl_positive"] is True

    def test_trades_inclusive_five(self):
        assert evaluate_two_dimensions(101.0, 100.0, 5)["trades_enough"] is True  # ==5 为 true
        assert evaluate_two_dimensions(101.0, 100.0, 4)["trades_enough"] is False

    def test_first_met_idempotent(self):
        dims = {"pnl_positive": True, "trades_enough": True}
        assert first_met_writable(None, dims) is True
        assert first_met_writable("2026-01-01T00:00:00", dims) is False  # 已写不再写
        assert first_met_writable(None, {"pnl_positive": False, "trades_enough": True}) is False


class TestTriggerConsistency:
    def test_consistency_inclusive_010(self):
        # 分母=重放侧基数 10、对称差 1（多触发方向）→ 0.10 ==0.10 为 passed（含等）
        paper = [(f"t{i}", "open") for i in range(9)]
        replay = paper + [("t_extra", "open")]  # 重放多 1 个
        out = trigger_consistency(paper, replay)
        assert out["deviation_ratio"] == pytest.approx(0.10)
        assert out["passed"] is True

    def test_consistency_over_threshold(self):
        paper = [(f"t{i}", "open") for i in range(10)]
        replay = paper[:-2]  # 漏 2 个 → 0.2 > 0.10
        assert trigger_consistency(paper, replay)["passed"] is False

    def test_replay_empty_null_tristate(self):
        out = trigger_consistency([( "t0", "open")], [])
        assert out["passed"] is None  # NULL≠false≠true 三态
        assert out["deviation_ratio"] is None

    def test_paper_whitelist_and_exit_kinds_excluded(self):
        # 六 kind 全剔除（含 exit_max_loss——pt_signal 独有最易漏）+白名单四值命中
        rows = [
            {"signal_kind": k, "bar_time": f"2026-01-0{i}T00:00:00"} for i, k in enumerate(
                ["open", "close", "add", "reduce",
                 "exit_sl", "exit_tp", "exit_max_loss", "exit_max_hold",
                 "exit_time", "exit_liquidation"], start=1)
        ] + [{"signal_kind": "open", "bar_time": "2026-01-11T00:00:00"}]
        out = paper_trigger_set(rows)
        # 白名单四值命中 + 第 11 行 open；六执行出场 kind 全不在集
        assert len(out) == 5
        kinds = {a for _, a in out}
        assert kinds == {"open", "close", "add", "reduce"}
        assert all(k not in kinds for k in EXECUTION_EXIT_KINDS)

    def test_rejected_expired_counted(self):
        # 被拒/过期计入纸盘侧（求值产生即算触发）——fixture 里 outcome 字段
        # 不影响 paper_trigger_set（过滤只看 kind 白名单）
        rows = [{"signal_kind": "open", "bar_time": "t0", "outcome": "rejected"},
                {"signal_kind": "open", "bar_time": "t1", "outcome": "expired"}]
        assert len(paper_trigger_set(rows)) == 2


class TestFinalJudgment:
    def test_qualified_all_three(self):
        c = {"passed": True, "deviation_ratio": 0.05, "numerator": 1, "denominator": 20, "detail": {}}
        out = final_judgment(105.0, 100.0, 6, c)
        assert out["qualified"] is True

    def test_null_consistency_not_qualified(self):
        c = {"passed": None, "deviation_ratio": None, "numerator": None, "denominator": 0, "detail": {}}
        out = final_judgment(105.0, 100.0, 6, c)
        assert out["qualified"] is False  # 宁缺勿滥


class TestProgressPayload:
    def test_pending_task(self):
        out = qualification_progress_payload(None, 100.0, 0, None)
        assert out == {"pnl_positive": False, "trades_enough": False, "first_met_at": None}

    def test_profile_semantics(self):
        # 语义分立：自有常量（不 import 研究域 DEFAULT_PROFILE）
        assert QUALIFICATION_PROFILE["min_trades"] == 5
        assert QUALIFICATION_PROFILE["trigger_consistency_max"] == 0.10


class TestTransitWiring:
    """子件四接线核心语义（D3：非终态零触碰/解耦/幂等由状态机承载）。"""

    def test_non_terminal_no_transit(self, tmp_path):
        # 非终态（running）：attempted=false 零触碰（rolling 报告不触发迁移）
        from core.paper_trading.qualification import _maybe_transit_candidate
        task = {"task_id": "t", "strategy_id": "s", "status": "running",
                "dna_snapshot_json": "{}", "initial_cash": 100.0, "total_trades": 0}
        out = _maybe_transit_candidate(tmp_path / "q.db", task, {"qualified": True})
        assert out == {"attempted": False, "migrated": False, "not_migrated_reason": None}

    def test_not_qualified_no_transit(self, tmp_path):
        from core.paper_trading.qualification import _maybe_transit_candidate
        task = {"task_id": "t", "strategy_id": "s", "status": "stopped",
                "dna_snapshot_json": "{}", "initial_cash": 100.0, "total_trades": 0}
        out = _maybe_transit_candidate(tmp_path / "q.db", task, {"qualified": False})
        assert out["attempted"] is False

    def test_transit_failure_decoupled(self, tmp_path):
        # 终态+qualified 但库不可用：迁移失败不抛（解耦）+原因入段
        from core.paper_trading.qualification import _maybe_transit_candidate
        task = {"task_id": "t", "strategy_id": "s", "status": "completed",
                "dna_snapshot_json": "not-json", "initial_cash": 100.0, "total_trades": 6}
        out = _maybe_transit_candidate(tmp_path / "nonexistent" / "q.db", task,
                                       {"qualified": True, "judged_at": "x",
                                        "pnl_positive": True, "trades_enough": True})
        assert out["attempted"] is True
        assert out["migrated"] is False
        assert out["not_migrated_reason"]
