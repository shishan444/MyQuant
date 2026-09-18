"""L3-H 编排层 happy path: 正常进化任务完整执行契约。

审计发现 test_runner.py 8 个测试全是错误路径, 正常任务完整执行(数据→进化→评分→冠军
持久化)零守护——编排层 happy path 裸奔。本契约跑通一次正常任务, 断言 status=completed +
champion 落库 + 进度持久化。

注: evaluate 用 stub(基于 stop_loss 的确定性 score)聚焦编排流程验证(status/phase/snapshot
串联)。回测正确性由 L0-B 守护, 此处不重复。真实回测 happy path 因进化随机 DNA 的 condition
解析健壮性问题(KeyError threshold)需后续独立处理——本身是一个发现。
"""
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import get_task, save_task  # noqa: E402
from core.persistence.db_ext import init_db_ext  # noqa: E402
from tests.helpers.data_factory import make_dna, make_ohlcv  # noqa: E402


def _stub_score(dna) -> float:
    """确定性 score(进化有效性已由 L0-A 守护, 此处仅驱动编排流程)。"""
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test_hp.db"
    init_db_ext(db)  # 含 phase/heartbeat_at/progress_json 扩展列(基础 init_db 不含)
    return db


def _make_task_row(db, task_id="happy-1"):
    dna = make_dna(indicator="RSI")
    save_task(db, task_id=task_id, target_score=80.0, template="profit_first",
              symbol="BTCUSDT", timeframe="4h", initial_dna=dna)
    row = get_task(db, task_id)
    assert row is not None
    return dict(row)


class TestEvolutionRunnerHappyPath:
    """L3-H: 正常进化任务完整执行(数据→进化→评分→冠军持久化)。"""

    def test_normal_task_completes_with_champion(self, tmp_db, tmp_path):
        from api.runner import EvolutionRunner
        from core.features.indicators import compute_all_indicators

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_path)
        task_row = _make_task_row(tmp_db, "happy-1")
        task_row["max_generations"] = 2
        task_row["continuous"] = 0

        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [_stub_score(d) for d in pop]):
            runner._run_task(task_row)

        row = get_task(tmp_db, "happy-1")
        assert row["status"] == "completed", (
            f"任务未完成: status={row['status']} stop={row.get('stop_reason')}"
        )
        assert row["champion_dna"], "完成任务应有 champion 落库"

    def test_happy_path_progress_persisted(self, tmp_db, tmp_path):
        """正常任务跑完后, generation_snapshot 被持久化。"""
        from api.runner import EvolutionRunner
        from core.persistence.db_ext import _connect
        from core.features.indicators import compute_all_indicators

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_path)
        task_row = _make_task_row(tmp_db, "happy-2")
        task_row["max_generations"] = 2
        task_row["continuous"] = 0

        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [_stub_score(d) for d in pop]):
            runner._run_task(task_row)

        with _connect(tmp_db) as conn:
            snap_count = conn.execute(
                "SELECT COUNT(*) FROM generation_snapshot WHERE task_id = ?",
                ("happy-2",)).fetchone()[0]
        assert snap_count >= 1, "正常任务应持久化 generation snapshot"
        assert get_task(tmp_db, "happy-2")["status"] == "completed"
