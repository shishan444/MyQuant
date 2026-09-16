"""B1 吞错误导止血的复现测试（refactor-myquant-backend-b1-b11 · B1 组）。

五处 P0 锚点，每处测试先行（红→绿）：
- 1.1 core/evolution/evaluation.py build_requirements 解析失败静默回退默认 objective
- 1.2 api/runner.py 快照写失败静默丢弃
- 1.3 core/trading/runner.py bar 游标前移与事件落库的原子性
- 1.4 api/routes/strategies.py verify mark_failed 路径自身吞错
- 1.5 api/routes/data.py 图表六连吞错拖垮整层
"""

import json
import logging
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """B1.2/B1.4 场景共用的扩展 schema 测试库。"""
    from core.persistence.db_ext import init_db_ext

    db = tmp_path / "test_b1.db"
    init_db_ext(db)
    return db


# ===========================================================================
# B1.1: build_requirements 解析失败不得静默回退默认 objective
# ===========================================================================

class TestBuildRequirementsMalformedVisible:
    """requirements_json 存在但解析失败 → 显式报错 + error 日志，不回退 sharpe。

    静默回退会偷换进化优化目标（objective 默认 sharpe），任务朝错误目标
    优化数百代且用户无感知——属数据正确性失败，必须可见。
    """

    def test_malformed_json_raises_with_log(self, caplog):
        """非法 JSON 字符串 → raise ValueError + error 日志含原因链。"""
        from core.evolution import evaluation

        task_row = {
            "task_id": "b1-malformed",
            "requirements_json": "{invalid json!!!",
            "min_annual_return": 0.10,
        }
        with caplog.at_level(logging.ERROR, logger="core.evolution.evaluation"):
            with pytest.raises(ValueError):
                evaluation.build_requirements(task_row)

        # 日志含原因链（exc_info）与定位信息
        assert any("requirements_json" in r.message for r in caplog.records)
        assert any(r.exc_info is not None for r in caplog.records)

    def test_valid_json_non_object_raises(self):
        """合法 JSON 但顶层非对象（list/null）→ 同样显式报错。"""
        from core.evolution import evaluation

        for raw in (json.dumps([1, 2, 3]), "null", '"sharpe"'):
            with pytest.raises(ValueError):
                evaluation.build_requirements({"requirements_json": raw})

    def test_falsy_keeps_legacy_path(self):
        """requirements_json 缺失/None/空串 → legacy 列路径，行为不变。"""
        from core.evolution import evaluation

        for raw in (None, ""):
            req = evaluation.build_requirements(
                {"requirements_json": raw, "min_annual_return": 0.12}
            )
            assert req.min_annual_return == 0.12
        req = evaluation.build_requirements({"min_annual_return": 0.12})
        assert req.min_annual_return == 0.12

    def test_valid_json_still_parses(self):
        """合法配置解析不受影响（回归守护）。"""
        from core.evolution import evaluation

        req = evaluation.build_requirements({
            "requirements_json": json.dumps({
                "objective": "annual_return",
                "min_annual_return": 0.25,
            }),
        })
        assert req.objective == "annual_return"
        assert req.min_annual_return == 0.25

    def test_runner_delegate_propagates(self):
        """EvolutionRunner._build_requirements（委托转发）同语义透传。"""
        from api.runner import EvolutionRunner

        runner = EvolutionRunner.__new__(EvolutionRunner)
        with pytest.raises(ValueError):
            runner._build_requirements({"requirements_json": "{invalid"})


# ===========================================================================
# B1.2: 快照写失败不得静默丢弃
# ===========================================================================

@pytest.fixture
def trading_db_with_task(tmp_path: Path) -> Path:
    """B1.3 场景：模拟盘任务库（含 task-001）。"""
    from core.persistence.db_ext import init_db_ext, save_paper_trading_task
    from tests.helpers.data_factory import make_dna

    db_path = tmp_path / "test_b1_trading.db"
    init_db_ext(db_path)
    dna = make_dna(direction="long", leverage=1)
    save_paper_trading_task(
        db_path, task_id="task-001", dna_json=dna.to_json(),
        symbol="BTCUSDT", timeframe="4h", initial_cash=100_000, fee=0.001,
    )
    return db_path

@pytest.mark.integration
class TestSnapshotWriteFailureVisible:
    """save_snapshot 失败 → error 日志暴露（含原因链），进化继续不中断。

    快照为每代种群状态持久化（非关键路径数据）：写失败降级为「本代快照
    缺失 + 可见错误」，不应吞掉也不应杀死任务。
    """

    def test_snapshot_failure_logs_error_and_task_completes(self, tmp_db, tmp_path, caplog):
        """快照写失败 → error 日志 + 任务仍 completed。"""
        from unittest.mock import patch

        from api.runner import EvolutionRunner
        from core.features.indicators import compute_all_indicators
        from core.persistence.db import get_task
        from tests.helpers.data_factory import make_dna, make_ohlcv

        def _stub_score(dna) -> float:
            sl = float(dna.risk_genes.stop_loss)
            return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)

        runner = EvolutionRunner(db_path=tmp_db, data_dir=tmp_path)
        from core.persistence.db import save_task
        save_task(tmp_db, task_id="b1-snap", target_score=80.0, template="profit_first",
                  symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna(indicator="RSI"))
        task_row = dict(get_task(tmp_db, "b1-snap"))
        task_row["max_generations"] = 2
        task_row["continuous"] = 0

        enhanced_df = compute_all_indicators(make_ohlcv(120, "4h"))
        with caplog.at_level(logging.ERROR, logger="api.runner"), \
             patch("core.data.mtf_loader.load_and_prepare_df", return_value=enhanced_df), \
             patch("api.runner._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [_stub_score(d) for d in pop]), \
             patch("api.runner.save_snapshot", side_effect=RuntimeError("disk full")):
            runner._run_task(task_row)

        row = get_task(tmp_db, "b1-snap")
        assert row["status"] == "completed", (
            f"快照失败不应杀死任务: status={row['status']} stop={row.get('stop_reason')}"
        )
        # error 日志可见且含原因链
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("save_snapshot" in r.message for r in errors), "快照写失败必须留下 error 日志"
        assert any(r.exc_info is not None for r in errors), "日志必须含原因链 (exc_info)"


# ===========================================================================
# B1.3: bar 游标前移与事件落库的原子性/幂等性
# ===========================================================================

class _StopAfterTwoWaits:
    """跑两轮主循环后停止（第一轮中断重放，第二轮重试）。"""

    def __init__(self):
        self._waits = 0

    @property
    def stop_requested(self):
        return self._waits >= 2

    def check_stop(self):
        from core.trading.runner import TaskStopRequested
        if self._waits >= 2:
            raise TaskStopRequested()

    def wait(self, timeout):
        self._waits += 1

    def request_stop(self):
        self._waits = 2


@pytest.mark.integration
class TestBarCursorReplayNoDuplicate:
    """bar 循环中途失败后重试，不得重复落库 paper_trade 事件。

    现状缺陷：游标（df）在全部 bar 处理完后才前移（runner.py:406），
    中途异常被 :407 兜底后循环继续，下一轮从旧游标重放全部新 bar，
    已落库事件重复写入。
    """

    def test_replayed_bars_do_not_duplicate_events(self, trading_db_with_task, tmp_path):
        """第 3 根新 bar 处理失败一次 → 重试后每 (bar_time, action) 恰一行。"""
        import sqlite3
        from types import SimpleNamespace
        from unittest.mock import patch

        from core.trading.runner import TradingRunner
        from core.persistence.db_ext import get_paper_trading_task
        from tests.helpers.data_factory import make_ohlcv

        runner = TradingRunner(db_path=trading_db_with_task, data_dir=tmp_path)
        task_row = get_paper_trading_task(trading_db_with_task, "task-001")
        task_row["created_at"] = None  # 不过滤历史 bar

        df_initial = make_ohlcv(100, "4h")
        df_extended = make_ohlcv(105, "4h")
        new_bar_times = [ts.isoformat() for ts in df_extended.index[100:]]
        fail_target = new_bar_times[2]  # 第 3 根新 bar 首次处理时模拟中断
        failed_once = set()

        def fake_process_bar(**kwargs):
            bt = kwargs["bar_time"]
            if bt == fail_target and bt not in failed_once:
                failed_once.add(bt)
                raise RuntimeError("simulated interruption")
            return SimpleNamespace(
                events=[{
                    "type": "position_opened", "side": "long",
                    "entry_price": 100.0, "quantity": 1.0, "fee_paid": 0.0,
                }],
                pending_decision=None,
            )

        controller = _StopAfterTwoWaits()
        with patch.object(runner, "_load_data", return_value=df_initial), \
             patch.object(runner, "_fetch_and_update", return_value=df_extended), \
             patch.object(runner, "_init_predictor", return_value=None), \
             patch.object(runner, "_push_position_update"), \
             patch("core.trading.pipeline.DecisionPipeline") as _Pipeline:
            _Pipeline.return_value.process_bar.side_effect = fake_process_bar
            runner._execute_task(task_row, "task-001", controller)

        conn = sqlite3.connect(str(trading_db_with_task))
        dup = conn.execute(
            "SELECT task_id, bar_time, action, COUNT(*) c FROM paper_trade "
            "GROUP BY task_id, bar_time, action HAVING c > 1"
        ).fetchall()
        per_bar = conn.execute(
            "SELECT bar_time, COUNT(*) c FROM paper_trade GROUP BY bar_time"
        ).fetchall()
        conn.close()

        assert dup == [], f"重放产生重复事件: {dup}"
        # 5 根新 bar 的事件全部恰好落库一次（重试覆盖失败 bar，不漏不重）
        assert len(per_bar) == 5, f"应有 5 根新 bar 的事件，实际 {len(per_bar)}"
        assert all(c == 1 for _, c in per_bar)


class TestSavePaperTradeIdempotent:
    """幂等兜底层：同 (task_id, bar_time, action) 重复写入只留一行。"""

    def test_duplicate_insert_ignored(self, trading_db_with_task):
        """同一事件插入两次 → 表中恰一行（INSERT OR IGNORE + 唯一约束）。"""
        from core.persistence.db_ext import save_paper_trade, count_paper_trades

        for _ in range(2):
            save_paper_trade(
                trading_db_with_task,
                task_id="task-001", bar_time="2024-01-01T00:00:00",
                side="long", action="open",
                price=100.0, quantity=1.0,
            )
        assert count_paper_trades(trading_db_with_task, "task-001") == 1

    def test_same_bar_different_action_both_kept(self, trading_db_with_task):
        """同 bar 的 open 与 close 是不同事件，各自保留。"""
        from core.persistence.db_ext import save_paper_trade, count_paper_trades

        save_paper_trade(
            trading_db_with_task,
            task_id="task-001", bar_time="2024-01-01T00:00:00",
            side="long", action="open", price=100.0, quantity=1.0,
        )
        save_paper_trade(
            trading_db_with_task,
            task_id="task-001", bar_time="2024-01-01T00:00:00",
            side="long", action="close", price=110.0, quantity=1.0, pnl=10.0,
        )
        assert count_paper_trades(trading_db_with_task, "task-001") == 2


# ===========================================================================
# B1.4: verify mark_failed 路径自身不吞错
# ===========================================================================

class TestVerifyMarkFailedNotSwallowed:
    """失败状态写入失败必须可见——否则 session 卡 running 且无任何痕迹。

    mark_failed 是失败路径的兜底写入；它自身失败（DB 故障）时静默 pass
    意味着用户面对永久 running 的 session，无从排查。
    """

    def test_mark_failed_write_failure_is_logged(self, tmp_db, caplog):
        """update_verify_session 抛错 → error 日志含 session 与原因链。"""
        from unittest.mock import patch

        from api.routes.strategies import _VerifyProcessor

        processor = _VerifyProcessor.__new__(_VerifyProcessor)
        processor.db_path = tmp_db
        processor.session_id = "b1-verify-session"

        with caplog.at_level(logging.ERROR, logger="api.routes.strategies"), \
             patch("api.routes.strategies.verify.update_verify_session",
                   side_effect=RuntimeError("db locked")):
            # 不得再抛出（调用方在异常处理路径中），但必须留痕
            processor.mark_failed("boom")

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("b1-verify-session" in r.message for r in errors), (
            "失败状态写入失败必须留下含 session_id 的 error 日志"
        )
        assert any(r.exc_info is not None for r in errors), "日志必须含原因链"

    def test_mark_failed_success_path_unchanged(self, tmp_db):
        """写入成功路径行为不变（status=failed + error_message）。"""
        from api.routes.strategies import _VerifyProcessor
        from core.persistence.db_ext import save_verify_session, get_verify_session

        save_verify_session(
            tmp_db, session_id="b1-ok",
            strategy_ids='["s1"]', data_ranges='[{"start":"2024-01-01","end":"2024-06-01"}]',
        )
        processor = _VerifyProcessor.__new__(_VerifyProcessor)
        processor.db_path = tmp_db
        processor.session_id = "b1-ok"

        processor.mark_failed("some error")

        row = get_verify_session(tmp_db, "b1-ok")
        assert row["status"] == "failed"
        assert row["error_message"] == "some error"


# ===========================================================================
# B1.5: 图表指标六连吞错改逐指标降级 + 日志
# ===========================================================================

@pytest.mark.integration
class TestChartIndicatorsPerLayerDegradation:
    """单指标计算失败 → 该层缺失必须留下 warning 日志，其他层不受影响。

    现状六处 except-pass（BB/RSI/RVOL/VWMA/MACD/KDJ）+ EMA 的
    except-continue 均静默——图表「缺层」与「未开启」无法区分。
    """

    def test_failed_indicator_logs_and_others_survive(self, tmp_path, caplog):
        """RSI 计算抛错 → warning 日志 + rsi 层缺失 + macd 层正常。"""
        from unittest.mock import patch

        from api.routes.data import get_chart_indicators
        from core.features import indicators as _ind
        from tests.helpers.data_factory import make_ohlcv

        df = make_ohlcv(200, "4h")
        df.to_parquet(tmp_path / "BTCUSDT_4h.parquet")

        real_compute = _ind._compute_indicator

        def fake_compute(df, name, params):
            if name == "RSI":
                raise RuntimeError("rsi boom")
            return real_compute(df, name, params)

        with caplog.at_level(logging.WARNING, logger="api.routes.data"), \
             patch("core.features.indicators._compute_indicator", fake_compute):
            result = get_chart_indicators(
                "BTCUSDT", "4h",
                boll_enabled=False,
                rsi_enabled=True,
                macd_enabled=True,
                data_dir=tmp_path,
            )

        # 失败层缺失（降级语义保持）
        assert result["rsi"] is None
        # 其他层不受拖累
        assert result["macd"] is not None and "macd" in result["macd"]
        # 静默消失必须有痕
        warnings_ = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("RSI" in r.getMessage() for r in warnings_), (
            "指标层计算失败必须留下含指标名的 warning 日志"
        )
        assert any(r.exc_info is not None for r in warnings_), "日志必须含原因链"
