"""策略生命周期状态机常量锚（build-strategy-lifecycle 1.1）。

计数锚+逐对清单+派生互推+trigger 映射完备+36 网格分类封闭——
常量与矩阵谁错改谁（多写/漏写/重复必红）。形制沿 tests/paper_trading
状态机常量测试（PT_STATUS_PREDECESSORS 对拍）。
"""
from __future__ import annotations

import pytest

from core.strategy.lifecycle.machine import (
    INITIAL_STATUSES, MANUAL_EDGES, MANUAL_TRIGGERS, STRATEGY_STATUSES,
    SUCCESSORS, TRIGGER_EDGES, TRIGGERS, TRANSITION_MATRIX,
    WATERLINE_ORDER, _TRANSITION_PAIRS)

pytestmark = [pytest.mark.unit]


class TestValueDomain:
    """六态值域与水位序。"""

    def test_statuses_six_values(self):
        """六值封闭（三态旧值原义保留+新三态）。"""
        assert STRATEGY_STATUSES == frozenset({
            "draft", "active", "paper", "candidate", "live", "archived"})

    def test_waterline_order_five_values_archived_excluded(self):
        """水位序五值；archived 不入序（→archived 事件不代表执行阶）。"""
        assert WATERLINE_ORDER == {"draft": 0, "active": 1, "paper": 2,
                                   "candidate": 3, "live": 4}
        assert "archived" not in WATERLINE_ORDER
        assert set(WATERLINE_ORDER) == STRATEGY_STATUSES - {"archived"}

    def test_initial_statuses_whitelist(self):
        """初始合法集恰 {draft, active}（不得擅自加严/放宽）。"""
        assert INITIAL_STATUSES == frozenset({"draft", "active"})
        assert INITIAL_STATUSES <= STRATEGY_STATUSES


class TestTransitionMatrix:
    """矩阵 11 对逐对清单与计数锚。"""

    def test_matrix_len_anchor_11(self):
        """计数锚：len==11（多写/漏写/重复必红）。"""
        assert len(TRANSITION_MATRIX) == 11

    def test_matrix_pairwise_listing(self):
        """逐对清单：七条单列边+「任意→archived」四 from 展开。"""
        expected = {
            ("draft", "active"), ("active", "paper"), ("paper", "active"),
            ("paper", "candidate"), ("candidate", "live"),
            ("live", "archived"), ("archived", "draft"),
            ("draft", "archived"), ("active", "archived"),
            ("paper", "archived"), ("candidate", "archived"),
        }
        assert TRANSITION_MATRIX == frozenset(expected)

    def test_source_tuple_no_duplicates(self):
        """声明元组逐行互异（frozenset 吞重复——计数锚的声明面防线）。"""
        assert len(_TRANSITION_PAIRS) == len(set(_TRANSITION_PAIRS)) == 11
        assert frozenset(_TRANSITION_PAIRS) == TRANSITION_MATRIX

    def test_matrix_edges_within_domain(self):
        """每条边的 from/to 都在六值域内。"""
        for f, t in TRANSITION_MATRIX:
            assert f in STRATEGY_STATUSES, f
            assert t in STRATEGY_STATUSES, t

    def test_successors_roundtrip_with_matrix(self):
        """派生互推一致：SUCCESSORS 重建边集 == 矩阵（两视图不漂移）。"""
        rebuilt = frozenset(
            (f, t) for f, succs in SUCCESSORS.items() for t in succs)
        assert rebuilt == TRANSITION_MATRIX
        assert set(SUCCESSORS) == STRATEGY_STATUSES


class TestTriggers:
    """trigger 九枚举与边映射完备性。"""

    def test_triggers_nine_values(self):
        """九枚举封闭。"""
        assert TRIGGERS == frozenset({
            "credential", "mount", "task_end", "qualify", "manual_promote",
            "manual_archive", "retire", "revalidate", "refute"})

    def test_trigger_edges_cover_matrix_exactly(self):
        """映射完备：trigger 边并集 == 矩阵（每边至少一个 trigger 覆盖，
        且 trigger 侧不发明矩阵外的边）。"""
        assert set(TRIGGER_EDGES) == TRIGGERS
        covered = frozenset().union(*TRIGGER_EDGES.values())
        assert covered == TRANSITION_MATRIX

    def test_manual_edge_closed_set(self):
        """人工边封闭集（REQ-1-02 人工点封闭列举）：candidate→live+
        五 from 人工归档+archived→draft，共 7 对。"""
        assert MANUAL_EDGES == frozenset({
            ("candidate", "live"),
            ("draft", "archived"), ("active", "archived"),
            ("paper", "archived"), ("candidate", "archived"),
            ("live", "archived"),
            ("archived", "draft")})
        assert MANUAL_EDGES <= TRANSITION_MATRIX
        assert MANUAL_TRIGGERS == frozenset({
            "manual_promote", "manual_archive", "retire", "revalidate"})

    def test_refute_edge_pinned_to_draft(self):
        """证伪边 from 恒 draft（refuted 时谱系恒 draft 或不存在）。"""
        assert TRIGGER_EDGES["refute"] == frozenset({("draft", "archived")})


def _grid_36():
    """六态全网格 (from, to) 36 格（11 合法+19 异态非法+6 同态）。"""
    return [(f, t) for f in sorted(STRATEGY_STATUSES)
            for t in sorted(STRATEGY_STATUSES)]


class TestGrid36ClosedClassification:
    """36 全网格三组封闭分类（与 4.2 计数锚五联动同口径）。"""

    _LEGAL = frozenset(TRANSITION_MATRIX)

    @staticmethod
    def _expected_legal(pair):
        f, t = pair
        return f != t and pair in TRANSITION_MATRIX

    def test_grid_completeness_36(self):
        """网格完整性：36==11 合法+19 异态非法+6 同态。"""
        grid = _grid_36()
        legal = [p for p in grid if self._expected_legal(p)]
        same = [p for p in grid if p[0] == p[1]]
        illegal = [p for p in grid
                   if p[0] != p[1] and p not in TRANSITION_MATRIX]
        assert len(grid) == 36
        assert len(legal) == 11
        assert len(illegal) == 19
        assert len(same) == 6

    @pytest.mark.parametrize("pair", _grid_36())
    def test_grid_membership_matches_classification(self, pair):
        """逐格断言：(from,to) 在矩阵 ⟺ 异态且属 11 对合法边——
        同态 6 格一律不在矩阵（not_migrated 域）。"""
        f, t = pair
        if f == t:
            assert pair not in TRANSITION_MATRIX
            assert t not in SUCCESSORS[f]
        elif pair in TRANSITION_MATRIX:
            assert t in SUCCESSORS[f]
        else:
            assert t not in SUCCESSORS[f]
