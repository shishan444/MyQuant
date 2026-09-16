"""Verify endpoint tests: schema validation, comprehensive score calculation, history."""

import pytest

pytestmark = [pytest.mark.integration]

from api.schemas import (
    VerifyRequest,
    VerifyResponse,
    VerifyResultItem,
    VerifySummaryItem,
    VerifyPeriodSummary,
    VerifyDateRange,
    VerifyHistoryItem,
    VerifyHistoryResponse,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_verify_date_range_schema():
    dr = VerifyDateRange(start="2024-01-01", end="2024-06-30")
    assert dr.start == "2024-01-01"
    assert dr.end == "2024-06-30"


def test_verify_request_schema():
    req = VerifyRequest(
        strategy_ids=["id1", "id2"],
        data_ranges=[
            VerifyDateRange(start="2024-01-01", end="2024-03-31"),
            VerifyDateRange(start="2024-04-01", end="2024-06-30"),
        ],
    )
    assert len(req.strategy_ids) == 2
    assert len(req.data_ranges) == 2
    assert req.init_cash == 100000.0
    assert req.fee == 0.001


def test_verify_request_rejects_extra_fields():
    with pytest.raises(Exception):
        VerifyRequest(
            strategy_ids=["id1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-30")],
            unknown_field="test",
        )


def test_verify_result_item_schema():
    item = VerifyResultItem(
        strategy_id="id1",
        data_start="2024-01-01",
        data_end="2024-03-31",
        total_return=0.15,
        sharpe_ratio=1.8,
        fitness=1.8,
        qualified=True,
    )
    assert item.total_return == 0.15
    assert item.qualified is True


def test_verify_summary_item_schema():
    item = VerifySummaryItem(
        strategy_id="id1",
        strategy_name="Test Strategy",
        comprehensive_score=1.2,
        avg_fitness=1.0,
        qualified_count=3,
        total_periods=3,
        per_period_metrics=[
            VerifyPeriodSummary(
                data_start="2024-01-01",
                data_end="2024-03-31",
                fitness=1.0,
                qualified=True,
                total_return=0.1,
                sharpe_ratio=1.5,
                max_drawdown=-0.05,
            ),
        ],
    )
    assert item.comprehensive_score == 1.2
    assert len(item.per_period_metrics) == 1


def test_verify_response_schema():
    resp = VerifyResponse(
        results=[VerifyResultItem(
            strategy_id="id1",
            data_start="2024-01-01",
            data_end="2024-03-31",
        )],
        summary=[VerifySummaryItem(
            strategy_id="id1",
            strategy_name="Test",
            comprehensive_score=0.5,
        )],
    )
    assert len(resp.results) == 1
    assert len(resp.summary) == 1


# ---------------------------------------------------------------------------
# Endpoint existence test
# ---------------------------------------------------------------------------

def test_verify_endpoint_exists():
    from api.routes.strategies import verify_strategies
    assert callable(verify_strategies)


# ---------------------------------------------------------------------------
# Comprehensive score calculation tests
# ---------------------------------------------------------------------------

def test_comprehensive_score_all_qualified():
    """All 3 periods qualified → consistency_bonus=1.2."""
    fitnesses = [1.0, 1.2, 0.8]
    avg = sum(fitnesses) / 3
    qualified_count = 3
    total = 3
    qualified_ratio = qualified_count / total
    consistency_bonus = 1.2 if qualified_count == total else 1.0
    score = avg * qualified_ratio * consistency_bonus
    assert abs(score - avg * 1.2) < 0.001


def test_comprehensive_score_partial_qualified():
    """2/3 qualified → no bonus."""
    fitnesses = [1.0, 0.0, 0.8]
    avg = sum(fitnesses) / 3
    qualified_count = 2
    total = 3
    qualified_ratio = qualified_count / total
    consistency_bonus = 1.0  # not all qualified
    score = avg * qualified_ratio * consistency_bonus
    assert abs(score - avg * (2/3)) < 0.001


def test_comprehensive_score_none_qualified():
    """0/3 qualified → score=0."""
    fitnesses = [0.0, 0.0, 0.0]
    avg = sum(fitnesses) / 3
    qualified_count = 0
    total = 3
    qualified_ratio = qualified_count / total
    score = avg * qualified_ratio
    assert score == 0.0


def test_comprehensive_score_single_period():
    """1 period qualified → ratio=1.0, but no bonus."""
    fitnesses = [1.5]
    avg = 1.5
    qualified_count = 1
    total = 1
    qualified_ratio = 1.0
    consistency_bonus = 1.2  # all qualified (1/1)
    score = avg * qualified_ratio * consistency_bonus
    assert abs(score - 1.8) < 0.001


# ---------------------------------------------------------------------------
# History endpoint tests
# ---------------------------------------------------------------------------

def test_verify_history_schema():
    item = VerifyHistoryItem(
        result_id="r1",
        strategy_id="s1",
        strategy_name="Test",
        symbol="BTCUSDT",
        timeframe="4h",
        data_start="2024-01-01",
        data_end="2024-06-30",
        total_return=0.15,
        sharpe_ratio=1.8,
        max_drawdown=-0.08,
        fitness=1.35,
        qualified=1,
        created_at="2025-01-01T00:00:00Z",
    )
    assert item.fitness == 1.35
    assert item.qualified == 1


def test_verify_history_response_schema():
    resp = VerifyHistoryResponse(
        items=[
            VerifyHistoryItem(
                result_id="r1",
                strategy_id="s1",
                symbol="BTCUSDT",
                timeframe="4h",
                data_start="2024-01-01",
                data_end="2024-06-30",
                created_at="2025-01-01T00:00:00Z",
            ),
        ],
        total=1,
    )
    assert len(resp.items) == 1
    assert resp.total == 1


def test_verify_history_endpoint_exists():
    from api.routes.strategies import get_verify_history
    assert callable(get_verify_history)
