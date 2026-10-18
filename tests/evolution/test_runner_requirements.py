"""Tests for runner requirements building and best_fitness DB writes.

Covers:
- _build_requirements() priority path (from requirements_json)
- _build_requirements() fallback path (from legacy columns)
- _build_requirements() edge cases (malformed JSON, partial fields, type coercion)
- best_fitness column write verification
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from core.scoring.scorer import RequirementsConfig

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helper: instantiate EvolutionRunner minimally for testing _build_requirements
# ---------------------------------------------------------------------------

def _make_runner():
    """Create a minimal EvolutionRunner instance with mocked dependencies."""
    from unittest.mock import MagicMock, patch

    # EvolutionRunner.__init__ requires several args; mock them all
    with patch("api.runner.EvolutionRunner.__init__", return_value=None):
        from api.runner import EvolutionRunner
        runner = EvolutionRunner.__new__(EvolutionRunner)
    return runner


# ===========================================================================
# S1: _build_requirements() priority path
# ===========================================================================

class TestBuildRequirementsPriority:
    """requirements_json present and valid → parse full 5-dim config."""

    def test_valid_json_string(self):
        """JSON string with all 5 fields parsed correctly."""
        runner = _make_runner()
        task_row = {
            "requirements_json": json.dumps({
                "min_annual_return": 0.25,
                "max_drawdown": 0.15,
                "min_win_rate": 0.55,
                "min_total_trades": 20,
                "min_profit_factor": 1.5,
            }),
        }
        result = runner._build_requirements(task_row)
        assert isinstance(result, RequirementsConfig)
        assert result.min_annual_return == 0.25
        assert result.max_drawdown == 0.15
        assert result.min_win_rate == 0.55
        assert result.min_total_trades == 20
        assert result.min_profit_factor == 1.5

    def test_already_parsed_dict(self):
        """requirements_json as dict (not string) used directly."""
        runner = _make_runner()
        task_row = {
            "requirements_json": {
                "min_annual_return": 0.20,
                "max_drawdown": 0.25,
                "min_win_rate": 0.50,
                "min_total_trades": 30,
                "min_profit_factor": 2.0,
            },
        }
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.20
        assert result.max_drawdown == 0.25
        assert result.min_profit_factor == 2.0

    def test_partial_fields_use_defaults(self):
        """Missing fields in requirements_json get default values."""
        runner = _make_runner()
        task_row = {
            "requirements_json": json.dumps({
                "min_annual_return": 0.18,
                # max_drawdown missing → default 0.30
            }),
        }
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.18
        assert result.max_drawdown == 0.30  # default
        assert result.min_win_rate == 0.0  # disabled by default
        assert result.min_total_trades == 10  # default
        assert result.min_profit_factor == 1.2  # default

    def test_empty_dict_uses_all_defaults(self):
        """Empty requirements_json dict → all defaults from dataclass."""
        runner = _make_runner()
        task_row = {"requirements_json": json.dumps({})}
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.0
        assert result.max_drawdown == 0.30
        assert result.min_win_rate == 0.0
        assert result.min_total_trades == 10
        assert result.min_profit_factor == 1.2

    def test_legacy_columns_ignored_when_requirements_json_present(self):
        """Legacy min_annual_return column ignored when requirements_json exists."""
        runner = _make_runner()
        task_row = {
            "requirements_json": json.dumps({"min_annual_return": 0.20}),
            "min_annual_return": 0.50,  # legacy — should be ignored
            "max_drawdown_limit": 0.10,  # legacy — should be ignored
        }
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.20  # from requirements_json
        assert result.max_drawdown == 0.30  # default, not legacy


# ===========================================================================
# S2: _build_requirements() fallback path
# ===========================================================================

class TestBuildRequirementsFallback:
    """requirements_json absent/invalid → fall back to legacy columns."""

    def test_none_falls_back(self):
        """requirements_json=None → legacy path."""
        runner = _make_runner()
        task_row = {
            "requirements_json": None,
            "min_annual_return": 0.12,
            "max_drawdown_limit": 0.25,
        }
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.12
        assert result.max_drawdown == 0.25

    def test_missing_key_falls_back(self):
        """No requirements_json key → legacy path."""
        runner = _make_runner()
        task_row = {
            "min_annual_return": 0.12,
            "max_drawdown_limit": 0.25,
        }
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.12
        assert result.max_drawdown == 0.25

    def test_empty_string_falls_back(self):
        """requirements_json='' (falsy) → legacy path."""
        runner = _make_runner()
        task_row = {
            "requirements_json": "",
            "min_annual_return": 0.12,
        }
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.12

    def test_malformed_json_raises(self):
        """Invalid JSON string → 显式报错，不静默回退 legacy（B1.1 重锚定）。

        旧行为（malformed → fallback legacy 列）会静默偷换进化优化目标，
        已由 B1.1 修复为显式 ValueError + error 日志（含原因链）。
        """
        runner = _make_runner()
        task_row = {
            "requirements_json": "{invalid json!!!",
            "min_annual_return": 0.10,
            "max_drawdown_limit": 0.30,
        }
        with pytest.raises(ValueError):
            runner._build_requirements(task_row)

    def test_legacy_min_annual_return_default(self):
        """Legacy path: min_annual_return missing → default 0.0 (constraint disabled)."""
        runner = _make_runner()
        task_row = {}  # no requirements_json, no min_annual_return
        result = runner._build_requirements(task_row)
        assert result.min_annual_return == 0.0  # constraint disabled by default

    def test_legacy_max_drawdown_zero_falls_back(self):
        """Legacy path: max_drawdown_limit=0 → 'or' falls back to 0.30."""
        runner = _make_runner()
        task_row = {"max_drawdown_limit": 0}
        result = runner._build_requirements(task_row)
        assert result.max_drawdown == 0.30  # 0 is falsy, or → 0.30

    def test_legacy_max_drawdown_none_falls_back(self):
        """Legacy path: max_drawdown_limit=None → 'or' falls back to 0.30."""
        runner = _make_runner()
        task_row = {"max_drawdown_limit": None}
        result = runner._build_requirements(task_row)
        assert result.max_drawdown == 0.30

    def test_fallback_uses_dataclass_defaults_for_3_extra_dims(self):
        """Legacy path: win_rate/trades/pf use RequirementsConfig defaults."""
        runner = _make_runner()
        task_row = {"min_annual_return": 0.10}
        result = runner._build_requirements(task_row)
        assert result.min_win_rate == 0.0  # disabled
        assert result.min_total_trades == 10
        assert result.min_profit_factor == 1.2


# ===========================================================================
# S3: best_fitness DB write verification
# ===========================================================================

@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a test DB with evolution_task table."""
    db_path = tmp_path / "test_req.db"
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db_path)
    return db_path


class TestBestFitnessDBWrite:
    """Verify best_fitness column can be written and read."""

    def _insert_task(self, db_path: Path, task_id: str = "test-req-001"):
        """Insert a task row using save_task (same as test_evolution_arch.py)."""
        from core.persistence.db import save_task
        from tests.helpers.data_factory import make_dna
        dna = make_dna()
        save_task(db_path, task_id, 80.0, "profit_first", "BTCUSDT", "4h", dna)

    def _get_task(self, db_path: Path, task_id: str) -> Dict[str, Any]:
        """Read task row as dict."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM evolution_task WHERE task_id = ?", (task_id,)
        ).fetchone()
        result = dict(row) if row else {}
        conn.close()
        return result

    def test_best_fitness_initially_null(self, test_db: Path):
        """best_fitness should be NULL after task creation."""
        self._insert_task(test_db, "init-null")
        task = self._get_task(test_db, "init-null")
        assert task["best_fitness"] is None

    def test_update_best_fitness(self, test_db: Path):
        """UPDATE can set best_fitness to a float value."""
        self._insert_task(test_db, "update-fit")
        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "UPDATE evolution_task SET best_fitness = ?, best_score = ? WHERE task_id = ?",
            (1.2345, 1.2345, "update-fit"),
        )
        conn.commit()
        conn.close()

        task = self._get_task(test_db, "update-fit")
        assert task["best_fitness"] == pytest.approx(1.2345)
        assert task["best_score"] == pytest.approx(1.2345)

    def test_update_best_fitness_with_metrics(self, test_db: Path):
        """UPDATE with best_fitness + champion_metrics + champion_dimension_scores."""
        self._insert_task(test_db, "update-metrics")
        metrics = {"annual_return": 0.25, "sharpe_ratio": 1.5}
        dim_scores = {"annual_return": 1.67, "max_drawdown": 1.2}
        satisfaction = {"annual_return": {"actual": 0.25, "required": 0.15, "ratio": 1.67, "met": True}}

        conn = sqlite3.connect(str(test_db))
        conn.execute(
            """UPDATE evolution_task
               SET best_fitness = ?, champion_metrics = ?,
                   champion_dimension_scores = ?, champion_satisfaction = ?
               WHERE task_id = ?""",
            (1.5, json.dumps(metrics), json.dumps(dim_scores), json.dumps(satisfaction), "update-metrics"),
        )
        conn.commit()
        conn.close()

        task = self._get_task(test_db, "update-metrics")
        assert task["best_fitness"] == pytest.approx(1.5)
        assert json.loads(task["champion_metrics"])["annual_return"] == 0.25
        assert json.loads(task["champion_dimension_scores"])["annual_return"] == 1.67
        assert json.loads(task["champion_satisfaction"])["annual_return"]["met"] is True

    def test_conditional_update_best_fitness(self, test_db: Path):
        """UPDATE with optimistic lock: only update if current value is lower."""
        self._insert_task(test_db, "cond-update")
        conn = sqlite3.connect(str(test_db))

        # First update: NULL → 0.8
        conn.execute(
            "UPDATE evolution_task SET best_fitness = ?, best_score = ? WHERE task_id = ? AND (best_fitness IS NULL OR best_fitness < ?)",
            (0.8, 0.8, "cond-update", 0.8),
        )
        conn.commit()

        # Second update: 0.8 → 1.2 (should succeed, 0.8 < 1.2)
        cursor = conn.execute(
            "UPDATE evolution_task SET best_fitness = ?, best_score = ? WHERE task_id = ? AND (best_fitness IS NULL OR best_fitness < ?)",
            (1.2, 1.2, "cond-update", 1.2),
        )
        conn.commit()
        assert cursor.rowcount == 1

        # Third update: 1.2 → 0.5 (should NOT succeed, 1.2 > 0.5)
        cursor = conn.execute(
            "UPDATE evolution_task SET best_fitness = ?, best_score = ? WHERE task_id = ? AND (best_fitness IS NULL OR best_fitness < ?)",
            (0.5, 0.5, "cond-update", 0.5),
        )
        conn.commit()
        assert cursor.rowcount == 0

        conn.close()
        task = self._get_task(test_db, "cond-update")
        assert task["best_fitness"] == pytest.approx(1.2)  # unchanged

    def test_requirements_json_roundtrip(self, test_db: Path):
        """requirements_json can be stored and retrieved faithfully."""
        self._insert_task(test_db, "req-rt")
        req_json = json.dumps({
            "min_annual_return": 0.18,
            "max_drawdown": 0.25,
            "min_win_rate": 0.50,
            "min_total_trades": 25,
            "min_profit_factor": 1.8,
        })

        conn = sqlite3.connect(str(test_db))
        conn.execute(
            "UPDATE evolution_task SET requirements_json = ? WHERE task_id = ?",
            (req_json, "req-rt"),
        )
        conn.commit()
        conn.close()

        task = self._get_task(test_db, "req-rt")
        parsed = json.loads(task["requirements_json"])
        assert parsed["min_annual_return"] == 0.18
        assert parsed["min_profit_factor"] == 1.8
