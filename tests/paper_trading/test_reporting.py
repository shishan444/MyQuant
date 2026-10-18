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
               research_status: str = "unverified",
               leverage: int = 5, started_at: str = BASE_TIME) -> str:
    """直建任务（测试通道）+按合法迁移链迁到目标态（组 2 缺陷⑤ CAS
    守门后 update_task 拒非法直迁——pending→running→终态）。"""
    from core.persistence.repository import paper
    paper.create_task(
        db, task_id=task_id, strategy_id="strat-1",
        dna_snapshot_json=DNA_JSON, symbol="BTCUSDT", timeframe=timeframe,
        research_status_snapshot=research_status,
        initial_cash=50_000.0, leverage=leverage, window_bars=180)
    if status != "pending":
        paper.update_task(db, task_id, status="running",
                          started_at=started_at)
        if status != "running":
            paper.update_task(db, task_id, status=status)
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


def _seed_exit_fills_of(db: Path, task_id: str,
                        specs: list[tuple[str, str | None]]) -> None:
    """指定 (kind, reason) 的出场类成交逐行落库（exit_reasons 造数面）。

    同 bar 复用既有权益 bar（唯一键吸收）；入场类行（realized_pnl
    None）混入造数验证「出场类=realized_pnl 非 NULL」判据不误收。
    """
    from core.persistence.repository import paper
    for j, (kind, reason) in enumerate(specs):
        is_exit = kind not in ("open", "add")
        paper.append_bar(
            db, task_id, bar_time=_bar_time(10 + j, 240),
            fills=[{"seq": 3 if is_exit else 4, "kind": kind,
                    "side": "long", "price": 105.0, "quantity": 1.0,
                    "fee": 0.1,
                    "realized_pnl": -30.0 if is_exit else None,
                    "reason": reason}],
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
        tid = _seed_task(db, status="pending")

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

    def test_position_bars_same_domain_as_kernel(self, tmp_path):
        """持仓根数=最近 open 起 equity 行数（kernel bars_held=当前 seq−
        入场 seq+1 的库内等价口径，含入场当根）：反手 open 重建锚（不
        从旧仓期起数）、减仓不重置、空仓 0。"""
        from core.paper_trading.reporting import build_report
        from core.persistence.repository import paper
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 5)  # bar 0..4
        # bar1 开多 → bar3 信号平+反手开空（同 bar close+open）
        paper.append_bar(
            db, tid, bar_time=_bar_time(1, 240),
            fills=[{"seq": 4, "kind": "open", "side": "long",
                    "price": 100.0, "quantity": 1.5, "fee": 0.15,
                    "realized_pnl": None, "reason": "signal"}],
            equity={"equity": 50_000.0, "balance": 49_800.0,
                    "unrealized_pnl": 100.0, "position_side": "long"})
        paper.append_bar(
            db, tid, bar_time=_bar_time(3, 240),
            fills=[
                {"seq": 1, "kind": "close", "side": "long",
                 "price": 105.0, "quantity": 1.5, "fee": 0.15,
                 "realized_pnl": 60.0, "reason": "reverse"},
                {"seq": 2, "kind": "open", "side": "short",
                 "price": 105.0, "quantity": 1.4, "fee": 0.15,
                 "realized_pnl": None, "reason": "reverse"},
            ],
            equity={"equity": 50_060.0, "balance": 49_900.0,
                    "unrealized_pnl": 0.0, "position_side": "short"},
            task_state={"bars_processed": 4, "position_side": "short",
                        "position_entry_bar_seq": 3})

        evidence = build_report(db, tmp_path, tid)["evidence"]
        # 反手后新仓自 bar3 起：bar3/4 两根（旧多头 bar1-3 期不计）
        assert evidence["position_bars"] == 2

    def test_position_bars_domain_separation(self, tmp_path):
        """start_after 域分离锚（修复卡 A1）：bars_processed 是任务感知
        计数（start_after 跳过的历史 bar 不+1）、position_entry_bar_seq
        是 feed 全量帧行号——生产场景两域相差数千。持仓根数必须按
        open 起的数据行数计（正数、与实际持仓根数一致）；旧混域相减
        （bars_processed−entry_seq）在此场景=3−101=−98 必红。"""
        from core.paper_trading.reporting import build_report
        from core.persistence.repository import paper
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        # 任务自全量帧第 100 根起（前 100 根为 start_after 跳过的历史）：
        # equity 行=bar 100/101/102 共 3 根，bar 101 入场
        for i in (100, 101, 102):
            paper.append_bar(
                db, tid, bar_time=_bar_time(i, 240),
                fills=([{"seq": 4, "kind": "open", "side": "long",
                         "price": 100.0, "quantity": 1.5, "fee": 0.15,
                         "realized_pnl": None, "reason": "signal"}]
                       if i == 101 else []),
                equity={"equity": 50_000.0, "balance": 49_800.0,
                        "unrealized_pnl": 100.0,
                        "position_side": "long" if i >= 101 else None},
                task_state={"bars_processed": i - 99,
                            "position_side": "long" if i >= 101 else None,
                            "position_entry_bar_seq": 101 if i >= 101
                            else None})

        evidence = build_report(db, tmp_path, tid)["evidence"]
        # 自入场 bar 101 起两根（101/102），为正且与实际持仓根数一致
        assert evidence["position_bars"] == 2
        assert evidence["position_bars"] > 0

    def test_position_bars_flat_zero(self, tmp_path):
        """空仓（position_side=NULL）披露 0。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 3)

        evidence = build_report(db, tmp_path, tid)["evidence"]
        assert evidence["position_bars"] == 0


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
        tid = _seed_task(db, status=status)

        with pytest.raises(TaskNotConcludedError):
            finalize_report(db, tmp_path, tid)

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_get_final_not_concluded(self, tmp_path, status):
        """未结题取 final→TaskNotConcludedError（GET report/final 409 语义
        的报告器层判定）。"""
        from core.paper_trading.reporting import (
            TaskNotConcludedError, get_final_report)
        db = _make_db(tmp_path)
        tid = _seed_task(db, status=status)

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
        tid = _seed_task(db, status=status)

        with pytest.raises(TaskNotConcludedError):
            regenerate_final_report(db, tmp_path, tid)


class TestSingleSourceGuard:
    """绩效指标单源守护（spec R8：指标公式单源引用 compute_metrics
    ——inspect 静态锚；行为守护（bars_per_year 双锚+diff 手算锚）之外
    的防回潮层）。
    """

    def test_compute_metrics_single_source(self):
        """单源三断言：import 在场+调用在场+手写指标公式特征零命中
        （np.std/.cummax/pct_change/.rolling——sharpe/回撤公式体特征；
        total_return 区间口径直算为模块 docstring 声明的合法例外）。"""
        import inspect

        import core.paper_trading.reporting as reporting_mod

        src = inspect.getsource(reporting_mod)
        assert "from core.scoring.metrics import compute_metrics" in src
        assert "compute_metrics(" in src, "消费点须在场"
        for feature in ("np.std", ".cummax", "pct_change", ".rolling"):
            assert feature not in src, (
                f"手写指标公式特征 {feature} 在场——绩效层指标须单源经 "
                f"compute_metrics 计算")


class TestPositionBarsZeroTrades:
    """闸门盲区消解：无 open 成交的 position_bars=0 分支。"""

    def test_position_bars_zero_without_open_fills(self, tmp_path):
        """零交易任务：无 open 成交 → position_bars=0（A1 口径的
        无持仓退化分支）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 5)

        report = build_report(db, tmp_path, tid)
        assert report["evidence"]["position_bars"] == 0


class TestExitReasons:
    """出场原因分布（add-position-sizing-params 族 7a-e）。

    归因口径=close+exit_* 全出场族七种（close 按 reason 细分）；reduce
    仓位管理动作不归键（两层口径分工：reduce 收益计入 performance 层
    trade_returns 组装口径）；无出场 {} 键恒在；权威源=pt_fill（报告
    值==直查值）。
    """

    def test_aggregation_anchor(self, tmp_path):
        """数值锚：两笔金额防线+三笔止损+close 两种细分+max_hold →
        逐类计数正确；reduce 行在场不归键（对质②裁决）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 30)
        _seed_exit_fills_of(db, tid, [
            ("exit_max_loss", "max_loss"),
            ("exit_max_loss", "max_loss"),
            ("exit_sl", "sl"), ("exit_sl", "sl"), ("exit_sl", "sl"),
            ("close", "signal"), ("close", "reverse"),
            ("exit_max_hold", "max_hold"),
            ("reduce", "signal"),
        ])

        exit_reasons = build_report(db, tmp_path, tid)["evidence"][
            "exit_reasons"]
        assert exit_reasons == {
            "exit_max_loss": 2, "exit_sl": 3,
            "close:signal": 1, "close:reverse": 1,
            "exit_max_hold": 1,
        }

    def test_empty_object_key_always_present(self, tmp_path):
        """无出场任务：exit_reasons={}（键恒在——与「无数据」区分）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 3)

        evidence = build_report(db, tmp_path, tid)["evidence"]
        assert "exit_reasons" in evidence
        assert evidence["exit_reasons"] == {}

    def test_entry_and_reduce_fills_not_counted(self, tmp_path):
        """入场类行（realized_pnl NULL）与 reduce 行（仓位管理动作非
        出场）均不入归因聚合（判据面锚）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills_of(db, tid, [
            ("open", "signal"), ("add", "signal"),
            ("reduce", "signal"),
            ("exit_max_loss", "max_loss"),
        ])

        exit_reasons = build_report(db, tmp_path, tid)["evidence"][
            "exit_reasons"]
        assert exit_reasons == {"exit_max_loss": 1}

    def test_reduce_returns_in_performance_not_attribution(self, tmp_path):
        """两层口径分工（对质②裁决）：reduce 的部分实现收益计入
        performance 层（total_trades/trade_returns 组装口径=realized_pnl
        非 NULL 含 reduce），evidence 层归因不含它——防止「realized_
        pnl 非 NULL 行不归键」被误读为覆盖缺口。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills_of(db, tid, [
            ("reduce", "signal"), ("reduce", "signal"),
            ("exit_max_loss", "max_loss"),
        ])

        report = build_report(db, tmp_path / "nodata", tid)
        # 归因层：仅防线类目，无 reduce 键
        assert report["evidence"]["exit_reasons"] == {"exit_max_loss": 1}
        assert "reduce" not in report["evidence"]["exit_reasons"]
        # 绩效层：returns 组装口径含 reduce（3 行 realized_pnl 全计入）
        assert report["performance"]["total_trades"] == 3
        assert report["evidence"]["closed_trades"] == 3

    def test_single_source_parity_with_pt_fill(self, tmp_path):
        """权威单源对拍：报告聚合值==pt_fill 直查同判据聚合值。"""
        from core.paper_trading.reporting import build_report
        from core.persistence.repository import paper
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 30)
        _seed_exit_fills_of(db, tid, [
            ("exit_max_loss", "max_loss"), ("exit_sl", "sl"),
            ("close", "reverse"), ("close", "signal"), ("reduce", "signal"),
        ])

        report_value = build_report(db, tmp_path, tid)["evidence"][
            "exit_reasons"]
        direct: dict = {}
        for f in paper.list_fills(db, tid, limit=100_000):
            if f["realized_pnl"] is None or f["kind"] == "reduce":
                continue
            key = (f"close:{f['reason']}" if f["kind"] == "close"
                   else f["kind"])
            direct[key] = direct.get(key, 0) + 1
        assert report_value == direct

    def test_close_reason_subdivision_unchanged_semantics(self, tmp_path):
        """close 细分：signal 与 reverse 分立两键（不合并为 close 单键）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills_of(db, tid, [
            ("close", "signal"), ("close", "signal"),
            ("close", "reverse"),
        ])

        exit_reasons = build_report(db, tmp_path, tid)["evidence"][
            "exit_reasons"]
        assert exit_reasons["close:signal"] == 2
        assert exit_reasons["close:reverse"] == 1
        assert "close" not in exit_reasons  # 无未细分裸键

    def test_no_new_performance_keys(self, tmp_path):
        """performance 层零触碰：键集不含 exit_reasons（schema 无新
        performance 键——聚合只进 evidence 层）。"""
        from core.paper_trading.reporting import build_report
        db = _make_db(tmp_path)
        tid = _seed_task(db)
        _seed_equity_bars(db, tid, 10)
        _seed_exit_fills_of(db, tid, [("exit_max_loss", "max_loss")])

        report = build_report(db, tmp_path / "nodata", tid)
        assert "exit_reasons" not in report["performance"]
        assert report["evidence"]["exit_reasons"] == {"exit_max_loss": 1}
        # 既有 performance 键面不受影响
        assert "total_trades" in report["performance"]
        assert "win_rate" in report["performance"]


class TestDeviationCarriesNewParams:
    """对照层三断言+深判定（add-position-sizing-params 族 7e+5h）。

    偏差对照经 dna_override（deepcopy 快照）自然携带新参数——零专门
    设计的行为锚；深判定=对照区间内防线触发→声明与不声明对照成绩
    差异（参数被消费的端到端证据）。
    """

    def test_deviation_layer_three_assertions(self, tmp_path, monkeypatch):
        """三断言：对照层在场+计算成功+两侧 DNA 同值含新键（快照与
        dna_override 副本在 initial_fraction/max_position_loss 同值）。"""
        from core.paper_trading.reporting import build_report
        from core.strategy.dna import StrategyDNA

        db = _make_db(tmp_path)
        # 快照声明两新键（含 initial_fraction——传输面全键携带验证）
        dna_json = json.dumps({
            "risk_genes": {"leverage": 2, "initial_fraction": 0.2,
                           "max_position_loss": 500.0}})
        from core.persistence.repository import paper
        paper.create_task(
            db, task_id="pt_dev00001", strategy_id="strat-1",
            dna_snapshot_json=dna_json, symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=50_000.0, leverage=5, window_bars=180)
        paper.update_task(db, "pt_dev00001", status="running",
                          started_at=BASE_TIME)
        _seed_equity_bars(db, "pt_dev00001", 10)
        _seed_exit_fills(db, "pt_dev00001", [100.0])
        captured: dict = {}
        _patch_bridge(monkeypatch, captured, metrics=dict(BT_METRICS))

        report = build_report(db, tmp_path, "pt_dev00001")
        # ① 在场：deviation 层结构键齐备
        for key in ("backtest", "diff", "unavailable_reason",
                    "attribution_notes"):
            assert key in report["deviation"]
        # ② 计算成功：backtest 非 None+无失败原因
        assert report["deviation"]["backtest"] == BT_METRICS
        assert report["deviation"]["unavailable_reason"] is None
        # ③ 两侧 DNA 同值含新键：dna_override 副本携带快照声明值
        snapshot_rg = StrategyDNA.from_dict(
            json.loads(dna_json)).risk_genes
        override_rg = captured["dna"].risk_genes
        assert override_rg.initial_fraction \
            == snapshot_rg.initial_fraction == 0.2
        assert override_rg.max_position_loss \
            == snapshot_rg.max_position_loss == 500.0
        assert override_rg.leverage == 5  # 任务覆写仍生效

    def test_declared_vs_undeclared_performance_differs(
            self, tmp_path, monkeypatch):
        """深判定（族 5h）：同数据同任务参数（除 max_position_loss）
        两任务完整回放——声明侧对照区间内防线触发提前出场、不声明侧
        持仓到窗口尾 → 两报告 performance 成绩差异（参数被消费的
        端到端证据）。"""
        from core.paper_trading.reporting import build_report
        from tests.helpers.data_factory import make_dna
        from tests.paper_trading.test_paper_executor import (
            _Harness, _max_loss_dna)

        reports = {}
        final_equities = {}
        for label, dna in (("declared", _max_loss_dna(100.0)),
                           ("undeclared", make_dna())):
            work_dir = tmp_path / label
            work_dir.mkdir()
            h = _Harness(work_dir, dna=dna, window_bars=50)
            ex = h.make_executor()
            ex.process_frames(h.enhanced, 0)
            captured: dict = {}
            _patch_bridge(monkeypatch, captured, metrics=dict(BT_METRICS))
            report = build_report(h.db, h.data_dir, h.task_id)
            reports[label] = report
            from core.persistence.repository import paper
            final_equities[label] = paper.list_equity_snapshots(
                h.db, h.task_id, limit=100_000)[-1]["equity"]

        declared, undeclared = reports["declared"], reports["undeclared"]
        # 声明侧：防线出场类目在证据层（entry 信号持续亮——出场后
        # 再入场再达线可多次触发，至少一次）
        assert declared["evidence"]["exit_reasons"].get("exit_max_loss", 0) \
            >= 1
        # 不声明侧无防线出场（其持仓最终走止损类目——防线缺位对照）
        assert "exit_max_loss" not in undeclared["evidence"]["exit_reasons"]
        assert undeclared["evidence"]["exit_reasons"].get("exit_sl", 0) >= 1
        # 对照成绩差异：出场类目不同+末根权益不同（防线提前截断 vs
        # 持仓到止损——参数被消费的成绩面证据）
        assert final_equities["declared"] != final_equities["undeclared"]
