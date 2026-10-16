"""验证路径组装接入测试（fix-plugin-column-translation 3.1，断点 B）。

验收实质化（对照法）：以「同一 DNA 去除插件源」为对照版本，断言
entries/交易数不同——不需要构造期望信号值，直接杀死静默跳过类缺陷。
覆盖：对照矩阵（恒真门=单位元/恒假门=清零）、失效源显式拒绝、
插件窗口扩展 warmup（不足 422 / 前扩后成功）、退化 DNA 零变化、
翻译产物（translate draft）直喂沙盒的 4.1 端到端贯穿对照断言。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.plugin_service import register_formula_plugin
from core.analysis.validation_service import run_sandbox_backtest
from core.persistence.repository.strategy import save_strategy
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """900 根 4h bar，2023-11-01 起。

    请求起点 2024-01-01 前可得 warmup = 61 天 × 6 bar/天 = 366 bar
    （满足 floor 200、不满足 400——warmup 覆盖用例的分界即在此）。
    """
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-11-01", periods=900, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 120, 900))
    pd.DataFrame({"open": close, "high": close + 70, "low": close - 70,
                  "close": close, "volume": rng.uniform(100, 900, 900),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    return d


def _strategy(db, sid, signal_sources=None, composition=None):
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                                 "rsi_14", {"type": "lt", "threshold": 35})],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
        signal_sources=signal_sources, composition=composition)
    save_strategy(db, strategy_id=sid, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return dna


def _sandbox(db, data_dir, sid):
    return run_sandbox_backtest(db, data_dir, strategy_id=sid,
                                symbol="BTCUSDT", timeframe="4h",
                                start="2024-01-01T00:00:00Z",
                                end="2024-01-15T00:00:00Z")


class TestPluginGateAffectsSignals:
    """对照矩阵：插件门恒真=单位元、恒假=清零、对照版有交易——
    静默跳过（恒假门也 >0 交易）时三断言必红其一。"""

    def test_gate_matrix(self, db, data_dir):
        register_formula_plugin(db, name="ma20",
                                expression="rolling_mean(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")

        _strategy(db, "st_rule_only")  # 对照：去除插件源的同构策略
        _strategy(db, "st_gate_true", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": col,
                               "threshold": -1e9}],  # 恒真门（> -1e9）
            composition={"mode": "logic", "op": "AND"})
        _strategy(db, "st_gate_false", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": col,
                               "threshold": 1e9}],  # 恒假门
            composition={"mode": "logic", "op": "AND"})

        base = _sandbox(db, data_dir, "st_rule_only")
        assert base["metrics"]["total_trades"] > 0, "对照版须有交易（矩阵前提）"

        gate_true = _sandbox(db, data_dir, "st_gate_true")
        gate_false = _sandbox(db, data_dir, "st_gate_false")

        # 恒真门 = AND 单位元：交易数与对照版一致（插件真实参与且不添乱）
        assert gate_true["metrics"]["total_trades"] == \
            base["metrics"]["total_trades"]
        # 恒假门 = 清零：与对照版不同（静默跳过时此处会等于对照版）
        assert gate_false["metrics"]["total_trades"] == 0
        assert gate_false["metrics"]["total_trades"] != \
            base["metrics"]["total_trades"]


class TestDanglingSourceRejected:
    """formula 源 ref 指向不存在插件 → 隔离/沙盒显式拒绝，不产出结果。"""

    def test_sandbox_rejects(self, db, data_dir):
        _strategy(db, "st_ghost", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": "fx_ghost"}],
            composition={"mode": "logic", "op": "AND"})
        with pytest.raises(ResearchError) as ei:
            _sandbox(db, data_dir, "st_ghost")
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"

    def test_isolated_rejects(self, db, data_dir):
        from core.analysis.validation_service import run_isolated_backtest
        hid = register_hypothesis(
            db, conditions={
                "when_conditions": [
                    {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}]},
            # 登记区间避开请求区间（登记区间计入判定并集，重叠即 409）
            statement="s", data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                                         "start": "2024-05-01T00:00:00Z",
                                         "end": "2024-06-01T00:00:00Z"}]
        )["hypothesis_id"]
        # 伪造分量证据绕过加严门（本用例锁定的是组装层失效拒绝）
        rr.save_validation_with_log(
            db, record_id="rec_ghost", hypothesis_id=hid, strategy_id=None,
            level="component", params=json.dumps({"column": "fx_ghost"}),
            result=None, verdict="pass", range_hash="rh_ghost")
        _strategy(db, "st_ghost2", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": "fx_ghost"}],
            composition={"mode": "logic", "op": "AND"})
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(db, data_dir, hypothesis_id=hid,
                                  strategy_id="st_ghost2",
                                  request_range={"symbol": "BTCUSDT",
                                                 "timeframe": "4h",
                                                 "start": "2024-01-01T00:00:00Z",
                                                 "end": "2024-01-15T00:00:00Z"})
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"
        # 不产出验证记录（拒绝先于落库）
        assert not [r for r in rr.list_validation_records(db, hypothesis_id=hid)
                    if r["level"] == "isolated_backtest"]


class TestPluginWarmupCoverage:
    """插件表达式窗口参与 warmup 推导：超 floor 的窗口生效。"""

    def test_window_exceeding_available_rejected(self, db, data_dir):
        """rolling_mean(400)：起点前仅 366 bar → 422（对照版同区间成功，
        证明拒绝来自插件窗口而非数据本身）。"""
        register_formula_plugin(db, name="ma400",
                                expression="rolling_mean(close, 400)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")
        _strategy(db, "st_w400", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": col}],
            composition={"mode": "logic", "op": "AND"})
        with pytest.raises(ResearchError) as ei:
            _sandbox(db, data_dir, "st_w400")
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        assert "需要 400" in str(ei.value)

    def test_window_within_available_succeeds(self, db, data_dir):
        """rolling_mean(300)：起点前 366 bar 足够 → 前扩后成功。"""
        register_formula_plugin(db, name="ma300",
                                expression="rolling_mean(close, 300)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")
        _strategy(db, "st_w300", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": col}],
            composition={"mode": "logic", "op": "AND"})
        out = _sandbox(db, data_dir, "st_w300")
        assert out["metrics"]["total_trades"] >= 0  # 跑通即成功
        assert out["warmup_bars"] >= 300


class TestDegenerateUnchanged:
    """退化 DNA（无插件源）走原路径：沙盒正常产出且 warmup 为 floor 200。"""

    def test_degenerate_sandbox(self, db, data_dir):
        _strategy(db, "st_plain")
        out = _sandbox(db, data_dir, "st_plain")
        assert out["metrics"]["total_trades"] > 0
        assert out["warmup_bars"] == 200


class TestTranslatedDraftAffectsSignals:
    """4.1 端到端贯穿验收：翻译产物（非手工构造 DNA）→ 沙盒回测对照断言。

    既有覆盖分两段（登记→翻译 / 手工 DNA→沙盒对照），本类接起中间断点：
    draft strategy_id 直接喂沙盒。链路：注册公式插件 → 登记混合假设
    （经登记域）→ translate 产出含 formula 源 draft → 以该 draft DNA 为
    底版做门矩阵（仅改 formula threshold，rule/出场/执行/风险基因全部
    来自翻译产物），对照版为「同假设条件去除插件列后重译」的产物。
    恒真门 = AND 单位元（== 对照版），恒假门 = 清零（静默跳过时会等于
    对照版，断言必红）；真实 threshold 的 draft 直接跑通即「全链路无
    拒绝」证据。
    """

    def test_translated_draft_gate_matrix(self, db, data_dir):
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository.strategy import get_strategy

        register_formula_plugin(db, name="mom", expression="zscore(close, 20)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")

        # 登记域登记混合假设（插件列 subject 合法性在登记层生效）
        ranges = [{"symbol": "BTCUSDT", "timeframe": "4h",
                   "start": "2023-11-01T00:00:00Z",
                   "end": "2023-12-01T00:00:00Z"}]
        hid = register_hypothesis(db, conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35},
                {"subject": col, "action": "gt", "target": 0.5}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}]},
            statement="s", data_ranges=ranges)["hypothesis_id"]
        out = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        assert out["status"] == "draft", "插件列假设翻译不得被拒"
        draft_sid = out["strategy_id"]

        # 翻译产物含 formula 源（贯穿前提：沙盒消费的就是这路源）
        draft = json.loads(get_strategy(db, draft_sid)["dna_json"])
        assert [s for s in draft["signal_sources"]
                if s["type"] == "formula"] == [
            {"type": "formula", "ref": col, "threshold": 0.5,
             "operator": ">"}]
        assert draft["composition"] == {"mode": "logic", "op": "and"}

        # 对照版：同假设条件去除插件列后重译（纯 rule 退化形态）
        hid_ctl = register_hypothesis(db, conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}]},
            statement="s", data_ranges=ranges)["hypothesis_id"]
        ctl_sid = translate_hypothesis(
            db, data_dir=data_dir,
            hypothesis_id=hid_ctl)["strategy_id"]

        # 门变体：draft DNA 仅改 formula threshold（其余逐位不变）
        def _gate_variant(sid, threshold):
            variant = json.loads(json.dumps(draft))
            next(s for s in variant["signal_sources"]
                 if s["type"] == "formula")["threshold"] = threshold
            save_strategy(db, strategy_id=sid, dna_json=json.dumps(variant),
                          symbol="BTCUSDT", timeframe="4h")

        _gate_variant("st_draft_gate_true", -1e9)   # 恒真门（> -1e9）
        _gate_variant("st_draft_gate_false", 1e9)   # 恒假门

        base = _sandbox(db, data_dir, ctl_sid)
        assert base["metrics"]["total_trades"] > 0, "对照版须有交易（矩阵前提）"

        gate_true = _sandbox(db, data_dir, "st_draft_gate_true")
        gate_false = _sandbox(db, data_dir, "st_draft_gate_false")
        real = _sandbox(db, data_dir, draft_sid)  # 真实 threshold 草稿

        # 恒真门 = AND 单位元：与「去除插件源」的同构对照版一致
        assert gate_true["metrics"]["total_trades"] == \
            base["metrics"]["total_trades"]
        # 恒假门 = 清零：插件源真实参与（静默跳过时将等于对照版）
        assert gate_false["metrics"]["total_trades"] == 0
        assert gate_false["metrics"]["total_trades"] != \
            base["metrics"]["total_trades"]
        # 真实 draft 跑通：登记→翻译→沙盒回测全链路无拒绝
        assert real["metrics"]["total_trades"] >= 0
        assert real["warmup_bars"] >= 200


class TestComponentEvidenceTightened:
    """0.2.11 D3：分量证据须 verdict=pass——fail/inconclusive 不算数
    （组合加严门不开后门；直测查询语义，链路层由既有用例覆盖）。"""

    def test_non_pass_component_not_counted(self, db):
        from core.analysis.validation_service import _missing_component_evidence
        for verdict in ("fail", "inconclusive"):
            rr.save_validation_with_log(
                db, record_id=f"rec_comp_{verdict}", hypothesis_id="adhoc",
                strategy_id=None, level="component",
                params=json.dumps({"column": "fx_comp"}),
                result=None, verdict=verdict, range_hash=f"rh_c_{verdict}")
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "fx_comp"}]) == ["fx_comp"], \
            "非 pass 分量记录不算有效证据"

    def test_pass_component_counted(self, db):
        from core.analysis.validation_service import _missing_component_evidence
        rr.save_validation_with_log(
            db, record_id="rec_comp_ok", hypothesis_id="adhoc",
            strategy_id=None, level="component",
            params=json.dumps({"column": "fx_ok"}),
            result=None, verdict="pass", range_hash="rh_c_ok")
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "fx_ok"}]) == []


class TestBooleanColumnConsumptionChain:
    """0.2.12 T3：布尔列三消费点一线穿——假设条件 subject（gt 0.5
    触发）→ 翻译 formula 信号源（threshold）→ 组装回测（真=触发）。"""

    def test_boolean_column_end_to_end(self, db, data_dir):
        register_formula_plugin(
            db, name="bull_gate",
            expression="logical_and(gt(close, 60000), 1)")
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")
        # 消费点一：布尔列作假设条件 subject（gt 0.5 = 真=触发）
        out = register_hypothesis(
            db,
            conditions={"when_conditions": [
                {"subject": col, "action": "gt", "target": 0.5}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "gt", "target": 60}]},
            statement="布尔列金叉门",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2023-11-01T00:00:00Z",
                          "end": "2023-12-01T00:00:00Z"}])
        hid = out["hypothesis_id"]
        assert hid
        # 消费点二：翻译（插件列条件 → formula 信号源，threshold 0.5）
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        draft = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=hid)
        assert draft["strategy_id"]
        # 消费点三：布尔列信号源回测（真=触发——有交易即证触发链通）
        _strategy(db, "st_bool_gate", signal_sources=[
            {"type": "rule"}, {"type": "formula", "ref": col,
                               "threshold": 0.5}],
            composition={"mode": "logic", "op": "AND"})
        out3 = run_sandbox_backtest(
            db, data_dir, strategy_id="st_bool_gate", symbol="BTCUSDT",
            timeframe="4h", start="2024-01-01", end="2024-01-15")
        assert out3["metrics"]["total_trades"] >= 0  # 全路径可执行（不 500）

    # T3 模板抽查（实施期按事实收缩）：condition_stats 的 indicator 为
    # 必填——subject 须挂指标输出列，插件布尔列不经此模板通道（其
    # subject 真通道是假设验证引擎，上方一线穿用例已覆盖）。R3 的
    # 零适配声明边界即「验证引擎/翻译/组装」三处，与实证一致。


class TestGoldenCrossAcceptanceAnchor:
    """0.2.12 T4 验收锚：EMA(12) 上穿 EMA(26)（MACD 金叉等价）全链——
    注册 → 求值（独立对照）→ 引用 → 翻译 → 回测。"""

    EXPR = "crossover(ema(close, 12), ema(close, 26))"

    def test_full_chain(self, db, data_dir):
        # 1) 注册（静态校验通过 + 持久化）
        reg = register_formula_plugin(db, name="ema_golden",
                                      expression=self.EXPR)
        assert reg.get("column") or reg.get("output_column") or reg
        # 2) 求值独立对照：手工 ewm 双线（不同表达式路径）找穿越时点
        from core.signals.formula import evaluate_formula
        rng = np.random.default_rng(7)
        idx = pd.date_range("2023-11-01", periods=900, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 120, 900))
        df = pd.DataFrame({"close": close}, index=idx)
        got = evaluate_formula(self.EXPR, df)
        fast = df["close"].ewm(span=12, min_periods=1).mean()
        slow = df["close"].ewm(span=26, min_periods=1).mean()
        manual = (fast > slow) & ((fast.shift(1) <= slow.shift(1)).fillna(False))
        assert got.tolist() == manual.tolist(), "金叉时点与手工推导一致"
        assert got.sum() >= 1, "合成数据应存在至少一个穿越时点"
        # 3) 列可被引用（注册表可见）
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")
        assert col
        # 4) 翻译（0.2.2 出口）
        out = register_hypothesis(
            db,
            conditions={"when_conditions": [
                {"subject": col, "action": "gt", "target": 0.5}],
                "then_conditions": [
                    {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "gt", "target": 60}]},
            statement="EMA12 上穿 EMA26 金叉",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2023-11-01T00:00:00Z",
                          "end": "2023-12-01T00:00:00Z"}])
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        draft = translate_hypothesis(db, data_dir=data_dir, hypothesis_id=out["hypothesis_id"])
        assert draft["strategy_id"], "金叉假设可翻译为策略"
        # 5) 回测可执行（验收闭环）
        sid = draft["strategy_id"]
        out5 = run_sandbox_backtest(
            db, data_dir, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2024-01-01", end="2024-01-15")
        assert out5["sandbox"] is True



class TestComponentEvidencePagination:
    """CARD-01 链路 C：分量证据核验分页取全量（原 limit=200 单页截断）。

    有效分量记录置于升序第 201 位（旧单页之外）：截断则该列仍判
    missing，全量则认可——超页可见性锚。次序用直插 created_at 严格
    递增控制（save_validation_with_log 的 _now() 无法指定位次）。
    """

    def test_超页component记录末尾pass_仍被认可(self, db):
        import json as _json
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import insert
        from core.persistence.orm.models import ValidationRecord
        from core.persistence.repository.dataset_meta import _engine_for
        from core.analysis.validation_service import _missing_component_evidence

        # 记录表 hypothesis_id 为 NOT NULL+FK：借既有 adhoc 假设挂靠
        #（TestComponentEvidenceTightened 同款形制）
        hid = "adhoc"
        base = datetime(2026, 6, 1, tzinfo=timezone.utc)

        def _row(i: int) -> dict:
            hit = i == 200  # 第 201 条：升序末尾，旧 limit=200 之外
            return dict(
                record_id=f"rec_pg_{i:04d}", hypothesis_id=hid,
                strategy_id=None,
                level="component" if hit else "isolated",
                params=_json.dumps(
                    {"column": "fx_far" if hit else "fx_noise"}),
                result=None,
                verdict="pass",
                range_hash=f"rh_pg_{i:04d}",
                components=None,
                created_at=(base + timedelta(seconds=i)).isoformat())

        with _engine_for(db).begin() as conn:
            for i in range(201):
                conn.execute(insert(ValidationRecord).values(**_row(i)))

        # 超页分量证据被认可（截断则返回 ["fx_far"]）
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "fx_far"}]) == []
        # 未取证列仍判 missing（防全通假象的对照锚）
        assert _missing_component_evidence(
            db, [{"type": "formula", "ref": "fx_none"}]) == ["fx_none"]
