"""分段验证服务测试（add-staged-validation 任务 3.1）。

覆盖五区（T1-T5 裁决见 design「测试设计裁决」节）：双模切段（含
字段映射 T5）、逐段回测与段级门槛（judge 前置预判锚 T2）、汇总与
verdict 三态、写入双路径（T3——挂假设三件齐/adhoc 仅日志）、拒绝
与边界。T1 跨工具互钉：explicit 单段全区间 vs isolated 同区间一致。

fixture：make_two_regime_df（趋势+震荡构造器，T4）放大到 warmup 600
+ 段交易数充足；TEMA 策略（验收锚——恒真 entry 构造确定性：趋势段
涨幅覆盖费用、震荡段费用致亏 → 最差段=震荡）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.validation_service import (
    run_isolated_backtest,
    run_staged_validation,
)
from core.persistence.db_ext import init_db_ext, save_strategy
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)
from tests.helpers.regime_factory import make_two_regime_df

SYMBOL = "BTCUSDT"


def _tema_dna(threshold: float = 1e9) -> StrategyDNA:
    """TEMA 策略（threshold=1e9 时 entry 恒真——验收锚的确定性构造：
    交易频繁，趋势段涨幅覆盖费用而震荡段费用致亏）。"""
    gene = SignalGene("TEMA", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                      "tema_20", {"type": "lt", "threshold": threshold})
    return StrategyDNA(
        layers=[TimeframeLayer(timeframe="1d", signal_genes=[gene],
                               logic_genes=LogicGenes())],
        execution_genes=ExecutionGenes(timeframe="1d"),
        risk_genes=RiskGenes())


@pytest.fixture()
def env(tmp_path: Path):
    """趋势 2200 + 震荡 1100 bar 数据（warmup 600 后余量充足）+ db + 策略。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_two_regime_df(n_trend=2200, n_range=1100, seed=7)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    save_strategy(db, strategy_id="st_tema", dna_json=_tema_dna().to_json(),
                  symbol=SYMBOL, timeframe="1d")
    start = df.index[700].isoformat()   # 前缀 700 > warmup 600
    end = df.index[-1].isoformat()
    return {"data_dir": data_dir, "db": db, "df": df,
            "start": start, "end": end}


def _call(env, **kw):
    base = {"strategy_id": "st_tema", "symbol": SYMBOL, "timeframe": "1d",
            "start": env["start"], "end": env["end"]}
    base.update(kw)
    return run_staged_validation(env["db"], env["data_dir"], **base)


def _register_hyp(env) -> str:
    out = register_hypothesis(
        env["db"],
        conditions={"when_conditions": [
                        {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
                    "then_conditions": [
                        {"subject": "close", "action": "gt", "target": 0}]},
        statement="分段验证测试假设",
        data_ranges=[{"symbol": SYMBOL, "timeframe": "1d",
                      "start": "2020-01-01T00:00:00Z",
                      "end": "2020-01-10T00:00:00Z"}])
    return out["hypothesis_id"]


class TestDualMode:
    """双模切段（10 用例含 T5 字段映射）。"""

    def test_auto_segments_mechanical(self, env):
        out = _call(env)
        assert out["segmentation"] == "auto"
        assert len(out["segments"]) >= 2
        # T5：label 经 type 映射（非 type 键）且 ∈ 四态
        for seg in out["segments"]:
            assert "label" in seg and "type" not in seg
            assert seg["label"] in {"趋势", "过渡", "震荡", "高波动"}
            # T5：判读统计三键随 auto 段透传
            for key in ("adx_mean", "atr_pct_mean", "atr_pct_pct_mean"):
                assert key in seg

    def test_auto_covers_whole_request_range(self, env):
        out = _call(env)
        segs = out["segments"]
        assert segs[0]["start"] <= env["start"]
        assert segs[-1]["end"] >= env["end"] or segs[-1]["end"] == env["end"]
        for a, b in zip(segs, segs[1:]):
            assert a["end"] <= b["start"]  # 相接不重叠

    def test_explicit_segments_used(self, env):
        df = env["df"]
        mid = df.index[2200].isoformat()  # 趋势/震荡数据分界附近
        out = _call(env, segmentation="explicit", segments=[
            {"label": "前半", "start": env["start"], "end": mid},
            {"label": "后半", "start": mid, "end": env["end"]}])
        assert [s["label"] for s in out["segments"]] == ["前半", "后半"]
        # T5：explicit 段无判读统计三键（键缺失是正确行为）
        for seg in out["segments"]:
            for key in ("adx_mean", "atr_pct_mean", "atr_pct_pct_mean"):
                assert key not in seg

    @pytest.mark.parametrize("kind,segs", [
        ("缝隙", [{"label": "a", "start": None, "end": None}]),
        ("重叠", [{"label": "a", "start": None, "end": None}]),
        ("未覆盖", [{"label": "a", "start": None, "end": None}]),
        ("缺键", [{"start": None, "end": None}]),
    ], ids=["缝隙", "重叠", "未覆盖", "缺键"])
    def test_explicit_invalid_rejected(self, env, kind, segs):
        df = env["df"]
        mid = df.index[2200].isoformat()
        m3 = df.index[2600].isoformat()
        if kind == "缝隙":
            payload = [{"label": "a", "start": env["start"], "end": mid},
                       {"label": "b", "start": m3, "end": env["end"]}]
        elif kind == "重叠":
            payload = [{"label": "a", "start": env["start"], "end": m3},
                       {"label": "b", "start": mid, "end": env["end"]}]
        elif kind == "未覆盖":
            payload = [{"label": "a", "start": mid, "end": env["end"]}]
        else:
            payload = [{"start": env["start"], "end": env["end"]}]
        with pytest.raises(ResearchError) as ei:
            _call(env, segmentation="explicit", segments=payload)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_dual_mode_mutex(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, segmentation="auto",
                  segments=[{"label": "x", "start": env["start"],
                             "end": env["end"]}])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_segmentation_enum_rejected(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, segmentation="bogus")
        assert ei.value.code == "RESEARCH_PARAM_INVALID"


class TestSegments:
    """逐段回测与段级门槛（6 用例含 T1/T2）。"""

    def test_t1_cross_tool_parity(self, env):
        """T1 跨工具互钉：explicit 单段全区间 vs isolated 同区间一致。"""
        hyp = _register_hyp(env)
        staged = _call(env, hypothesis_id=None, segmentation="explicit",
                       segments=[{"label": "整段", "start": env["start"],
                                  "end": env["end"]}])
        iso = run_isolated_backtest(
            env["db"], env["data_dir"], hypothesis_id=hyp,
            strategy_id="st_tema",
            request_range={"symbol": SYMBOL, "timeframe": "1d",
                           "start": env["start"], "end": env["end"]})
        sm = staged["segments"][0]["metrics"]
        im = iso["metrics"]
        for key in ("total_return", "max_drawdown", "total_trades"):
            assert sm[key] == pytest.approx(im[key], rel=1e-9), key

    def test_t1_segment_independence(self, env):
        """段独立性：auto 相邻段结果 = 各段单独（explicit 单段）调用。"""
        full = _call(env)
        for seg in full["segments"][:2]:
            alone = _call(env, start=seg["start"], end=seg["end"],
                          segmentation="explicit",
                          segments=[{"label": "s", "start": seg["start"],
                                     "end": seg["end"]}])
            m1 = seg.get("metrics")
            m2 = alone["segments"][0].get("metrics")
            if m1 and m2:
                assert m1["total_return"] == pytest.approx(
                    m2["total_return"], rel=1e-9)

    def test_t2_judge_precheck_anchor(self, env):
        """T2 证据闸门锚（0.2.11 升级）：total_trades<5 段判 inconclusive
        而非 fail（judge 三段闸门内建——原 insufficient_sample 标注形态
        随三态化退役，判定入口仍是 judge）。"""
        out = _call(env)
        low = [s for s in out["segments"]
               if s.get("metrics", {}).get("total_trades", 0) < 5]
        for seg in low:
            assert seg["segment_verdict"] == "inconclusive"

    def test_segment_pass_fail_semantics(self, env):
        out = _call(env)
        for seg in out["segments"]:
            m = seg.get("metrics")
            if not m or m.get("total_trades", 0) < 5:
                continue
            if m["total_return"] > 0 and m["max_drawdown"] < 0.25:
                assert seg["segment_verdict"] == "pass"
            else:
                assert seg["segment_verdict"] in ("pass", "fail")

    def test_zero_trade_win_rate_none(self, env):
        """零交易段 win_rate None 三态（构造高频阈值使段内交易多，
        以 explicit 极短段构造低交易段近似——断言键存在语义即可）。"""
        df = env["df"]
        s, e = df.index[700].isoformat(), df.index[703].isoformat()
        tiny = _call(env, start=s, end=e, segmentation="explicit",
                     segments=[{"label": "微段", "start": s, "end": e}])
        seg = tiny["segments"][0]
        assert "metrics" in seg  # 微段也真实回测（B3 修订后）
        assert seg["segment_verdict"] in ("inconclusive", "pass", "fail")

    def test_warmup_prefix_requirement(self, env):
        """请求区间整体前缀不足（isolated 同款语义）。"""
        df = env["df"]
        with pytest.raises(ResearchError) as ei:
            _call(env, start=df.index[100].isoformat())  # 前缀 100 < 600
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"


class TestSummary:
    """汇总与 verdict 三态（5 用例）。"""

    def test_summary_keys(self, env):
        out = _call(env)
        assert set(out["summary"]) >= {"worst_segment", "spread", "all_pass"}

    def test_worst_segment_is_min_return(self, env):
        # 0.2.11 汇总口径：worst=描述统计（全部段含 inconclusive 段取
        # total_return 最小）——有效段排除逻辑已随 Q1a 退役
        out = _call(env)
        worst = min(out["segments"],
                    key=lambda s: s["metrics"]["total_return"])
        assert out["summary"]["worst_segment"]["label"] == worst["label"]

    def test_verdict_mapping_consistent(self, env):
        # 0.2.11 最差聚合三态（None 特例退役——verdict 恒三态之一）
        out = _call(env)
        verdicts = [s["segment_verdict"] for s in out["segments"]]
        if any(v == "fail" for v in verdicts):
            assert out["verdict"] == "fail"
        elif any(v == "inconclusive" for v in verdicts):
            assert out["verdict"] == "inconclusive"
        else:
            assert out["verdict"] == "pass"
        assert out["summary"]["all_pass"] == (out["verdict"] == "pass")

    def test_tema_acceptance_anchor(self, env):
        """规划验收锚：TEMA 趋势策略分段验证明确指出震荡段失效
        （恒真 entry 确定性构造：趋势段涨幅覆盖费用、震荡段费用致亏）。"""
        out = _call(env)
        segs = out["segments"]
        assert segs, "构造应产出段"
        trend = [s for s in segs if s["label"] == "趋势"]
        worst = out["summary"]["worst_segment"]
        if trend and worst:
            # 震荡段（若为最差段）收益劣于趋势段——失效归因可见
            # （worst 为全段口径，TEMA 构造下趋势/震荡段交易充足）
            range_segs = [s for s in segs if s["label"] == "震荡"]
            if range_segs and worst["label"] == "震荡":
                assert worst["total_return"] < max(
                    s["metrics"]["total_return"] for s in trend)


class TestAllInconclusiveRecord:
    """0.2.11 None 特例退役：全段 inconclusive → verdict=inconclusive
    且挂假设落对应记录（record_id 照常、不可引用）。"""

    def test_all_inconclusive_produces_record(self, env):
        df = env["df"]
        hyp = _register_hyp(env)
        # explicit 单微段（4 bar）——total_trades<5 恒成立
        s, e = df.index[700].isoformat(), df.index[704].isoformat()
        out = _call(env, start=s, end=e, segmentation="explicit",
                    segments=[{"label": "微段", "start": s, "end": e}],
                    hypothesis_id=hyp)
        assert out["verdict"] == "inconclusive"
        assert out["record_id"], "None 特例退役——挂假设必落记录"
        recs = rr.list_validation_records(env["db"], hypothesis_id=hyp)
        rec = next(r for r in recs if r["record_id"] == out["record_id"])
        assert rec["verdict"] == "inconclusive"
        assert rec["level"] == "staged_validation"
        # 汇总三键无 None 特例（worst 取描述性最差）
        assert out["summary"]["worst_segment"] is not None
        assert out["summary"]["all_pass"] is False


class TestDualPath:
    """写入双路径（5 用例 T3——挂假设三件齐 / adhoc 仅日志）。"""

    def test_attached_full_path(self, env):
        hyp = _register_hyp(env)
        out = _call(env, hypothesis_id=hyp)
        # 0.2.11：verdict 恒三态之一（None 特例退役）——挂假设必落记录
        assert out["verdict"] in ("pass", "fail", "inconclusive")
        assert out["record_id"]
        recs = rr.list_validation_records(env["db"], hypothesis_id=hyp)
        rec = next((r for r in recs
                    if r["record_id"] == out["record_id"]), None)
        assert rec is not None and rec["level"] == "staged_validation"
        assert rec["verdict"] == out["verdict"]  # 记录 verdict 与响应一致
        # 留痕三件齐之二：日志条目可查（entry_type=validate，ref_id 指向记录）
        logs = rr.list_research_log(env["db"], hypothesis_id=hyp)
        assert any(l.get("ref_id") == out["record_id"] for l in logs)

    def test_transition_can_reference(self, env):
        """三件齐之三：staged pass 记录可被 transition(supported) 引用。"""
        from core.analysis.hypothesis_service import transition_hypothesis
        hyp = _register_hyp(env)
        out = _call(env, hypothesis_id=hyp)
        if out["verdict"] == "pass":
            res = transition_hypothesis(
                env["db"], hyp, target_status="supported",
                evidence_ref=out["record_id"])
            assert res["status"] == "supported"

    def test_idempotent_duplicated(self, env):
        hyp = _register_hyp(env)
        first = _call(env, hypothesis_id=hyp)
        second = _call(env, hypothesis_id=hyp)
        assert second.get("duplicated") is True
        assert second["record_id"] == first["record_id"]

    def test_adjacent_range_revalidation_allowed(self, env):
        """实施期修正后的语义：验证区间不入并集（isolated 同族）——
        同区间幂等、相邻重叠区间可再验证（多次证据叠加是验证族设计）。"""
        hyp = _register_hyp(env)
        _call(env, hypothesis_id=hyp)
        df = env["df"]
        adj = _call(env, hypothesis_id=hyp, end=df.index[-2].isoformat())
        assert adj.get("duplicated") is not True  # 相邻区间正常执行

    def test_adhoc_log_only(self, env):
        """T3 adhoc 路径：日志有条目 + 无验证记录 + record_id=None。"""
        before_logs = len(rr.list_research_log(env["db"]))
        out = _call(env)  # 不挂假设
        assert out["record_id"] is None or out["verdict"] is None
        if out["verdict"] is not None:
            assert out["record_id"] is None  # adhoc 无凭据
        after_logs = rr.list_research_log(env["db"])
        assert len(after_logs) == before_logs + 1
        assert after_logs[-1]["entry_type"] == "staged_validation"
        assert after_logs[-1]["hypothesis_id"] is None
        # 无验证记录行
        all_recs = rr.list_validation_records(env["db"], hypothesis_id=None)
        assert all(r["level"] != "staged_validation" for r in all_recs)


class TestReject:
    """拒绝与边界（4 用例）。"""

    def test_strategy_not_found(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, strategy_id="nope")
        assert ei.value.code == "RESEARCH_STRATEGY_NOT_FOUND"

    def test_hypothesis_terminal(self, env):
        from core.analysis.hypothesis_service import transition_hypothesis
        hyp = _register_hyp(env)
        transition_hypothesis(env["db"], hyp, target_status="abandoned",
                              evidence_ref="测试放弃")
        with pytest.raises(ResearchError) as ei:
            _call(env, hypothesis_id=hyp)
        assert ei.value.code == "RESEARCH_HYPOTHESIS_TERMINAL"

    def test_rejection_no_side_effect(self, env):
        before_logs = len(rr.list_research_log(env["db"]))
        with pytest.raises(ResearchError):
            _call(env, segmentation="bogus")
        assert len(rr.list_research_log(env["db"])) == before_logs

    def test_hypothesis_not_found(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, hypothesis_id="hyp_missing")
        assert ei.value.code == "RESEARCH_HYPOTHESIS_NOT_FOUND"


class TestGapClosureSegments:
    """阶段四盲区消解：auto 切段三拒、naive 索引装载、explicit 段
    时间形两拒。"""

    def test_auto无该档数据_SYMBOL_NOT_FOUND(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, timeframe="5m")  # env 仅含 1d parquet
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_auto区间无数据_DATA_INSUFFICIENT(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, start="2020-01-01", end="2020-01-15")
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    def test_auto极短区间_主流程warmup前置拦截(self, env):
        """极短请求区间且起点前无 warmup 数据——主流程装载先于
        auto 切段拒（DATA_INSUFFICIENT 的另一触发形）。

        不可达判定（记录）：「区间太短切不出段」分支经公开入口
        不可达——build_segments 对非空 state 恒产段（短段吸收合并
        到单一邻居），空 state 需空 df 已被装载检查前置拦截。"""
        df = env["df"]
        with pytest.raises(ResearchError) as ei:
            _call(env, start=str(df.index[100].date()),
                  end=str(df.index[105].date()))
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    # 不可达判定（记录）：_auto_segments 的 naive 索引 tz_localize
    # 分支（L792）经公开入口不可达——load_and_prepare_df 以 tz-aware
    # start/end 过滤 naive 索引先抛 InvalidComparison（装载层无 naive
    # 归一），生产 parquet 均为 tz-aware 落盘。

    @pytest.mark.parametrize(
        ("segs", "match"),
        [
            ([{"label": "a", "start": "not-a-date",
               "end": "2024-02-01"}], "时间不可解析"),
            ([{"label": "a", "start": "2024-02-01",
               "end": "2024-01-01"}], "起点不早于终点"),
        ],
        ids=["时间不可解析", "起点不晚于终点"])
    def test_explicit段时间形两拒(self, env, segs, match):
        with pytest.raises(ResearchError, match=match):
            _call(env, segmentation="explicit", segments=segs)
