"""冠军行挂盘分派锚（fix-evolution-champion-persist M4，判别式第 1/3 行挂盘 201）。

L1 主文件不承载（TestClient lifespan 线程会拖死批次，既有服务线程形制所限）；
落 tests/paper_trading/ 既有族旁——与纸盘域既有挂盘端点测试同环境形制。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import get_task, save_task  # noqa: E402
from core.persistence.db_ext import init_db_ext  # tests/evolution 形制
from core.strategy.dna import StrategyDNA  # noqa: E402
from tests.helpers.data_factory import make_dna, make_ohlcv  # noqa: E402


def _stub_score(dna) -> float:
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * (1.0 - abs(sl - 0.075) / 0.075) if abs(sl - 0.075) < 0.075 else 0.0, 4)


def _make_task(db, task_id: str) -> dict:
    dna = make_dna(indicator="RSI")
    save_task(db, task_id=task_id, target_score=80.0, template="profit_first",
              symbol="BTCUSDT", timeframe="4h", initial_dna=dna)
    row = get_task(db, task_id)
    assert row is not None
    task_row = dict(row)
    task_row["max_generations"] = 2
    task_row["continuous"] = 0
    return task_row


def test_champion_row_mounts_paper_task_201(tmp_path):
    """冠军库行经挂盘端点受理 201（判别式第 1/3 行挂盘面）。"""
    from core.features.indicators import compute_all_indicators
    from core.persistence.repository.strategy import get_strategy
    from api.app import create_app
    from fastapi.testclient import TestClient

    db = tmp_path / "cp-mount.db"
    init_db_ext(db)
    data_dir = tmp_path / "datamount"; data_dir.mkdir()

    task_row = _make_task(db, "cp-mount")
    enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))

    from api.runner import EvolutionRunner
    runner = EvolutionRunner(db_path=db, data_dir=data_dir)
    with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
         patch("api.runner_progress._push_ws"), \
         patch.object(runner, "_evaluate_population",
                      lambda pop, *a, **kw: [_stub_score(d) for d in pop]):
        runner._run_task(task_row)

    row = get_task(db, "cp-mount")
    assert row["status"] == "completed"
    assert row["champion_dna"], "completed 冠军 champion_dna 必落库"

    champion = StrategyDNA.from_json(row["champion_dna"])
    lib_row = get_strategy(db, champion.strategy_id)
    assert lib_row is not None, "冠军入库前提"
    assert lib_row["status"] == "active"

    app = create_app(db_path=db, data_dir=data_dir)
    with TestClient(app) as client:
        r = client.post("/api/paper-trading/tasks", json={
            "strategy_id": champion.strategy_id,
        })
        assert r.status_code == 201, f"挂盘受理: {r.status_code} {r.text[:200]}"
