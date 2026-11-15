"""M6 沙盒回测测试（improve-mcp-agent-loop 6.1/6.2）。

挂假设后同区间隔离验证被 409（窥探拦截——沙盒区间计入判定并集）；
超 bar 上限拒绝。原「直返 metrics 不产生验证记录」断言已按 R1 反转
（add-sandbox-records-unified-flow 2.1）：沙盒执行现入验证记录统一流
——每次执行恰写一行 level=sandbox_backtest 记录（test_writes_sandbox_
validation_record 原位演进自 test_returns_metrics_no_validation_record）。
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import (
    ResearchError, register_hypothesis)
from core.analysis.isolation import judged_ranges_union
from core.analysis.validation_service import (
    SANDBOX_MAX_BARS, run_isolated_backtest, run_sandbox_backtest)
from core.persistence.repository.strategy import save_strategy
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA)

_CONDS = {
    "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
    "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
}


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(9)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


@pytest.fixture(scope="module")
def strategy_id_module(tmp_path_factory):
    """模块级策略 + 库（沙盒/隔离共用一条策略）。"""
    db = tmp_path_factory.mktemp("sb_mod") / "q.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    # 卡 R 起装载挂全量 validate_dna（Q-1）：补恒假 exit（gt 1e9
    # 永不触发——与原无 exit 构造行为等效，引擎走同默认路径）
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_sb", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return db, "st_sb"


class TestSandboxBacktest:
    def test_writes_sandbox_validation_record(
            self, strategy_id_module, data_dir):
        """R1 反转（add-sandbox-records-unified-flow 2.1）：沙盒执行现入
        验证记录统一流——原「不产生验证记录」断言（assert
        rr.list_validation_records(db) == []）按 R1 反转为「恰 1 行且
        level=sandbox_backtest」（反转依据=R1，禁静默删除——本用例即
        原 test_returns_metrics_no_validation_record 原位演进）。保留
        metrics 直返/trade_returns 不进响应断言（行为不变面）。

        模块级共享库字母序在前已有沙盒写入用例，「恰 1 行」以执行
        前后差集断言：本次执行净增恰 1 行。
        """
        db, sid = strategy_id_module
        before = {r["record_id"] for r in rr.list_validation_records(db)}
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-20",
            fee=0.002, slippage=0.001, init_cash=50000.0)
        assert out["sandbox"] is True
        assert set(out["metrics"]) >= {"total_return", "sharpe_ratio",
                                       "max_drawdown", "total_trades"}
        # 每次沙盒执行写一行 level=sandbox_backtest 记录（R1 反转锚）
        rows = rr.list_validation_records(db)
        new_rows = [r for r in rows if r["record_id"] not in before]
        assert len(new_rows) == 1
        assert new_rows[0]["level"] == "sandbox_backtest"
        assert new_rows[0]["record_id"] == out["record_id"]
        # 0.2.11 四元组改造冒烟：trade_returns 是判定通道不进沙盒响应
        # （沙盒不判门槛不披露 CI——评估差异 1 回归锚）
        assert "trade_returns" not in out
        assert "trade_returns" not in out["metrics"]

    def test_hypothesis_linked_sandbox_enters_union(
            self, data_dir, strategy_id_module):
        """挂假设的沙盒区间计入判定并集——同区间隔离验证被 409。"""
        db, sid = strategy_id_module
        hid = register_hypothesis(
            db, conditions=_CONDS, statement="s",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2023-01-01T00:00:00Z",
                          "end": "2023-02-01T00:00:00Z"}], strategy_params={"stop_loss": 0.05})["hypothesis_id"]
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-20",
            hypothesis_id=hid)
        assert out["hypothesis_id"] == hid
        # 同区间隔离验证：区间重叠被拒（窥探拦截）
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(
                db, data_dir, hypothesis_id=hid, strategy_id=sid,
                request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                               "start": "2023-03-05T00:00:00Z",
                               "end": "2023-03-15T00:00:00Z"})
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP"
        judged = ei.value.detail["judged_ranges"]
        assert any(r["start"].startswith("2023-03-01") for r in judged), \
            "沙盒区间应出现在判定并集明细中"

    def test_over_limit_rejected(self, strategy_id_module, data_dir):
        db, sid = strategy_id_module
        # 4h × 60000 bar ≈ 27 年 > 50k 上限
        with pytest.raises(ResearchError) as ei:
            run_sandbox_backtest(
                db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                timeframe="4h", start="1990-01-01", end="2026-01-01")
        assert ei.value.code == "RESEARCH_SANDBOX_LIMIT"
        assert ei.value.detail["max"] == SANDBOX_MAX_BARS

    def test_adhoc_log_without_hypothesis(self, strategy_id_module, data_dir):
        """不挂假设：adhoc 日志留痕（hypothesis_id=None）。"""
        db, sid = strategy_id_module
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10")
        logs = rr.list_research_log(db)
        assert any(e["entry_type"] == "sandbox_backtest"
                   and e["hypothesis_id"] is None for e in logs)
        # 叠加（TC-6-1 呼应）：表行 hypothesis_id='adhoc'，响应回显 None 如实
        assert out["hypothesis_id"] is None
        adhoc_rows = rr.list_validation_records(db, hypothesis_id="adhoc")
        assert any(r["record_id"] == out["record_id"] for r in adhoc_rows)


@pytest.fixture()
def fresh_env(tmp_path: Path):
    """函数级独立环境（库+策略）——「恰 1 行」类净计数断言用
    （模块级共享库有字母序在前的跨用例残留）。"""
    db = tmp_path / "q.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_fx", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return db, "st_fx"


def _hyp(db: Path) -> str:
    """注册假设（自报区间 2022-11~12——避开沙盒/隔离执行区间，
    并集计数用例互不污染）。"""
    return register_hypothesis(
        db, conditions=_CONDS, statement="统一流契约",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2022-11-01T00:00:00Z",
                      "end": "2022-12-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]


def _sbx_row(db: Path, record_id: str) -> dict:
    """按 record_id 取沙盒表行。"""
    rows = [r for r in rr.list_validation_records(db)
            if r["record_id"] == record_id]
    assert len(rows) == 1
    return rows[0]


class TestSandboxRecordUnification:
    """统一流写入契约组（add-sandbox-records-unified-flow C2——
    TC-1-1~1-9/TC-5-2/TC-6-1/6-2/TC-7-1/7-2）。"""

    def test_sandbox_writes_record_with_contract_fields(
            self, fresh_env, data_dir):
        """TC-1-1：挂 H 沙盒——恰 1 行+三不变值+五列 NULL+record_id==主键。"""
        db, sid = fresh_env
        hid = _hyp(db)
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-20",
            fee=0.002, slippage=0.001, init_cash=50000.0,
            hypothesis_id=hid)
        assert out["record_id"].startswith("rec_")
        assert len(rr.list_validation_records(db)) == 1
        row = _sbx_row(db, out["record_id"])
        assert row["hypothesis_id"] == hid
        assert row["strategy_id"] == sid
        assert row["level"] == "sandbox_backtest"
        assert row["segmentation"] == ""
        assert row["verdict"] == ""
        assert row["pollution_cause"] is None
        assert row["components"] is None
        assert row["signal_trace"] is None
        assert row["trade_semantics"] is None
        assert row["stop_price"] is None
        assert row["created_at"]

    def test_sandbox_result_carries_full_metrics(
            self, fresh_env, data_dir):
        """TC-1-2：result=完整 metrics+四键，不含冗余身份键。"""
        db, sid = fresh_env
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-20",
            hypothesis_id=_hyp(db))
        result = json.loads(_sbx_row(db, out["record_id"])["result"])
        assert set(result) == {"metrics", "symbol", "timeframe",
                               "bars_used", "warmup_bars"}
        metrics = result["metrics"]
        assert {"total_return", "total_trades", "win_rate",
                "max_drawdown", "excess_return"} <= set(metrics)
        assert "sharpe_ratio" in metrics  # 完整携带非五项白名单
        assert result["symbol"] == "BTCUSDT"
        assert result["timeframe"] == "4h"
        assert result["bars_used"] == out["bars_used"]
        assert result["warmup_bars"] == out["warmup_bars"]
        assert result["metrics"] == out["metrics"]

    @pytest.mark.parametrize("extra,expected_n", [
        (dict(), 7),
        (dict(leverage=2), 8),
        (dict(leverage=2, margin_cap=0.5, halt_drawdown_pct=0.1), 10),
    ])
    def test_sandbox_params_optional_keys_written_only_when_passed(
            self, fresh_env, data_dir, extra, expected_n):
        """TC-1-3：params 三态——七恒键+可选键传了才写，无 request_range。"""
        db, sid = fresh_env
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10", **extra)
        params = json.loads(_sbx_row(db, out["record_id"])["params"])
        assert len(params) == expected_n
        assert {"symbol", "timeframe", "start", "end", "fee",
                "slippage", "init_cash"} == set(params) - set(extra)
        for k, v in extra.items():
            assert params[k] == v
        assert "request_range" not in params

    def test_sandbox_record_zero_side_effects(self, fresh_env, data_dir):
        """TC-1-4：写零副作用——明细三表零行+零 validate 日志。"""
        db, sid = fresh_env
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10")
        import sqlite3
        conn = sqlite3.connect(str(db))
        for table in ("vr_trade_detail", "vr_equity_curve",
                      "vr_signal_trace"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE record_id = ?",
                (out["record_id"],)).fetchone()[0]
            assert n == 0, f"{table} 出现沙盒行"
        conn.close()
        logs = rr.list_research_log(db)
        assert not any(e["entry_type"] == "validate" for e in logs)

    def test_sandbox_repeated_runs_independent_rows(
            self, fresh_env, data_dir):
        """TC-1-5：同参数连跑 2 次——2 行互异不覆盖不幂等合并。"""
        db, sid = fresh_env
        kw = dict(strategy_id=sid, symbol="BTCUSDT", timeframe="4h",
                  start="2023-03-01", end="2023-03-10")
        out1 = run_sandbox_backtest(db, data_dir, **kw)
        out2 = run_sandbox_backtest(db, data_dir, **kw)
        rows = rr.list_validation_records(db)
        assert len(rows) == 2
        assert out1["record_id"] != out2["record_id"]
        assert rows[0]["range_hash"] != rows[1]["range_hash"]
        assert all(r["verdict"] == "" for r in rows)

    def test_sandbox_unknown_hypothesis_rejected_before_execution(
            self, fresh_env, data_dir):
        """TC-1-6：假 id 404 三无（先于回测执行）；对照组存在假设正常跑。"""
        db, sid = fresh_env
        with pytest.raises(ResearchError) as ei:
            run_sandbox_backtest(
                db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                timeframe="4h", start="2023-03-01", end="2023-03-10",
                hypothesis_id="hyp_ghost")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_NOT_FOUND"
        assert ei.value.status == 404
        assert ei.value.detail == {"hypothesis_id": "hyp_ghost"}
        assert rr.list_research_log(db) == []
        assert rr.list_validation_records(db) == []
        # 对照组：同区间+存在假设 → 正常跑（证 404 非数据错伪装）
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10",
            hypothesis_id=_hyp(db))
        assert out["record_id"].startswith("rec_")

    def test_sandbox_none_metrics_passthrough(self, tmp_path, data_dir):
        """TC-1-7：恒假入场零成交——metrics 透传+None 序列化 null 保真。"""
        db = tmp_path / "q.db"
        init_db_ext(db)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(db)
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14},
                                     SignalRole.ENTRY_TRIGGER, "rsi_14",
                                     {"type": "lt", "threshold": -1e9}),
                          SignalGene("RSI", {"period": 14},
                                     SignalRole.EXIT_TRIGGER, "rsi_14",
                                     {"type": "gt", "threshold": 1e9})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        save_strategy(db, strategy_id="st_never", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_sandbox_backtest(
            db, data_dir, strategy_id="st_never", symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10")
        row = _sbx_row(db, out["record_id"])
        result = json.loads(row["result"])
        # 透传断言：表行 metrics 与响应 metrics 逐键一致
        assert result["metrics"] == out["metrics"]
        # null 保真：响应侧为 None 的键，表行回读仍 None 不捏造
        # （P4 修复——原断言对照/被断言同取 result 自身恒真；对照侧改
        # out：序列化丢 None 键/变 0 时 result[k] 非 None 或 KeyError 红）
        for k, v in out["metrics"].items():
            if v is None:
                assert result["metrics"][k] is None, \
                    f"metrics.{k} 表行回读 null 保真"

    def test_sandbox_response_keys_backward_compatible(
            self, fresh_env, data_dir):
        """TC-1-8：既有九键全保留+新增恰 record_id+note 逐字不动。"""
        db, sid = fresh_env
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10")
        nine = {"strategy_id", "symbol", "timeframe", "metrics",
                "bars_used", "warmup_bars", "hypothesis_id", "sandbox",
                "note"}
        assert set(out) == nine | {"record_id"}
        assert out["note"] == ("沙盒结果不构成验证证据；关联假设的区间"
                               "已计入判定并集")

    def test_idem_query_level_isolation_bidirectional(
            self, fresh_env, data_dir):
        """TC-1-9：幂等面双向隔离——沙盒行入库不改变幂等面行为。"""
        db, sid = fresh_env
        hid = _hyp(db)
        iso = run_isolated_backtest(
            db, data_dir, hypothesis_id=hid, strategy_id=sid,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-01-05T00:00:00Z",
                           "end": "2023-01-25T00:00:00Z"})
        sbx = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10",
            hypothesis_id=hid)
        rows = {r["record_id"]: r for r in rr.list_validation_records(db)}
        iso_hash = rows[iso["record_id"]]["range_hash"]
        sbx_hash = rows[sbx["record_id"]]["range_hash"]
        # 正向对照：正式 hash 以 isolated level 查命中自身（幂等面未变）
        hit = rr.find_validation_by_identity(
            db, hid, sid, iso_hash, level="isolated_backtest")
        assert hit is not None and hit["record_id"] == iso["record_id"]
        # 沙盒 hash 以 isolated level 查零命中正式行
        assert rr.find_validation_by_identity(
            db, hid, sid, sbx_hash, level="isolated_backtest") is None
        # 正式 hash 以 sandbox level 查零命中沙盒行
        assert rr.find_validation_by_identity(
            db, hid, sid, iso_hash, level="sandbox_backtest") is None

    def test_sandbox_log_entry_form_byte_identical(
            self, fresh_env, data_dir):
        """TC-5-2：research_log 条目形制逐位（并集唯一事实源防漂移锚）。"""
        db, sid = fresh_env
        run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10",
            fee=0.002, slippage=0.001)
        logs = [e for e in rr.list_research_log(db)
                if e["entry_type"] == "sandbox_backtest"]
        assert len(logs) == 1
        entry = logs[0]
        assert entry["hypothesis_id"] is None
        assert entry["ref_id"] == sid
        assert json.loads(entry["data_ranges"]) == [
            {"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2023-03-01", "end": "2023-03-10"}]
        assert entry["summary"] == (
            "sandbox backtest BTCUSDT 4h [2023-03-01,2023-03-10) "
            "fee=0.002 slippage=0.001")

    def test_adhoc_sandbox_writes_adhoc_literal_row(
            self, fresh_env, data_dir):
        """TC-6-1：adhoc 表行 hypothesis_id='adhoc' 可查，statement null。"""
        db, sid = fresh_env
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01", end="2023-03-10")
        assert out["hypothesis_id"] is None  # 响应回显 None 如实
        rows = rr.list_validation_records(db, hypothesis_id="adhoc")
        assert len(rows) == 1
        assert rows[0]["level"] == "sandbox_backtest"
        assert rows[0]["statement"] is None

    def test_adhoc_and_linked_rows_coexist(self, fresh_env, data_dir):
        """TC-6-2：同策略挂 H 与 adhoc 两跑共存不串。"""
        db, sid = fresh_env
        hid = _hyp(db)
        kw = dict(strategy_id=sid, symbol="BTCUSDT", timeframe="4h",
                  start="2023-03-01", end="2023-03-10")
        run_sandbox_backtest(db, data_dir, hypothesis_id=hid, **kw)
        run_sandbox_backtest(db, data_dir, **kw)
        rows = rr.list_validation_records(db)
        assert len(rows) == 2
        assert {r["hypothesis_id"] for r in rows} == {hid, "adhoc"}
        linked = rr.list_validation_records(db, hypothesis_id=hid)
        assert len(linked) == 1 and linked[0]["hypothesis_id"] == hid

    def test_record_write_failure_fails_loud_no_halfsuccess(
            self, fresh_env, data_dir, monkeypatch):
        """TC-7-1：记录写失败裸穿（不成 200 半成功）；日志已落记录零增。"""
        db, sid = fresh_env

        def _boom(*_a, **_k):
            raise RuntimeError("record write failure")

        monkeypatch.setattr(rr, "save_sandbox_validation_record", _boom)
        with pytest.raises(RuntimeError):
            run_sandbox_backtest(
                db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                timeframe="4h", start="2023-03-01", end="2023-03-10")
        logs = [e for e in rr.list_research_log(db)
                if e["entry_type"] == "sandbox_backtest"]
        assert len(logs) == 1  # 步骤 4 已提交
        assert rr.list_validation_records(db) == []

    def test_rerun_recovers_record_union_not_duplicated(
            self, fresh_env, data_dir, monkeypatch):
        """TC-7-2：写失败后重跑同区间——记录补上且并集不重复计数。"""
        db, sid = fresh_env
        hid = _hyp(db)
        before = judged_ranges_union(db, hid)

        def _boom(*_a, **_k):
            raise RuntimeError("record write failure")

        monkeypatch.setattr(rr, "save_sandbox_validation_record", _boom)
        kw = dict(strategy_id=sid, symbol="BTCUSDT", timeframe="4h",
                  start="2023-03-01", end="2023-03-10",
                  hypothesis_id=hid)
        with pytest.raises(RuntimeError):
            run_sandbox_backtest(db, data_dir, **kw)
        monkeypatch.undo()
        after_fail = judged_ranges_union(db, hid)

        out = run_sandbox_backtest(db, data_dir, **kw)
        assert out["record_id"].startswith("rec_")
        rows = rr.list_validation_records(db)
        assert len(rows) == 1  # 记录补上（恰 1 行）
        after_rerun = judged_ranges_union(db, hid)
        assert after_rerun == after_fail  # 重跑不重复计数
        assert len(after_rerun) == len(before) + 1  # 计入且仅计一次
        logs = [e for e in rr.list_research_log(db)
                if e["entry_type"] == "sandbox_backtest"]
        assert len(logs) == 2  # 两条日志如实
