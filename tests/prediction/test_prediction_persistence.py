"""Phase 3 integration tests: PredictionDNA persistence and API wiring."""

import pytest

pytestmark = [pytest.mark.unit]


class TestPredictionDNAPersistence:
    """Tests that prediction_dna_json is stored and retrieved correctly."""

    def test_save_and_retrieve_prediction_dna(self, db_path):
        """save_paper_trading_task should store prediction_dna_json."""
        from core.persistence.db_ext import init_db_ext, save_paper_trading_task, get_paper_trading_task
        init_db_ext(db_path)

        dna_json = '{"signal_genes": []}'
        pred_json = '{"omega": 1e-5, "alpha": 0.1, "beta": 0.8}'

        save_paper_trading_task(
            db_path,
            task_id="test_pred_001",
            dna_json=dna_json,
            prediction_dna_json=pred_json,
        )
        row = get_paper_trading_task(db_path, "test_pred_001")
        assert row is not None
        assert row["prediction_dna_json"] == pred_json

    def test_save_without_prediction_dna(self, db_path):
        """Should work fine without prediction_dna_json (uses defaults in runner)."""
        from core.persistence.db_ext import init_db_ext, save_paper_trading_task, get_paper_trading_task
        init_db_ext(db_path)

        save_paper_trading_task(
            db_path,
            task_id="test_pred_002",
            dna_json='{"signal_genes": []}',
        )
        row = get_paper_trading_task(db_path, "test_pred_002")
        assert row is not None
        assert row.get("prediction_dna_json") is None

    def test_update_prediction_dna(self, db_path):
        """Should be able to update prediction_dna_json via update_paper_trading_task."""
        from core.persistence.db_ext import init_db_ext, save_paper_trading_task, get_paper_trading_task, update_paper_trading_task
        init_db_ext(db_path)

        save_paper_trading_task(
            db_path,
            task_id="test_pred_003",
            dna_json='{"signal_genes": []}',
        )
        pred_json = '{"omega": 2e-5}'
        update_paper_trading_task(db_path, "test_pred_003", prediction_dna_json=pred_json)

        row = get_paper_trading_task(db_path, "test_pred_003")
        assert row["prediction_dna_json"] == pred_json


class TestPredictionDNAAPI:
    """Tests that the API layer accepts and returns prediction_dna_json."""

    def _make_dna_json(self):
        """Return a minimal valid StrategyDNA JSON."""
        from core.strategy.dna import StrategyDNA, RiskGenes, ExecutionGenes, SignalGene, SignalRole
        dna = StrategyDNA(
            signal_genes=[SignalGene(
                indicator="EMA", params={"period": 10},
                role=SignalRole.ENTRY_TRIGGER,
                condition={"type": "price_above"},
            )],
            risk_genes=RiskGenes(stop_loss=0.05, position_size=0.3, leverage=1, direction="long"),
            execution_genes=ExecutionGenes(timeframe="4h"),
        )
        return dna.to_json()

    def _make_prediction_dna_json(self):
        """Return a valid PredictionDNA JSON."""
        from core.prediction.genes import PredictionDNA
        dna = PredictionDNA(
            omega=1e-5, alpha=0.10, beta=0.80,
            k_base=0.8, k_min=0.3,
            factor_weights={},
            short_window=15, mid_window=60, long_window=200,
        )
        return dna.to_json()

    def test_create_task_with_prediction_dna(self, api_client):
        """POST /tasks should accept prediction_dna_json."""
        dna_json = self._make_dna_json()
        pred_json = self._make_prediction_dna_json()

        resp = api_client.post("/api/trading/tasks", json={
            "dna_json": dna_json,
            "prediction_dna_json": pred_json,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["prediction_dna_json"] == pred_json

    def test_create_task_without_prediction_dna(self, api_client):
        """POST /tasks should work without prediction_dna_json."""
        dna_json = self._make_dna_json()

        resp = api_client.post("/api/trading/tasks", json={
            "dna_json": dna_json,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data.get("prediction_dna_json") is None

    def test_restart_copies_prediction_dna(self, api_client):
        """POST /tasks/{id}/restart should copy prediction_dna_json to new task."""
        dna_json = self._make_dna_json()
        pred_json = self._make_prediction_dna_json()

        # Create original task
        resp = api_client.post("/api/trading/tasks", json={
            "dna_json": dna_json,
            "prediction_dna_json": pred_json,
        })
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Stop the task first
        api_client.post(f"/api/trading/tasks/{task_id}/stop")

        # Restart
        restart_resp = api_client.post(f"/api/trading/tasks/{task_id}/restart")
        assert restart_resp.status_code == 200
        new_data = restart_resp.json()
        assert new_data["prediction_dna_json"] == pred_json
        assert new_data["task_id"] != task_id

    def test_get_task_returns_prediction_dna(self, api_client):
        """GET /tasks/{id} should include prediction_dna_json."""
        dna_json = self._make_dna_json()
        pred_json = self._make_prediction_dna_json()

        create_resp = api_client.post("/api/trading/tasks", json={
            "dna_json": dna_json,
            "prediction_dna_json": pred_json,
        })
        task_id = create_resp.json()["task_id"]

        get_resp = api_client.get(f"/api/trading/tasks/{task_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["prediction_dna_json"] == pred_json
