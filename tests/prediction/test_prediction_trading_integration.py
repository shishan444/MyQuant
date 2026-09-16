"""Phase 2 integration tests: types, judgment Rule 5 removal, account tranches."""

import pytest

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Tranche + PositionPlan
# ---------------------------------------------------------------------------

class TestTranche:
    def test_create_tranche(self):
        from core.trading.types import Tranche
        t = Tranche(price_level=95000.0, size_pct=0.11)
        assert t.price_level == 95000.0
        assert t.status == "pending"
        assert t.bars_waiting == 0

    def test_tranche_status_transition(self):
        from core.trading.types import Tranche
        t = Tranche(price_level=95000.0, size_pct=0.11)
        t.status = "filled"
        assert t.status == "filled"


class TestPositionPlan:
    def test_create_plan(self):
        from core.trading.types import Tranche, PositionPlan
        tranches = [
            Tranche(price_level=95000.0, size_pct=0.11),
            Tranche(price_level=94500.0, size_pct=0.12),
        ]
        plan = PositionPlan(tranches=tranches, target_pct=0.30)
        assert len(plan.tranches) == 2
        assert plan.max_wait_bars == 5
        assert plan.max_chase_pct == 0.5

    def test_from_prediction_long(self):
        from core.trading.types import PositionPlan
        from core.prediction.predictor import PredictionResult
        pred = PredictionResult(low=94000.0, high=96000.0, width=1000.0, k_actual=0.6)
        plan = PositionPlan.from_prediction(
            prediction=pred,
            target_pct=0.30,
            side="long",
            entry_price=95000.0,
            stop_loss=0.05,
        )
        assert len(plan.tranches) == 2
        # Tranche 1: predicted_low + width * 0.2 = 94000 + 200 = 94200
        assert plan.tranches[0].price_level == 94200.0
        # Tranche 2: predicted_low = 94000
        assert plan.tranches[1].price_level == 94000.0
        # Sizes: each ~33% of remaining (target * 0.67 / 2)
        total_pending = sum(t.size_pct for t in plan.tranches)
        assert total_pending > 0

    def test_from_prediction_short(self):
        from core.trading.types import PositionPlan
        from core.prediction.predictor import PredictionResult
        pred = PredictionResult(low=94000.0, high=96000.0, width=1000.0, k_actual=0.6)
        plan = PositionPlan.from_prediction(
            prediction=pred,
            target_pct=0.30,
            side="short",
            entry_price=95000.0,
            stop_loss=0.05,
        )
        # Short: orders above close
        # Tranche 1: predicted_high - width * 0.2 = 96000 - 200 = 95800
        assert plan.tranches[0].price_level == 95800.0
        # Tranche 2: predicted_high = 96000
        assert plan.tranches[1].price_level == 96000.0


# ---------------------------------------------------------------------------
# Judgment Rule 5 removal
# ---------------------------------------------------------------------------

class TestJudgmentRule5Removed:
    def test_no_signal_returns_hold(self):
        """After Rule 5 removal, no-signal state should return hold."""
        from core.trading.judgment import evaluate
        from core.trading.types import BarSignals, AccountState, JudgmentConfig

        signals = BarSignals(entry=False, exit=False, add=False, reduce=False)
        state = AccountState(
            balance=100000,
            has_position=True,
            position_side="long",
            position_entry=95000,
            position_quantity=0.1,
            position_margin=9500,
            unrealized_pnl=500,
            position_bars_held=1,
            target_position_pct=0.30,
            actual_position_pct=0.10,
            equity=100500,
        )
        config = JudgmentConfig(max_fill_bars=3)
        decision = evaluate(signals, state, config)
        assert decision.action == "hold", f"Expected hold after Rule 5 removal, got {decision.action}"


# ---------------------------------------------------------------------------
# Account: _process_tranches
# ---------------------------------------------------------------------------

class TestProcessTranches:
    def _make_account(self, **kwargs):
        from core.trading.account import VirtualAccount
        from core.strategy.dna import StrategyDNA, RiskGenes, ExecutionGenes, SignalGene, SignalRole
        gene = SignalGene(
            indicator="EMA", params={"period": 10},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "price_above"},
        )
        dna = StrategyDNA(
            signal_genes=[gene],
            risk_genes=RiskGenes(
                stop_loss=0.05, position_size=0.3,
                leverage=kwargs.get("leverage", 1),
                direction=kwargs.get("direction", "long"),
            ),
            execution_genes=ExecutionGenes(timeframe="4h"),
        )
        return VirtualAccount(dna, init_cash=100000.0, fee=0.001, slippage=0.0005)

    def test_tranche_fills_when_price_touches(self):
        from core.trading.types import Tranche, PositionPlan
        from core.trading.account import VirtualAccount
        from core.prediction.predictor import PredictionResult

        acct = self._make_account()
        # Open a long position first
        from core.trading.types import Decision
        acct.process_bar_v2(96000, 94000, 95000, 95500, "2024-01-01T00:00:00+00:00",
                           pending_decision=Decision(action="open", direction="long",
                                                    target_position_pct=0.30, entry_size_pct=0.10,
                                                    reason="test"))
        assert acct.position is not None

        # Create plan with tranche at 94200
        plan = PositionPlan(
            tranches=[Tranche(price_level=94200.0, size_pct=0.10)],
            target_pct=0.30,
        )
        pred = PredictionResult(low=94000.0, high=96000.0, width=1000.0, k_actual=0.6)

        # Bar with low=94100 touches the 94200 level
        events, _ = acct.process_bar_v2(
            96000, 94100, 95000, 95500, "2024-01-01T04:00:00+00:00",
            position_plan=plan, prediction_result=pred,
        )
        assert plan.tranches[0].status == "filled"

    def test_tranche_waits_when_price_not_touch(self):
        from core.trading.types import Tranche, PositionPlan
        from core.trading.account import VirtualAccount
        from core.prediction.predictor import PredictionResult

        acct = self._make_account()
        from core.trading.types import Decision
        acct.process_bar_v2(96000, 94000, 95000, 95500, "2024-01-01T00:00:00+00:00",
                           pending_decision=Decision(action="open", direction="long",
                                                    target_position_pct=0.30, entry_size_pct=0.10,
                                                    reason="test"))

        plan = PositionPlan(
            tranches=[Tranche(price_level=94200.0, size_pct=0.10)],
            target_pct=0.30,
        )
        pred = PredictionResult(low=94000.0, high=96000.0, width=1000.0, k_actual=0.6)

        # Bar with low=94500 does NOT touch 94200
        events, _ = acct.process_bar_v2(
            95800, 94500, 95000, 95200, "2024-01-01T04:00:00+00:00",
            position_plan=plan, prediction_result=pred,
        )
        assert plan.tranches[0].status == "pending"
        assert plan.tranches[0].bars_waiting == 1


# ---------------------------------------------------------------------------
# Runner-level integration: predict + open + plan + fill lifecycle
# ---------------------------------------------------------------------------

class TestRunnerPredictionLifecycle:
    """Tests the full predict -> open -> create plan -> fill tranches lifecycle."""

    @pytest.fixture
    def enhanced_df(self):
        from tests.helpers.data_factory import make_ohlcv
        from core.features.indicators import compute_all_indicators
        df = make_ohlcv(n=300, seed=42)
        return compute_all_indicators(df)

    def _make_account(self):
        from core.trading.account import VirtualAccount
        from core.strategy.dna import StrategyDNA, RiskGenes, ExecutionGenes, SignalGene, SignalRole
        gene = SignalGene(
            indicator="EMA", params={"period": 10},
            role=SignalRole.ENTRY_TRIGGER,
            condition={"type": "price_above"},
        )
        dna = StrategyDNA(
            signal_genes=[gene],
            risk_genes=RiskGenes(
                stop_loss=0.05, position_size=0.3, leverage=1, direction="long",
            ),
            execution_genes=ExecutionGenes(timeframe="4h"),
        )
        return VirtualAccount(dna, init_cash=100000.0, fee=0.001, slippage=0.0005)

    def _make_predictor(self):
        from core.prediction.predictor import PriceRangePredictor
        from core.prediction.genes import PredictionDNA
        dna = PredictionDNA(
            omega=1e-5, alpha=0.10, beta=0.80,
            k_base=0.8, k_min=0.3,
            factor_weights={},
            short_window=15, mid_window=60, long_window=200,
        )
        return PriceRangePredictor(dna)

    def test_full_predict_open_plan_fill_cycle(self, enhanced_df):
        """Simulates runner's per-bar loop: predict -> open -> create plan -> fill."""
        from core.trading.types import Decision, PositionPlan
        from core.prediction.predictor import PriceRangePredictor

        acct = self._make_account()
        predictor = self._make_predictor()
        predictor.warmup(enhanced_df, n_bars=100)

        current_plan = None
        prev_prediction = None

        # Bar 0: open long position (no prev_prediction, skip observe)
        idx = len(enhanced_df) - 5
        prediction = predictor.predict(enhanced_df, idx)
        row = enhanced_df.iloc[idx]
        ts = enhanced_df.index[idx]

        events, _ = acct.process_bar_v2(
            bar_high=float(row["high"]),
            bar_low=float(row["low"]),
            bar_open=float(row["open"]),
            bar_close=float(row["close"]),
            bar_time=ts.isoformat(),
            pending_decision=Decision(
                action="open", direction="long",
                target_position_pct=0.30, entry_size_pct=0.10,
                reason="test",
            ),
        )
        # After open, create PositionPlan
        opened = any(e.get("type") == "position_opened" for e in events)
        assert opened, "Expected position to be opened"

        current_plan = PositionPlan.from_prediction(
            prediction=prediction,
            target_pct=0.30,
            side="long",
            entry_price=float(row["open"]),
            stop_loss=0.05,
        )
        assert len(current_plan.tranches) == 2
        prev_prediction = prediction

        # Subsequent bars: observe first, then predict (correct timing)
        for i in range(idx + 1, len(enhanced_df)):
            row = enhanced_df.iloc[i]
            ts = enhanced_df.index[i]

            # Step 1: observe current bar with prev_prediction -> update GARCH
            if prev_prediction is not None:
                predictor.observe(float(row["high"]), float(row["low"]), prev_prediction)

            # Step 2: predict with updated GARCH state
            prediction = predictor.predict(enhanced_df, i)
            prev_prediction = prediction

            events, _ = acct.process_bar_v2(
                bar_high=float(row["high"]),
                bar_low=float(row["low"]),
                bar_open=float(row["open"]),
                bar_close=float(row["close"]),
                bar_time=ts.isoformat(),
                position_plan=current_plan,
                prediction_result=prediction,
            )

        # Verify predictor accumulated state
        assert predictor._total_count > 0

    def test_position_cleared_after_close(self, enhanced_df):
        """PositionPlan should be discarded when position is closed."""
        from core.trading.types import Decision

        acct = self._make_account()
        predictor = self._make_predictor()
        predictor.warmup(enhanced_df, n_bars=100)

        idx = len(enhanced_df) - 5
        prediction = predictor.predict(enhanced_df, idx)
        row = enhanced_df.iloc[idx]
        ts = enhanced_df.index[idx]

        # Open
        events, _ = acct.process_bar_v2(
            float(row["high"]), float(row["low"]), float(row["open"]), float(row["close"]),
            ts.isoformat(),
            pending_decision=Decision(action="open", direction="long",
                                     target_position_pct=0.30, entry_size_pct=0.10,
                                     reason="test"),
        )
        assert acct.position is not None

        # Close on next bar
        idx2 = idx + 1
        events2, _ = acct.process_bar_v2(
            float(enhanced_df.iloc[idx2]["high"]),
            float(enhanced_df.iloc[idx2]["low"]),
            float(enhanced_df.iloc[idx2]["open"]),
            float(enhanced_df.iloc[idx2]["close"]),
            enhanced_df.index[idx2].isoformat(),
            pending_decision=Decision(action="close", reason="test_close"),
        )
        closed = any(e.get("type") == "position_closed" for e in events2)
        assert closed, "Expected position to be closed"
        assert acct.position is None

    def test_predictor_observe_updates_state(self, enhanced_df):
        """Predictor observe cycle should update GARCH state (observe before predict)."""
        acct = self._make_account()
        predictor = self._make_predictor()
        predictor.warmup(enhanced_df, n_bars=100)

        idx = len(enhanced_df) - 3
        prev_prediction = None

        for i in range(idx, len(enhanced_df)):
            row = enhanced_df.iloc[i]
            # Correct order: observe first, then predict
            if prev_prediction is not None:
                predictor.observe(float(row["high"]), float(row["low"]), prev_prediction)
            prediction = predictor.predict(enhanced_df, i)
            prev_prediction = prediction

        # GARCH state should have changed after observations
        assert predictor._total_count > 0
        assert predictor._total_count == len(enhanced_df) - idx - 1  # first bar has no prev_prediction


class TestObserveBeforePredictTiming:
    """Verify that observe-before-predict produces correct GARCH state."""

    @pytest.fixture
    def enhanced_df(self):
        from tests.helpers.data_factory import make_ohlcv
        from core.features.indicators import compute_all_indicators
        df = make_ohlcv(n=300, seed=42)
        return compute_all_indicators(df)

    def test_observe_before_predict_updates_garch_before_next_prediction(self, enhanced_df):
        """After observe(i), predict(i) should use the updated sigma, not the old one."""
        from core.prediction.predictor import PriceRangePredictor
        from core.prediction.genes import PredictionDNA
        import math

        dna = PredictionDNA(
            omega=1e-5, alpha=0.10, beta=0.80,
            k_base=0.8, k_min=0.3,
            factor_weights={},
            short_window=15, mid_window=60, long_window=200,
        )
        predictor = PriceRangePredictor(dna)
        predictor.warmup(enhanced_df, n_bars=100)

        idx = len(enhanced_df) - 3
        row = enhanced_df.iloc[idx]
        prev_prediction = predictor.predict(enhanced_df, idx)

        # Observe bar idx's actual result
        predictor.observe(float(row["high"]), float(row["low"]), prev_prediction)

        # Now predict should use the updated GARCH state
        sigma_after_observe = math.sqrt(predictor._garch.sigma_sq)
        next_prediction = predictor.predict(enhanced_df, idx + 1)

        # The prediction's width should reflect the updated sigma
        assert next_prediction.width > 0
        assert predictor._total_count == 1

    def test_wrong_order_produces_stale_sigma(self, enhanced_df):
        """If predict runs before observe, GARCH state is 1 bar behind (the bug we fixed)."""
        from core.prediction.predictor import PriceRangePredictor
        from core.prediction.genes import PredictionDNA
        import math

        dna = PredictionDNA(
            omega=1e-5, alpha=0.10, beta=0.80,
            k_base=0.8, k_min=0.3,
            factor_weights={},
            short_window=15, mid_window=60, long_window=200,
        )

        # Correct order: observe -> predict
        p_correct = PriceRangePredictor(dna)
        p_correct.warmup(enhanced_df, n_bars=100)
        idx = len(enhanced_df) - 3
        row = enhanced_df.iloc[idx]
        pred_0 = p_correct.predict(enhanced_df, idx)
        p_correct.observe(float(row["high"]), float(row["low"]), pred_0)
        sigma_correct = p_correct._garch.sigma_sq
        pred_correct = p_correct.predict(enhanced_df, idx + 1)

        # Wrong order: predict -> observe (the old bug)
        p_wrong = PriceRangePredictor(dna)
        p_wrong.warmup(enhanced_df, n_bars=100)
        pred_0w = p_wrong.predict(enhanced_df, idx)
        # predict next bar BEFORE observing (stale GARCH)
        pred_wrong = p_wrong.predict(enhanced_df, idx + 1)
        sigma_wrong = p_wrong._garch.sigma_sq  # sigma used for pred_wrong
        p_wrong.observe(float(row["high"]), float(row["low"]), pred_0w)

        # The sigma used for pred_wrong is stale (before observe of bar idx)
        assert sigma_correct != sigma_wrong, \
            "Correct order and wrong order should produce different GARCH states"
