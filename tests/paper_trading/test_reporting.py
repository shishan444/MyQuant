"""rebuild-paper-trading 4.1：验证报告器三层组装行为锚。

覆盖任务完成标准：bars_per_year 两锚（4h=2190 缺省等值+1h=8760 传入）、
零交易退化透传（win_rate=None 非 0.0）、对照失败不炸报告（真桥空数据
目录走 ResearchError 实路径+pending 无区间路径）、60/5 四格参数化、
schema 无 verdict/feasible/recommendation 断言、三终态固化+未结题
TaskNotConcludedError（组 5 转 409 的判定支持）、修订版路径（恒
version=2 重算覆盖/初版可取/缺省最新/v1-v2 指标相同差异仅 funding
披露段）。库建形制沿 tests/persistence/test_repository_paper.py。
"""

import json
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

BASE_TIME = "2026-10-01T00:00:00+00:00"
DNA_JSON = '{"risk_genes": {"leverage": 2}}'

# fake bridge 回传的对照 metrics（形制沿 bridge 返回十键）
BT_METRICS = {
    "total_return": 0.05, "sharpe_ratio": 1.5, "max_drawdown": -0.2,
    "win_rate": 0.6, "total_trades": 8, "liquidated": False, "alpha": 0.01,
    "market_annual_return": 0.04, "benchmark_return": 0.04,
    "excess_return": 0.01,
}


def _make_db(tmp_path: Path) -> Path:
    from core.persistence.db_ext import init_db_ext
    db = tmp_path / "paper.db"
    init_db_ext(db)
    return db


def _seed_task(db: Path, task_id: str = "pt_report00001", *,
               timeframe: str = "4h", status: str = "running",
               started: bool = True, research_status: str = "unverified",
               leverage: int = 5, started_at: str = BASE_TIME) -> str:
    """直建任务（测试通道）+状态机迁移到目标态。"""
    from core.persistence.repository import paper
    paper.create_task(
        db, task_id=task_id, strategy_id="strat-1",
        dna_snapshot_json=DNA_JSON, symbol="BTCUSDT", timeframe=timeframe,
        research_status_snapshot=research_status,
        initial_cash=50_000.0, leverage=leverage, window_bars=180)
    kwargs = {"status": status}
    if started:
        kwargs["started_at"] = started_at
    paper.update_task(db, task_id, **kwargs)
    return task_id


def _bar_time(i: int, tf_minutes: int) -> str:
    return (pd.Timestamp(BASE_TIME)
            + pd.Timedelta(minutes=tf_minutes * i)).isoformat()


def _seed_equity_bars(db: Path, task_id: str, n_bars: int, *,
                      tf_minutes: int = 240, equity0: float = 50_000.0,
                      step: float = 100.0) -> None:
    """n_bars 根权益快照（bar_time 等间隔升序；bars_processed 随根推进）。"""
    from core.persistence.repository import paper
    for i in range(n_bars):
        equity = equity0 + step * i
        paper.append_bar(
            db, task_id, bar_time=_bar_time(i, tf_minutes),
            fills=[],
            equity={"equity": equity, "balance": equity,
                    "unrealized_pnl": 0.0, "position_side": None},
            task_state={"bars_processed": i + 1,
                        "last_bar_time": _bar_time(i, tf_minutes)})


def _seed_exit_fills(db: Path, task_id: str, pnls: list[float], *,
                     tf_minutes: int = 240, base: int = 2) -> None:
    """出场类成交（realized_pnl 回填行——报告「出场类」判据），落在
    既有权益 bar 上：append_bar 的 equity 行被 (task_id, bar_time) 唯一键
    OR IGNORE 首写吸收，不虚增权益行数（bars_run 口径不被出场构造污染）。"""
    from core.persistence.repository import paper
    for j, pnl in enumerate(pnls):
        paper.append_bar(
            db, task_id, bar_time=_bar_time(base + j, tf_minutes),
            fills=[{"seq": 3, "kind": "close", "side": "long",
                    "price": 105.0, "quantity": 1.0, "fee": 0.1,
                    "realized_pnl": pnl, "reason": "signal"}],
            equity={"equity": 50_000.0, "balance": 50_000.0,
                    "unrealized_pnl": 0.0, "position_side": None})


def _seed_funding(db: Path, task_id: str, rows: list[dict]) -> None:
    """资金费明细（status/rate/amount 直收——覆盖三态造数面）。"""
    from core.persistence.repository import paper
    paper.append_bar(
        db, task_id, bar_time=_bar_time(2000, 240), fills=[],
        equity={"equity": 50_000.0, "balance": 50_000.0,
                "unrealized_pnl": 0.0, "position_side": None},
        funding=rows)


def _patch_bridge(monkeypatch, captured: dict, *,
                  metrics=None, error=None):
    """替身桥：捕获全参；按需回传 metrics 或抛 error。"""
    def fake(db_path, data_dir, dna, symbol, timeframe, start, end,
             warmup_bars, *, init_cash, fee, slippage):
        captured.update({
            "dna": dna, "symbol": symbol, "timeframe": timeframe,
            "start": start, "end": end, "warmup_bars": warmup_bars,
            "init_cash": init_cash, "fee": fee, "slippage": slippage,
        })
        if error is not None:
            raise error
        return metrics, None, 200, 100
    monkeypatch.setattr(
        "core.analysis.backtest_bridge.run_backtest_on_range", fake)


def _collect_keys(obj, acc: set) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            acc.add(key)
            _collect_keys(value, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_keys(item, acc)


class TestPerformanceLayer:
    """绝对绩效层：bars_per_year 换算锚+零交易退化透传。"""

    def test_bars_per_year_anchors(self):
        """4h=2190 与 compute_metrics 缺省等值；1h=8760 显式换算。"""
        from core.paper_trading.reporting import _bars_per_year
        from core.scoring.metrics import compute_metrics
        import inspect

        assert _bars_per_year("4h") == 2190
        assert _bars_per_year("1h") == 8760
        default = inspect.signature(compute_metrics).parameters[
            "bars_per_year"].default
        assert default == 2190  # 4h 档=缺省等值（365×86400/14400）
        # 非 4h 档必须显式传入（design §6.2）——换算不落缺省档
        assert _bars_per_year("1h") != default

    @pytest.mark.parametrize("timeframe,tf_minutes,expected", [
        ("4h", 240, 2190), ("1h", 60, 8760),
    ])
    def test_bars_per_year_flows_into_metrics(
            self, tmp_path, timeframe, tf_minutes, expected):
        """黑盒锚：backtest_years=根数/换算档（年化周期不走错档）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db, timeframe=timeframe)
        _seed_equity_bars(db, tid, 10, tf_minutes=tf_minutes)
        _seed_exit_fills(db, tid, [100.0, -50.0], tf_minutes=tf_minutes)

        report = build_report(db, tmp_path, tid)
        assert report["performance"]["backtest_years"] == pytest.approx(
            10 / expected)

    def test_zero_trades_win_rate_none(self, tmp_path):
        """零交易退化：win_rate=None（缺失语义透传，非 0.0）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)

        report = build_report(db, tmp_path, tid)
        assert report["performance"]["win_rate"] is None
        assert report["performance"]["win_rate"] != 0.0
        assert report["performance"]["total_trades"] == 0


class TestDeviationLayer:
    """偏差对照层：失败不炸报告+成功路径参数与杠杆覆写。"""

    def test_research_error_real_bridge_not_fatal(self, tmp_path):
        """真桥空数据目录→ResearchError→该层 unavailable，其余两层正常
        （spec Scenario「偏差对照失败不炸报告」——真集成路径非纯替身）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills(db, tid, [100.0])

        report = build_report(db, tmp_path / "nodata", tid)
        assert report["deviation"]["backtest"] is None
        assert "RESEARCH_DATA_INSUFFICIENT" in report["deviation"][
            "unavailable_reason"]
        # performance 与 evidence 层正常返回
        assert report["performance"]["total_trades"] == 1
        assert report["evidence"]["bars_run"] == 10
        assert len(report["deviation"]["attribution_notes"]) == 4

    def test_not_started_no_range(self, tmp_path):
        """pending 未开跑：无对照区间→unavailable（不调桥）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db, status="pending", started=False)

        report = build_report(db, tmp_path, tid)
        assert report["deviation"]["backtest"] is None
        assert "无对照区间" in report["deviation"]["unavailable_reason"]

    def test_success_path_params_and_leverage_override(
            self, tmp_path, monkeypatch):
        """成功路径：任务参数全量透传+dna_override 杠杆覆写（deepcopy
        快照——库内快照原文不被污染）+diff 四键手算锚。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        # started_at 落在 bar 中段（05:30）——start 须 floor 对齐到所在
        # bar 开盘（04:00），bridge 的 index >= start 切区间才含该 bar
        tid = _seed_task(db, leverage=5,
                         started_at="2026-10-01T05:30:00+00:00")
        _seed_equity_bars(db, tid, 10, equity0=50_000.0, step=500.0)
        _seed_exit_fills(db, tid, [100.0, -50.0])
        captured: dict = {}
        _patch_bridge(monkeypatch, captured, metrics=dict(BT_METRICS))

        report = build_report(db, tmp_path, tid)
        # 杠杆缝隙：任务覆写 5 写入 DNA 副本（快照原文 2）
        assert captured["dna"].risk_genes.leverage == 5
        assert json.loads(
            _get_task_dna(db, tid))["risk_genes"]["leverage"] == 2
        # 同策略同任务参数+区间+桥零改动契约值
        assert captured["symbol"] == "BTCUSDT"
        assert captured["timeframe"] == "4h"
        assert captured["start"] == "2026-10-01T04:00:00+00:00"
        assert captured["end"] == _bar_time(9, 240)  # 末根权益 bar
        assert captured["warmup_bars"] == 200
        assert captured["init_cash"] == 50_000.0
        assert captured["fee"] == 0.001
        assert captured["slippage"] == 0.0005
        # 对照成绩+diff（纸盘 50000→54500 → total_return=0.09；bt=0.05）
        assert report["deviation"]["backtest"] == BT_METRICS
        assert report["deviation"]["unavailable_reason"] is None
        diff = report["deviation"]["diff"]
        assert diff["total_return"] == pytest.approx(0.09 - 0.05)
        assert diff["sharpe_ratio"] == pytest.approx(
            report["performance"]["sharpe_ratio"] - 1.5)
        assert diff["max_drawdown"] == pytest.approx(
            report["performance"]["max_drawdown"] - (-0.2))
        assert diff["win_rate"] == pytest.approx(0.5 - 0.6)
        notes = report["deviation"]["attribution_notes"]
        assert any("资金费" in n for n in notes)
        assert any("清算" in n for n in notes)
        assert any("区间边界" in n for n in notes)
        assert any("挂起补记" in n for n in notes)


def _get_task_dna(db: Path, task_id: str) -> str:
    from core.persistence.repository import paper
    return paper.get_task(db, task_id)["dna_snapshot_json"]


class TestEvidenceLayer:
    """证据强度层：60/5 四格+研究状态透传+funding 计数+持仓时长。"""

    @pytest.mark.parametrize("bars,trades,insufficient", [
        (59, 5, True),   # K 线维命中
        (60, 4, True),   # 平仓维命中
        (59, 4, True),   # 双维命中
        (60, 5, False),  # 双过线
    ])
    def test_min_sample_matrix(self, tmp_path, bars, trades, insufficient):
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, bars)
        _seed_exit_fills(db, tid, [100.0 if i % 2 == 0 else -50.0
                                   for i in range(trades)])

        report = build_report(db, tmp_path, tid)
        evidence = report["evidence"]
        assert evidence["bars_run"] == bars
        assert evidence["closed_trades"] == trades
        assert evidence["insufficient_evidence"] is insufficient

    def test_research_status_snapshot_passthrough(self, tmp_path):
        """研究状态四值快照透传（§9.2——创建时快照，报告时不再查库）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db, research_status="verified")
        _seed_equity_bars(db, tid, 3)

        assert build_report(db, tmp_path, tid)["evidence"][
            "research_status"] == "verified"

    def test_funding_counts_mixed(self, tmp_path):
        """三态混合计数：settled 2+disclosure 1→settled=3/pending=1/
        终态后补记披露段（点数+合计金额）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 3)
        _seed_funding(db, tid, [
            {"settle_time": "2026-10-01T08:00:00+00:00", "status": "settled",
             "rate": 0.0001, "amount": -0.03, "position_quantity": 1.5,
             "settle_price": 200.0},
            {"settle_time": "2026-10-01T16:00:00+00:00", "status": "settled",
             "rate": 0.0001, "amount": -0.03, "position_quantity": 1.5,
             "settle_price": 200.0},
            {"settle_time": "2026-10-02T00:00:00+00:00", "status": "pending",
             "rate": None, "amount": None, "position_quantity": 1.5,
             "settle_price": 200.0},
            {"settle_time": "2026-10-02T08:00:00+00:00",
             "status": "settled_disclosure_only", "rate": 0.0002,
             "amount": -0.06, "position_quantity": 1.5,
             "settle_price": 200.0},
        ])

        evidence = build_report(db, tmp_path, tid)["evidence"]
        assert evidence["funding_settled_points"] == 3
        assert evidence["funding_pending_points"] == 1
        assert evidence["funding_post_terminal_backfill"] == {
            "points": 1, "total_amount": pytest.approx(-0.06)}

    def test_no_funding_rows_pending_none(self, tmp_path):
        """无资金费数据：pending=None（与「0 笔挂起」语义区分）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 3)

        evidence = build_report(db, tmp_path, tid)["evidence"]
        assert evidence["funding_settled_points"] == 0
        assert evidence["funding_pending_points"] is None
        assert evidence["funding_post_terminal_backfill"] is None

    def test_position_bars(self, tmp_path):
        """持仓时长披露：bars_processed−entry_seq（无仓=0）。"""
        from core.paper_trading.reporting import build_report
        from core.persistence.repository import paper
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        paper.append_bar(
            db, tid, bar_time=_bar_time(0, 240), fills=[],
            equity={"equity": 50_000.0, "balance": 49_000.0,
                    "unrealized_pnl": 100.0, "position_side": "long"},
            task_state={"bars_processed": 10, "position_entry_bar_seq": 2})

        evidence = build_report(db, tmp_path, tid)["evidence"]
        assert evidence["position_bars"] == 8


class TestSchemaGuard:
    """报告结构无自动判定字段（spec Scenario——考场无观点）。"""

    def test_no_auto_verdict_fields(self, tmp_path, monkeypatch):
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills(db, tid, [100.0, -50.0])
        captured: dict = {}
        _patch_bridge(monkeypatch, captured, metrics=dict(BT_METRICS))

        report = build_report(db, tmp_path, tid)
        keys: set = set()
        _collect_keys(report, keys)
        forbidden = {"verdict", "feasible", "recommendation"}
        assert keys & forbidden == set()
        # 三层结构在场
        for layer in ("performance", "deviation", "evidence"):
            assert layer in report


class TestFinalization:
    """终态固化与未结题判定支持。"""

    @pytest.mark.parametrize("status", ["completed", "stopped", "error"])
    def test_finalize_three_terminal_states(self, tmp_path, status):
        """三终态均固化 version=1（error 态保留已产出数据同样出报告）。"""
        from core.paper_trading.reporting import build_report, finalize_report
        from core.persistence.repository import paper
        db = _make_db(tmp_path)
        tid = _seed_task(db, status=status)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills(db, tid, [100.0, -50.0])

        report = finalize_report(db, tmp_path / "nodata", tid)
        assert report["status"] == status
        row = paper.get_report(db, tid, 1)
        assert row is not None
        assert row["report_version"] == 1
        assert json.loads(row["report_json"])["status"] == status
        # 滚动版与固化版同 schema（三层）
        rolling = build_report(db, tmp_path / "nodata", tid)
        assert set(rolling) == set(json.loads(row["report_json"]))

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_finalize_not_concluded(self, tmp_path, status):
        """未结题固化→TaskNotConcludedError（组 5 转 409 的判定支持）。"""
        from core.paper_trading.reporting import (
            TaskNotConcludedError, finalize_report)
        db = _make_db(tmp_path)
        tid = _seed_task(db, status=status, started=(status == "running"))

        with pytest.raises(TaskNotConcludedError):
            finalize_report(db, tmp_path, tid)

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_get_final_not_concluded(self, tmp_path, status):
        """未结题取 final→TaskNotConcludedError（GET report/final 409 语义
        的报告器层判定）。"""
        from core.paper_trading.reporting import (
            TaskNotConcludedError, get_final_report)
        db = _make_db(tmp_path)
        tid = _seed_task(db, status=status, started=(status == "running"))

        with pytest.raises(TaskNotConcludedError):
            get_final_report(db, tid)

    def test_rolling_report_not_persisted(self, tmp_path):
        """滚动=实时计算不落库（结题固化是唯一写 pt_report 路径）。"""
        from core.paper_trading.reporting import build_report
        from core.persistence.repository import paper
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)

        report = build_report(db, tmp_path / "nodata", tid)
        assert report["status"] == "running"
        assert paper.get_report(db, tid) is None


class TestRevisionPath:
    """终态后补记触发的修订版（design §4.5 版本机制）。"""

    def test_revision_version2_overwrite_and_v1_kept(self, tmp_path):
        """恒 version=2 重算覆盖/初版可取/缺省最新/v1-v2 指标相同差异仅
        funding 披露段/再次触发仍 v2 不增版。"""
        from core.persistence.repository import paper
        from core.paper_trading.reporting import (
            finalize_report, get_final_report, regenerate_final_report)

        db = _make_db(tmp_path)
        tid = _seed_task(db, status="completed")
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills(db, tid, [100.0, -50.0])
        # v1 时点：2 点已结算+1 点挂起
        _seed_funding(db, tid, [
            {"settle_time": "2026-10-01T08:00:00+00:00", "status": "settled",
             "rate": 0.0001, "amount": -0.03, "position_quantity": 1.5,
             "settle_price": 200.0},
            {"settle_time": "2026-10-01T16:00:00+00:00", "status": "settled",
             "rate": 0.0001, "amount": -0.03, "position_quantity": 1.5,
             "settle_price": 200.0},
            {"settle_time": "2026-10-02T00:00:00+00:00", "status": "pending",
             "rate": None, "amount": None, "position_quantity": 1.5,
             "settle_price": 200.0},
        ])
        v1 = finalize_report(db, tmp_path / "nodata", tid,
                             generated_at="2026-10-02T06:00:00+00:00")
        assert v1["evidence"]["funding_settled_points"] == 2
        assert v1["evidence"]["funding_pending_points"] == 1
        assert v1["evidence"]["funding_post_terminal_backfill"] is None

        # 模拟 3.5 终态后回补例程的库效果：走组 2 正式回补接口
        # （pending→settled_disclosure_only，不动 balance）
        backfilled = paper.backfill_funding_rows(db, tid, [
            {"settle_time": "2026-10-02T00:00:00+00:00",
             "rate": 0.0002, "amount": -0.06},
        ])
        assert backfilled == 1

        v2 = regenerate_final_report(
            db, tmp_path / "nodata", tid,
            generated_at="2026-10-02T18:00:00+00:00")
        # 指标数字相同（终态后补记不改成绩）——差异仅 funding 披露段
        assert v2["performance"] == v1["performance"]
        assert v2["evidence"]["funding_settled_points"] == 3
        assert v2["evidence"]["funding_pending_points"] == 0
        assert v2["evidence"]["funding_post_terminal_backfill"] == {
            "points": 1, "total_amount": pytest.approx(-0.06)}

        # 版本机制：缺省最新=v2、初版可取、无第三版
        latest = get_final_report(db, tid)
        assert latest["report_version"] == 2
        assert latest["as_of"] == "2026-10-02T18:00:00+00:00"
        first = get_final_report(db, tid, version=1)
        assert first["report_version"] == 1
        assert first["as_of"] == "2026-10-02T06:00:00+00:00"
        assert first["report"]["evidence"][
            "funding_post_terminal_backfill"] is None
        assert paper.get_report(db, tid, 3) is None

        # 再次触发：恒 v2 重算覆盖不增版
        regenerate_final_report(db, tmp_path / "nodata", tid)
        assert paper.get_report(db, tid, 3) is None
        assert get_final_report(db, tid)["report_version"] == 2

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_revision_not_concluded(self, tmp_path, status):
        """未结题不产修订版（终态封账前提）。"""
        from core.paper_trading.reporting import (
            TaskNotConcludedError, regenerate_final_report)
        db = _make_db(tmp_path)
        tid = _seed_task(db, status=status, started=(status == "running"))

        with pytest.raises(TaskNotConcludedError):
            regenerate_final_report(db, tmp_path, tid)
