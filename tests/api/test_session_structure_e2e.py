"""包三收口测试（add-session-structure-expression 任务 8）。

U9a 多空全链/U9b 抽查两锚/U9c 非法族四项——真实合成数据全链
（登记→翻译→隔离验证）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from tests.helpers.data_factory import make_ohlcv


def _env(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    # 1h 合成数据（含 warmup 前扩余量）
    make_ohlcv(n=2500, freq="1h", start="2023-06-01").to_parquet(
        d / "BTCUSDT_1h.parquet")
    return db, d


class TestEndToEndBothDirections:
    """X4=U9a：SessionRange 突破入场多空两假设全链至隔离验证出结果。"""

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_u9a_full_chain_to_isolated(self, tmp_path, direction):
        db, d = _env(tmp_path)
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.analysis.validation_service import run_isolated_backtest
        from core.persistence.repository.strategy import get_strategy

        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": {"indicator": "SessionRange",
                                 "params": {"start_minute": 0,
                                            "end_minute": 420},
                                 "field": "session_high"},
                     "action": "gt", "target": 60000}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 30}},
                     "action": "gt", "target": 70}],
                "direction": direction},
            statement="时段突破假设",
            # data_ranges=考察期（6-7 月，进判定并集）——验证窗用
            # 其后未消耗时段（考察/验证分离体制）
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1h",
                          "start": "2023-06-01T00:00:00Z",
                          "end": "2023-08-01T00:00:00Z"}],
            strategy_params={"stop_loss": 0.05, "time_exit_utc": 600},
        )["hypothesis_id"]
        out = translate_hypothesis(db, hypothesis_id=hid, data_dir=d,
                                   strategy_timeframe="1h")
        assert out["status"] == "draft"
        assert out["direction"] == direction
        assert out["time_exit_utc"] == 600, "回执三键携带 time_exit"
        genes_raw = get_strategy(db, out["strategy_id"])
        # 结构化 subject 以指标名+params 进 DNA（列名 session_high_0_420
        # 在求值时经 naming 解析——DNA 层锚定指标名）
        assert '"SessionRange"' in genes_raw["dna_json"]

        result = run_isolated_backtest(
            db, d, hypothesis_id=hid, strategy_id=out["strategy_id"],
            request_range={"symbol": "BTCUSDT", "timeframe": "1h",
                           "start": "2023-08-01" if direction == "long"
                           else "2023-09-01",
                           "end": "2023-09-01" if direction == "long"
                           else "2023-10-01"})
        assert result["verdict"] in {"pass", "fail", "inconclusive"}
        assert "metrics" in result and "time_exit" not in result  # 检查无泄漏键
        assert result["direction"] == direction


class TestInspectCasesAnchors:
    """X3=U9b：抽查两锚——pivot 确认前不可见+session t-1。"""

    def test_u9b_pivot_pre_confirmation_invisible(self, tmp_path):
        from core.analysis.case_inspection import inspect_cases
        db, d = _env(tmp_path)
        out = inspect_cases(
            db, d, symbol="BTCUSDT", timeframe="1h",
            start="2023-08-01", end="2023-08-15",
            selector={"condition": {
                "subject": {"indicator": "Pivot",
                            "params": {"left": 5, "right": 5},
                            "field": "pivot_high"},
                "action": "gt", "target": 0}},
        )
        # 确认前不可见：命中案例数 < 全窗 bar 数+context 上界（前
        # left+right 根无已确认枢轴——非全命中）
        assert 0 <= out["n_matched"] < 400
        # 加严（F3）：确认延迟结构锚——e2e 链 warmup 前缀恒充足使
        # 「请求首段零命中」不可观测，下沉列级锚定：Pivot(left=5,
        # right=5) 输出列前 right 根必 NaN（任何候选需后 right 根
        # 确认——确认前候选值不可引用，杜绝未来函数）
        from core.features.indicators import _compute_indicator
        pdf = make_ohlcv(n=80, freq="1h", start="2023-06-01")
        ph = _compute_indicator(
            pdf, "Pivot", {"left": 5, "right": 5})["pivot_high_5_5"]
        assert ph.iloc[:5].isna().all(), "前 right=5 根必 NaN——确认延迟"
        assert ph.notna().any(), "窗内应有已确认枢轴（锚检出力前提）"

    def test_u9b_session_no_lookahead(self, tmp_path):
        from core.analysis.case_inspection import inspect_cases
        db, d = _env(tmp_path)
        out = inspect_cases(
            db, d, symbol="BTCUSDT", timeframe="1h",
            start="2023-08-01", end="2023-08-15",
            selector={"condition": {
                "subject": {"indicator": "SessionRange",
                            "params": {"start_minute": 0,
                                       "end_minute": 420},
                            "field": "session_high"},
                "action": "gt", "target": 0}},
        )
        # t-1 累积：会话首根 bar 不产生 case（NaN 不命中——非全命中）
        assert 0 <= out["n_matched"] < 400
        # 加严（tester 补——新语义 D-3 方向 B 命中结构）：非 NaN ⟺
        # 会话内（t-1 累积）或会话外（终值保持）——命中 bar hour
        # ⊆ {1..23}（0:00 日界重置 NaN 不可命中）；且存在 hour>=8 的
        # 会话外位点（终值保持使会话外可表达——把语义差异钉进 e2e，
        # 防再次静默回退到会话外 NaN）。
        assert out["n_matched"] > 0, "合成数据下会话列应有命中"
        hours = {pd.Timestamp(c["trigger_ts"]).hour for c in out["cases"]}
        assert hours <= set(range(1, 24)), \
            f"命中 bar hour 不得含 0（日界重置 NaN），实际: {hours}"
        assert any(h >= 8 for h in hours), \
            f"应存在会话外（hour>=8）命中位点（终值保持语义），实际: {hours}"


class TestIllegalFamily:
    """X5=U9c：非法族四项全拒。"""

    def test_u9c_four_rejections(self, tmp_path):
        from core.analysis.hypothesis_service import (
            ResearchError, register_hypothesis)
        db, d = _env(tmp_path)
        rng = [{"symbol": "BTCUSDT", "timeframe": "1h",
                "start": "2023-08-01T00:00:00Z",
                "end": "2023-09-01T00:00:00Z"}]
        then = [{"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 70}]
        cases = [
            # ① 时段倒序（cross_params 钩子）
            {"when_conditions": [
                {"subject": {"indicator": "SessionRange",
                             "params": {"start_minute": 500,
                                        "end_minute": 100},
                             "field": "session_high"},
                 "action": "gt", "target": 60000}], "then": then},
            # ② 时刻越界
            {"when": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
             "params": {"time_exit_utc": 1440}},
            # ③ Pivot 确认根数零
            {"when": [
                {"subject": {"indicator": "Pivot",
                             "params": {"left": 0, "right": 5},
                             "field": "pivot_high"},
                 "action": "gt", "target": 0}], "then": then},
        ]
        # ①③ 登记拒
        for c in (cases[0], cases[2]):
            with pytest.raises(ResearchError):
                register_hypothesis(db, conditions={
                    "when_conditions": c["when_conditions"] if "when_conditions" in c else c["when"],
                    "then_conditions": then},
                    statement="s", data_ranges=rng)
        # ② 声明块拒
        with pytest.raises(ResearchError):
            register_hypothesis(db, conditions={
                "when_conditions": cases[1]["when"],
                "then_conditions": then},
                statement="s2", data_ranges=rng,
                strategy_params=cases[1]["params"])
        # ④ 日线引用 hour_utc（域拒）
        with pytest.raises(ResearchError):
            register_hypothesis(db, conditions={
                "when_conditions": [
                    {"subject": "hour_utc", "action": "gt", "target": 12}],
                "then_conditions": then},
                statement="s3",
                data_ranges=[{"symbol": "BTCUSDT", "timeframe": "1d",
                              "start": "2023-08-01T00:00:00Z",
                              "end": "2023-09-01T00:00:00Z"}])
